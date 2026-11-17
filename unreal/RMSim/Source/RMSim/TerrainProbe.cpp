#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

void AImportLabGameMode::TickTerrainProbe()
{
    if (!Rig) { Abort(TEXT("terrain probe rig unavailable")); return; }
    if (!ControlPendingCapture.IsEmpty())
    {
        if (++ControlCaptureWarmFrames<24) return;
        if (!CaptureControlView(ControlPendingCapture)) { Abort(TEXT("terrain capture failed")); return; }
        ControlPendingCapture.Reset(); ControlCaptureWarmFrames=0; return;
    }
    const TSharedPtr<FJsonObject> Before=ManualControlState();
    const FTransform Pose=Rig->GetActorTransform();
    double TraversalDistance=0;FParse::Value(FCommandLine::Get(),TEXT("TraversalDistanceCm="),TraversalDistance);
    if(TraversalDistance>0&&ControlProbePhase>0)
    {
        if(ControlProbePhase==1)
        {
            bool Accepted=true;double Distance=0;
            while(Distance<TraversalDistance&&Accepted)
            {
                const double D=FMath::Min(2.,TraversalDistance-Distance);Accepted=Rig->MoveManual(FVector(D,0,0),0);Distance+=D;
                Accepted &= !Rig->GetManualState().bBlockingOverlap;
            }
            const double Travel=FVector::Dist2D(Pose.GetLocation(),Rig->GetActorLocation());
            const FString Detail=FString::Printf(TEXT("Requested%.2fcm, accepted%.2fcm, heightchange%.4fcm, reason=%s"),
                TraversalDistance,Travel,Rig->GetActorLocation().Z-Pose.GetLocation().Z,*Rig->GetManualState().TerrainBlockReason);
            if(RecordControlCheck(TEXT("source_ramp_traversal"),Accepted&&Travel>TraversalDistance-1,Before,Detail))
            {++ControlProbePhase;ControlPendingCapture=TEXT("drive");}
            return;
        }
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot finish traversal report"));return;}
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
    FManualControlInput Input;
    bool Passed=false; FString Name,Detail;
    auto Step=[this](const FManualControlInput& Frame,int32 Count)
    {
        for (int32 I=0;I<Count;++I) ApplyManualInput(Frame,1.0/60.0);
    };
    switch (ControlProbePhase)
    {
    case 0:
    {
        const FManualRigState State=Rig->GetManualState();
        Name=TEXT("supported_spawn"); Detail=TEXT("Real wheel rays hit imported map collision; supported vehicle has no blocking overlap");
        Passed=State.bTerrainFollowing && State.GroundContacts.Num()>=3 && !State.bBlockingOverlap && State.GroundNormal.Z>0.819;
        ControlPendingCapture=TEXT("spawn");
        break;
    }
    case 1:
        Input.Throttle=1; Step(Input,60);
        Name=TEXT("terrain_forward"); Detail=TEXT("One second of the common throttle path moves at least 50cm with supported, nonoverlapping pose");
        Passed=FVector::Dist2D(Pose.GetLocation(),Rig->GetActorLocation())>50 && !Rig->GetManualState().bBlockingOverlap;
        ControlPendingCapture=TEXT("drive");
        break;
    case 2:
        Input.Brake=true; Step(Input,60);
        Name=TEXT("terrain_brake"); Detail=TEXT("Space brakes motion to zero on the imported terrain");
        Passed=FMath::Abs(SpeedCmPerSecond)<0.01 && FMath::Abs(TurnDegreesPerSecond)<0.01;
        break;
    case 3:
        Input.Throttle=-1; Step(Input,60);
        Name=TEXT("terrain_reverse"); Detail=TEXT("Common reverse input moves at least 50cm without penetration");
        Passed=FVector::Dist2D(Pose.GetLocation(),Rig->GetActorLocation())>50 && SpeedCmPerSecond<-100 && !Rig->GetManualState().bBlockingOverlap;
        Input.Throttle=0; Input.Brake=true; Step(Input,60);
        break;
    case 4:
    {
        Input.Steer=1; Step(Input,30);
        Input.Steer=0; Input.Brake=true; Step(Input,60);
        USceneCaptureComponent2D* Aim=GetAimCapture();
        const double Turn=FMath::Abs(FMath::FindDeltaAngleDegrees(Pose.Rotator().Yaw,Rig->GetActorRotation().Yaw));
        const double Dot=Aim ? FVector::DotProduct((AimWorldTarget-Aim->GetComponentLocation()).GetSafeNormal(),Aim->GetForwardVector()) : 0;
        Name=TEXT("terrain_turn_and_aim"); Detail=TEXT("Hull turns >10 degrees; gimbal uses the tilted chassis frame and converges within 1 degree");
        Passed=Turn>10 && Dot>FMath::Cos(FMath::DegreesToRadians(1.0)) && !Rig->GetManualState().bBlockingOverlap;
        break;
    }
    case 5:
    {
        FTransform Outside=Pose; Outside.AddToTranslation(FVector(1000000,1000000,0));
        Rig->SetActorTransform(Outside);
        FString Error;
        const bool Rejected=!Rig->SnapToTerrain(Error) && Error.Contains(TEXT("missing ground")) && Rig->GetActorTransform().Equals(Outside);
        Rig->SetActorTransform(Pose); FString RestoreError;
        const bool Restored=Rig->SnapToTerrain(RestoreError);
        Name=TEXT("missing_ground_rejected"); Detail=TEXT("Test-only placement outside the map rejects unsupported ground without snapping; original supported pose restored");
        Passed=Rejected && Restored;
        break;
    }
    case 6:
    {
        bool Blocked=false;
        for(int32 I=0;I<8 && !Blocked;++I) Blocked=!Rig->MoveManual(FVector(10000,0,0),0);
        const FManualRigState State=Rig->GetManualState();
        const double Travel=FVector::Dist2D(Pose.GetLocation(),State.Actor.GetLocation());
        Name=TEXT("terrain_obstacle_or_edge_stop");
        Detail=FString::Printf(TEXT("Long substepped move blocked after %.2fcm: %s; pose remains nonoverlapping and is restored"),Travel,*State.TerrainBlockReason);
        Passed=Blocked && !State.bBlockingOverlap && !State.TerrainBlockReason.IsEmpty() && Travel<80000;
        ControlReport->SetObjectField(TEXT("blocked_state"),ManualControlState());
        Rig->SetActorTransform(Pose); FString Error;
        Passed &= Rig->SnapToTerrain(Error);
        break;
    }
    case 7:
    {
        Input.Steer=1;Step(Input,60);Input.Steer=0;Input.Brake=true;Step(Input,30);
        const double Turn=FMath::Abs(FMath::FindDeltaAngleDegrees(Pose.Rotator().Yaw,Rig->GetActorRotation().Yaw));
        Name=TEXT("terrain_full_turn");Detail=FString::Printf(TEXT("1s turn input must rotate >40deg on clear spawn ground; measured %.4fdeg, reason=%s"),Turn,*Rig->GetManualState().TerrainBlockReason);
        Passed=Turn>40&&!Rig->GetManualState().bBlockingOverlap;break;
    }
    case 8:
    {
        Input.Throttle=1;Step(Input,60);
        const double Distance=FVector::Dist2D(Pose.GetLocation(),Rig->GetActorLocation());
        Name=TEXT("terrain_drive_after_turn");Detail=FString::Printf(TEXT("1s forward input after turn must travel >50cm; measured %.4fcm, reason=%s"),Distance,*Rig->GetManualState().TerrainBlockReason);
        Passed=Distance>50&&!Rig->GetManualState().bBlockingOverlap;break;
    }
    default:
        if(!WriteControlReport(TEXT("passed"))) { Abort(TEXT("cannot finish terrain report")); return; }
        UE_LOG(LogRMSim,Display,TEXT("TERRAIN_PROBE_COMPLETE %d checks"),ControlChecks.Num());
        bReady=false; FPlatformMisc::RequestExitWithStatus(false,0); return;
    }
    if (RecordControlCheck(Name,Passed,Before,Detail)) ++ControlProbePhase;
}
