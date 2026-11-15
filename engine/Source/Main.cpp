// RiffEngine: the prototype of RiffCam's own audio engine (what REAPER does for RiffCam today).
//
//   RiffEngine.exe                       the window: pick the interface, load an amp, play, record
//   RiffEngine.exe --diagnose            runs every check below, saves one report on the Desktop (for testers)
//   RiffEngine.exe --list                audio drivers and devices
//   RiffEngine.exe --selftest [--nam F]  checks the safety guards (RISKS.md) without a guitar or an interface
//   RiffEngine.exe --render IN OUT [--nam F] [--ir F] [--vst3 F] [--block N]
//                                        runs a dry recording through the amp offline (how takes get mixed)
//   RiffEngine.exe --rt-test [--type ASIO] [--device NAME] [--buffer N] [--seconds S] [--nam F] [--ir F] [--vst3 F] [--monitor]
//                                        opens the interface and runs the amp live, reporting timing every second
// Add --report FILE to any test to also write what it prints into FILE.
#include "MainComponent.h"
#include "BinaryData.h"
#include <cstdio>

namespace
{
juce::File reportFile;

void say (const juce::String& s)
{
    std::fputs ((s + "\n").toRawUTF8(), stdout);
    std::fflush (stdout);
    if (reportFile != juce::File())
        reportFile.appendText (s + "\n", false, false, "\n");
}

juce::String arg (const juce::StringArray& a, const char* name, const juce::String& fallback = {})
{
    const int i = a.indexOf (name);
    return i >= 0 && i + 1 < a.size() ? a[i + 1].unquoted() : fallback;
}

juce::String loadStages (Chain& c, const juce::StringArray& a)
{
    juce::String err;
    if (auto f = arg (a, "--nam"); f.isNotEmpty() && (err = c.loadNam (juce::File (f))).isNotEmpty()) return err;
    if (auto f = arg (a, "--ir"); f.isNotEmpty() && (err = c.loadIr (juce::File (f))).isNotEmpty()) return err;
    if (auto f = arg (a, "--vst3"); f.isNotEmpty() && (err = c.loadPlugin (juce::File (f))).isNotEmpty()) return err;
    return {};
}

int describe (const juce::StringArray& a)   // the plugin's description as XML, e.g. for JUCE's AudioPluginHost
{
    juce::VST3PluginFormat format;
    juce::OwnedArray<juce::PluginDescription> types;
    format.findAllTypesForFile (types, arg (a, "--describe"));
    for (auto* t : types)
        say (t->createXml()->toString());
    return types.isEmpty() ? 1 : 0;
}

juce::PropertiesFile::Options settingsOptions()   // the GUI's settings file
{
    juce::PropertiesFile::Options o;
    o.applicationName = "RiffEngine";
    o.folderName = "RiffCam";
    o.filenameSuffix = ".settings";
    return o;
}

juce::File embeddedModel()   // a NAM model built into the app, so tests work on a machine with no models
{
    auto f = juce::File::getSpecialLocation (juce::File::tempDirectory).getChildFile ("RiffEngine-test-model.nam");
    if (f.getSize() != BinaryData::wavenet_a1_standard_namSize)
        f.replaceWithData (BinaryData::wavenet_a1_standard_nam, BinaryData::wavenet_a1_standard_namSize);
    return f;
}

// --selftest [--nam MODEL]: the guards from RISKS.md that can be checked without a guitar or an interface.
int selftest (const juce::StringArray& a)
{
    int failed = 0;
    auto check = [&] (bool ok, const juce::String& what, const juce::String& detail = {})
    {
        say ((ok ? "PASS " : "FAIL ") + what + (detail.isNotEmpty() ? "  (" + detail + ")" : juce::String()));
        failed += ok ? 0 : 1;
    };
    constexpr int block = 128;
    constexpr double sr = 48000;
    std::vector<float> in (block), out (block);

    {   // 2: broken audio never reaches the ears
        Chain c;
        c.prepare (sr, block);
        std::fill (in.begin(), in.end(), std::numeric_limits<float>::quiet_NaN());
        c.process (in.data(), out.data(), block);
        check (std::all_of (out.begin(), out.end(), [] (float v) { return v == 0.0f; }) && c.invalidBlocks == 1,
               "NaN from the chain is muted");
        std::fill (in.begin(), in.end(), 10.0f);
        c.process (in.data(), out.data(), block);
        check (juce::FloatVectorOperations::findMaximum (out.data(), block) <= 1.0f, "output limited to 0 dBFS");
    }

    const juce::File model (arg (a, "--nam", embeddedModel().getFullPathName()));
    auto tmp = juce::File::getSpecialLocation (juce::File::tempDirectory).getChildFile (juce::String::fromUTF8 ("RiffEngine тест"));
    tmp.deleteRecursively();
    tmp.createDirectory();
    if (model.existsAsFile())
    {   // 9: non-English paths, 3: denormals
        auto copy = tmp.getChildFile (juce::String::fromUTF8 ("модель підсилювача.nam"));
        model.copyFileTo (copy);
        Chain c;
        c.prepare (sr, block);
        auto err = c.loadNam (copy);
        check (err.isEmpty(), "NAM model loads from a Cyrillic path", err);
        if (err.isEmpty())
        {
            juce::Random rnd (1);
            auto run = [&] (float level, int blocks)
            {
                const auto t0 = juce::Time::getHighResolutionTicks();
                bool finite = true;
                for (int b = 0; b < blocks; ++b)
                {
                    for (auto& v : in) v = level * (rnd.nextFloat() * 2 - 1);
                    c.process (in.data(), out.data(), block);
                    finite = finite && std::all_of (out.begin(), out.end(), [] (float v) { return isFiniteSample (v); });
                }
                return std::pair { juce::Time::highResolutionTicksToSeconds (juce::Time::getHighResolutionTicks() - t0), finite };
            };
            const int blocks = (int) sr / block * 2;  // 2 s of audio
            auto [loud, ok1] = run (0.3f, blocks);
            auto [tiny, ok2] = run (1e-30f, blocks);  // denormal-range input, like a decaying tail
            check (ok1 && ok2, "NAM output stays finite");
            check (tiny < loud * 3, "near-silence costs no more CPU than playing",
                   juce::String (loud * 1000 / blocks, 3) + " vs " + juce::String (tiny * 1000 / blocks, 3) + " ms per block");
            check (loud < 2.0, "NAM runs faster than realtime", juce::String (2.0 / loud, 1) + "x realtime");
        }
    }
    else
        say ("SKIP NAM checks (pass --nam MODEL.nam)");

    {   // 9: recording into a Cyrillic folder
        Engine e;
        auto folder = tmp.getChildFile (juce::String::fromUTF8 ("записи"));
        auto err = e.startRecording (folder);
        e.stopRecording();
        juce::AudioFormatManager fm;
        fm.registerBasicFormats();
        std::unique_ptr<juce::AudioFormatReader> r (fm.createReaderFor (e.dryFile));
        check (err.isEmpty() && r != nullptr && r->numChannels == 1 && r->bitsPerSample == 24,
               "recording writes a valid 24-bit WAV into a Cyrillic folder", err);
    }
    tmp.deleteRecursively();
    say (failed == 0 ? "ALL PASSED" : juce::String (failed) + " FAILED");
    return failed == 0 ? 0 : 1;
}

int list()
{
    juce::AudioDeviceManager dm;
    for (auto* t : dm.getAvailableDeviceTypes())
    {
        t->scanForDevices();
        say (t->getTypeName() + ":");
        for (auto& n : t->getDeviceNames (true))
            say ("  in:  " + n);
        for (auto& n : t->getDeviceNames (false))
            say ("  out: " + n);
    }
    return 0;
}

int render (const juce::StringArray& a)
{
    const int i = a.indexOf ("--render");
    if (i + 2 >= a.size())
        return say ("usage: --render IN.wav OUT.wav [--nam F] [--ir F] [--vst3 F] [--block N]"), 2;
    const juce::File in (a[i + 1].unquoted()), out (a[i + 2].unquoted());
    const int block = arg (a, "--block", "128").getIntValue();

    juce::AudioFormatManager fm;
    fm.registerBasicFormats();
    std::unique_ptr<juce::AudioFormatReader> r (fm.createReaderFor (in));
    if (! r)
        return say ("can't read " + in.getFullPathName()), 1;
    const int len = (int) r->lengthInSamples;
    const double sr = r->sampleRate;
    juce::AudioBuffer<float> src (1, len), dst (1, len);
    r->read (&src, 0, len, 0, true, false);

    Chain c;
    c.log = [] (const juce::String& s) { say ("  [load] " + s); };
    c.prepare (sr, block);
    if (auto err = loadStages (c, a); err.isNotEmpty())
        return say ("load failed: " + err), 1;
    if (c.namFile != juce::File())
        say ("NAM: " + c.namFile.getFileName() + ", trained at " + juce::String (c.namRate, 0) + " Hz; file is " + juce::String (sr, 0) + " Hz");
    if (c.getPlugin())
        say ("VST3: " + c.getPlugin()->getName() + ", " + juce::String (c.getPlugin()->getTotalNumInputChannels()) + " in / "
             + juce::String (c.getPlugin()->getTotalNumOutputChannels()) + " out, reports "
             + juce::String (c.getPlugin()->getLatencySamples()) + " samples latency");

    std::vector<float> zeros ((size_t) block, 0.0f), sink ((size_t) block);
    if (c.irFile != juce::File())
        juce::Thread::sleep (500);          // the IR loads on a background thread
    for (int k = 0; k < (int) (sr * 0.3) / block; ++k)  // let the IR swap in and the plugin settle
        c.process (zeros.data(), sink.data(), block);

    double worst = 0;
    int late = 0, blocks = 0;
    const double budget = block / sr;
    const auto t0 = juce::Time::getHighResolutionTicks();
    for (int p = 0; p < len; p += block)
    {
        const int n = juce::jmin (block, len - p);
        const auto b0 = juce::Time::getHighResolutionTicks();
        c.process (src.getReadPointer (0, p), dst.getWritePointer (0, p), n);
        const double t = juce::Time::highResolutionTicksToSeconds (juce::Time::getHighResolutionTicks() - b0);
        worst = juce::jmax (worst, t);
        late += t > budget;
        ++blocks;
    }
    const double took = juce::Time::highResolutionTicksToSeconds (juce::Time::getHighResolutionTicks() - t0);
    const double dur = len / sr;

    out.deleteFile();
    std::unique_ptr<juce::OutputStream> os = std::make_unique<juce::FileOutputStream> (out);
    juce::WavAudioFormat wav;
    auto w = wav.createWriterFor (os, juce::AudioFormatWriterOptions{}.withSampleRate (sr).withNumChannels (1).withBitsPerSample (24));
    if (! w || ! w->writeFromAudioSampleBuffer (dst, 0, len))
        return say ("can't write " + out.getFullPathName()), 1;
    w.reset();

    say ("rendered " + juce::String (dur, 1) + " s of audio in " + juce::String (took, 2) + " s ("
         + juce::String (dur / took, 1) + "x realtime); worst block " + juce::String (worst * 1000, 3) + " ms of "
         + juce::String (block * 1000.0 / sr, 3) + " ms; late blocks " + juce::String (late) + " of " + juce::String (blocks) + "; peak out " + juce::String (juce::Decibels::gainToDecibels (dst.getMagnitude (0, len)), 1) + " dB");
    return 0;
}

struct LiveResult { int callbacks = 0, overruns = 0; double worstMs = 0, avgMs = 0, budgetMs = 0; };

LiveResult runLive (Engine& e, int secs, bool perSecond)
{
    e.takeStats();
    LiveResult r;
    double sum = 0;
    for (int s = 1; s <= secs; ++s)
    {
        juce::Thread::sleep (1000);
        auto st = e.takeStats();
        r.callbacks += st.callbacks;
        r.overruns += st.overruns;
        r.worstMs = juce::jmax (r.worstMs, st.maxMs);
        r.budgetMs = st.budgetMs;
        sum += st.avgMs;
        if (perSecond)
            say ("t=" + juce::String (s) + "s callbacks " + juce::String (st.callbacks) + ", avg " + juce::String (st.avgMs, 3)
                 + " ms, worst " + juce::String (st.maxMs, 3) + " ms of " + juce::String (st.budgetMs, 3) + " ms, overruns "
                 + juce::String (st.overruns) + ", xruns " + juce::String (st.xruns) + ", in peak "
                 + juce::String (juce::Decibels::gainToDecibels (st.inPeak), 1) + " dB");
    }
    r.avgMs = sum / juce::jmax (1, secs);
    say ("  " + juce::String (secs) + " s: load " + juce::String (juce::roundToInt (r.budgetMs > 0 ? 100 * r.avgMs / r.budgetMs : 0))
         + "%, worst block " + juce::String (r.worstMs, 2) + " of " + juce::String (r.budgetMs, 2) + " ms, dropouts "
         + juce::String (r.overruns) + (r.callbacks == 0 ? "  (NO AUDIO CALLBACKS: the interface isn't running)" : ""));
    return r;
}

int rtTest (const juce::StringArray& a)
{
    Engine e;
    e.chain.log = [] (const juce::String& s) { say ("  [load] " + s); };
    e.monitor = a.contains ("--monitor");
    auto err = e.open (nullptr, arg (a, "--type"), arg (a, "--device"), arg (a, "--buffer", "0").getIntValue());
    say (e.describe());
    if (e.devices.getCurrentAudioDevice() == nullptr)
        return say ("open failed: " + err), 1;
    if (auto le = loadStages (e.chain, a); le.isNotEmpty())
        return say ("load failed: " + le), 1;
    say (juce::String ("chain: ") + (e.chain.namFile != juce::File() ? "NAM " : "") + (e.chain.irFile != juce::File() ? "IR " : "")
         + (e.chain.getPlugin() ? e.chain.getPlugin()->getName() : juce::String()) + (e.monitor ? "" : "  (output muted)"));
    auto r = runLive (e, arg (a, "--seconds", "10").getIntValue(), true);
    return r.callbacks > 0 && r.overruns == 0 ? 0 : 1;
}

#include "Diagnose.inc"
}

//==============================================================================
class RiffEngineApp : public juce::JUCEApplication
{
public:
    const juce::String getApplicationName() override { return "RiffEngine"; }
    const juce::String getApplicationVersion() override { return JUCE_APPLICATION_VERSION_STRING; }
    bool moreThanOneInstanceAllowed() override { return true; }

    void initialise (const juce::String& commandLine) override
    {
        auto a = juce::StringArray::fromTokens (commandLine, true);
        if (auto rf = arg (a, "--report"); rf.isNotEmpty())
            reportFile = juce::File (rf);
        if (a.contains ("--list") || a.contains ("--render") || a.contains ("--rt-test") || a.contains ("--describe") || a.contains ("--selftest") || a.contains ("--diagnose"))
        {
            // after the message loop is up: plugins expect one while they load, like inside any DAW
            juce::MessageManager::callAsync ([this, a]
            {
                const int code = a.contains ("--list") ? list() : a.contains ("--describe") ? describe (a)
                               : a.contains ("--selftest") ? selftest (a) : a.contains ("--diagnose") ? diagnose (a)
                               : a.contains ("--render") ? render (a) : rtTest (a);
                setApplicationReturnValue (code);
                quit();
            });
            return;
        }

        props = std::make_unique<juce::PropertiesFile> (settingsOptions());

        engine = std::make_unique<Engine>();
        auto saved = props->getXmlValue ("devices");
        engine->open (saved.get());
        auto& c = engine->chain;
        if (auto f = juce::File (props->getValue ("nam")); f.existsAsFile()) c.loadNam (f);
        if (auto f = juce::File (props->getValue ("ir")); f.existsAsFile()) c.loadIr (f);
        window = std::make_unique<Window> (*engine, *props);
        auto* ui = static_cast<MainComponent*> (window->getContentComponent());

        // "sessionOpen" is still set if the last run didn't end normally – most likely a plugin crashed it.
        // Auto-loading that plugin again would crash every start, so leave it out and say so.
        const bool crashedLastTime = props->getBoolValue ("sessionOpen", false);
        props->setValue ("sessionOpen", true);
        props->saveIfNeeded();
        if (auto f = juce::File (props->getValue ("plugin")); f.exists())
        {
            if (crashedLastTime)
            {
                ui->say ("RiffEngine closed unexpectedly last time, so " + f.getFileNameWithoutExtension()
                         + " wasn't loaded. Load it again with Load plugin.");
                props->removeValue ("plugin");  // or the next normal start would load it and crash again
                props->saveIfNeeded();
            }
            else
                ui->restorePlugin (f, props->getValue ("pluginState"));
        }
    }

    void shutdown() override
    {
        window = nullptr;   // GUI (and plugin editor) first, then the engine that owns the plugin
        engine = nullptr;
        if (props)
        {
            props->setValue ("sessionOpen", false);  // a normal exit
            props->saveIfNeeded();
        }
        props = nullptr;
    }

    void systemRequestedQuit() override { quit(); }

private:
    struct Window : juce::DocumentWindow
    {
        Window (Engine& e, juce::PropertiesFile& p) : DocumentWindow ("RiffEngine", juce::Colours::black, allButtons)
        {
            setUsingNativeTitleBar (true);
            setContentOwned (new MainComponent (e, p), true);
            setResizable (true, false);
            centreWithSize (getWidth(), getHeight());
            setVisible (true);
        }
        void closeButtonPressed() override { juce::JUCEApplication::getInstance()->systemRequestedQuit(); }
    };

    std::unique_ptr<juce::PropertiesFile> props;
    std::unique_ptr<Engine> engine;
    std::unique_ptr<Window> window;
};

START_JUCE_APPLICATION (RiffEngineApp)
