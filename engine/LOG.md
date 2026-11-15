# RiffEngine prototype – work log

Goal: prove we can replace REAPER's audio part ourselves: ASIO in/out with low latency, guitar through an amp
(NAM model, cab IR, or the user's VST3 plugin) into headphones, and a dry recording of the guitar.

## 2026-11-11

- Toolchain: VS Build Tools 2022 (MSVC 14.44) + Windows SDK 10.0.26100 already installed. No CMake: installed
  `cmake` + `ninja` from pip into `riffcam/.venv`.
- Deps (in `engine/deps/`, not committed):
  - JUCE 8.0.15 (tag). Chose 8.x over the newer 9.0.5: 8.x API is the one I know; 9 is a new major.
  - NeuralAmpModelerCore v0.6.0 (MIT) with eigen + nlohmann submodules; ships example `.nam` models.
  - ASIO SDK 2.3.4 (2025-10-15, GPLv3 option) downloaded from steinberg.net, then deleted: JUCE 8.0.15 bundles
    Steinberg's ASIO headers itself (`juce_audio_devices/native/asio`).
- Licensing consequence: ASIO under GPLv3 + JUCE under AGPLv3/Starter -> a shipped RiffCam with this engine is
  GPLv3/AGPLv3 open source (today it is MIT).
- Test material on this PC: Focusrite USB ASIO driver, Neural DSP "Archetype Gojira X" VST3, a real dry guitar
  take `Videos\RiffCam\raw\2026-11-07 22-00-47 guitar.wav`.
- JUCE 8.0.15 API notes (checked in source, not from memory): `addDefaultFormatsToManager()` replaces
  `addDefaultFormats()`; writers use `createWriterFor(std::unique_ptr<OutputStream>&, AudioFormatWriterOptions)`.
- Wrote Source/{Chain,Engine,MainComponent,Main}.cpp; first build clean (no warnings).
- Tests: --list ok; --render NAM ok (A1 standard 5.1x RT, A2 5.5x, LSTM 100x; ~0.1% late blocks offline);
  --rt-test Focusrite ASIO 48k: buf 64 -> 7.8 ms driver latency, 8 overruns/12 s on battery; buf 128 -> 14.5 ms, 0.
- Gojira X VST3: hangs in createInstanceFromDescription (main thread 100%). Moved CLI work into callAsync: no
  change. Started building JUCE AudioPluginHost to compare; stopped for the night. See PROGRESS.md.

## 2026-11-15

- Built JUCE's AudioPluginHost (`-DWITH_JUCE_HOST=ON`) and opened a one-node graph with Gojira X
  (description from new `RiffEngine --describe`). It hangs the same way: "Not Responding", main thread 100%.
  => not a bug in our code; it is JUCE 8.0.15 hosting vs this plugin.
- `tools/where_spins.py` (samples a thread's instruction pointer, no debugger needed): 60/60 samples inside
  `Archetype Gojira X.vst3+0xaf71a8`. Disassembly: a spin-wait `lock xadd [global]; cmp -1; cmp rdi; pause; jmp`
  – waits while a global equals -1 or equals its argument. Global read 240 = the argument => waits forever.
- Wrote engine/TESTING.md (Ukrainian play-test notes for Taras).
- Next: rebuild with /Zi and name the JUCE host call on the stuck stack.
- Probes in JUCE's initialiseParameterList (temporary, removed): Gojira X has 2219 parameters; the loop got ~1
  param per 0.4 s. Not a deadlock: JUCE calls `editController->getParameterCount()` in the loop condition, and
  in this plugin that call is slow (the spin-wait we saw). REAPER asks once.
- Fix: ask once (`patches/juce-8.0.15-vst3-param-count.patch`, applied by build.ps1). Gojira now loads in
  ~5-20 s. Worth reporting upstream to JUCE.
- Gojira X results: offline render 3.2x realtime; live ASIO buf 128: 14.5 ms, ~30% load, 0 dropouts/12 s;
  buf 64: 7.8 ms, ~32%, 1 dropout/15 s (plugged in).
- GUI tested without mouse/screen capture (tools/ui.ps1 = UI Automation, tools/capture_window.ps1 = one window):
  opens Focusrite ASIO 48k/128 by itself; restores NAM/IR/plugin + plugin state on restart; plugin window opens
  (Gojira UI fully drawn, activated); Record/Stop writes 24-bit mono dry + wet WAVs; clean exit (code 0).
  NAM + IR + Gojira together: 86-90% load, hundreds of dropouts -> one amp at a time.
- Fixed in GUI: load % showed raw decimals and wrapped; window now shows first and the saved plugin loads after
  ("Loading..." label).
- Not tested by me: anything that needs a guitar played or a cable (latency by ear, loopback measurement),
  the file pickers (native dialogs). Handed to Taras with TESTING.md.

## 2026-11-15 (evening)

- Taras played through the prototype: "seems to work fine". No details yet on buffer size, latency feel or dropouts.

## 2026-11-16 – hardening + macOS port

- RISKS.md: 15 ways it can break. Handled in code: crash guard ("sessionOpen" mark -> plugin not auto-loaded
  after a crash, and removed from settings), NaN/Inf mute + 0 dBFS limiter (bit test, safe under fast-math),
  ScopedNoDenormals, reconnect to the interface every 2 s, device error shown with "close REAPER", NAM model
  switches the interface to its sample rate, crackle hint, lost recording blocks counted.
- `--selftest`: 7 checks (NaN muted, limiter, NAM from Cyrillic path, finite output, denormal CPU, speed,
  recording into a Cyrillic folder) – ALL PASSED on Windows.
- AVX2 measured (same machine, plugged in, block 128): A1 standard 4.6x -> 3.65x realtime without AVX2, A2 5.3x -> 5.0x.
  Decision: off by default (app must start on any x64 CPU); option RIFF_AVX2.
- macOS: CoreAudio default, VST3 + Audio Unit via AudioPluginFormatManager, microphone permission text,
  universal arm64+x86_64, macOS 11+, Movies folder, UTF-8 paths for NAM. fetch_deps.sh + build.sh.
  No Mac here: .github/workflows/engine.yml builds + self-tests on macos-14 and windows-2022 (needs a push).
- Found: the Bash tool collapses double backslashes in heredocs (broke "\n" in C++ strings twice) – use Edit.
- Linking failed while Taras had the app open: renamed the running exe aside (RiffEngine.old.exe) instead of closing it.
- `--diagnose` + launchers/Test RiffEngine.(bat|command): one report on the Desktop (computer, power, settings,
  drivers, self-test, offline speed, live 128 (+64 if the driver offers it), the user's plugin load + live,
  RiffCam log tails). Built-in NAM test model (BinaryData). Here: 37 s, all passed, Gojira 10-14 s load, 0 dropouts.
  Focusrite USB ASIO offers one buffer size (the one set in its control panel), so the 64 test reports "skipped".
- Pushed to branch `riffengine` (Taras: push without asking). GPLv3 text in engine/LICENSE.
- CI run 2: Windows failed – windows-latest has a newer Visual Studio and build.ps1/CI pinned 'Visual Studio 17 2022' (would break for users too) -> let CMake pick. macOS failed – clang rejects 'pluginFile = {}' (ambiguous), MSVC accepted it -> juce::File(). Mac build took 30 min: bare --parallel = 1 job with make -> pass the core count. build.ps1 found the wrong bash when run from Git Bash -> search upward for Gitinash.exe.
