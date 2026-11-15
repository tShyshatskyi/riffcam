#include "Chain.h"
#include "NAM/dsp.h"
#include "NAM/get_dsp.h"

Chain::Chain() = default;

Chain::~Chain()
{
    if (plugin)
        plugin->releaseResources();
}

void Chain::prepare (double rate, int block)
{
    const juce::ScopedLock sl (lock);
    sampleRate = rate;
    maxBlock = block;
    work.setSize (2, block);
    if (nam)
        nam->Reset (rate, block);
    ir.prepare ({ rate, (juce::uint32) block, 1 });
    if (plugin)
    {
        plugin->releaseResources();
        plugin->prepareToPlay (rate, block);
        pluginBuf.setSize (juce::jmax (2, plugin->getTotalNumInputChannels(), plugin->getTotalNumOutputChannels()), block);
    }
}

void Chain::process (const float* in, float* out, int n)
{
    const juce::ScopedNoDenormals noDenormals;  // near-silent tails would otherwise cost 10-100x CPU
    const juce::ScopedTryLock sl (lock);
    if (! sl.isLocked() || n > maxBlock)
    {
        juce::FloatVectorOperations::clear (out, n);
        return;
    }

    float* x = work.getWritePointer (0);
    juce::FloatVectorOperations::multiply (x, in, juce::Decibels::decibelsToGain (inGainDb.load()), n);

    if (nam && namOn)
    {
        float* y = work.getWritePointer (1);
        float* src[] = { x };
        float* dst[] = { y };
        nam->process (src, dst, n);
        x = y;
        if (normalize)
            juce::FloatVectorOperations::multiply (x, namLoudnessGain, n);
    }

    if (irLoaded && irOn)
    {
        float* chans[] = { x };
        juce::dsp::AudioBlock<float> block (chans, 1, (size_t) n);
        ir.process (juce::dsp::ProcessContextReplacing<float> (block));
    }

    if (plugin && pluginOn)
    {
        pluginBuf.setSize (pluginBuf.getNumChannels(), n, false, false, true);  // no allocation
        for (int c = 0; c < pluginBuf.getNumChannels(); ++c)
            pluginBuf.copyFrom (c, 0, x, n);
        midi.clear();
        plugin->processBlock (pluginBuf, midi);
        juce::FloatVectorOperations::copy (x, pluginBuf.getReadPointer (0), n);  // amps are mono at heart
    }

    juce::FloatVectorOperations::multiply (out, x, juce::Decibels::decibelsToGain (outGainDb.load()), n);
    protect (out, n);
}

void Chain::protect (float* out, int n)
{
    // a buggy model or plugin must never reach the ears: invalid audio -> silence, the rest capped at 0 dBFS
    for (int i = 0; i < n; ++i)
        if (! isFiniteSample (out[i]))
        {
            juce::FloatVectorOperations::clear (out, n);
            ++invalidBlocks;
            return;
        }
    juce::FloatVectorOperations::clip (out, out, -1.0f, 1.0f, n);
}

juce::String Chain::loadNam (const juce::File& f)
{
    std::unique_ptr<nam::DSP> m;
    try
    {
       #if JUCE_WINDOWS
        m = nam::get_dsp (std::filesystem::path (f.getFullPathName().toWideCharPointer()));  // UTF-16: Cyrillic paths work
       #else
        m = nam::get_dsp (std::filesystem::path (f.getFullPathName().toStdString()));       // UTF-8
       #endif
    }
    catch (const std::exception& e)
    {
        return "Can't read this NAM model: " + juce::String (e.what());
    }
    if (! m)
        return "Can't read this NAM model.";
    if (m->NumInputChannels() != 1 || m->NumOutputChannels() != 1)
        return "This NAM model isn't mono in / mono out.";

    m->Reset (sampleRate, juce::jmax (maxBlock, 32));  // prewarms: no click when it goes live
    // NAM's own plugin levels models to -18 dB loudness, so models of quiet and loud amps sound equally loud
    const float g = m->HasLoudness() ? (float) juce::Decibels::decibelsToGain (-18.0 - m->GetLoudness()) : 1.0f;
    const double rate = m->GetExpectedSampleRate();
    {
        const juce::ScopedLock sl (lock);
        std::swap (nam, m);
        namLoudnessGain = g;
        namRate = rate;
        namFile = f;
    }
    return {};  // the old model is freed here, outside the lock
}

juce::String Chain::loadIr (const juce::File& f)
{
    if (! f.existsAsFile())
        return "No such file.";
    // Convolution loads on its own background thread and swaps the IR in without stopping the audio
    ir.loadImpulseResponse (f, juce::dsp::Convolution::Stereo::no, juce::dsp::Convolution::Trim::yes, 0,
                            juce::dsp::Convolution::Normalise::yes);
    const juce::ScopedLock sl (lock);
    irLoaded = true;
    irFile = f;
    return {};
}

juce::String Chain::loadPlugin (const juce::File& f)
{
    juce::AudioPluginFormatManager formats;
    juce::addDefaultFormatsToManager (formats);  // VST3; plus Audio Unit on macOS
    juce::OwnedArray<juce::PluginDescription> types;
    log ("scanning " + f.getFileName());
    for (auto* format : formats.getFormats())
        if (format->fileMightContainThisPluginType (f.getFullPathName()))
            format->findAllTypesForFile (types, f.getFullPathName());
    if (types.isEmpty())
        return "Not a plugin RiffEngine can load (VST3" + juce::String (isMac ? " or Audio Unit" : "")
             + "), or it's built for another processor (Intel vs Apple Silicon, 32 vs 64-bit).";

    juce::String err;
    log ("creating " + types[0]->name);
    auto p = formats.createPluginInstance (*types[0], sampleRate, juce::jmax (maxBlock, 32), err);
    if (! p)
        return "The plugin didn't start: " + err;

    log ("preparing");
    p->enableAllBuses();
    p->prepareToPlay (sampleRate, juce::jmax (maxBlock, 32));
    log ("ready");
    juce::AudioBuffer<float> buf (juce::jmax (2, p->getTotalNumInputChannels(), p->getTotalNumOutputChannels()),
                                  juce::jmax (maxBlock, 32));
    {
        const juce::ScopedLock sl (lock);
        std::swap (plugin, p);
        std::swap (pluginBuf, buf);
        pluginFile = f;
    }
    if (p)
        p->releaseResources();
    return {};
}

void Chain::unloadPlugin()
{
    std::unique_ptr<juce::AudioPluginInstance> p;
    {
        const juce::ScopedLock sl (lock);
        std::swap (plugin, p);
        pluginFile = {};
    }
    if (p)
        p->releaseResources();
}
