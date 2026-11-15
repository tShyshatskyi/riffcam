#pragma once
#include <juce_audio_utils/juce_audio_utils.h>
#include <juce_dsp/juce_dsp.h>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <memory>

namespace nam { class DSP; }

// JUCE_MAC / JUCE_WINDOWS exist only on their own platform; this works in ordinary expressions everywhere
#if JUCE_MAC
constexpr bool isMac = true;
#else
constexpr bool isMac = false;
#endif

// NaN/Inf test on the bits: fast-math builds may assume NaN never happens and delete std::isfinite checks
inline bool isFiniteSample (float v)
{
    uint32_t bits;
    std::memcpy (&bits, &v, sizeof bits);
    return (bits & 0x7f800000u) != 0x7f800000u;
}

// What sits between the guitar input and the headphones: input gain -> NAM amp model -> cab IR -> VST3 plugin
// -> output gain. Each stage is optional. Loads happen on the message thread; process() runs on the audio
// thread and never waits: while a load swaps a stage in, that one block comes out silent.
class Chain
{
public:
    Chain();
    ~Chain();

    void prepare (double sampleRate, int maxBlock);   // audio stopped
    void process (const float* in, float* out, int n); // realtime-safe, mono in -> mono out

    juce::String loadNam (const juce::File&);          // "" = ok, else the reason
    juce::String loadIr (const juce::File&);
    juce::String loadPlugin (const juce::File&);
    void unloadPlugin();

    juce::AudioPluginInstance* getPlugin() const { return plugin.get(); }
    juce::File namFile, irFile, pluginFile;
    double namRate = 0;                                // sample rate the NAM model was trained at
    double sampleRate = 48000;

    std::function<void (const juce::String&)> log = [] (const juce::String&) {};  // load progress, for tests

    std::atomic<float> inGainDb { 0 }, outGainDb { 0 };
    std::atomic<bool> namOn { true }, irOn { true }, pluginOn { true }, normalize { true };
    std::atomic<int> invalidBlocks { 0 };              // blocks muted because the chain made NaN/Inf

private:
    void protect (float* out, int n);
    juce::CriticalSection lock;
    int maxBlock = 0;
    std::unique_ptr<nam::DSP> nam;
    float namLoudnessGain = 1;
    juce::dsp::Convolution ir;
    bool irLoaded = false;
    std::unique_ptr<juce::AudioPluginInstance> plugin;
    juce::AudioBuffer<float> work, pluginBuf;
    juce::MidiBuffer midi;
};
