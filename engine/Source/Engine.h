#pragma once
#include "Chain.h"
#include <array>

// The audio device (ASIO first), the realtime callback that runs the guitar through the Chain into the
// headphones, the dry + wet recorders, timing stats, and a round-trip latency test (a cable from an output
// to an input).
class Engine : public juce::AudioIODeviceCallback
{
public:
    Engine();
    ~Engine() override;

    // Opens `saved` device state if given, else `type` (ASIO on Windows, CoreAudio on macOS) / `name` at 48 kHz.
    juce::String open (const juce::XmlElement* saved, juce::String type = {}, juce::String name = {}, int buffer = 0);
    juce::String describe();                  // device, rate, buffer and the latency the driver reports
    bool reconnectIfLost();                   // true while the device is still gone; call every couple of seconds
    juce::String lastError;

    juce::AudioDeviceManager devices;
    Chain chain;
    std::atomic<int> guitarChannel { 0 };     // index among the enabled input channels
    std::atomic<bool> monitor { true };       // false: run everything but send silence out (tests)

    juce::String startRecording (const juce::File& folder);
    void stopRecording();
    bool isRecording() const { return dry != nullptr; }
    juce::File dryFile, wetFile;
    std::atomic<int> lostBlocks { 0 };        // blocks the disk couldn't take in the current recording

    struct Stats { double avgMs = 0, maxMs = 0, budgetMs = 0; int callbacks = 0, overruns = 0, xruns = -1; float inPeak = 0, outPeak = 0; };
    Stats takeStats();                        // since the last call

    void startLoopbackTest();                 // clicks out, listens for them coming back in
    bool loopbackRunning() const { return loopState.load() == 1; }
    juce::String loopbackResult();

    // AudioIODeviceCallback
    void audioDeviceIOCallbackWithContext (const float* const* in, int numIn, float* const* out, int numOut, int n,
                                           const juce::AudioIODeviceCallbackContext&) override;
    void audioDeviceAboutToStart (juce::AudioIODevice*) override;
    void audioDeviceStopped() override {}
    void audioDeviceError (const juce::String& message) override  // audio thread: hand it to the message thread
    {
        juce::MessageManager::callAsync ([this, message] { lastError = message; });
    }

private:
    void loopback (const float* in, float* out, int n);

    std::vector<float> silence, mono;
    double rate = 48000;

    juce::TimeSliceThread writerThread { "RiffEngine writer" };
    juce::CriticalSection writerLock;
    std::unique_ptr<juce::AudioFormatWriter::ThreadedWriter> dry, wet;

    std::atomic<double> sumMs { 0 }, maxMs { 0 }, budget { 0 };
    std::atomic<int> callbacks { 0 }, overruns { 0 };
    std::atomic<float> inPeak { 0 }, outPeak { 0 };

    // loopback test, touched only by the audio thread while loopState == 1
    std::atomic<int> loopState { 0 };        // 0 idle, 1 running, 2 done
    juce::int64 pos = 0, clickAt = -1;
    int found = 0, misses = 0;
    std::array<int, 7> lat {};
};
