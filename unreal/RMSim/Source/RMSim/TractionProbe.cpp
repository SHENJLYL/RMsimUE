#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"

void AImportLabGameMode::TickTractionProbe(double DeltaSeconds)
{
    if(!Rig||!Rig->HasSteeringModules()){Abort(TEXT("traction probe needs physical steering modules"));return;}
    if(!TractionSlope)
    {
        const FRotator SlopeRotation(20,0,0);const FVector SlopeCentre(0,0,200);
        TractionSlope=GetWorld()->SpawnActor<AStaticMeshActor>(SlopeCentre,SlopeRotation);
        auto* Mesh=TractionSlope->GetStaticMeshComponent();Mesh->SetMobility(EComponentMobility::Movable);
        Mesh->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube")));
        Mesh->SetWorldScale3D(FVector(8,4,.1));Mesh->SetCollisionProfileName(TEXT("BlockAll"));
        Mesh->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);Mesh->RecreatePhysicsState();
        TractionPose=FTransform(SlopeRotation,SlopeCentre+SlopeRotation.Quaternion().GetUpVector()*7);
        Rig->ResetContactPose(TractionPose);ContactStageStart=TractionPose;
        ControlReport->SetStringField(TEXT("fixture"),TEXT("20_degree_8x4m_incline_above_flat_floor; test setup teleports only"));
        return;
    }
    if(!TractionSlope->GetStaticMeshComponent()->IsPhysicsStateCreated())return;
    UpdatePlayerView(GetWorld()->GetFirstPlayerController());
    if(!ControlPendingCapture.IsEmpty())
    {
        if(++ControlCaptureWarmFrames<24)return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("traction capture failed"));return;}
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    ContactPhaseTime+=DeltaSeconds;
    const auto State=ManualControlState();
    if(ContactSamples.IsEmpty()||ContactPhaseTime-FMath::Floor((ContactPhaseTime-DeltaSeconds)*4)/4>=.25)
    {
        auto S=MakeShared<FJsonObject>();S->SetNumberField(TEXT("phase"),ControlProbePhase);S->SetNumberField(TEXT("phase_time"),ContactPhaseTime);S->SetObjectField(TEXT("state"),State);
        ContactSamples.Add(MakeShared<FJsonValueObject>(S));ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    }
    if(Rig->GetActorLocation().Z<100||Rig->GetActorUpVector().Z<.7){Abort(TEXT("traction fixture lost physical support"));return;}
    const FVector Delta=Rig->GetActorLocation()-ContactStageStart.GetLocation();
    const double Downhill=-FVector::DotProduct(Delta,TractionPose.GetRotation().GetForwardVector());
    FManualControlInput Input;bool Passed=false;FString Name,Detail;
    switch(ControlProbePhase)
    {
    case 0:
        Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<3)return;
        Name=TEXT("traction_supported_slope");Passed=Rig->GetVelocity().Size()<5&&Rig->GetActorUpVector().Z>.9;
        Rig->SetFreeRollingDiagnostic(true,0);break;
    case 1:
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<.8)return;
        TractionParallelDistanceCm=Downhill;
        Name=TEXT("parallel_wheels_free_roll");Detail=FString::Printf(TEXT("Motor-disabled wheels aligned down slope roll %.3fcm in %.3fs"),Downhill,ContactPhaseTime);
        Passed=Downhill>30;Rig->ResetContactPose(TractionPose);Rig->SetFreeRollingDiagnostic(false,PI/2);break;
    case 2:
    {
        Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        double MaximumError=0;
        const auto CurrentContact=Rig->GetContactState();
        for(const auto& W:CurrentContact->GetArrayField(TEXT("wheels")))
            MaximumError=FMath::Max(MaximumError,FMath::Abs(FMath::FindDeltaAngleRadians(W->AsObject()->GetNumberField(TEXT("steering_measured_rad")),PI/2)));
        Name=TEXT("transverse_wheels_settle");Detail=FString::Printf(TEXT("Four modules steer across slope; maximum error %.5frad"),MaximumError);
        Passed=MaximumError<.04&&Rig->GetVelocity().Size()<5;Rig->SetFreeRollingDiagnostic(true,PI/2);break;
    }
    case 3:
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<.8)return;
        Name=TEXT("transverse_contact_grip");Detail=FString::Printf(TEXT("Same slope, motors disabled: transverse downhill %.3fcm vs aligned %.3fcm"),Downhill,TractionParallelDistanceCm);
        Passed=FMath::Abs(Downhill)<3&&FMath::Abs(Downhill)<TractionParallelDistanceCm*.1;
        ControlPendingCapture=TEXT("traction-cross");break;
    case 4:
        if(ContactPhaseTime<=DeltaSeconds){Rig->ResetContactPose(TractionPose);TractionStationarySeconds=0;}
        Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);
        TractionStationarySeconds=Rig->GetCommandOriginVelocity().Size()<.5?TractionStationarySeconds+DeltaSeconds:0;
        if(ContactPhaseTime<2||(TractionStationarySeconds<.5&&ContactPhaseTime<12))return;
        Name=TEXT("traction_reset_for_hold");
        Detail=FString::Printf(TEXT("Hold measurement starts after %.3fs setup, %.3fs continuously below 0.5cm/s at chassis origin"),ContactPhaseTime,TractionStationarySeconds);
        Passed=TractionStationarySeconds>=.5;break;
    case 5:
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<3)return;
        Name=TEXT("released_keys_hold_slope");Detail=FString::Printf(TEXT("Zero movement input, motors active: downhill %.3fcm, speed %.3fcm/s"),Downhill,Rig->GetVelocity().Size());
        Passed=FMath::Abs(Downhill)<1&&Rig->GetVelocity().Size()<.5;
        ControlPendingCapture=TEXT("traction-hold");break;
    case 6:
        Input.Throttle=-.35/Rig->GetDriveParameter(TEXT("MaxReverseSpeedMps"));ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<2)return;
        Name=TEXT("downhill_drive_before_release");
        Detail=FString::Printf(TEXT("Actual downhill distance %.3fcm, speed %.3fcm/s"),Downhill,Rig->GetCommandOriginVelocity().Size());
        Passed=Downhill>40&&Rig->GetCommandOriginVelocity().Size()>25;break;
    case 7:
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<3)return;
        Name=TEXT("downhill_key_release_brake");
        Detail=FString::Printf(TEXT("Released moving input on 20deg slope: stop distance %.3fcm, final origin speed %.3fcm/s"),Downhill,Rig->GetCommandOriginVelocity().Size());
        Passed=FMath::Abs(Downhill)<12&&Rig->GetCommandOriginVelocity().Size()<.5;break;
    case 8:
        ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<=DeltaSeconds)
        {
            TractionExternalPeakSpeedCm=0;
            // External 10 N.s impulse through the COM; the driving code must not
            // freeze a parked body. Only the test supplies this impulse.
            CastChecked<UStaticMeshComponent>(Rig->GetRootComponent())->AddImpulse(
                -TractionPose.GetRotation().GetForwardVector()*1000,NAME_None,false);
        }
        TractionExternalPeakSpeedCm=FMath::Max(TractionExternalPeakSpeedCm,Rig->GetCommandOriginVelocity().Size());
        if(ContactPhaseTime<.2)return;
        Name=TEXT("parked_external_impulse_wakes_body");
        Detail=FString::Printf(TEXT("External impulse: physical movement %.3fcm, peak origin speed %.3fcm/s"),Delta.Size(),TractionExternalPeakSpeedCm);
        Passed=Delta.Size()>.1&&TractionExternalPeakSpeedCm>5;break;
    case 9:
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<3)return;
        Name=TEXT("parked_external_impulse_recovery");
        Passed=Rig->GetCommandOriginVelocity().Size()<.5;break;
    default:
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot save traction report"));return;}
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
    if(Detail.IsEmpty())Detail=FString::Printf(TEXT("speed %.4fcm/s upright %.5f"),Rig->GetVelocity().Size(),Rig->GetActorUpVector().Z);
    if(!Passed){Rig->SetFreeRollingDiagnostic(false,0);Rig->SetContactDrive(0,0,true);CaptureControlView(TEXT("traction-failed"));}
    if(RecordControlCheck(Name,Passed,State,Detail))
    {++ControlProbePhase;ContactPhaseTime=0;ContactStageStart=Rig->GetActorTransform();}
}
