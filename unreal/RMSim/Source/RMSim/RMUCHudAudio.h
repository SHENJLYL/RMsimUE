#pragma once

#include "CoreTypes.h"

class UWorld;

enum class ERMUCHudAudioCue : uint8
{
    Hit,
    Overheat,
    Countdown,
    Start,
    Finish
};

class FRMUCHudAudio
{
public:
    static void Play(UWorld* World, ERMUCHudAudioCue Cue);
    static void RunSelfTest();
};
