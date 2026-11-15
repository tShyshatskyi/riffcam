#include "MainComponent.h"

static juce::File pluginFolder()
{
   #if JUCE_MAC
    return juce::File ("/Library/Audio/Plug-Ins/Components");
   #else
    return juce::File::getSpecialLocation (juce::File::globalApplicationsDirectory).getChildFile ("Common Files").getChildFile ("VST3");
   #endif
}

static const juce::Colour BG (0xff101214), PANEL (0xff1a1d20), ACID (0xff9dff00), HOT (0xffff2a4a), DIM (0xff8a9098);

void Meter::set (float peak)
{
    const float v = juce::Decibels::gainToDecibels (peak, -100.0f);
    db = juce::jmax (v, db - 3.0f);  // fall slowly so peaks are readable
    if (v >= hold || --holdTicks <= 0)
    {
        hold = v;
        holdTicks = 15;
    }
    repaint();
}

void Meter::paint (juce::Graphics& g)
{
    auto r = getLocalBounds().toFloat();
    g.setColour (juce::Colours::black);
    g.fillRect (r);
    auto x = [&] (float d) { return r.getX() + r.getWidth() * juce::jlimit (0.0f, 1.0f, (d + 60.0f) / 60.0f); };
    g.setColour (db > -1.0f ? HOT : ACID);
    g.fillRect (r.withRight (x (db)));
    g.setColour (hold > -1.0f ? HOT : juce::Colours::white);
    g.fillRect (juce::Rectangle<float> (x (hold) - 1.0f, r.getY(), 2.0f, r.getHeight()));
}

//==============================================================================
MainComponent::MainComponent (Engine& e, juce::PropertiesFile& p)
    : engine (e), props (p), selector (e.devices, 1, 2, 1, 2, false, false, false, false)
{
    addAndMakeVisible (selector);
    for (auto* c : { &inputLabel, &inGainLabel, &outGainLabel })
        addAndMakeVisible (*c);
    addAndMakeVisible (inputBox);
    inputBox.onChange = [this] { engine.guitarChannel = juce::jmax (0, inputBox.getSelectedItemIndex()); };
    addAndMakeVisible (inMeter);
    addAndMakeVisible (outMeter);

    auto gain = [this] (juce::Slider& s, double lo, double hi, std::atomic<float>& target, const char* key)
    {
        s.setRange (lo, hi, 0.5);
        s.setTextValueSuffix (" dB");
        s.setTextBoxStyle (juce::Slider::TextBoxRight, false, 70, 20);
        s.setValue (props.getDoubleValue (key, 0.0), juce::dontSendNotification);
        target = (float) s.getValue();
        s.onValueChange = [&s, &target] { target = (float) s.getValue(); };
        addAndMakeVisible (s);
    };
    gain (inGain, -24, 24, engine.chain.inGainDb, "inGain");
    gain (outGain, -40, 12, engine.chain.outGainDb, "outGain");

    auto toggle = [this] (juce::ToggleButton& b, std::atomic<bool>& target, const char* key)
    {
        b.setToggleState (props.getBoolValue (key, true), juce::dontSendNotification);
        target = b.getToggleState();
        b.onClick = [&b, &target] { target = b.getToggleState(); };
        addAndMakeVisible (b);
    };
    toggle (namOn, engine.chain.namOn, "namOn");
    toggle (irOn, engine.chain.irOn, "irOn");
    toggle (pluginOn, engine.chain.pluginOn, "pluginOn");
    toggle (normalize, engine.chain.normalize, "normalize");

    for (auto* b : { &namBtn, &irBtn, &pluginBtn, &editorBtn, &unloadBtn, &recBtn, &loopBtn })
        addAndMakeVisible (*b);
    for (auto* l : { &namName, &irName, &pluginName, &info, &stats, &recLabel, &loopLabel, &warning })
    {
        l->setColour (juce::Label::textColourId, DIM);
        addAndMakeVisible (*l);
    }
    warning.setColour (juce::Label::textColourId, HOT);
    info.setJustificationType (juce::Justification::topLeft);
    stats.setFont (juce::FontOptions (juce::Font::getDefaultMonospacedFontName(), 13.0f, juce::Font::plain));
    recBtn.setColour (juce::TextButton::buttonColourId, HOT.darker (0.6f));

    namBtn.onClick = [this] { choose ("NAM amp model", "*.nam", "nam", [this] (const juce::File& f)
    {
        auto err = engine.chain.loadNam (f);
        if (err.isEmpty())
            matchModelRate();
        return err;
    }); };
    irBtn.onClick = [this] { choose ("Cabinet impulse response", "*.wav", "ir", [this] (const juce::File& f) { return engine.chain.loadIr (f); }); };
    pluginBtn.onClick = [this]
    {
        chooser = std::make_unique<juce::FileChooser> ("Plugin", pluginFolder(), isMac ? "*.vst3;*.component" : "*.vst3");
        chooser->launchAsync (juce::FileBrowserComponent::openMode | juce::FileBrowserComponent::canSelectFiles
                                  | juce::FileBrowserComponent::canSelectDirectories,  // .vst3 can be a folder bundle
                              [this] (const juce::FileChooser& fc) { if (fc.getResult() != juce::File()) loadPlugin (fc.getResult()); });
    };
    editorBtn.onClick = [this] { openEditor(); };
    unloadBtn.onClick = [this]
    {
        closeEditor();
        engine.chain.unloadPlugin();
        props.removeValue ("plugin");
        props.removeValue ("pluginState");
        updateNames();
    };
    recBtn.onClick = [this]
    {
        if (engine.isRecording())
        {
            engine.stopRecording();
            recBtn.setButtonText ("Record");
            recLabel.setText ("Saved: " + engine.dryFile.getFileName() + " + wet.wav in " + engine.dryFile.getParentDirectory().getFullPathName(),
                              juce::dontSendNotification);
            return;
        }
        auto folder = juce::File::getSpecialLocation (juce::File::userMoviesDirectory).getChildFile ("RiffCam").getChildFile ("engine-tests");
        auto err = engine.startRecording (folder);
        if (err.isNotEmpty())
            return recLabel.setText (err, juce::dontSendNotification);
        recStarted = juce::Time::getMillisecondCounterHiRes();
        recBtn.setButtonText ("Stop");
    };
    loopBtn.onClick = [this]
    {
        engine.startLoopbackTest();
        loopLabel.setText ("Clicking... (cable from an output into the guitar input)", juce::dontSendNotification);
    };

    engine.guitarChannel = props.getIntValue ("guitarChannel", 0);
    engine.devices.addChangeListener (this);
    refreshDevice();
    updateNames();
    setSize (980, 640);
    startTimerHz (15);
}

MainComponent::~MainComponent()
{
    engine.devices.removeChangeListener (this);
    saveState();
    closeEditor();
}

void MainComponent::saveState()
{
    if (auto xml = engine.devices.createStateXml())
        props.setValue ("devices", xml.get());
    props.setValue ("inGain", inGain.getValue());
    props.setValue ("outGain", outGain.getValue());
    props.setValue ("namOn", namOn.getToggleState());
    props.setValue ("irOn", irOn.getToggleState());
    props.setValue ("pluginOn", pluginOn.getToggleState());
    props.setValue ("normalize", normalize.getToggleState());
    props.setValue ("guitarChannel", engine.guitarChannel.load());
    if (auto* p = engine.chain.getPlugin())
    {
        juce::MemoryBlock state;
        p->getStateInformation (state);   // the amp settings you dialled in come back next time
        props.setValue ("pluginState", state.toBase64Encoding());
    }
    props.saveIfNeeded();
}

void MainComponent::restorePlugin (const juce::File& f, const juce::String& base64State)
{
    pluginName.setText ("Loading " + f.getFileNameWithoutExtension() + "... (big plugins take ~20 s)", juce::dontSendNotification);
    pluginBtn.setEnabled (false);
    // load on a later message, so the window can paint the line above first
    juce::Timer::callAfterDelay (300, [safe = juce::Component::SafePointer<MainComponent> (this), f, base64State]
    {
        if (safe == nullptr)
            return;
        auto* self = safe.getComponent();
        self->doRestore (f, base64State);
    });
}

void MainComponent::doRestore (const juce::File& f, const juce::String& base64State)
{
    auto err = engine.chain.loadPlugin (f);
    juce::MemoryBlock state;
    if (err.isEmpty() && state.fromBase64Encoding (base64State) && state.getSize() > 0)
        engine.chain.getPlugin()->setStateInformation (state.getData(), (int) state.getSize());
    pluginBtn.setEnabled (true);
    updateNames();
    if (err.isNotEmpty())
        pluginName.setText ("Couldn't load " + f.getFileName() + ": " + err, juce::dontSendNotification);
}

void MainComponent::choose (const juce::String& title, const juce::String& patterns, const juce::String& key,
                            std::function<juce::String (const juce::File&)> load)
{
    chooser = std::make_unique<juce::FileChooser> (title, juce::File (props.getValue (key)).getParentDirectory(), patterns);
    chooser->launchAsync (juce::FileBrowserComponent::openMode | juce::FileBrowserComponent::canSelectFiles,
                          [this, key, load] (const juce::FileChooser& fc)
                          {
                              auto f = fc.getResult();
                              if (f == juce::File())
                                  return;
                              auto err = load (f);
                              if (err.isNotEmpty())
                                  return juce::AlertWindow::showMessageBoxAsync (juce::MessageBoxIconType::WarningIcon, "RiffEngine", err);
                              props.setValue (key, f.getFullPathName());
                              updateNames();
                          });
}

void MainComponent::loadPlugin (const juce::File& f)
{
    closeEditor();
    pluginName.setText ("Loading " + f.getFileNameWithoutExtension() + "...", juce::dontSendNotification);
    if (auto* peer = getPeer())
        peer->performAnyPendingRepaintsNow();  // show "Loading..." before the load blocks the window
    auto err = engine.chain.loadPlugin (f);
    if (err.isNotEmpty())
        return juce::AlertWindow::showMessageBoxAsync (juce::MessageBoxIconType::WarningIcon, "RiffEngine", err);
    props.setValue ("plugin", f.getFullPathName());
    props.removeValue ("pluginState");
    updateNames();
    openEditor();
}

namespace
{
struct PluginWindow : juce::DocumentWindow
{
    PluginWindow (juce::AudioPluginInstance& p, std::function<void()> close)
        : DocumentWindow (p.getName(), BG, DocumentWindow::closeButton), onClose (std::move (close))
    {
        setUsingNativeTitleBar (true);
        auto* ed = p.hasEditor() ? p.createEditorIfNeeded() : nullptr;
        if (ed == nullptr)
            ed = new juce::GenericAudioProcessorEditor (p);
        setContentOwned (ed, true);
        setResizable (ed->isResizable(), false);
        setTopLeftPosition (80, 80);
        setVisible (true);
    }
    void closeButtonPressed() override { onClose(); }
    std::function<void()> onClose;
};
}

void MainComponent::openEditor()
{
    auto* p = engine.chain.getPlugin();
    if (p == nullptr)
        return;
    if (pluginWindow)
        return pluginWindow->toFront (true);
    pluginWindow = std::make_unique<PluginWindow> (*p, [this] { juce::MessageManager::callAsync ([this] { closeEditor(); }); });
}

void MainComponent::closeEditor()
{
    pluginWindow = nullptr;  // the editor goes before the plugin it belongs to
}

void MainComponent::matchModelRate()
{
    auto* d = engine.devices.getCurrentAudioDevice();
    const double want = engine.chain.namRate;
    if (d == nullptr || want <= 0 || std::abs (d->getCurrentSampleRate() - want) < 1 || ! d->getAvailableSampleRates().contains (want))
        return;
    auto setup = engine.devices.getAudioDeviceSetup();
    setup.sampleRate = want;
    engine.devices.setAudioDeviceSetup (setup, true);  // NAM sounds right only at the rate it was trained at
}

void MainComponent::updateNames()
{
    auto& c = engine.chain;
    namName.setText (c.namFile.existsAsFile() ? c.namFile.getFileNameWithoutExtension() : "no model", juce::dontSendNotification);
    irName.setText (c.irFile.existsAsFile() ? c.irFile.getFileNameWithoutExtension() : "no cab", juce::dontSendNotification);
    pluginName.setText (c.getPlugin() ? c.getPlugin()->getName() : "no plugin", juce::dontSendNotification);
    editorBtn.setEnabled (c.getPlugin() != nullptr);
    unloadBtn.setEnabled (c.getPlugin() != nullptr);
}

void MainComponent::changeListenerCallback (juce::ChangeBroadcaster*)
{
    refreshDevice();
}

void MainComponent::refreshDevice()
{
    inputBox.clear (juce::dontSendNotification);
    if (auto* d = engine.devices.getCurrentAudioDevice())
    {
        auto names = d->getInputChannelNames();
        auto active = d->getActiveInputChannels();
        for (int i = 0; i < names.size(); ++i)
            if (active[i])
                inputBox.addItem (names[i], inputBox.getNumItems() + 1);
    }
    if (engine.guitarChannel.load() >= inputBox.getNumItems())
        engine.guitarChannel = 0;
    inputBox.setSelectedItemIndex (engine.guitarChannel.load(), juce::dontSendNotification);
    info.setText (engine.describe(), juce::dontSendNotification);
    dropouts = 0;
}

void MainComponent::timerCallback()
{
    auto s = engine.takeStats();
    inMeter.set (s.inPeak);
    outMeter.set (s.outPeak);
    dropouts += s.overruns;
    const double load = s.budgetMs > 0 ? 100.0 * s.avgMs / s.budgetMs : 0;
    stats.setText ("Load " + juce::String (juce::roundToInt (load)) + "%  (avg " + juce::String (s.avgMs, 2) + " ms, worst "
                   + juce::String (s.maxMs, 2) + " ms of " + juce::String (s.budgetMs, 2) + " ms per block)\nDropouts so far: "
                   + juce::String (dropouts) + (s.xruns >= 0 ? "   driver xruns: " + juce::String (s.xruns) : juce::String()),
                   juce::dontSendNotification);

    auto* d = engine.devices.getCurrentAudioDevice();
    const double rate = d ? d->getCurrentSampleRate() : 0;
    const double want = engine.chain.namRate;
    juce::String warn;
    if (want > 0 && rate > 0 && std::abs (want - rate) > 1)
        warn = "The NAM model is made for " + juce::String (juce::roundToInt (want)) + " Hz but the interface runs at "
             + juce::String (juce::roundToInt (rate)) + " Hz: it will sound off. Set the interface to " + juce::String (juce::roundToInt (want)) + " Hz.";
    else if (engine.chain.invalidBlocks > 0)
        warn = "The amp/plugin produced broken audio (" + juce::String (engine.chain.invalidBlocks.load())
             + " blocks), muted to protect your ears. Reload it or turn it off.";
    else if (s.overruns > 0)
        warn = "Crackles: the computer can't keep up. Raise the buffer size, plug in the charger, or turn a stage off.";
    if (warn.isNotEmpty() || ++quietTicks > 45)  // keep a crackle hint up for ~3 s after the last one
    {
        warning.setText (warn, juce::dontSendNotification);
        quietTicks = 0;
    }

    if (++reconnectTicks >= 30)                  // every 2 s
    {
        reconnectTicks = 0;
        const bool lost = engine.reconnectIfLost();
        if (lost)
            info.setText ("Audio interface lost (" + engine.lastError + ").\nReconnecting... plug it back in, close other audio apps.",
                          juce::dontSendNotification);
        else if (wasLost)
            refreshDevice();
        wasLost = lost;
    }

    if (engine.isRecording())
        recLabel.setText ("Recording dry + wet... " + juce::String (juce::roundToInt ((juce::Time::getMillisecondCounterHiRes() - recStarted) / 1000.0)) + " s"
                          + (engine.lostBlocks > 0 ? "   GAPS: " + juce::String (engine.lostBlocks.load()) + " blocks lost, disk too slow/full" : juce::String()),
                          juce::dontSendNotification);
    auto res = engine.loopbackResult();
    if (res.isNotEmpty())
        loopLabel.setText (res, juce::dontSendNotification);
}

void MainComponent::paint (juce::Graphics& g)
{
    g.fillAll (BG);
    g.setColour (PANEL);
    g.fillRect (getLocalBounds().withTrimmedLeft (430));
    g.setColour (ACID);
    g.setFont (juce::FontOptions (22.0f, juce::Font::bold));
    g.drawText ("RiffEngine prototype", 446, 10, 400, 30, juce::Justification::left);
}

void MainComponent::resized()
{
    selector.setBounds (0, 10, 420, getHeight() - 10);
    const int x = 446, w = getWidth() - x - 16;
    int y = 48;
    auto row = [&] (int h) { auto r = juce::Rectangle<int> (x, y, w, h); y += h + 6; return r; };

    auto r = row (24);
    inputLabel.setBounds (r.removeFromLeft (100));
    inputBox.setBounds (r.removeFromLeft (200));
    inMeter.setBounds (r.withTrimmedLeft (10).reduced (0, 4));
    r = row (24);
    inGainLabel.setBounds (r.removeFromLeft (100));
    inGain.setBounds (r);
    y += 6;

    auto slot = [&] (juce::TextButton& b, juce::ToggleButton& on, juce::Label& name)
    {
        auto rr = row (28);
        b.setBounds (rr.removeFromLeft (150));
        on.setBounds (rr.removeFromLeft (50).withTrimmedLeft (8));
        name.setBounds (rr);
    };
    slot (namBtn, namOn, namName);
    normalize.setBounds (row (20).withTrimmedLeft (150));
    slot (irBtn, irOn, irName);
    slot (pluginBtn, pluginOn, pluginName);
    r = row (26);
    editorBtn.setBounds (r.removeFromLeft (150));
    unloadBtn.setBounds (r.removeFromLeft (90).withTrimmedLeft (8));
    y += 6;

    r = row (24);
    outGainLabel.setBounds (r.removeFromLeft (100));
    outGain.setBounds (r.removeFromLeft (300));
    outMeter.setBounds (r.withTrimmedLeft (10).reduced (0, 4));
    warning.setBounds (row (36));

    info.setBounds (row (54));
    stats.setBounds (row (36));
    r = row (28);
    loopBtn.setBounds (r.removeFromLeft (200));
    loopLabel.setBounds (r.withTrimmedLeft (8));
    r = row (32);
    recBtn.setBounds (r.removeFromLeft (120));
    recLabel.setBounds (r.withTrimmedLeft (8));
}
