#!/usr/bin/env bash
# Downloads the exact library versions RiffEngine is built and tested with into deps/, and applies our fixes.
# Works in macOS Terminal, Linux and Git Bash on Windows. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p deps

[ -d deps/JUCE ] || git clone -q --depth 1 --branch 8.0.15 https://github.com/juce-framework/JUCE.git deps/JUCE
[ -d deps/NAM ] || git clone -q --depth 1 --branch v0.6.0 --recurse-submodules --shallow-submodules \
    https://github.com/sdatkinson/NeuralAmpModelerCore.git deps/NAM

for p in patches/juce-*.patch; do
    if git -C deps/JUCE apply --reverse --check "../../$p" 2>/dev/null; then
        echo "already applied: $p"
    else
        git -C deps/JUCE apply "../../$p" && echo "applied: $p"
    fi
done
