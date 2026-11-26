#include "RMUCGameReferee.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "VehicleRig.h"

AActor* ARMUCGameReferee::GetRuneProbeCamera() const
{
    return RuneLifecycleProbe.Camera.Get();
}

void ARMUCGameReferee::StepRuneLifecycleProbe()
{
    using Phase = FRMUCRuneRules::Phase;
    static const FString Output = []()
    {
        FString Path;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeRuneLifecycleProbe="), Path);
        return Path;
    }();
    if (Output.IsEmpty() || GameProgress != ERMUCGameProgress::Running) return;
    static double DarkSince = -1., MaxDark = 0., MinPlate = 100., MaxPlate = 0.;
    static bool bSawFade = false;
    auto& Probe = RuneLifecycleProbe;
    const bool bRed = IsFriendlyRed();
    const TCHAR* Mode = Probe.bBig ? TEXT("large") : TEXT("small");
    auto Finish = [Mode](bool bPassed, const TCHAR* Reason)
    {
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_PROOF_FINISHED mode=%s passed=%d reason=%s"), Mode, bPassed, Reason);
        FPlatformMisc::RequestExitWithStatus(true, bPassed ? 0 : 2);
    };
    if (!FieldVisuals.IsRuneReady()) { Finish(false, TEXT("missing_mesh")); return; }
    if (!Probe.Camera.IsValid())
    {
        FTransform Plate;
        FVector Centre = FVector::ZeroVector;
        for (int32 Index = 0; Index < 5; ++Index)
        {
            if (!FieldVisuals.GetRuneFixtureFace(bRed, Index, Plate)) { Finish(false, TEXT("missing_plate")); return; }
            Centre += .2 * Plate.GetLocation();
        }
        const FVector Out = (bRed ? 1. : -1.) * Plate.GetRotation().GetForwardVector();
        const FVector Location = Centre + 465. * Out + FVector(0., 0., 35.);
        Probe.Camera = GetWorld()->SpawnActor<ACameraActor>(Location, (Centre - Location).Rotation());
        if (!Probe.Camera.IsValid()) { Finish(false, TEXT("missing_probe_camera")); return; }
        Probe.Camera->GetCameraComponent()->FieldOfView = 42.f;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_PROOF_WAITING mode=small"));
    }
    if (auto* PC = GetWorld()->GetFirstPlayerController()) PC->SetViewTarget(Probe.Camera.Get());
    const auto Capture = [&](const TCHAR* Name)
    {
        FScreenshotRequest::RequestScreenshot(FPaths::Combine(Output, FString::Printf(TEXT("%s-%s.png"), Mode, Name)),
            false, false);
    };
    const double Time = MatchElapsedSeconds;
    const auto& Face = RuneRules.Get(bRed);
    const auto Light = FieldVisuals.GetRuneLampEvidence(bRed);
    if (!Probe.bStarted)
    {
        if (Face.State != Phase::Activating) return;
        const int32 ExpectedTargets = Probe.bBig ? 2 : 1;
        if (FMath::CountBits(static_cast<uint32>(Face.Targets)) != ExpectedTargets || Face.bBig != Probe.bBig)
        { Finish(false, TEXT("opening_targets")); return; }
        DarkSince = -1.;
        MaxDark = 0.;
        MinPlate = 100.;
        MaxPlate = 0.;
        bSawFade = false;
        Probe.bStarted = true;
        Probe.BegunAt = Time;
        Probe.NextShot = Time + .90;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_PROOF_ACTIVATING mode=%s time=%.3f targets=%d"), Mode, Time, Face.Targets);
    }

    if (Face.State == Phase::Activating)
    {
        if (Time - Probe.BegunAt < .90)
        {
            if (Light.Flow > .05)
            {
                Probe.MinFlow = FMath::Min(Probe.MinFlow, Light.FlowMoment);
                Probe.MaxFlow = FMath::Max(Probe.MaxFlow, Light.FlowMoment);
            }
            if (Time - Probe.BegunAt > .22)
            {
                MinPlate = FMath::Min(MinPlate, Light.Plate);
                MaxPlate = FMath::Max(MaxPlate, Light.Plate);
            }
            if (Time - Probe.BegunAt > .20 && !Probe.bTargetScreenshot)
            {
                Probe.bTargetScreenshot = true;
                Capture(TEXT("targets"));
            }
        }
        const bool bHasPartialProgress = Probe.bBig ? Light.ProgressSegments > 0 : Light.FullArm > .05;
        if (Face.Groups > 0 && Face.Targets != 0 && bHasPartialProgress && !Probe.bProgressScreenshot)
        {
            Probe.bProgressScreenshot = true;
            Capture(TEXT("progress"));
        }
        if (Face.Targets == 0 && Face.Groups > 0 && Light.Plate <= .01 && Light.Flow <= .01 && Light.Progress <= .01)
        {
            Probe.bBlackout = true;
            if (DarkSince < 0.) DarkSince = Time;
        }
        else if (DarkSince >= 0.)
        {
            MaxDark = FMath::Max(MaxDark, Time - DarkSince);
            DarkSince = -1.;
        }
        if (Face.Confirmed && Light.Plate > .05) Probe.bConfirmed = true;
        if (Time >= Probe.NextShot && Face.Targets)
        {
            const int32 Target = FMath::CountTrailingZeros(static_cast<uint32>(Face.Targets));
            FTransform Plate;
            if (!FieldVisuals.GetRuneFixtureFace(bRed, Target, Plate)) { Finish(false, TEXT("lost_plate")); return; }
            const FVector Out = (bRed ? 1. : -1.) * Plate.GetRotation().GetForwardVector();
            const FTransform TestMuzzle((-Out).Rotation(), Plate.GetLocation() + 20. * Out);
            if (RequestRosShot(Rig, TestMuzzle) != TEXT("accepted")) { Finish(false, TEXT("test_muzzle_rejected")); return; }
            Probe.NextShot = Time + .23;
        }
        if (Time - Probe.BegunAt > 12.) Finish(false, TEXT("no_full_hit_sequence"));
        return;
    }
    if (Face.State == Phase::Failed) { Finish(false, TEXT("test_missed_moving_plate")); return; }
    if (Face.State == Phase::Activated)
    {
        if (Probe.ActiveAt < 0.)
        {
            const double Duration = Face.BuffUntil - Time;
            const int32 ExpectedHits = Probe.bBig ? 10 : 5;
            const int32 ExpectedDefence = Probe.bBig ? 50 : 25;
            const int32 ExpectedAttack = Probe.bBig ? 300 : 0;
            const int32 ExpectedCooling = Probe.bBig ? 5 : 1;
            const double ExpectedDuration = Probe.bBig ? 60. : 45.;
            if (Face.Groups != 5 || Face.Hits != ExpectedHits || Face.DefencePercent != ExpectedDefence ||
                Face.AttackPercent != ExpectedAttack || Face.CoolingMultiplier != ExpectedCooling ||
                !FMath::IsNearlyEqual(Duration, ExpectedDuration, .10) || !Probe.bBlackout || MaxDark <= 0. || MaxDark > .20 ||
                (Probe.bBig && !Probe.bConfirmed) || Probe.MaxFlow - Probe.MinFlow < .15 || MaxPlate - MinPlate < .5 || !Probe.bProgressScreenshot)
            { Finish(false, TEXT("settlement_or_visual_transition_missing")); return; }
            Probe.ActiveAt = Time;
            UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_PROOF_SETTLED mode=%s time=%.3f groups=%d hits=%d ring_total=%d defence=%d attack=%d cooling=%d duration=%.3f flow_span=%.3f blink_span=%.3f max_blackout_ms=%.1f"),
                Mode, Time, Face.Groups, Face.Hits, Face.RingTotal, Face.DefencePercent, Face.AttackPercent,
                Face.CoolingMultiplier, Duration, Probe.MaxFlow - Probe.MinFlow, MaxPlate - MinPlate, 1000. * MaxDark);
        }
        if (Time - Probe.ActiveAt > .30 && !Probe.bActiveScreenshot)
        {
            Probe.bActiveScreenshot = true;
            if (Light.FullArm <= .05 || Own().EnergyBuffTimer <= 0. || Light.ActivePointLights != 15)
            { Finish(false, TEXT("no_activated_lamps_or_robot_buff")); return; }
            UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_DIRECT_LIGHTS mode=%s arm_emitters=%d"),
                Mode, Light.ActivePointLights);
            Capture(TEXT("activated"));
        }
        return;
    }

    if (Probe.ActiveAt < 0.) { Finish(false, TEXT("activation_expired_early")); return; }
    if (Time < Face.BuffUntil + .40)
    {
        bSawFade |= Light.FullArm > .10 && Light.FullArm < 4.;
        return;
    }
    if (!bSawFade || Face.DefencePercent || Face.AttackPercent || Face.CoolingMultiplier != 1 || Own().EnergyBuffTimer > 0. ||
        FMath::Max(FMath::Max(Light.Plate, Light.Flow), FMath::Max(Light.Progress, Light.FullArm)) > .03 ||
        Light.ActivePointLights != 0)
    {
        UE_LOG(LogRMSim, Warning, TEXT("RMUC_RUNE_PROOF_EXPIRY_MISMATCH mode=%s groups=%d fade=%d plate=%.3f flow=%.3f progress=%.3f full=%.3f buff_seconds=%.3f"),
            Mode, Face.Groups, bSawFade, Light.Plate, Light.Flow, Light.Progress, Light.FullArm, Own().EnergyBuffTimer);
        Finish(false, TEXT("buff_or_lights_not_expired"));
        return;
    }
    if (Probe.ExpiredAt < 0.)
    {
        Probe.ExpiredAt = Time;
        Capture(TEXT("expired"));
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_PROOF_EXPIRED mode=%s time=%.3f elapsed=%.3f off=1"),
            Mode, Time, Time - Probe.ActiveAt);
    }
    if (Time < Probe.ExpiredAt + 2.50) return;
    if (Probe.bBig) { Finish(true, TEXT("both_lifecycles_complete")); return; }
    const auto Camera = Probe.Camera;
    Probe = {};
    Probe.Camera = Camera;
    Probe.bBig = true;
    MatchElapsedSeconds = 180.;
    StageRemainSeconds = 240.;
    RuneRules.AdvanceTo(MatchElapsedSeconds);
    UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_PROOF_WAITING mode=large"));
}
#endif
