#include "Engine.h"

Engine::Engine()
{
    writerThread.startThread();
}

Engine::~Engine()
{
    stopRecording();
    devices.removeAudioCallback (this);
    devices.closeAudioDevice();
}

juce::String Engine::open (const juce::XmlElement* saved, juce::String type, juce::String name, int buffer)
{
    auto err = devices.initialise (2, 2, saved, true);
    if (saved == nullptr)
    {
        // low-latency driver per OS: ASIO on Windows, CoreAudio (low latency by itself) on macOS
        devices.setCurrentAudioDeviceType (type.isNotEmpty() ? type : isMac ? "CoreAudio" : "ASIO", true);
        auto s = devices.getAudioDeviceSetup();
        if (name.isNotEmpty())
            s.inputDeviceName = s.outputDeviceName = name;
        s.sampleRate = 48000;  // what NAM models are trained at
        if (buffer > 0)
            s.bufferSize = buffer;
        s.useDefaultInputChannels = s.useDefaultOutputChannels = true;
        err = devices.setAudioDeviceSetup (s, true);
    }
    devices.addAudioCallback (this);
    if (devices.getCurrentAudioDevice() == nullptr && err.isEmpty())
        err = "No audio device opened.";
    lastError = err;
    return err;
}

juce::String Engine::describe()
{
    auto* d = devices.getCurrentAudioDevice();
    if (d == nullptr)
        return "No audio device" + (lastError.isNotEmpty() ? ": " + lastError : juce::String())
             + "\nIs the interface plugged in? Close REAPER or other audio apps (an ASIO driver serves one app at a time).";
    const double sr = d->getCurrentSampleRate();
    const int bs = d->getCurrentBufferSizeSamples(), li = d->getInputLatencyInSamples(), lo = d->getOutputLatencyInSamples();
    auto ms = [sr] (int s) { return juce::String (s * 1000.0 / sr, 1) + " ms"; };
    return d->getTypeName() + " / " + d->getName() + "\n" + juce::String (sr, 0) + " Hz, buffer " + juce::String (bs)
         + " samples (" + ms (bs) + ")\nDriver says: in " + ms (li) + " + out " + ms (lo) + " = " + ms (li + lo)
         + " guitar to headphones";
}

void Engine::audioDeviceAboutToStart (juce::AudioIODevice* d)
{
    rate = d->getCurrentSampleRate();
    const int bs = d->getCurrentBufferSizeSamples();
    const size_t room = (size_t) juce::jmax (bs * 2, 4096);  // some drivers hand over a bigger block now and then
    silence.assign (room, 0.0f);
    mono.assign (room, 0.0f);
    chain.prepare (rate, (int) room);
    takeStats();
}

void Engine::audioDeviceIOCallbackWithContext (const float* const* in, int numIn, float* const* out, int numOut, int n,
                                               const juce::AudioIODeviceCallbackContext&)
{
    const auto t0 = juce::Time::getHighResolutionTicks();
    if ((size_t) n > mono.size())
    {
        for (int c = 0; c < numOut; ++c)
            if (out[c] != nullptr)
                juce::FloatVectorOperations::clear (out[c], n);
        return;
    }

    const int ch = guitarChannel.load();
    const float* g = (ch < numIn && in[ch] != nullptr) ? in[ch] : silence.data();
    float* o = mono.data();

    if (loopState.load() == 1)
        loopback (g, o, n);
    else
        chain.process (g, o, n);

    {
        const juce::ScopedTryLock sl (writerLock);  // a stop in progress can't make us wait
        if (sl.isLocked() && dry != nullptr)
        {
            const float* d[] = { g };
            const float* w[] = { o };
            if (! dry->write (d, n) | ! wet->write (w, n))
                ++lostBlocks;   // the disk didn't keep up: this block has a gap
        }
    }

    const bool live = monitor.load() || loopState.load() == 1;
    for (int c = 0; c < numOut; ++c)
        if (out[c] != nullptr)
        {
            if (live && c < 2)
                juce::FloatVectorOperations::copy (out[c], o, n);
            else
                juce::FloatVectorOperations::clear (out[c], n);
        }

    const float ip = juce::FloatVectorOperations::findMaximum (g, n), im = -juce::FloatVectorOperations::findMinimum (g, n);
    const float op = juce::FloatVectorOperations::findMaximum (o, n), om = -juce::FloatVectorOperations::findMinimum (o, n);
    if (juce::jmax (ip, im) > inPeak.load()) inPeak = juce::jmax (ip, im);
    if (juce::jmax (op, om) > outPeak.load()) outPeak = juce::jmax (op, om);

    const double ms = juce::Time::highResolutionTicksToSeconds (juce::Time::getHighResolutionTicks() - t0) * 1000.0;
    const double b = n * 1000.0 / rate;
    budget = b;
    sumMs = sumMs.load() + ms;
    if (ms > maxMs.load()) maxMs = ms;
    if (ms > b) ++overruns;   // took longer than the sound it made: that's a dropout
    ++callbacks;
    pos += n;
}

Engine::Stats Engine::takeStats()
{
    Stats s;
    s.callbacks = callbacks.exchange (0);
    s.avgMs = s.callbacks ? sumMs.exchange (0) / s.callbacks : 0;
    s.maxMs = maxMs.exchange (0);
    s.budgetMs = budget.load();
    s.overruns = overruns.exchange (0);
    s.inPeak = inPeak.exchange (0);
    s.outPeak = outPeak.exchange (0);
    if (auto* d = devices.getCurrentAudioDevice())
        s.xruns = d->getXRunCount();
    return s;
}

juce::String Engine::startRecording (const juce::File& folder)
{
    stopRecording();
    folder.createDirectory();
    const auto stamp = juce::Time::getCurrentTime().formatted ("%Y-%m-%d %H-%M-%S");
    auto make = [&] (const juce::File& f) -> std::unique_ptr<juce::AudioFormatWriter::ThreadedWriter>
    {
        f.deleteFile();
        std::unique_ptr<juce::OutputStream> os = std::make_unique<juce::FileOutputStream> (f);
        if (! static_cast<juce::FileOutputStream*> (os.get())->openedOk())
            return nullptr;
        juce::WavAudioFormat wav;
        auto w = wav.createWriterFor (os, juce::AudioFormatWriterOptions{}.withSampleRate (rate).withNumChannels (1).withBitsPerSample (24));
        return w ? std::make_unique<juce::AudioFormatWriter::ThreadedWriter> (w.release(), writerThread, 1 << 16) : nullptr;
    };
    auto d = make (folder.getChildFile (stamp + " dry.wav"));
    auto w = make (folder.getChildFile (stamp + " wet.wav"));
    if (! d || ! w)
        return "Can't write to " + folder.getFullPathName();
    lostBlocks = 0;
    dryFile = folder.getChildFile (stamp + " dry.wav");
    wetFile = folder.getChildFile (stamp + " wet.wav");
    const juce::ScopedLock sl (writerLock);
    dry = std::move (d);
    wet = std::move (w);
    return {};
}

void Engine::stopRecording()
{
    std::unique_ptr<juce::AudioFormatWriter::ThreadedWriter> d, w;
    {
        const juce::ScopedLock sl (writerLock);
        std::swap (dry, d);
        std::swap (wet, w);
    }
    // destroying the writers flushes what's left and finishes the wav headers
}

bool Engine::reconnectIfLost()
{
    auto* d = devices.getCurrentAudioDevice();
    if (d != nullptr && d->isPlaying())
        return false;
    devices.restartLastAudioDevice();  // unplugged, slept, or taken by another app: try the last setup again
    d = devices.getCurrentAudioDevice();
    return ! (d != nullptr && d->isPlaying());
}

void Engine::startLoopbackTest()
{
    clickAt = -1;
    found = misses = 0;
    loopState = 1;
}

void Engine::loopback (const float* in, float* out, int n)
{
    juce::FloatVectorOperations::clear (out, n);
    const juce::int64 period = (juce::int64) (rate * 0.5);  // a click every half second
    for (int i = 0; i < n; ++i)
    {
        const juce::int64 now = pos + i;
        if (clickAt >= 0 && std::abs (in[i]) > 0.05f)       // it came back
        {
            lat[(size_t) found++] = (int) (now - clickAt);
            clickAt = -1;
            if (found == (int) lat.size())
            {
                loopState = 2;
                return;
            }
        }
        if (clickAt >= 0 && now - clickAt > period - 1)     // never came back: no cable or too quiet
        {
            clickAt = -1;
            if (++misses > 6)
            {
                loopState = 2;
                return;
            }
        }
        if (clickAt < 0 && now % period == 0)
        {
            out[i] = 0.5f;
            clickAt = now;
        }
    }
}

juce::String Engine::loopbackResult()
{
    if (loopState.load() != 2)
        return {};
    loopState = 0;
    if (found < (int) lat.size())
        return "No click came back. Connect an output to the guitar input with a cable, turn the input gain up, try again.";
    auto v = lat;
    std::sort (v.begin(), v.end());
    const int med = v[v.size() / 2];
    return "Measured round trip: " + juce::String (med * 1000.0 / rate, 1) + " ms (" + juce::String (med)
         + " samples, spread " + juce::String ((v.back() - v.front()) * 1000.0 / rate, 1) + " ms)";
}
