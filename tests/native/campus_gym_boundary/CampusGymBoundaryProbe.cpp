#include "ImportLabGameMode.h"
#include "CampusGym.h"
#include "VehicleRig.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "HAL/PlatformMisc.h"

// Opt-in evidence fixture. Spawn/SnapToTerrain is the ordinary initialization;
// only wheel motor commands change the vehicle after initialization.
struct FCampusGymBoundaryProbeState
{
    TWeakObjectPtr<UStaticMeshComponent> Wall;
    FTransform WallTransform;
    FVector Start=FVector::ZeroVector,Forward=FVector::ForwardVector,Side=FVector::RightVector;
    FVector HoldStart=FVector::ZeroVector,ReverseStart=FVector::ZeroVector;
    uint64 MotionGeneration=0;
    double Seconds=0,NextSample=0,PeakSpeedMps=0,PeakApproachSpeedMps=0,PeakImpulseNs=0;
    double FirstHitSeconds=-1,LastHitWorldSeconds=-1,HoldFirstHitWorld=-1,HoldLastHitWorld=-1;
    double HoldStoppedSeconds=0,HoldRecentSeconds=0,HoldAdvanceCm=0;
    double MinimumBoundaryMarginCm=TNumericLimits<double>::Max();
    double MaxDriftCm=0,MaxRiseCm=0,ReverseClearanceCm=0;
    int32 RecentContactFrames=0,Captures=0;
    TMap<FString,double> LastRecordedHit;
    TSet<FString> ContactBodies;
    TArray<TSharedPtr<FJsonValue>> Events;
};

namespace
{
TArray<TSharedPtr<FJsonValue>> GymVector(const FVector& V)
{
    return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),
        MakeShared<FJsonValueNumber>(V.Z)};
}
bool ReadGymVector(const TSharedPtr<FJsonObject>& Object,const TCHAR* Key,FVector& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* Array=nullptr;
    if(!Object.IsValid()||!Object->TryGetArrayField(Key,Array)||Array->Num()!=3)return false;
    for(int32 I=0;I<3;++I)
        if(!(*Array)[I]->TryGetNumber(Out[I])||!FMath::IsFinite(Out[I]))return false;
    return true;
}
}

void AImportLabGameMode::TickCampusGymBoundaryProbe(double DeltaSeconds)
{
    if(!Rig||!Rig->IsContactPhysics()||!ControlReport.IsValid()||!OverviewCapture||
        StageMap.Id!=TEXT("rmuc2027")||!FCampusGym::IsActive(GetWorld()))
    {Abort(TEXT("gym boundary probe requires ContactProbe, campus gym, native vehicle and EvidenceDir"));return;}
    if(!CampusGymBoundaryProbe.IsValid())
    {
        CampusGymBoundaryProbe=MakeShared<FCampusGymBoundaryProbeState>();
        auto& P=*CampusGymBoundaryProbe;
        AStaticMeshActor* WallActor=MapBoxes.FindRef(TEXT("arena_guard_south"));
        if(!WallActor){Abort(TEXT("real south perimeter wall is missing"));return;}
        P.Wall=WallActor->GetStaticMeshComponent();
        P.WallTransform=P.Wall->GetComponentTransform();
        P.Start=Rig->GetActorLocation();
        P.Forward=Rig->GetActorForwardVector().GetSafeNormal2D();
        P.Side=FVector::CrossProduct(FVector::UpVector,P.Forward);
        P.MotionGeneration=Rig->GetMotionGeneration();
        // This source-audited approach is beside the southern decorative door.
        // UE +Y is ROS -Y, so the southward approach is UE +Y.
        if(FMath::Abs(P.Start.X+1100)>15||FMath::Abs(P.Start.Y-660)>15||
            FVector::DotProduct(P.Forward,FVector(0,1,0))<.99)
        {Abort(TEXT("use the audited copied-map spawn ROS(-11,-6.6), yaw=-pi/2"));return;}
        ControlReport->SetStringField(TEXT("probe"),TEXT("campus_gym_actual_closed_boundary_v1"));
        ControlReport->SetStringField(TEXT("expected_wall"),TEXT("arena_guard_south"));
        ControlReport->SetStringField(TEXT("wall_component"),P.Wall->GetPathName());
        ControlReport->SetNumberField(TEXT("approach_command_speed_m_s"),2.0);
        ControlReport->SetNumberField(TEXT("reverse_command_speed_m_s"),.75);
        ControlReport->SetBoolField(TEXT("pose_overrides_used"),false);
        ControlReport->SetBoolField(TEXT("os_keyboard_test"),false);
        ControlReport->SetNumberField(TEXT("initial_motion_generation"),static_cast<double>(P.MotionGeneration));
        ControlReport->SetStringField(TEXT("contact_evidence_semantics"),
            TEXT("Exact component OnComponentHit observations via GetRecentHitAgainst; repeated recent frames are not unique solver events; no preimpact velocity estimate claimed"));
    }
    auto& P=*CampusGymBoundaryProbe;
    P.Seconds+=DeltaSeconds;
    UStaticMeshComponent* Wall=P.Wall.Get();
    auto Fail=[&](const FString& Reason)
    {
        Rig->SetContactDrive(0,0,true);
        CaptureControlView(TEXT("gym-boundary-failed"));
        Abort(Reason);
    };
    if(!Wall||Wall->GetCollisionEnabled()!=ECollisionEnabled::QueryAndPhysics||
        Wall->GetCollisionResponseToChannel(ECC_PhysicsBody)!=ECR_Block||
        !Wall->GetComponentTransform().Equals(P.WallTransform,.001))
    {Fail(TEXT("real perimeter wall changed or lost physical blocking"));return;}
    const FVector Position=Rig->GetActorLocation(),Displacement=Position-P.Start;
    const double Progress=FVector::DotProduct(Displacement,P.Forward);
    const double Drift=FVector::DotProduct(Displacement,P.Side);
    const double SpeedCmS=Rig->GetCommandOriginVelocity().Size();
    const double UpZ=Rig->GetActorUpVector().Z;
    P.PeakSpeedMps=FMath::Max(P.PeakSpeedMps,SpeedCmS/100.);
    if(ControlProbePhase==1)P.PeakApproachSpeedMps=FMath::Max(P.PeakApproachSpeedMps,SpeedCmS/100.);
    P.MaxDriftCm=FMath::Max(P.MaxDriftCm,FMath::Abs(Drift));
    P.MaxRiseCm=FMath::Max(P.MaxRiseCm,Displacement.Z);
    const FBox BodyBounds=Rig->GetFoliageContactBounds();
    const FBox& Source=StageMap.SourceBoundsMetres;
    const double Margin=FMath::Min(
        FMath::Min(BodyBounds.Min.X-Source.Min.X*100.,Source.Max.X*100.-BodyBounds.Max.X),
        FMath::Min(BodyBounds.Min.Y+Source.Max.Y*100.,-Source.Min.Y*100.-BodyBounds.Max.Y));
    P.MinimumBoundaryMarginCm=FMath::Min(P.MinimumBoundaryMarginCm,Margin);
    ControlReport->SetNumberField(TEXT("minimum_vehicle_envelope_boundary_margin_cm"),P.MinimumBoundaryMarginCm);
    ControlReport->SetNumberField(TEXT("maximum_actual_speed_m_s"),P.PeakSpeedMps);
    ControlReport->SetNumberField(TEXT("peak_approach_actual_speed_m_s"),P.PeakApproachSpeedMps);
    ControlReport->SetNumberField(TEXT("final_motion_generation"),static_cast<double>(Rig->GetMotionGeneration()));
    ControlReport->SetNumberField(TEXT("contact_recovery_count"),ContactRecoveryCount);
    ControlReport->SetNumberField(TEXT("elapsed_sim_seconds"),P.Seconds);
    ControlReport->SetNumberField(TEXT("maximum_lateral_drift_cm"),P.MaxDriftCm);
    ControlReport->SetNumberField(TEXT("maximum_body_rise_cm"),P.MaxRiseCm);
    if(!BodyBounds.IsValid||Rig->GetActorTransform().ContainsNaN()||!FMath::IsFinite(Margin)||
        !FMath::IsFinite(SpeedCmS)||Rig->GetMotionGeneration()!=P.MotionGeneration||
        ContactRecoveryCount!=0||Margin < -1.0||Displacement.Z < -100||Displacement.Z > 150||
        UpZ<.65||FMath::Abs(Drift)>60||P.Seconds>35)
    {Fail(TEXT("vehicle left physical boundary/route envelope, changed motion generation or timed out"));return;}

    // A fixed oblique interior camera includes the real glass door, its wall
    // contact and the complete robot; the vehicle cameras are captured too.
    const FVector Target(-1100,Wall->GetComponentLocation().Y,55);
    const FVector Eye=Target-P.Forward*300+P.Side*250+FVector(0,0,160);
    OverviewCapture->SetWorldLocationAndRotation(Eye,(Target-Eye).Rotation());
    OverviewCapture->FOVAngle=62;
    const FVector NearPoint(Position.X,Wall->GetComponentLocation().Y,Position.Z);
    double ImpulseNs=0;
    const bool Recent=Rig->GetRecentHitAgainst(Wall,NearPoint,80.,.10,ImpulseNs)&&ImpulseNs>.0001;
    if(Recent)
    {
        ++P.RecentContactFrames;
        P.PeakImpulseNs=FMath::Max(P.PeakImpulseNs,ImpulseNs);
        if(P.FirstHitSeconds<0)P.FirstHitSeconds=P.Seconds;
    }
    const auto Native=Rig->GetContactState();
    for(const auto& Value:Native->GetArrayField(TEXT("last_contact_per_body")))
    {
        const auto Observation=Value->AsObject();
        FVector Point,Normal;
        if(!Observation.IsValid()||!ReadGymVector(Observation,TEXT("point_cm"),Point)||
            !ReadGymVector(Observation,TEXT("normal"),Normal))continue;
        const double Age=Observation->GetNumberField(TEXT("age_seconds"));
        double ExactImpulse=0;
        // Component names are not globally unique. Exact pointer and a 5mm
        // contact-point match establish identity before using an observation.
        if(Age<0||Age>.10||!Rig->GetRecentHitAgainst(Wall,Point,.5,.10,ExactImpulse)||
            ExactImpulse<=.0001||FMath::Abs(FVector::DotProduct(Normal,P.Forward))<.5)continue;
        const FString Body=Observation->GetStringField(TEXT("body"));
        const double HitWorldSeconds=GetWorld()->GetTimeSeconds()-Age;
        P.ContactBodies.Add(Body);
        P.LastHitWorldSeconds=FMath::Max(P.LastHitWorldSeconds,HitWorldSeconds);
        if(ControlProbePhase==2&&ControlPendingCapture.IsEmpty())
        {
            if(P.HoldFirstHitWorld<0)P.HoldFirstHitWorld=HitWorldSeconds;
            P.HoldLastHitWorld=FMath::Max(P.HoldLastHitWorld,HitWorldSeconds);
        }
        const double* Previous=P.LastRecordedHit.Find(Body);
        if(Previous&&HitWorldSeconds<=*Previous+1.e-5)continue;
        P.LastRecordedHit.Add(Body,HitWorldSeconds);
        auto Event=MakeShared<FJsonObject>();
        Event->SetNumberField(TEXT("phase"),ControlProbePhase);
        Event->SetNumberField(TEXT("world_seconds"),HitWorldSeconds);
        Event->SetStringField(TEXT("body"),Body);
        Event->SetStringField(TEXT("exact_wall_component"),Wall->GetPathName());
        Event->SetArrayField(TEXT("point_cm"),GymVector(Point));
        Event->SetArrayField(TEXT("surface_normal"),GymVector(Normal));
        Event->SetNumberField(TEXT("impulse_n_s"),ExactImpulse);
        Event->SetNumberField(TEXT("observed_vehicle_speed_m_s"),SpeedCmS/100.);
        if(P.Events.Num()<4096)P.Events.Add(MakeShared<FJsonValueObject>(Event));
    }
    ControlReport->SetArrayField(TEXT("actual_wall_hit_observations"),P.Events);
    ControlReport->SetNumberField(TEXT("recent_exact_wall_contact_frames"),P.RecentContactFrames);
    ControlReport->SetNumberField(TEXT("peak_wall_impulse_n_s"),P.PeakImpulseNs);
    ControlReport->SetNumberField(TEXT("first_wall_contact_sim_seconds"),P.FirstHitSeconds);
    TArray<TSharedPtr<FJsonValue>> Bodies;
    for(const auto& Name:P.ContactBodies)Bodies.Add(MakeShared<FJsonValueString>(Name));
    ControlReport->SetArrayField(TEXT("wall_contacting_vehicle_bodies"),Bodies);
    if(P.Seconds>=P.NextSample)
    {
        P.NextSample=P.Seconds+.05;
        auto Sample=MakeShared<FJsonObject>();
        Sample->SetNumberField(TEXT("phase"),ControlProbePhase);
        Sample->SetNumberField(TEXT("elapsed_seconds"),P.Seconds);
        Sample->SetArrayField(TEXT("vehicle_ros_position_m"),GymVector(FVector(Position.X,-Position.Y,Position.Z)/100.));
        Sample->SetNumberField(TEXT("actual_speed_m_s"),SpeedCmS/100.);
        Sample->SetNumberField(TEXT("progress_cm"),Progress);
        Sample->SetNumberField(TEXT("lateral_drift_cm"),Drift);
        Sample->SetNumberField(TEXT("vehicle_envelope_boundary_margin_cm"),Margin);
        Sample->SetNumberField(TEXT("up_z"),UpZ);
        Sample->SetBoolField(TEXT("recent_exact_wall_hit"),Recent);
        Sample->SetObjectField(TEXT("native_contact_state"),Native);
        ContactSamples.Add(MakeShared<FJsonValueObject>(Sample));
        ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    }
    if(!ControlPendingCapture.IsEmpty())
    {
        Rig->SetContactDrive(0,0,true);
        if(++ControlCaptureWarmFrames<24)return;
        if(!CaptureControlView(ControlPendingCapture)){Fail(TEXT("boundary PNG capture failed"));return;}
        ++P.Captures;ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    ContactPhaseTime+=DeltaSeconds;
    const auto Before=ManualControlState();
    auto Check=[&](const TCHAR* Name,bool Passed,const FString& Detail)
    {
        if(!Passed)
        {
            Rig->SetContactDrive(0,0,true);
            CaptureControlView(TEXT("gym-boundary-failed"));
        }
        return RecordControlCheck(Name,Passed,Before,Detail);
    };
    auto Next=[&](const TCHAR* Capture)
    {
        ++ControlProbePhase;ContactPhaseTime=0;
        if(Capture){Rig->SetContactDrive(0,0,true);ControlPendingCapture=Capture;}
    };
    const double Direction=ControlProbePhase==4?-1.:1.;
    const double DesiredYaw=ContactStart.Rotator().Yaw+
        FMath::RadiansToDegrees(FMath::Atan2(-Direction*Drift,100.));
    const double YawError=FMath::FindDeltaAngleDegrees(Rig->GetActorRotation().Yaw,DesiredYaw);
    const double Steer=FMath::Clamp(-FMath::DegreesToRadians(YawError)*3,-.7,.7);
    const double DirectionScale=FMath::Clamp(1.-FMath::Abs(YawError)/40.,0.,1.);
    switch(ControlProbePhase)
    {
    case 0:
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<3)return;
        if(!Check(TEXT("physical_settle"),SpeedCmS<15&&UpZ>.9&&P.Events.IsEmpty(),
            FString::Printf(TEXT("Settled speed %.4fcm/s; upZ %.5f; wall contact observations %d"),SpeedCmS,UpZ,P.Events.Num())))return;
        ContactStart=Rig->GetActorTransform();P.Start=Position;
        Next(TEXT("gym-boundary-before"));return;
    case 1:
        Rig->SetContactDrive(2.*DirectionScale,Steer,false);
        if(P.Events.IsEmpty()&&ContactPhaseTime<8)return;
        if(!Check(TEXT("actual_closed_perimeter_contact"),
            !P.Events.IsEmpty()&&Progress>50&&P.PeakApproachSpeedMps>.5&&Recent,
            FString::Printf(TEXT("Real wall contact after %.3fcm; actual peak approach %.4fm/s; target 2m/s; peak impulse %.6fNs"),
                Progress,P.PeakApproachSpeedMps,P.PeakImpulseNs)))return;
        P.HoldStart=Position;P.HoldFirstHitWorld=-1;P.HoldLastHitWorld=-1;
        P.HoldRecentSeconds=0;P.HoldStoppedSeconds=0;
        Next(nullptr);return;
    case 2:
        Rig->SetContactDrive(2.*DirectionScale,Steer,false);
        P.HoldAdvanceCm=FVector::DotProduct(Position-P.HoldStart,P.Forward);
        if(Recent)P.HoldRecentSeconds+=DeltaSeconds;
        P.HoldStoppedSeconds=Recent&&SpeedCmS<15?P.HoldStoppedSeconds+DeltaSeconds:0.;
        if(ContactPhaseTime<1.5)return;
        ControlReport->SetNumberField(TEXT("held_drive_seconds"),ContactPhaseTime);
        ControlReport->SetNumberField(TEXT("held_drive_advance_cm"),P.HoldAdvanceCm);
        ControlReport->SetNumberField(TEXT("held_recent_contact_seconds"),P.HoldRecentSeconds);
        ControlReport->SetNumberField(TEXT("held_stopped_tail_seconds"),P.HoldStoppedSeconds);
        if(!Check(TEXT("closed_glass_boundary_blocks_continued_drive"),
            P.HoldRecentSeconds>=.5&&P.HoldStoppedSeconds>=.35&&
            P.HoldLastHitWorld-P.HoldFirstHitWorld>=.5&&
            P.HoldAdvanceCm<8&&P.HoldAdvanceCm>-35&&UpZ>.85,
            FString::Printf(TEXT("Continued2m/s target for %.3fs: advance %.3fcm, speed %.3fcm/s, fresh contact span %.3fs; boundary margin %.3fcm"),
                ContactPhaseTime,P.HoldAdvanceCm,SpeedCmS,P.HoldLastHitWorld-P.HoldFirstHitWorld,Margin)))return;
        Rig->SetContactDrive(0,0,true);Next(TEXT("gym-boundary-blocked"));return;
    case 3:
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<2)return;
        if(!Check(TEXT("physical_brake_at_boundary"),SpeedCmS<10&&UpZ>.85,
            FString::Printf(TEXT("Native wheel brake stops at %.4fcm/s; field margin %.3fcm"),SpeedCmS,Margin)))return;
        P.ReverseStart=Position;Next(nullptr);return;
    case 4:
        Rig->SetContactDrive(-.75*DirectionScale,Steer,false);
        P.ReverseClearanceCm=FVector::DotProduct(P.ReverseStart-Position,P.Forward);
        if(P.ReverseClearanceCm<60&&ContactPhaseTime<8)return;
        ControlReport->SetNumberField(TEXT("reverse_clearance_cm"),P.ReverseClearanceCm);
        if(!Check(TEXT("physical_reverse_releases_wall_contact"),
            P.ReverseClearanceCm>=60&&!Recent&&FMath::Abs(Drift)<40,
            FString::Printf(TEXT("Motor-driven reverse clearance %.3fcm; lateral drift %.3fcm; recent wall contact %s"),
                P.ReverseClearanceCm,Drift,Recent?TEXT("yes"):TEXT("no"))))return;
        Rig->SetContactDrive(0,0,true);Next(nullptr);return;
    case 5:
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<2)return;
        if(!Check(TEXT("final_stop_inside_original_field"),SpeedCmS<10&&UpZ>.85&&
            P.MinimumBoundaryMarginCm>=-1.&&ContactRecoveryCount==0&&Rig->GetMotionGeneration()==P.MotionGeneration,
            FString::Printf(TEXT("Final speed %.4fcm/s; every-frame minimum field margin %.3fcm; unchanged motion generation %llu; resets %d"),
                SpeedCmS,P.MinimumBoundaryMarginCm,static_cast<unsigned long long>(P.MotionGeneration),ContactRecoveryCount)))return;
        ControlReport->SetStringField(TEXT("physical_outcome"),TEXT("closed_boundary_blocked_then_reversed_without_pose_override"));
        Next(TEXT("gym-boundary-reverse"));return;
    default:
        Rig->SetContactDrive(0,0,true);
        ControlReport->SetNumberField(TEXT("captures"),P.Captures);
        ControlReport->SetBoolField(TEXT("boundary_envelope_checked_every_tick"),true);
        if(!WriteControlReport(TEXT("passed"))){Fail(TEXT("cannot save gym boundary report"));return;}
        UE_LOG(LogRMSim,Display,TEXT("CAMPUS_GYM_BOUNDARY_PASSED checks=%d wall_observations=%d actual_peak_mps=%.6f minimum_boundary_margin_cm=%.6f reverse_cm=%.6f"),
            ControlChecks.Num(),P.Events.Num(),P.PeakApproachSpeedMps,P.MinimumBoundaryMarginCm,P.ReverseClearanceCm);
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
}
