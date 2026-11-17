#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

// Explicit development fixture: actual chassis load/release and an impulse.
// No pose correction, friction changes, solver changes, or suspension overrides.
void AImportLabGameMode::TickSuspensionProbe(double DeltaSeconds)
{
    if(!Rig||!Rig->IsContactPhysics()||DeltaSeconds<=0||DeltaSeconds>.02)
    {Abort(TEXT("suspension probe requires contact physics and fixed step <=20ms"));return;}
    auto* Chassis=Cast<UStaticMeshComponent>(Rig->GetRootComponent());
    if(!Chassis||!Chassis->IsSimulatingPhysics())
    {Abort(TEXT("suspension probe requires the real simulated chassis"));return;}
    double LoadN=100.;FParse::Value(FCommandLine::Get(),TEXT("SuspensionLoadN="),LoadN);
    if(!FMath::IsFinite(LoadN)||LoadN<=0||LoadN>150)
    {Abort(TEXT("suspension test load must be in (0,150] N"));return;}
    UpdatePlayerView(GetWorld()->GetFirstPlayerController());
    Rig->SetContactDrive(0,0,true);
    ContactPhaseTime+=DeltaSeconds;
    const FVector Position=Chassis->GetComponentLocation();
    const FVector Velocity=Chassis->GetPhysicsLinearVelocityAtPoint(Position);
    const FRotator Rotation=Chassis->GetComponentRotation();
    if(Position.ContainsNaN()||Velocity.ContainsNaN()||Rig->GetActorUpVector().Z<.8)
    {Abort(TEXT("suspension fixture lost upright finite physical state"));return;}
    auto Sample=MakeShared<FJsonObject>();
    Sample->SetNumberField(TEXT("phase"),ControlProbePhase);
    Sample->SetNumberField(TEXT("phase_time_s"),ContactPhaseTime);
    Sample->SetNumberField(TEXT("dt_s"),DeltaSeconds);
    Sample->SetNumberField(TEXT("chassis_z_cm"),Position.Z);
    Sample->SetNumberField(TEXT("chassis_vz_cm_s"),Velocity.Z);
    Sample->SetNumberField(TEXT("pitch_deg"),Rotation.Pitch);
    Sample->SetNumberField(TEXT("roll_deg"),Rotation.Roll);
    Sample->SetNumberField(TEXT("up_z"),Rig->GetActorUpVector().Z);
    const auto Physical=Rig->GetContactState();
    TArray<TSharedPtr<FJsonValue>> Displacements;
    for(const auto& Wheel:Physical->GetArrayField(TEXT("wheels")))
        Displacements.Add(Wheel->AsObject()->GetArrayField(TEXT("slider_displacement_body_cm"))[2]);
    Sample->SetArrayField(TEXT("slider_z_body_cm"),Displacements);
    ContactSamples.Add(MakeShared<FJsonValueObject>(Sample));
    ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    ControlReport->SetStringField(TEXT("fixture"),TEXT("settle_3s_load_2s_release_3s_downward_impulse_3s"));
    ControlReport->SetStringField(TEXT("force_timing"),TEXT("post-physics forces affect next physics step; phase samples record its actual outcome"));
    ControlReport->SetNumberField(TEXT("external_load_n"),LoadN);
    ControlReport->SetNumberField(TEXT("downward_impulse_ns"),4.);
    ControlReport->SetObjectField(TEXT("physics_parameters"),Physical->GetObjectField(TEXT("parameters")));
    const auto State=ManualControlState();
    const auto ApplyLoad=[&](){Chassis->AddForce(FVector(0,0,-LoadN*100.));}; // N -> kg cm/s^2
    if(ControlProbePhase==0)
    {
        if(ContactPhaseTime<3.)return;
        if(!RecordControlCheck(TEXT("suspension_baseline_settled"),
            FMath::Abs(Velocity.Z)<.5&&Rig->GetActorUpVector().Z>.98,State,
            FString::Printf(TEXT("Unloaded physical equilibrium z=%.6fcm vz=%.6fcm/s"),Position.Z,Velocity.Z)))return;
        ContactStageStart=Rig->GetActorTransform();
        ControlReport->SetNumberField(TEXT("rest_height_cm"),Position.Z);
        if(!CaptureControlView(TEXT("suspension-rest"))){Abort(TEXT("suspension rest capture failed"));return;}
        ++ControlProbePhase;ContactPhaseTime=0;ApplyLoad();return;
    }
    if(ControlProbePhase==1)
    {
        if(ContactPhaseTime<2.){ApplyLoad();return;}
        const double CompressionCm=ContactStageStart.GetLocation().Z-Position.Z;
        if(!RecordControlCheck(TEXT("suspension_loaded_equilibrium"),
            CompressionCm>.1&&CompressionCm<3.&&FMath::Abs(Velocity.Z)<.5&&Rig->GetActorUpVector().Z>.95,State,
            FString::Printf(TEXT("%.3fN added chassis load, compression=%.6fcm vz=%.6fcm/s"),LoadN,CompressionCm,Velocity.Z)))return;
        if(!CaptureControlView(TEXT("suspension-loaded"))){Abort(TEXT("suspension load capture failed"));return;}
        ++ControlProbePhase;ContactPhaseTime=0;return; // Release the external load.
    }
    if(ControlProbePhase==2)
    {
        if(ContactPhaseTime<3.)return;
        if(!RecordControlCheck(TEXT("suspension_release_recovered"),
            FMath::Abs(Position.Z-ContactStageStart.GetLocation().Z)<.2&&FMath::Abs(Velocity.Z)<.5&&Rig->GetActorUpVector().Z>.98,
            State,TEXT("Unforced physical spring/damper response returns within 2mm of unloaded height")))return;
        ++ControlProbePhase;ContactPhaseTime=0;
        Chassis->AddImpulse(FVector(0,0,-400.)); // Same 4 N.s disturbance for both profiles.
        return;
    }
    if(ControlProbePhase==3)
    {
        if(ContactPhaseTime<3.)return;
        if(!RecordControlCheck(TEXT("suspension_impulse_recovered"),
            FMath::Abs(Position.Z-ContactStageStart.GetLocation().Z)<.2&&FMath::Abs(Velocity.Z)<.5&&Rig->GetActorUpVector().Z>.98,
            State,TEXT("Actual chassis impulse recovers without pose constraints or active height control")))return;
        if(!CaptureControlView(TEXT("suspension-recovered"))){Abort(TEXT("suspension recovery capture failed"));return;}
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot save suspension report"));return;}
        UE_LOG(LogRMSim,Display,TEXT("SUSPENSION_PROBE_COMPLETE samples=%d checks=%d"),ContactSamples.Num(),ControlChecks.Num());
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
    Abort(TEXT("invalid suspension fixture phase"));
}
