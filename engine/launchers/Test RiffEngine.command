#!/bin/bash
# Double-click on a Mac (first time: right-click -> Open). Runs every check and saves a report on the Desktop.
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine RiffEngine.app 2>/dev/null   # downloaded, unsigned app: let it run
echo
echo "  RiffEngine check - takes about 2 minutes."
echo "  1. Quit RiffEngine and other audio apps. 2. Keep the audio interface plugged in; nothing will play."
echo "  3. If macOS asks for microphone access, allow it (that is the guitar input)."
echo "  4. At the end a report opens (it is also on your Desktop): send that file to Taras."
echo
./RiffEngine.app/Contents/MacOS/RiffEngine --diagnose
echo
echo "Done. You can close this window."
