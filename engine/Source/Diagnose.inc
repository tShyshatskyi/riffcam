// Included by Main.cpp (inside its anonymous namespace): the --diagnose report.

juce::String run (const juce::String& command)   // a system command's output, for the report
{
    juce::ChildProcess p;
    if (! p.start (command))
        return "(couldn't run: " + command + ")";
    auto out = p.readAllProcessOutput().trim();
    p.waitForProcessToFinish (5000);
    return out;
}

juce::String tail (const juce::File& f, int lines)
{
    juce::StringArray all;
    all.addLines (f.loadFileAsString());
    all.removeRange (0, all.size() - lines);
    return all.joinIntoString ("\n");
}

// --diagnose: everything we can check on someone else's machine, in one report on the Desktop. The report is
// written line by line, so if something crashes on the way, the last line shows where.
int diagnose (const juce::StringArray&)
{
    if (reportFile == juce::File())
        reportFile = juce::File::getSpecialLocation (juce::File::userDesktopDirectory)
                         .getChildFile ("RiffEngine report " + juce::Time::getCurrentTime().formatted ("%Y-%m-%d %H-%M") + ".txt");
    reportFile.deleteFile();
    auto section = [] (const juce::String& t) { say ("\n=== " + t + " ==="); };
    juce::StringArray summary;

    say ("RiffEngine diagnostic report. Send this whole file to Taras.");
    say ("RiffEngine " + juce::String (JUCE_APPLICATION_VERSION_STRING) + ", built " + juce::String (__DATE__) + ", "
         + juce::Time::getCurrentTime().toString (true, true));

    section ("Computer");
    say ("OS: " + juce::SystemStats::getOperatingSystemName());
    say ("CPU: " + juce::SystemStats::getCpuModel() + ", " + juce::String (juce::SystemStats::getNumCpus()) + " threads, AVX2 "
         + (juce::SystemStats::hasAVX2() ? "yes" : "no"));
    say ("RAM: " + juce::String (juce::SystemStats::getMemorySizeInMegabytes() / 1024.0, 1) + " GB");
   #if JUCE_WINDOWS
    say ("Power plan: " + run ("cmd /c chcp 65001 >nul & powercfg /getactivescheme"));  // UTF-8, not the console's old codepage
    say ("Battery: " + run ("powershell -NoProfile -Command \"$b = Get-CimInstance Win32_Battery; if ($b) { if ($b.BatteryStatus -eq 1) { 'on battery' } else { 'plugged in' } } else { 'no battery' }\""));
   #else
    say ("Power: " + run ("pmset -g batt").replace ("\n", " | "));
   #endif

    section ("RiffEngine settings");
    juce::PropertiesFile props (settingsOptions());
    say ("file: " + props.getFile().getFullPathName() + (props.getFile().existsAsFile() ? "" : " (none yet: the window was never opened)"));
    for (auto& key : props.getAllProperties().getAllKeys())
        if (key != "pluginState" && key != "devices")
            say (key + " = " + props.getValue (key));
    auto savedDevice = props.getXmlValue ("devices");
    if (savedDevice)
        say ("device: " + savedDevice->toString (juce::XmlElement::TextFormat().singleLine().withoutHeader()));

    section ("Audio drivers and devices");
    list();

    section ("Self-test");
    const int st = selftest (juce::StringArray { "--selftest" });
    summary.add (st == 0 ? "self-test: all passed" : "self-test: FAILED, see above");

    section ("Amp speed, offline (built-in NAM model)");
    const auto model = embeddedModel();
    const auto pluck = juce::File::getSpecialLocation (juce::File::tempDirectory).getChildFile ("RiffEngine-pluck.wav");
    {
        juce::AudioBuffer<float> b (1, 48000 * 10);
        for (int i = 0; i < b.getNumSamples(); ++i)  // a low E plucked every second
            b.setSample (0, i, 0.3f * std::exp (-3.0f * (float) (i % 48000) / 48000.0f) * std::sin (6.2831853f * 82.4f * (float) i / 48000.0f));
        pluck.deleteFile();
        std::unique_ptr<juce::OutputStream> os = std::make_unique<juce::FileOutputStream> (pluck);
        juce::WavAudioFormat wav;
        if (auto w = wav.createWriterFor (os, juce::AudioFormatWriterOptions{}.withSampleRate (48000).withNumChannels (1).withBitsPerSample (24)))
            w->writeFromAudioSampleBuffer (b, 0, b.getNumSamples());
    }
    for (auto block : { "128", "64" })
        render (juce::StringArray { "--render", pluck.getFullPathName(), pluck.getSiblingFile ("RiffEngine-out.wav").getFullPathName(),
                                    "--nam", model.getFullPathName(), "--block", block });

    section ("Live test on the audio interface (output muted, nothing plays)");
    {
        Engine e;
        e.monitor = false;
        e.chain.log = [] (const juce::String& s) { say ("  [load] " + s); };
        auto err = e.open (savedDevice.get());
        say (e.describe());
        auto* d = e.devices.getCurrentAudioDevice();
        if (d == nullptr)
            summary.add ("live: NO AUDIO DEVICE (" + err + ")");
        else
        {
            e.chain.loadNam (model);
            const int original = d->getCurrentBufferSizeSamples();
            say ("NAM amp, buffer " + juce::String (original) + ":");
            auto r = runLive (e, 8, false);
            summary.add ("live NAM @" + juce::String (original) + ": " + juce::String (r.overruns) + " dropouts in 8 s"
                         + (r.callbacks == 0 ? " (interface not running!)" : ""));

            juce::StringArray sizes;
            for (auto bs : d->getAvailableBufferSizes())
                sizes.add (juce::String (bs));
            say ("buffer sizes the driver offers: " + sizes.joinIntoString (", "));
            if (original == 64)
                say ("buffer 64 test: already at 64");
            else if (! d->getAvailableBufferSizes().contains (64))
                say ("buffer 64 test: skipped, the driver doesn't offer 64 (set it in the interface's own control panel)");
            else
            {
                auto setup = e.devices.getAudioDeviceSetup();
                setup.bufferSize = 64;
                if (auto berr = e.devices.setAudioDeviceSetup (setup, true); berr.isNotEmpty())
                    say ("buffer 64 test: skipped, the driver refused: " + berr);
                else
                {
                    say ("NAM amp, buffer " + juce::String (e.devices.getCurrentAudioDevice()->getCurrentBufferSizeSamples()) + ":");
                    auto r64 = runLive (e, 8, false);
                    summary.add ("live NAM @64: " + juce::String (r64.overruns) + " dropouts in 8 s");
                    setup.bufferSize = original;  // leave the driver as we found it
                    e.devices.setAudioDeviceSetup (setup, true);
                }
            }

            if (auto f = juce::File (props.getValue ("plugin")); f.exists())
            {
                section ("Your plugin: " + f.getFileName());
                say ("loading... (if the report ends here, this plugin crashed RiffEngine)");
                const auto t0 = juce::Time::getMillisecondCounterHiRes();
                auto perr = e.chain.loadPlugin (f);
                const double secs = (juce::Time::getMillisecondCounterHiRes() - t0) / 1000.0;
                if (perr.isNotEmpty())
                {
                    say ("FAILED after " + juce::String (secs, 1) + " s: " + perr);
                    summary.add ("plugin " + f.getFileNameWithoutExtension() + ": failed to load");
                }
                else
                {
                    say ("loaded in " + juce::String (secs, 1) + " s; plugin alone, buffer " + juce::String (original) + ":");
                    e.chain.namOn = false;
                    auto rp = runLive (e, 8, false);
                    summary.add ("plugin " + f.getFileNameWithoutExtension() + ": loads in " + juce::String (juce::roundToInt (secs)) + " s, "
                                 + juce::String (rp.overruns) + " dropouts in 8 s");
                }
            }
        }
    }

    section ("RiffCam logs (last lines)");
    auto dataDir = juce::File::getSpecialLocation (juce::File::userApplicationDataDirectory).getChildFile ("RiffCam");
    for (auto name : { "riffcam.log", "camera.log", "selftest.txt" })
        if (auto f = dataDir.getChildFile (name); f.existsAsFile())
            say ("--- " + juce::String (name) + "\n" + tail (f, 40));

    section ("Summary");
    for (auto& line : summary)
        say ("- " + line);
    say ("\nReport saved: " + reportFile.getFullPathName());
    reportFile.startAsProcess();  // opens it, ready to send
    return 0;
}
