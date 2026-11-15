# Windows build: .\build.ps1  ->  build\RiffEngine_artefacts\Release\RiffEngine.exe
# Needs Visual Studio 2022 (or its Build Tools) with C++, git, and CMake (on PATH, or pip-installed in ..\.venv).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$gitBash = Join-Path (Split-Path (Split-Path (Get-Command git).Source)) "bin\bash.exe"  # Git's bash, not WSL's
& $gitBash ./fetch_deps.sh
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$cmake = "$PSScriptRoot\..\.venv\Scripts\cmake.exe"
if (-not (Test-Path $cmake)) { $cmake = "cmake" }
& $cmake -S . -B build -G "Visual Studio 17 2022" -A x64
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $cmake --build build --config Release --parallel -- /v:minimal
exit $LASTEXITCODE
