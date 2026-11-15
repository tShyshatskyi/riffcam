# RiffEngine

RiffCam's own audio engine (prototype): plays your guitar through an amp – a NAM model, a cab IR, or your own
VST3 / Audio Unit plugin – into your headphones with low latency, and records the dry and the amped guitar.
It is meant to replace REAPER inside RiffCam.

- **Built-in amps:** Clean, Crunch, Rock, Metal – NAM captures of real amps with their cabinets
  ([credits](amps/CREDITS.md), GPL-3.0), so it plays right away with no files or plugins.
- **Windows:** ASIO (or Windows Audio). **macOS 11+:** CoreAudio, universal app (Apple Silicon + Intel).
- What can go wrong and what guards against it: [RISKS.md](RISKS.md). How to try it: [TESTING.md](TESTING.md).
- Work log: [LOG.md](LOG.md), where we are: [PROGRESS.md](PROGRESS.md).

## Build

Windows (Visual Studio 2022 or newer with C++, git, CMake):

```powershell
.\build.ps1      # -> build\RiffEngine_artefacts\Release\RiffEngine.exe
```

macOS (Xcode command line tools, CMake: `brew install cmake`):

```bash
./build.sh       # -> build/RiffEngine_artefacts/Release/RiffEngine.app
```

Both fetch JUCE 8.0.15 and NeuralAmpModelerCore 0.6.0 into `deps/` and apply `patches/` (a JUCE fix that made
plugins with thousands of parameters, like Neural DSP's, take ~15 minutes to load).

On macOS the app isn't signed by Apple yet: the first time, right-click it → Open.

## Checks

For testers: double-click `launchers/Test RiffEngine.bat` (Windows) or `Test RiffEngine.command` (Mac) next to
the app: it runs `RiffEngine --diagnose` and opens one report to send back.

```bash
RiffEngine --selftest --nam deps/NAM/example_models/wavenet_a1_standard.nam   # safety guards, Unicode paths, speed
RiffEngine --render dry.wav out.wav --nam model.nam                           # a take through the amp, offline
RiffEngine --rt-test --buffer 64 --seconds 10 --nam model.nam                 # live on the interface, output muted
```

GitHub Actions builds and runs these on macOS and Windows for every change (`.github/workflows/engine.yml`).

## License

The engine uses JUCE (AGPLv3 or JUCE's licence) and, on Windows, Steinberg's ASIO under its GPLv3 option, so a
distributed RiffEngine has to be open source under GPLv3/AGPLv3 terms. RiffCam itself is MIT today.
