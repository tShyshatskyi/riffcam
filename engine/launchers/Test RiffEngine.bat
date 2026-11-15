@echo off
echo.
echo   RiffEngine check - takes about 2 minutes.
echo.
echo   1. Close RiffEngine and REAPER (they hold the audio interface).
echo   2. Keep the audio interface plugged in. Nothing will play, you can leave the guitar.
echo   3. At the end a report opens (it is also on your Desktop): send that file to Taras.
echo.
pause
echo Testing... the window closes by itself when done.
start "" /wait "%~dp0RiffEngine.exe" --diagnose
