# What can break in RiffEngine, and what we do about it

Status: **fixed** = handled in code and checked (`--selftest` or measured); **in code** = handled but not tested yet;
**noted** = known, not handled yet.

| # | What breaks | What the player sees | Status |
|---|---|---|---|
| 1 | A plugin crashes while loading or playing | The app vanishes; worse, it crashes again on every start, because it reloads that plugin | **in code**: a "session open" mark; after a crash the plugin isn't auto-loaded and the window says why |
| 2 | The amp or plugin outputs NaN / huge values (bug, bad model) | A blast of noise in the headphones | **fixed**: every block is checked; invalid audio is muted, output is hard-limited to 0 dBFS |
| 3 | Denormal numbers on near-silence (NAM, IR tails) | CPU spikes and crackles when you stop playing | **fixed**: denormals flushed to zero in the audio callback |
| 4 | USB interface unplugged or laptop wakes from sleep | Sound stops and never returns | **in code**: the app retries the interface every 2 s and says "reconnecting" |
| 5 | Another program (REAPER) holds the ASIO driver | No sound, no explanation | **in code**: the error is shown with "close REAPER / other audio apps" |
| 6 | Interface runs 44.1 kHz, NAM model was made for 48 kHz | Amp sounds wrong | **in code**: loading a model switches the interface to the model's rate when it can; otherwise a red warning |
| 7 | Too much load (two amps, big IR, small buffer) | Crackles | **in code**: a hint appears when dropouts grow ("raise the buffer or turn a stage off") |
| 8 | Disk too slow / full while recording | Silent gaps in the take | **in code**: lost blocks are counted and shown next to the recording |
| 9 | Cyrillic (or any non-English) folder or file names | Files not found (the bug our tester hit in RiffCam) | **checked**: JUCE + NAM use Unicode paths; `--selftest` loads a model and records into Cyrillic folders |
| 10 | Old PC without AVX2 (before ~2013) | The app won't start at all | **fixed**: AVX2 off by default (measured: typical NAM model 22% -> 27% load); `-DRIFF_AVX2=ON` for a faster build |
| 11 | Big plugin takes 5–20 s to load | Window looks frozen, people kill it | **noted**: "Loading..." is shown; plugins must load on the main thread, so a real fix means out-of-process hosting |
| 12 | Plugin built only for Intel on an Apple Silicon Mac (or only for Apple Silicon) | Plugin won't load | **noted**: the error says so; the Mac build is universal (Intel + Apple Silicon) |
| 13 | macOS blocks the microphone/interface input without permission | Silence from the guitar | **in code**: the app asks for input access (Info.plist text) |
| 14 | macOS Gatekeeper: app from the internet, not signed by Apple | "RiffEngine can't be opened" | **noted**: needs an Apple Developer ID ($99/year) + notarization; until then: right-click → Open |
| 15 | Monitoring a microphone into speakers | Feedback howl | **noted**: rare for a DI guitar; the safety limiter caps the level |
| 16 | "Fast math" compiler setting assumes NaN never happens | The broken-audio guard (#2) silently does nothing | **fixed**: caught by `--selftest` on macOS; fast math now only inside NAM's code, none in ours, no LTO (and NAM got faster: 4.0x -> 4.6-5.1x realtime) |
