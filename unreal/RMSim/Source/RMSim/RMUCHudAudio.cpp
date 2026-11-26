#include "RMUCHudAudio.h"

#include "Components/AudioComponent.h"
#include "Containers/Array.h"
#include "Containers/ArrayView.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "Math/UnrealMathUtility.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Sound/SoundWaveProcedural.h"

DEFINE_LOG_CATEGORY_STATIC(LogRMUCHudAudio, Log, All);

namespace
{
constexpr int32 SampleRate = 24000;
constexpr int32 SamplesPerMs = SampleRate / 1000;
constexpr float TwoPi = 2.f * UE_PI;

struct FTone
{
    float FirstHz;
    float LastHz;
    int32 DurationMs;
    float Level;
};

struct FCue
{
    const TCHAR* Name = nullptr;
    TArrayView<const FTone> Tones;
    float Volume = 0.f;
};

FCue Describe(ERMUCHudAudioCue Cue)
{
    constexpr static FTone Hit[] = {
        {920.f, 640.f, 34, .60f},
        {670.f, 615.f, 22, .36f}
    };
    constexpr static FTone Overheat[] = {
        {575.f, 515.f, 63, .55f},
        {0.f, 0.f, 31, 0.f},
        {520.f, 425.f, 74, .53f}
    };
    constexpr static FTone Countdown[] = {
        {550.f, 558.f, 66, .40f}
    };
    constexpr static FTone Start[] = {
        {660.f, 660.f, 54, .48f},
        {825.f, 825.f, 63, .53f},
        {1040.f, 1040.f, 115, .55f}
    };
    constexpr static FTone Finish[] = {
        {865.f, 865.f, 62, .53f},
        {685.f, 685.f, 78, .52f},
        {515.f, 515.f, 128, .49f}
    };

    switch (Cue)
    {
    case ERMUCHudAudioCue::Hit: return {TEXT("hit"), MakeArrayView(Hit), .51f};
    case ERMUCHudAudioCue::Overheat: return {TEXT("overheat"), MakeArrayView(Overheat), .56f};
    case ERMUCHudAudioCue::Countdown: return {TEXT("countdown"), MakeArrayView(Countdown), .42f};
    case ERMUCHudAudioCue::Start: return {TEXT("start"), MakeArrayView(Start), .48f};
    case ERMUCHudAudioCue::Finish: return {TEXT("finish"), MakeArrayView(Finish), .48f};
    }
    return {};
}

TArray<int16> RenderCue(TArrayView<const FTone> Tones)
{
    TArray<int16> Pcm;
    for (const FTone& Tone : Tones)
    {
        const int32 Frames = Tone.DurationMs * SamplesPerMs;
        const int32 Offset = Pcm.AddUninitialized(Frames);
        float Phase = 0.f;
        for (int32 Sample = 0; Sample < Frames; ++Sample)
        {
            const float Time = static_cast<float>(Sample) / (Frames - 1);
            const float Attack = FMath::Min(1.f, Sample / (5.f * SamplesPerMs));
            const float Release = FMath::Min(1.f, (Frames - 1 - Sample) / (26.f * SamplesPerMs));
            const float Envelope = FMath::SmoothStep(0.f, 1.f, FMath::Min(Attack, Release));
            const float Value = .90f * FMath::Sin(Phase) + .10f * FMath::Sin(2.f * Phase);
            Pcm[Offset + Sample] = static_cast<int16>(32767.f * Tone.Level * Envelope * Value);
            Phase += TwoPi * FMath::Lerp(Tone.FirstHz, Tone.LastHz, Time) / SampleRate;
            if (Phase >= TwoPi)
            {
                Phase -= TwoPi;
            }
        }
    }
    return Pcm;
}
}

void FRMUCHudAudio::Play(UWorld* World, ERMUCHudAudioCue Cue)
{
    if (!World || !FApp::CanEverRenderAudio() || !World->AllowAudioPlayback()
        || World->IsNetMode(NM_DedicatedServer) || !World->GetAudioDeviceRaw())
    {
        return;
    }
    const FCue Description = Describe(Cue);
    if (!Description.Name)
    {
        return;
    }
    const TArray<int16> Pcm = RenderCue(Description.Tones);
    auto* Wave = NewObject<USoundWaveProcedural>(World);
    Wave->SetSampleRate(SampleRate);
    Wave->NumChannels = 1;
    Wave->Duration = static_cast<float>(Pcm.Num()) / SampleRate;
    Wave->QueueAudio(reinterpret_cast<const uint8*>(Pcm.GetData()), Pcm.Num() * sizeof(int16));
    if (UAudioComponent* Emitter = UGameplayStatics::SpawnSound2D(World, Wave, Description.Volume))
    {
        Emitter->StopDelayed(Wave->Duration + .06f);
        static const bool bTrace = FParse::Param(FCommandLine::Get(), TEXT("RefereeHudAudioTrace"));
        if (bTrace)
        {
            UE_LOG(LogRMUCHudAudio, Display,
                TEXT("RMUC_HUD_AUDIO cue=%s source=original_synth duration_ms=%d"),
                Description.Name, Pcm.Num() / SamplesPerMs);
        }
    }
}

void FRMUCHudAudio::RunSelfTest()
{
#if WITH_DEV_AUTOMATION_TESTS
    if (!FParse::Param(FCommandLine::Get(), TEXT("RefereeHudAudioSelfTest")))
    {
        return;
    }

    constexpr ERMUCHudAudioCue Cues[] = {
        ERMUCHudAudioCue::Hit, ERMUCHudAudioCue::Overheat, ERMUCHudAudioCue::Countdown,
        ERMUCHudAudioCue::Start, ERMUCHudAudioCue::Finish
    };
    int32 Passed = 0;
    for (const ERMUCHudAudioCue Cue : Cues)
    {
        const FCue Description = Describe(Cue);
        const TArray<int16> Pcm = RenderCue(Description.Tones);
        int32 Peak = 0;
        for (const int16 Sample : Pcm)
        {
            Peak = FMath::Max(Peak, FMath::Abs(static_cast<int32>(Sample)));
        }
        const int32 MaxMs = Cue == ERMUCHudAudioCue::Hit ? 79 : 500;
        if (Description.Name && Pcm.Num() >= 50 * SamplesPerMs && Pcm.Num() <= MaxMs * SamplesPerMs
            && Pcm[0] == 0 && Pcm.Last() == 0 && Peak >= 1024 && Peak < 32000)
        {
            ++Passed;
        }
    }
    UE_LOG(LogRMUCHudAudio, Display,
        TEXT("RMUC_HUD_AUDIO_SELF_TEST passed=%d tests=%d"),
        Passed, UE_ARRAY_COUNT(Cues));
#endif
}
