cmake_minimum_required(VERSION 3.22)

# macOS: one app for Intel and Apple Silicon, runs on macOS 11+ (must be set before project())
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "Oldest macOS supported")
set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "Universal binary")

project(RiffEngine VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")  # static CRT: one exe, no VC++ redist

add_subdirectory(deps/JUCE)

juce_add_gui_app(RiffEngine
    PRODUCT_NAME "RiffEngine"
    COMPANY_NAME "RiffCam"
    BUNDLE_ID com.riffcam.riffengine
    MICROPHONE_PERMISSION_ENABLED TRUE   # macOS gives silence from the interface without this
    MICROPHONE_PERMISSION_TEXT "RiffEngine plays your guitar through the amp and records it, from your audio interface input.")

set(NAM_DIR ${CMAKE_CURRENT_SOURCE_DIR}/deps/NAM)
file(GLOB NAM_SOURCES ${NAM_DIR}/NAM/*.cpp ${NAM_DIR}/NAM/*/*.cpp)

target_sources(RiffEngine PRIVATE Source/Main.cpp Source/Chain.cpp Source/Engine.cpp Source/MainComponent.cpp
               ${NAM_SOURCES})
target_include_directories(RiffEngine PRIVATE Source ${NAM_DIR} ${NAM_DIR}/Dependencies/eigen
                           ${NAM_DIR}/Dependencies/nlohmann)

target_compile_definitions(RiffEngine PRIVATE
    JUCE_ASIO=1                 # Windows only; Steinberg's headers ship inside JUCE 8.0.15 (GPLv3 option)
    JUCE_PLUGINHOST_VST3=1
    JUCE_PLUGINHOST_AU=1        # macOS only: Audio Unit plugins
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_APPLICATION_NAME_STRING="$<TARGET_PROPERTY:RiffEngine,JUCE_PRODUCT_NAME>"
    JUCE_APPLICATION_VERSION_STRING="$<TARGET_PROPERTY:RiffEngine,JUCE_VERSION>"
    NAM_SAMPLE_FLOAT            # NAM works on float buffers, like the audio callback
    NAM_ENABLE_A2_FAST
    NOMINMAX)

# AVX2 makes NAM faster but the app then won't start on CPUs from before ~2013 (see RISKS.md #10, LOG.md)
option(RIFF_AVX2 "Build for AVX2 CPUs only (Windows/Intel)" OFF)

if(MSVC)
    target_compile_options(RiffEngine PRIVATE /utf-8 /bigobj $<$<CONFIG:Release>:/O2 /fp:fast>
                           $<$<BOOL:${RIFF_AVX2}>:/arch:AVX2>
                           /Zi)  # symbols in release: tools/where_spins.py names our functions on a stuck stack
    target_link_options(RiffEngine PRIVATE /DEBUG /OPT:REF /OPT:ICF)
else()
    target_compile_options(RiffEngine PRIVATE $<$<CONFIG:Release>:-O3 -ffast-math>)
endif()

# a NAM model inside the app, so --selftest / --diagnose work on a machine with no models (MIT, from NAM core)
juce_add_binary_data(RiffEngineData SOURCES ${NAM_DIR}/example_models/wavenet_a1_standard.nam)

target_link_libraries(RiffEngine PRIVATE
    RiffEngineData
    juce::juce_audio_utils
    juce::juce_dsp
    PUBLIC
    juce::juce_recommended_config_flags
    juce::juce_recommended_lto_flags)

# JUCE's own example host, to tell our bugs from JUCE's when a plugin misbehaves: cmake -DWITH_JUCE_HOST=ON
option(WITH_JUCE_HOST "Also build JUCE's AudioPluginHost" OFF)
if(WITH_JUCE_HOST)
    add_subdirectory(deps/JUCE/extras/AudioPluginHost)
endif()
