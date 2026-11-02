# Builds dist\RiffCam\RiffCam.exe and dist\RiffCam-windows.zip
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name RiffCam --icon riffcam.ico `
    --add-data "template.rpp;." --add-binary ".venv\Scripts\deno.exe;." `
    --collect-data imageio_ffmpeg --collect-data yt_dlp_ejs riffcam.py
Copy-Item README.md, LICENSE, THIRD_PARTY_NOTICES.md dist\RiffCam
Compress-Archive -Force dist\RiffCam dist\RiffCam-windows.zip
"Built: dist\RiffCam-windows.zip ({0:N0} MB)" -f ((Get-Item dist\RiffCam-windows.zip).Length / 1MB)
