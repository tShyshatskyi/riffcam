@echo off
echo RiffCam self-test: keep REAPER open, don't touch the camera. Takes about half a minute.
echo When Notepad opens, send that file (selftest.txt) to the developer.
start "" /wait "%~dp0RiffCam.exe" --test
