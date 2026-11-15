#pragma once
#include "Engine.h"

class Meter : public juce::Component
{
public:
    void set (float peak);   // linear
    void paint (juce::Graphics&) override;

private:
    float db = -100, hold = -100;
    int holdTicks = 0;
};

class MainComponent : public juce::Component, private juce::Timer, private juce::ChangeListener
{
public:
    MainComponent (Engine&, juce::PropertiesFile&);
    ~MainComponent() override;

    void paint (juce::Graphics&) override;
    void resized() override;
    void saveState();
    void restorePlugin (const juce::File&, const juce::String& base64State);  // shows the window first: big plugins take a while
    void say (const juce::String& text) { pluginName.setText (text, juce::dontSendNotification); }
    juce::StringArray checkAmpBox();  // --gui-test: drives the amp dropdown like a user, PASS/FAIL lines
    void doRestore (const juce::File&, const juce::String& base64State);

private:
    void timerCallback() override;
    void changeListenerCallback (juce::ChangeBroadcaster*) override;
    void refreshDevice();
    void choose (const juce::String& title, const juce::String& patterns, const juce::String& key,
                 std::function<juce::String (const juce::File&)> load);
    void loadPlugin (const juce::File&);
    void openEditor();
    void closeEditor();
    void updateNames();
    void matchModelRate();
    juce::String loadAmp (const juce::File&);  // load a NAM model, remember it, match the interface's rate

    Engine& engine;
    juce::PropertiesFile& props;

    juce::AudioDeviceSelectorComponent selector;
    juce::Label inputLabel { {}, "Guitar input" }, inGainLabel { {}, "Input gain" }, outGainLabel { {}, "Output" };
    juce::ComboBox inputBox;
    Meter inMeter, outMeter;
    juce::Slider inGain, outGain;

    juce::ComboBox ampBox;
    juce::Array<juce::File> builtInAmps;
    static constexpr int ownFileId = 100, customId = 200;
    juce::TextButton irBtn { "Load cab IR..." }, pluginBtn { "Load plugin..." },
                     editorBtn { "Plugin window" }, unloadBtn { "Remove" }, recBtn { "Record" },
                     loopBtn { "Measure latency (cable)" };
    juce::ToggleButton namOn { "on" }, irOn { "on" }, pluginOn { "on" }, normalize { "level-match" };
    juce::Label irName, pluginName, info, stats, recLabel, loopLabel, warning;

    std::unique_ptr<juce::FileChooser> chooser;
    std::unique_ptr<juce::DocumentWindow> pluginWindow;
    int dropouts = 0, quietTicks = 0, reconnectTicks = 0;
    bool wasLost = false;
    double recStarted = 0;
};
