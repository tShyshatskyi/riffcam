# Windows build: .\build.ps1  ->  build\RiffEngine_artefacts\Release\RiffEngine.exe
# Needs Visual Studio 2022 or newer (or its Build Tools) with C++, git, and CMake (on PATH, or pip-installed in ..\.venv).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
# Git's own bash, not WSL's: git.exe sits in Git\cmd or Git\mingw64\bin, bash.exe in Git\bin
$d = Split-Path (Get-Command git).Source
while ($d -and -not (Test-Path (Join-Path $d "bin\bash.exe"))) { $d = Split-Path $d }
if (-not $d) { throw "Git for Windows' bash.exe not found" }
& (Join-Path $d "bin\bash.exe") ./fetch_deps.sh
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$cmake = "$PSScriptRoot\..\.venv\Scripts\cmake.exe"
if (-not (Test-Path $cmake)) { $cmake = "cmake" }
& $cmake -S . -B build -A x64   # newest installed Visual Studio
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $cmake --build build --config Release --parallel -- /v:minimal
exit $LASTEXITCODE
