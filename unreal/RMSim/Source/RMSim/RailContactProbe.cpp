#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "PhysicsEngine/BodySetup.h"

// Opt-in validation only. The ordinary spawn, wheel motors, constraints and
// Chaos solver own every vehicle transform throughout this probe.
struct FRailContactProbeState
{
    TWeakObjectPtr<UInstancedStaticMeshComponent> Component;
    FTransform InstanceTransform;
    FString Mesh;
    int32 Instance=0,PositiveContacts=0,BlockingContacts=0,HoldContactStart=0,HoldBlockingStart=0;
    uint64 Cursor=0,MotionGeneration=0;
    FVector Target=FVector::ZeroVector,Forward=FVector::ForwardVector,Side=FVector::RightVector;
    FVector Start=FVector::ZeroVector,HoldStart=FVector::ZeroVector,ReverseStart=FVector::ZeroVector;
    double Speed=.35,Seconds=0,NextSample=0,PeakImpulse=0,PeakNormalDot=-1;
    double FirstContactTime=-1,LastContactTime=-1,FirstHoldContactTime=-1,LastHoldContactTime=-1;
    double MaximumRise=0,MaximumDrift=0,HoldAdvance=0;
    int32 CaptureCount=0;
    TArray<TSharedPtr<FJsonValue>> Events;
    TSet<FString> BodyNames;
};

namespace
{
TArray<TSharedPtr<FJsonValue>> RailVector(const FVector& V)
{
    return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),
        MakeShared<FJsonValueNumber>(V.Z)};
}

bool TraceRailTop(const FRailContactProbeState& P,FHitResult& Hit)
{
    auto* C=P.Component.Get();
    if(!C||!C->GetStaticMesh()||C->GetStaticMesh()->GetName()!=P.Mesh||
        C->GetInstanceCount()!=1||P.Instance!=0||
        C->GetCollisionEnabled()!=ECollisionEnabled::QueryAndPhysics||
        C->GetCollisionResponseToChannel(ECC_PhysicsBody)!=ECR_Block)return false;
    FTransform Current;
    if(!C->GetInstanceTransform(P.Instance,Current,true)||!Current.Equals(P.InstanceTransform,.001))return false;
    const auto* Body=C->GetStaticMesh()->GetBodySetup();
    if(!Body||Body->CollisionTraceFlag!=CTF_UseComplexAsSimple)return false;
    FCollisionQueryParams Params(SCENE_QUERY_STAT(RailSourceSurface),true);
    if(!C->GetWorld())return false;
    const bool Found=C->GetWorld()->LineTraceSingleByChannel(Hit,
        P.Target+FVector(0,0,150),P.Target-FVector(0,0,150),ECC_Visibility,Params);
    // Use the world query's blocking classification and exact instance identity.
    return Found&&Hit.bBlockingHit&&Hit.GetComponent()==C&&Hit.Item==P.Instance&&
        FVector::Dist2D(Hit.ImpactPoint,P.Target)<1.&&
        FMath::Abs(Hit.ImpactPoint.Z-P.Target.Z)<2.&&Hit.ImpactNormal.Z>.9;
}
}

void AImportLabGameMode::TickRailContactProbe(double DeltaSeconds)
{
    if(!Rig||!Rig->IsContactPhysics()||!ControlReport.IsValid()||!OverviewCapture)
    {Abort(TEXT("rail probe requires ContactProbe, native vehicle and EvidenceDir"));return;}
    if(!RailContactProbe.IsValid())
    {
        RailContactProbe=MakeShared<FRailContactProbeState>();
        auto& P=*RailContactProbe;
        double X=0,Y=0,Z=0;
        const bool Coordinates=FParse::Value(FCommandLine::Get(),TEXT("RailTargetX="),X)&&
            FParse::Value(FCommandLine::Get(),TEXT("RailTargetY="),Y)&&
            FParse::Value(FCommandLine::Get(),TEXT("RailTargetZ="),Z);
        FParse::Value(FCommandLine::Get(),TEXT("RailExpectedInstance="),P.Instance);
        FParse::Value(FCommandLine::Get(),TEXT("RailSpeedMps="),P.Speed);
        FParse::Value(FCommandLine::Get(),TEXT("RailExpectedMesh="),P.Mesh);
        if(!Coordinates||!FMath::IsFinite(X)||!FMath::IsFinite(Y)||!FMath::IsFinite(Z)||
            !FMath::IsFinite(P.Speed)||P.Speed<.1||P.Speed>.6||P.Instance!=0||
            !P.Mesh.StartsWith(TEXT("gc_static_rail_")))
        {Abort(TEXT("invalid audited source rail fixture"));return;}
        P.Target=FVector(X*100,-Y*100,Z*100);
        P.Start=Rig->GetActorLocation();P.Forward=Rig->GetActorForwardVector().GetSafeNormal2D();
        P.Side=FVector::CrossProduct(FVector::UpVector,P.Forward);
        P.MotionGeneration=Rig->GetMotionGeneration();
        const FVector ToTarget=P.Target-P.Start;
        if(FVector::DotProduct(ToTarget,P.Forward)<80||FVector::DotProduct(ToTarget,P.Forward)>1000||
            FMath::Abs(FVector::DotProduct(ToTarget,P.Side))>20)
        {Abort(TEXT("rail fixture spawn does not approach the audited target"));return;}
        int32 Matches=0;
        for(TActorIterator<AActor> It(GetWorld());It;++It)
        {
            TInlineComponentArray<UInstancedStaticMeshComponent*> Components;
            It->GetComponents(Components);
            for(auto* C:Components)
                if(C->GetStaticMesh()&&C->GetStaticMesh()->GetName()==P.Mesh)
                {P.Component=C;++Matches;}
        }
        if(Matches!=1||!P.Component->GetInstanceTransform(P.Instance,P.InstanceTransform,true))
        {Abort(TEXT("expected exactly one active source steel rail component"));return;}
        ControlReport->SetStringField(TEXT("probe"),TEXT("native_source_rail_contact_v1"));
        ControlReport->SetStringField(TEXT("expected_mesh"),P.Mesh);
        ControlReport->SetNumberField(TEXT("expected_instance"),P.Instance);
        ControlReport->SetArrayField(TEXT("source_target_ros_m"),RailVector(FVector(X,Y,Z)));
        ControlReport->SetStringField(TEXT("rail_component"),P.Component->GetPathName());
        ControlReport->SetStringField(TEXT("rail_asset"),P.Component->GetStaticMesh()->GetPathName());
        ControlReport->SetNumberField(TEXT("command_speed_m_s"),P.Speed);
        ControlReport->SetBoolField(TEXT("pose_overrides_used"),false);
        ControlReport->SetNumberField(TEXT("initial_motion_generation"),static_cast<double>(P.MotionGeneration));
        UE_LOG(LogRMSim,Display,TEXT("WT_RAIL_CONTACT_BEGIN mesh=%s instance=%d target_cm=%s speed_mps=%.3f"),
            *P.Mesh,P.Instance,*P.Target.ToString(),P.Speed);
    }
    auto& P=*RailContactProbe;
    P.Seconds+=DeltaSeconds;
    const FVector Position=Rig->GetActorLocation(),Displacement=Position-P.Start;
    const double Progress=FVector::DotProduct(Displacement,P.Forward);
    const double Drift=FVector::DotProduct(Displacement,P.Side);
    const double SpeedCmS=Rig->GetCommandOriginVelocity().Size();
    P.MaximumRise=FMath::Max(P.MaximumRise,Displacement.Z);
    P.MaximumDrift=FMath::Max(P.MaximumDrift,FMath::Abs(Drift));
    if(Rig->GetActorTransform().ContainsNaN()||Rig->GetMotionGeneration()!=P.MotionGeneration||
        ContactRecoveryCount!=0||Displacement.Z < -100||Displacement.Z > 150||
        Rig->GetActorUpVector().Z<.65||FMath::Abs(Drift)>60||P.Seconds>70)
    {Abort(TEXT("rail probe left its physical route envelope or reset the vehicle"));return;}
    auto* Rail=P.Component.Get();
    if(!Rail||Rail->GetInstanceCount()!=1)
    {Abort(TEXT("source steel rail was removed during contact"));return;}

    // A close fixed oblique view covers the visible steel rail and actual car.
    const FVector Eye=P.Target-P.Forward*310+P.Side*260+FVector(0,0,210);
    OverviewCapture->SetWorldLocationAndRotation(Eye,(P.Target+FVector(0,0,15)-Eye).Rotation());
    OverviewCapture->FOVAngle=55;

    const auto NativeState=Rig->GetContactState();
    for(const auto& Value:NativeState->GetArrayField(TEXT("last_contact_per_body")))
    {
        const auto Observation=Value->AsObject();
        if(Observation->GetStringField(TEXT("other"))==Rail->GetName()&&
            Observation->GetNumberField(TEXT("age_seconds"))<=.15)
            P.BodyNames.Add(Observation->GetStringField(TEXT("body")));
    }
    TArray<FVehicleObstacleContact> Contacts;
    Rig->GetObstacleContactsSince(P.Cursor,Contacts);
    for(const auto& Contact:Contacts)
    {
        if(Contact.OtherComponent.Get()!=Rail||Contact.InstanceIndex!=P.Instance||
            Contact.MotionGeneration!=P.MotionGeneration||Contact.ImpulseNs<=.0001||
            FVector::Dist2D(Contact.PointCm,P.Target)>125||
            FMath::Abs(Contact.PointCm.Z-P.Target.Z)>45)continue;
        ++P.PositiveContacts;
        const double NormalDot=FVector::DotProduct(Contact.IntoObstacleNormal,P.Forward);
        if(NormalDot>.25)++P.BlockingContacts;
        P.PeakNormalDot=FMath::Max(P.PeakNormalDot,NormalDot);
        P.PeakImpulse=FMath::Max(P.PeakImpulse,Contact.ImpulseNs);
        if(P.FirstContactTime<0)P.FirstContactTime=Contact.TimeSeconds;
        P.LastContactTime=Contact.TimeSeconds;
        if(ControlProbePhase==2&&ControlPendingCapture.IsEmpty())
        {
            if(P.FirstHoldContactTime<0)P.FirstHoldContactTime=Contact.TimeSeconds;
            P.LastHoldContactTime=Contact.TimeSeconds;
        }
        auto Event=MakeShared<FJsonObject>();
        Event->SetNumberField(TEXT("phase"),ControlProbePhase);
        Event->SetNumberField(TEXT("sequence"),static_cast<double>(Contact.Sequence));
        Event->SetNumberField(TEXT("world_seconds"),Contact.TimeSeconds);
        Event->SetNumberField(TEXT("impulse_n_s"),Contact.ImpulseNs);
        Event->SetNumberField(TEXT("blocking_normal_dot_approach"),NormalDot);
        Event->SetNumberField(TEXT("instance"),Contact.InstanceIndex);
        Event->SetArrayField(TEXT("point_cm"),RailVector(Contact.PointCm));
        Event->SetArrayField(TEXT("into_rail_normal"),RailVector(Contact.IntoObstacleNormal));
        Event->SetArrayField(TEXT("preimpact_body_velocity_cm_s"),RailVector(Contact.PreImpactVelocityCmS));
        Event->SetArrayField(TEXT("vehicle_position_cm"),RailVector(Position));
        if(P.Events.Num()<4096)P.Events.Add(MakeShared<FJsonValueObject>(Event));
    }
    ControlReport->SetArrayField(TEXT("actual_rail_contacts"),P.Events);
    ControlReport->SetNumberField(TEXT("positive_target_contacts"),P.PositiveContacts);
    ControlReport->SetNumberField(TEXT("forward_blocking_contacts"),P.BlockingContacts);
    ControlReport->SetNumberField(TEXT("peak_impulse_n_s"),P.PeakImpulse);
    ControlReport->SetNumberField(TEXT("peak_blocking_normal_dot_approach"),P.PeakNormalDot);
    ControlReport->SetNumberField(TEXT("maximum_body_rise_cm"),P.MaximumRise);
    ControlReport->SetNumberField(TEXT("maximum_route_drift_cm"),P.MaximumDrift);
    ControlReport->SetNumberField(TEXT("current_progress_cm"),Progress);
    ControlReport->SetNumberField(TEXT("current_speed_cm_s"),SpeedCmS);
    ControlReport->SetNumberField(TEXT("final_motion_generation"),static_cast<double>(Rig->GetMotionGeneration()));
    TArray<TSharedPtr<FJsonValue>> Names;
    for(const FString& Name:P.BodyNames)Names.Add(MakeShared<FJsonValueString>(Name));
    ControlReport->SetArrayField(TEXT("contacting_vehicle_bodies"),Names);
    if(P.Seconds>=P.NextSample)
    {
        P.NextSample=P.Seconds+.25;
        auto Sample=MakeShared<FJsonObject>();
        Sample->SetNumberField(TEXT("phase"),ControlProbePhase);
        Sample->SetNumberField(TEXT("elapsed_seconds"),P.Seconds);
        Sample->SetNumberField(TEXT("progress_cm"),Progress);
        Sample->SetNumberField(TEXT("drift_cm"),Drift);
        Sample->SetObjectField(TEXT("native_contact_state"),NativeState);
        ContactSamples.Add(MakeShared<FJsonValueObject>(Sample));
        ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    }
    if(!ControlPendingCapture.IsEmpty())
    {
        if(++ControlCaptureWarmFrames<24)return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("rail contact capture failed"));return;}
        ++P.CaptureCount;
        UE_LOG(LogRMSim,Display,TEXT("WT_RAIL_CONTACT_CAPTURE phase=%s"),*ControlPendingCapture);
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    const bool PhaseBeginning=ContactPhaseTime==0;
    ContactPhaseTime+=DeltaSeconds;
    const auto Before=ManualControlState();
    auto Check=[&](const TCHAR* Name,bool Passed,const FString& Detail)
    {
        if(!Passed)Rig->SetContactDrive(0,0,true);
        return RecordControlCheck(Name,Passed,Before,Detail);
    };
    auto Next=[&](const TCHAR* Capture)
    {
        Rig->SetContactDrive(0,0,true);
        ++ControlProbePhase;ContactPhaseTime=0;
        if(Capture)ControlPendingCapture=Capture;
    };
    const double Direction=ControlProbePhase==4?-1.:1.;
    const double DesiredYaw=ContactStart.Rotator().Yaw+
        FMath::RadiansToDegrees(FMath::Atan2(-Direction*Drift,100.));
    const double YawError=FMath::FindDeltaAngleDegrees(Rig->GetActorRotation().Yaw,DesiredYaw);
    const double Steer=FMath::Clamp(-FMath::DegreesToRadians(YawError)*3,-.7,.7);
    const double Drive=P.Speed*FMath::Clamp(1.-FMath::Abs(YawError)/40.,0.,1.);

    switch(ControlProbePhase)
    {
    case 0:
    {
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<3)return;
        if(!Check(TEXT("physical_settle"),SpeedCmS<15&&Rig->GetActorUpVector().Z>.9,
            FString::Printf(TEXT("Actual settled body speed %.4fcm/s, upZ %.5f"),SpeedCmS,Rig->GetActorUpVector().Z)))return;
        FHitResult Hit;
        const bool Aligned=TraceRailTop(P,Hit);
        ControlReport->SetArrayField(TEXT("queried_rail_top_cm"),RailVector(Hit.ImpactPoint));
        ControlReport->SetNumberField(TEXT("source_top_height_error_cm"),FMath::Abs(Hit.ImpactPoint.Z-P.Target.Z));
        ControlReport->SetNumberField(TEXT("source_top_trace_item"),Hit.Item);
        ControlReport->SetBoolField(TEXT("source_top_blocking_hit"),Hit.bBlockingHit);
        ControlReport->SetArrayField(TEXT("source_top_impact_normal"),RailVector(Hit.ImpactNormal));
        ControlReport->SetStringField(TEXT("source_top_hit_component"),GetPathNameSafe(Hit.GetComponent()));
        if(!Check(TEXT("source_rail_surface_alignment"),Aligned,
            FString::Printf(TEXT("Exact source rail top error %.5fcm, instance %d, blocking %d, normalZ %.5f"),
                FMath::Abs(Hit.ImpactPoint.Z-P.Target.Z),Hit.Item,Hit.bBlockingHit,Hit.ImpactNormal.Z)))return;
        ContactStart=Rig->GetActorTransform();P.Start=Rig->GetActorLocation();
        Next(TEXT("rail-before"));return;
    }
    case 1:
        Rig->SetContactDrive(Drive,Steer,false);
        if(P.PositiveContacts==0&&ContactPhaseTime<35)return;
        if(!Check(TEXT("actual_rail_contact"),P.PositiveContacts>0&&Progress>50&&P.BodyNames.Num()>0,
            FString::Printf(TEXT("Native solver reports %d positive contacts on %s/%d after %.3fcm travel; peak %.6fNs"),
                P.PositiveContacts,*P.Mesh,P.Instance,Progress,P.PeakImpulse)))return;
        Next(TEXT("rail-contact"));return;
    case 2:
        if(PhaseBeginning)
        {
            P.HoldStart=Position;P.HoldContactStart=P.PositiveContacts;P.HoldBlockingStart=P.BlockingContacts;
            P.FirstHoldContactTime=-1;P.LastHoldContactTime=-1;
        }
        Rig->SetContactDrive(Drive,Steer,false);
        P.HoldAdvance=FVector::DotProduct(Position-P.HoldStart,P.Forward);
        if(ContactPhaseTime<2.5)return;
        ControlReport->SetNumberField(TEXT("held_drive_advance_cm"),P.HoldAdvance);
        ControlReport->SetNumberField(TEXT("held_drive_seconds"),ContactPhaseTime);
        ControlReport->SetNumberField(TEXT("held_drive_positive_contacts"),P.PositiveContacts-P.HoldContactStart);
        ControlReport->SetNumberField(TEXT("held_drive_blocking_contacts"),P.BlockingContacts-P.HoldBlockingStart);
        if(!Check(TEXT("rail_blocks_low_speed_drive"),
            P.PositiveContacts-P.HoldContactStart>=3&&P.BlockingContacts-P.HoldBlockingStart>=2&&
            P.LastHoldContactTime-P.FirstHoldContactTime>=.5&&P.HoldAdvance<10&&P.HoldAdvance>-30&&
            SpeedCmS<12&&Rig->GetActorUpVector().Z>.85,
            FString::Printf(TEXT("Continued %.3fm/s wheel command: %.3fcm advance, %.3fcm/s speed, %d positive / %d forward-blocking contacts over %.3fs"),
                P.Speed,P.HoldAdvance,SpeedCmS,P.PositiveContacts-P.HoldContactStart,
                P.BlockingContacts-P.HoldBlockingStart,P.LastHoldContactTime-P.FirstHoldContactTime)))return;
        Next(nullptr);return;
    case 3:
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<2)return;
        if(!Check(TEXT("physical_brake_after_rail"),SpeedCmS<15,
            FString::Printf(TEXT("Physical wheel brake settles body to %.4fcm/s"),SpeedCmS)))return;
        P.ReverseStart=Position;Next(TEXT("rail-blocked"));return;
    case 4:
    {
        Rig->SetContactDrive(-Drive,Steer,false);
        const double Backoff=FVector::DotProduct(P.ReverseStart-Position,P.Forward);
        if(Backoff<70&&ContactPhaseTime<20)return;
        ControlReport->SetNumberField(TEXT("reverse_clearance_cm"),Backoff);
        if(!Check(TEXT("physical_reverse_from_rail"),Backoff>=70&&FMath::Abs(Drift)<40,
            FString::Printf(TEXT("Motor/contact-driven backoff %.3fcm with route drift %.3fcm"),Backoff,Drift)))return;
        Next(nullptr);return;
    }
    case 5:
    {
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<2)return;
        if(!Check(TEXT("physical_stop_after_reverse"),SpeedCmS<15,
            FString::Printf(TEXT("Final physical body speed %.4fcm/s"),SpeedCmS)))return;
        FHitResult Hit;
        if(!Check(TEXT("source_rail_survives_drive"),TraceRailTop(P,Hit),
            TEXT("The same source rail instance, transform and blocking top remain after contact and reverse")))return;
        ControlReport->SetStringField(TEXT("physical_outcome"),TEXT("blocked_then_reversed"));
        Next(TEXT("rail-reverse"));return;
    }
    default:
        ControlReport->SetNumberField(TEXT("captures"),P.CaptureCount);
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot save rail contact result"));return;}
        UE_LOG(LogRMSim,Display,TEXT("WT_RAIL_CONTACT_PASSED expected_mesh=%s expected_instance=%d positive_contacts=%d blocking_contacts=%d captures=%d checks=%d hold_advance_cm=%.5f peak_impulse_ns=%.6f"),
            *P.Mesh,P.Instance,P.PositiveContacts,P.BlockingContacts,P.CaptureCount,ControlChecks.Num(),P.HoldAdvance,P.PeakImpulse);
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
}
