#!/usr/bin/env bash
# macOS (and Linux) build: ./build.sh  ->  build/RiffEngine_artefacts/Release/RiffEngine.app
# Needs Xcode command line tools (xcode-select --install) and CMake (brew install cmake).
set -euo pipefail
cd "$(dirname "$0")"
./fetch_deps.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel "$(getconf _NPROCESSORS_ONLN)"
