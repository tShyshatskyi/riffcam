#pragma once
#include <juce_core/juce_core.h>
#include "BinaryData.h"

// Built-in amps: engine/amps/*.nam (see amps/CREDITS.md) are compiled into the app and unpacked next to the
// settings on first use, so they load like any other .nam file.
namespace amps
{
inline juce::File folder()
{
    return juce::File::getSpecialLocation (juce::File::userApplicationDataDirectory).getChildFile ("RiffCam").getChildFile ("amps");
}

inline juce::Array<juce::File> builtIn()   // clean, crunch, rock, metal
{
    juce::Array<juce::File> out;
    folder().createDirectory();
    for (int i = 0; i < BinaryData::namedResourceListSize; ++i)
    {
        const juce::String name (BinaryData::originalFilenames[i]);
        if (! name.endsWithIgnoreCase (".nam"))
            continue;
        int size = 0;
        auto* data = BinaryData::getNamedResource (BinaryData::namedResourceList[i], size);
        auto f = folder().getChildFile (name);
        if (f.getSize() != size)
            f.replaceWithData (data, (size_t) size);
        out.add (f);
    }
    const juce::StringArray order { "clean", "crunch", "rock", "metal" };
    std::sort (out.begin(), out.end(), [&] (const juce::File& a, const juce::File& b)
    {
        return order.indexOf (a.getFileName().upToFirstOccurrenceOf ("-", false, false))
             < order.indexOf (b.getFileName().upToFirstOccurrenceOf ("-", false, false));
    });
    return out;
}

inline juce::File named (const juce::String& style)   // e.g. "crunch"
{
    for (auto& f : builtIn())
        if (f.getFileName().startsWith (style + "-"))
            return f;
    return {};
}

inline juce::String label (const juce::File& f)   // "crunch-bugera-333.nam" -> "Crunch – Bugera 333"
{
    auto words = juce::StringArray::fromTokens (f.getFileNameWithoutExtension(), "-", "");
    for (auto& w : words)
        w = w.length() <= 3 && ! w.containsOnly ("0123456789") ? w.toUpperCase()   // jvm -> JVM, od2 -> OD2, 333 stays
                                                               : w.substring (0, 1).toUpperCase() + w.substring (1);
    const auto style = words[0];
    words.remove (0);
    return style.substring (0, 1) + style.substring (1).toLowerCase() + juce::String::fromUTF8 (" \xe2\x80\x93 ") + words.joinIntoString (" ");
}

inline bool isBuiltIn (const juce::File& f)
{
    return f.getParentDirectory() == folder();
}
}
