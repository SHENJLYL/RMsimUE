#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"

bool AImportLabGameMode::WriteControlReport(const FString& Status, const FString& Reason)
{
    if (!ControlReport.IsValid() || ControlEvidenceDirectory.IsEmpty()) return false;
    ControlReport->SetStringField(TEXT("status"), Status);
    ControlReport->SetBoolField(TEXT("complete"), Status != TEXT("running"));
    ControlReport->SetArrayField(TEXT("checks"), ControlChecks);
    ControlReport->SetStringField(TEXT("updated_utc"), FDateTime::UtcNow().ToIso8601());
    if (!Reason.IsEmpty()) ControlReport->SetStringField(TEXT("failure_reason"), Reason);
    FString Json;
    return IFileManager::Get().MakeDirectory(*ControlEvidenceDirectory, true) &&
        FJsonSerializer::Serialize(ControlReport.ToSharedRef(), TJsonWriterFactory<>::Create(&Json)) &&
        FFileHelper::SaveStringToFile(Json, *FPaths::Combine(ControlEvidenceDirectory, TEXT("control-result.json")),
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
}

bool AImportLabGameMode::RecordControlCheck(const FString& Name, bool Passed,
    const TSharedPtr<FJsonObject>& Before, const FString& Detail)
{
    TSharedRef<FJsonObject> Check = MakeShared<FJsonObject>();
    Check->SetStringField(TEXT("name"), Name);
    Check->SetBoolField(TEXT("passed"), Passed);
    Check->SetStringField(TEXT("detail"), Detail);
    Check->SetObjectField(TEXT("before"), Before);
    Check->SetObjectField(TEXT("after"), ManualControlState());
    ControlChecks.Add(MakeShared<FJsonValueObject>(Check));
    UE_LOG(LogRMSim, Display, TEXT("CONTROL_CHECK %s %s"), *Name, Passed ? TEXT("passed") : TEXT("failed"));
    if (!Passed) { Abort(Name + TEXT(": ") + Detail); return false; }
    if (!WriteControlReport(TEXT("running"))) { Abort(TEXT("cannot write control evidence")); return false; }
    return true;
}

bool AImportLabGameMode::CaptureControlView(const FString& Phase)
{
    CaptureDirectory = ControlEvidenceDirectory;
    return CapturePngs(Phase);
}

void AImportLabGameMode::TickControlProbe()
{
    if (bCombatProbeActive) { TickCombatProbe(); return; }
    if (bTerrainProbeActive) { TickTerrainProbe(); return; }
    APlayerController* PC = GetWorld()->GetFirstPlayerController();
    if (!Rig || !PC || !ChaseCamera || !SightCamera) { Abort(TEXT("control probe scene unavailable")); return; }
    if (!ControlPendingCapture.IsEmpty())
    {
        // Same camera transform/FOV as the actual player view; hold it for temporal-history warmup.
        ACameraActor* View = CastChecked<ACameraActor>(PC->GetViewTarget());
        OverviewCapture->SetWorldTransform(View->GetActorTransform());
        OverviewCapture->FOVAngle = View->GetCameraComponent()->FieldOfView;
        if (++ControlCaptureWarmFrames < 24) return;
        if (!CaptureControlView(ControlPendingCapture)) { Abort(TEXT("control view capture failed")); return; }
        ControlPendingCapture.Reset(); ControlCaptureWarmFrames = 0;
        return;
    }
    const TSharedPtr<FJsonObject> Before = ManualControlState();
    const FTransform BeforeActor = Rig->GetActorTransform();
    FManualControlInput Input;
    auto Step = [this](const FManualControlInput& Frame, int32 Count = 1)
    {
        for (int32 I = 0; I < Count; ++I) ApplyManualInput(Frame, 1.0 / 60.0);
    };
    bool Passed = false;
    FString Name, Detail;
    switch (ControlProbePhase)
    {
    case 0:
        Step(Input, 120);
        Name = TEXT("chase_roof_clearance");
        Detail = TEXT("Chase centre ray crosses at least 30cm above the roof; no turret-dependent pivot drift");
        Passed = ChasePivotHeightCm > Rig->GetCollisionBounds().Max.Z - Rig->GetActorLocation().Z + 30.0 &&
            PC->GetViewTarget() == ChaseCamera && FMath::IsNearlyEqual(ChaseCamera->GetCameraComponent()->FieldOfView, 55.0f);
        ControlPendingCapture = TEXT("chase");
        break;
    case 1:
        Input.Throttle = 1; Step(Input, 60);
        Name = TEXT("forward_throttle"); Detail = TEXT("One second of W accelerates and advances in local X, with no lateral translation");
        Passed = Rig->GetActorLocation().X > BeforeActor.GetLocation().X + 50 &&
            FMath::Abs(Rig->GetActorLocation().Y - BeforeActor.GetLocation().Y) < 0.01 && SpeedCmPerSecond > 100;
        break;
    case 2:
        Input.Brake = true; Step(Input, 30);
        Name = TEXT("space_brake"); Detail = TEXT("Space brakes speed and turn rate to zero");
        Passed = FMath::Abs(SpeedCmPerSecond) < 0.01 && FMath::Abs(TurnDegreesPerSecond) < 0.01;
        break;
    case 3:
        Input.Throttle = -1; Step(Input, 60);
        Name = TEXT("reverse_throttle"); Detail = TEXT("S reverses with negative speed and moves backward");
        Passed = SpeedCmPerSecond < -100 && Rig->GetActorLocation().X < BeforeActor.GetLocation().X - 50;
        break;
    case 4:
        Input.Brake = true; Step(Input, 30);
        Name = TEXT("brake_before_turn"); Detail = TEXT("Car is stationary before independent hull steering test");
        Passed = FMath::Abs(SpeedCmPerSecond) < 0.01;
        break;
    case 5:
    {
        const FVector Target = AimWorldTarget;
        const double ViewYaw = OrbitYaw;
        Input.Steer = 1; Step(Input, 30);
        Input.Steer = 0; Input.Brake = true; Step(Input, 60);
        const double Turn = FMath::Abs(FMath::FindDeltaAngleDegrees(BeforeActor.Rotator().Yaw, Rig->GetActorRotation().Yaw));
        USceneCaptureComponent2D* Aim = GetAimCapture();
        const FVector Desired = (AimWorldTarget - Aim->GetComponentLocation()).GetSafeNormal();
        Name = TEXT("hull_turn_preserves_world_aim"); Detail = TEXT("Hull turns >10deg while view yaw/target stay fixed and the actual gun follows within 1deg");
        Passed = Turn > 10 && FMath::IsNearlyEqual(OrbitYaw, ViewYaw, 1.e-6) && Target.Equals(AimWorldTarget, 0.05) &&
            FVector::DotProduct(Desired, Aim->GetForwardVector()) > FMath::Cos(FMath::DegreesToRadians(1.0));
        break;
    }
    case 6:
    {
        const FVector Target = AimWorldTarget;
        const double ViewYaw = OrbitYaw;
        Input.FreeLook = true; Input.MouseX = 200; Step(Input);
        Name = TEXT("free_look_preserves_target"); Detail = TEXT("C+200 raw counts changes view by the configured sensitivity without replacing world aim");
        Passed = bFreeLook && FMath::IsNearlyEqual(FMath::Abs(FMath::FindDeltaAngleDegrees(ViewYaw, OrbitYaw)),
            MouseSensitivity * 200.0, 1.e-6) && Target.Equals(AimWorldTarget, 1.e-6);
        ControlPendingCapture = TEXT("free-look");
        break;
    }
    case 7:
    {
        const FVector Target = AimWorldTarget;
        const double SavedYaw = SavedOrbitYaw;
        Step(Input);
        Name = TEXT("free_look_release"); Detail = TEXT("Releasing C returns the saved view and keeps the same target");
        Passed = !bFreeLook && FMath::IsNearlyEqual(OrbitYaw, SavedYaw, 1.e-6) && Target.Equals(AimWorldTarget, 1.e-6);
        break;
    }
    case 8:
    {
        const FVector Target = AimWorldTarget;
        Input.ToggleSight = true; Step(Input);
        Name = TEXT("shift_scope"); Detail = TEXT("Shift chooses actual muzzle camera at 30deg without changing target");
        Passed = bSightMode && PC->GetViewTarget() == SightCamera && Target.Equals(AimWorldTarget, 1.e-6) &&
            FMath::IsNearlyEqual(SightCamera->GetCameraComponent()->FieldOfView, 30.0f);
        ControlPendingCapture = TEXT("scope");
        break;
    }
    case 9:
    {
        const FVector Target = AimWorldTarget;
        const double Distance = ChaseDistanceCm;
        Input.WheelSteps = 2; Step(Input);
        Name = TEXT("scope_wheel_zoom"); Detail = TEXT("Wheel reduces scope FOV to 24deg, without changing chase distance or target");
        Passed = FMath::IsNearlyEqual(ScopeBaseFov, 24.0) && FMath::IsNearlyEqual(ChaseDistanceCm, Distance) &&
            Target.Equals(AimWorldTarget, 1.e-6) && FMath::IsNearlyEqual(SightCamera->GetCameraComponent()->FieldOfView, 24.0f);
        break;
    }
    case 10:
        Input.Zoom = true; Step(Input);
        Name = TEXT("right_button_zoom"); Detail = TEXT("Right button temporarily uses 15deg FOV");
        Passed = bSightZoom && FMath::IsNearlyEqual(SightCamera->GetCameraComponent()->FieldOfView, 15.0f);
        ControlPendingCapture = TEXT("scope-zoom");
        break;
    case 11:
        Step(Input);
        Name = TEXT("zoom_release_restores_selected_fov"); Detail = TEXT("Releasing right button returns wheel-selected 24deg FOV");
        Passed = !bSightZoom && FMath::IsNearlyEqual(SightCamera->GetCameraComponent()->FieldOfView, 24.0f);
        break;
    case 12:
    {
        const double ViewYaw = OrbitYaw;
        const FVector Target = AimWorldTarget;
        Input.MouseX = 100; Step(Input); Input.MouseX = 0; Step(Input, 60);
        Name = TEXT("scope_mouse_moves_aim"); Detail = TEXT("Mouse in scope changes desired yaw and target; it is not locked to old barrel direction");
        Passed = FMath::Abs(FMath::FindDeltaAngleDegrees(ViewYaw, OrbitYaw)) > 1 && !Target.Equals(AimWorldTarget, 1.0);
        break;
    }
    case 13:
    {
        const FVector Target = AimWorldTarget;
        Input.ToggleSight = true; Step(Input);
        const double Alignment = FVector::DotProduct((Target - ChaseCamera->GetActorLocation()).GetSafeNormal(),
            ChaseCamera->GetActorForwardVector());
        Name = TEXT("return_to_chase");
        Detail = FString::Printf(TEXT("Shift preserves aim and reprojects it to the chase centre; angular error %.6f deg"),
            FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(Alignment, -1.0, 1.0))));
        Passed = !bSightMode && PC->GetViewTarget() == ChaseCamera && Target.Equals(AimWorldTarget, 1.e-6) &&
            Alignment > FMath::Cos(FMath::DegreesToRadians(0.1));
        break;
    }
    case 14:
    {
        const double Distance = ChaseDistanceCm, Fov = ScopeBaseFov;
        Input.WheelSteps = 1; Step(Input);
        Name = TEXT("chase_wheel_distance"); Detail = TEXT("Chase wheel reduces distance 35cm and leaves scope FOV unchanged");
        Passed = FMath::IsNearlyEqual(ChaseDistanceCm, Distance - 35.0) && FMath::IsNearlyEqual(ScopeBaseFov, Fov);
        break;
    }
    case 15:
    {
        Input.Throttle = 1; Step(Input, 20);
        const FTransform MovingActor = Rig->GetActorTransform();
        Input.Focused = false; Input.ToggleSight = true; Input.Steer = 1; Input.MouseX = 1000; Step(Input);
        Name = TEXT("focus_loss_stops_input"); Detail = TEXT("Unfocused input stops speed and ignores movement, mouse and view toggles");
        Passed = Rig->GetActorTransform().Equals(MovingActor, 1.e-6) && SpeedCmPerSecond == 0 && !bHadInputFocus && !bSightMode;
        break;
    }
    case 16:
    {
        const double ViewYaw = OrbitYaw;
        Input.MouseX = 1000; Step(Input);
        Name = TEXT("focus_regain_discards_mouse_jump"); Detail = TEXT("First focused frame ignores stale mouse delta");
        Passed = FMath::IsNearlyEqual(ViewYaw, OrbitYaw, 1.e-6) && bHadInputFocus;
        break;
    }
    case 17:
    {
        Input.MouseX = 30.0 / MouseSensitivity; Step(Input);
        Input.MouseX = 0; Step(Input, 5); // 0.1 seconds total, through the real manual gimbal path.
        USceneCaptureComponent2D* Aim = GetAimCapture();
        const double Alignment = FVector::DotProduct((AimWorldTarget - Aim->GetComponentLocation()).GetSafeNormal(), Aim->GetForwardVector());
        const double Error = FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(Alignment, -1.0, 1.0)));
        Name = TEXT("rapid_aim_convergence");
        Detail = FString::Printf(TEXT("After a 30deg mouse aim step, actual gun error after 0.1s is %.6f deg (must be <0.2deg)"), Error);
        Passed = Error < 0.2;
        ControlPendingCapture = TEXT("aim-converged");
        break;
    }
    default:
        if (ControlChecks.Num() != 18 || !WriteControlReport(TEXT("passed")))
        { Abort(TEXT("control probe report incomplete")); return; }
        UE_LOG(LogRMSim, Display, TEXT("CONTROL_PROBE_PASSED %s"), *ControlEvidenceDirectory);
        bReady = false;
        FPlatformMisc::RequestExitWithStatus(false, 0);
        return;
    }
    if (!RecordControlCheck(Name, Passed, Before, Detail)) return;
    ++ControlProbePhase;
}
