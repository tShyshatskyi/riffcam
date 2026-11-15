# RiffEngine prototype – where we are (2026-11-15, late)

Ready for Taras's play test: `engine\TESTING.md` (Ukrainian). Exe: `engine\build\RiffEngine_artefacts\Release\RiffEngine.exe`.
Settings (`%APPDATA%\RiffCam\RiffEngine.settings`) preloaded with Focusrite ASIO 48k/128 + Gojira X.

## Verified by me
- ASIO Focusrite live: NAM ~30% / Gojira X ~30% load at 128 (0 dropouts) and 64 (7.8 ms, ~1 dropout per 15 s).
- Gojira X loads (after our JUCE patch), its window opens, state saves/restores; dry+wet recording works.

## Taras play test: "seems fine" (2026-11-15); still open: latency feel vs REAPER, dropouts, cable test
- Feel of latency vs REAPER, dropouts while playing, loopback measurement with a cable, file pickers.

## 2026-11-16: hardening + macOS port done in code, Windows-verified (--selftest all pass)
- Not yet verified: GUI guards by hand (crash guard, reconnect) – Taras's RiffEngine window was open.
- macOS: builds on GitHub Actions (branch `riffengine`), universal x86_64+arm64; self-test all passed, NAM 5.8x realtime.
  Downloads: Actions run 43765914671 -> RiffEngine-macOS (zip with app + Test RiffEngine.command), RiffEngine-Windows.
  Not yet run on a real Mac with an interface – needs a tester with a Mac.
- Testers: `Test RiffEngine` launcher -> `--diagnose` report on the Desktop (37 s here, all passed).

## Next
- Load plugins without freezing the window (background/async).
- Song playback in sync + offline mix render (REAPER's jobs 3-4), then wire into riffcam.py instead of REAPER.
- Report the getParameterCount loop to JUCE upstream.
