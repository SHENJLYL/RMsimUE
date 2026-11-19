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
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "PhysicsEngine/BodySetup.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

// Opt-in source fixture. Ordinary map spawn and the wheel motors/Chaos solver
// own every vehicle transform. Only the overview camera is repositioned.
struct FZeppelinContactProbeState
{
    TWeakObjectPtr<UInstancedStaticMeshComponent> Component;
    FTransform InstanceTransform;
    FString Mesh,Asset,Fixture;
    int32 Instance=0,PositiveContacts=0,BlockingContacts=0,HoldContactStart=0,HoldBlockingStart=0,Captures=0;
    uint64 MotionGeneration=0;
    FVector Target=FVector::ZeroVector,RayStart=FVector::ZeroVector,RayEnd=FVector::ZeroVector;
    FVector Forward=FVector::ForwardVector,Side=FVector::RightVector,Start=FVector::ZeroVector;
    FVector HoldStart=FVector::ZeroVector,ReverseStart=FVector::ZeroVector;
    double Speed=.35,Seconds=0,NextSample=0,PeakImpulse=0,PeakApproachSpeed=0;
    double FirstHoldContactTime=-1,LastHoldContactTime=-1,HoldAdvance=0,HoldStoppedSeconds=0,HoldRecentSeconds=0;
    double MaximumRise=0,MaximumDrift=0,ReverseClearance=0;
    TMap<FString,double> LastRecordedHit;
    TSet<FString> BodyNames;
    TArray<TSharedPtr<FJsonValue>> Events;
};

namespace
{
const FName ZeppelinOwnerTag(TEXT("RMSim.WTMissionObjects.Zeppelin"));
const FName ZeppelinFixedTag(TEXT("RMSim.WTFixedLandmark"));

TArray<TSharedPtr<FJsonValue>> ZeppelinVector(const FVector& V)
{
    return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)};
}

bool ReadZeppelinVector(const TSharedPtr<FJsonObject>& Object,const TCHAR* Key,FVector& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* Values=nullptr;
    if(!Object.IsValid()||!Object->TryGetArrayField(Key,Values)||Values->Num()!=3)return false;
    for(int32 I=0;I<3;++I)
        if(!(*Values)[I]->TryGetNumber(Out[I])||!FMath::IsFinite(Out[I]))return false;
    return true;
}

bool ZeppelinInstanceUnchanged(const FZeppelinContactProbeState& P)
{
    auto* C=P.Component.Get();
    if(!C||!C->GetOwner()||!C->GetOwner()->ActorHasTag(ZeppelinOwnerTag)||
        !C->GetOwner()->ActorHasTag(ZeppelinFixedTag)||!C->ComponentHasTag(ZeppelinOwnerTag)||
        !C->ComponentHasTag(ZeppelinFixedTag)||!C->GetStaticMesh()||
        C->GetStaticMesh()->GetPathName()!=P.Asset||C->GetInstanceCount()!=1||P.Instance!=0||
        C->GetMobility()!=EComponentMobility::Static||C->IsSimulatingPhysics()||
        C->GetCollisionEnabled()!=ECollisionEnabled::QueryAndPhysics||
        C->GetCollisionResponseToChannel(ECC_PhysicsBody)!=ECR_Block)return false;
    FTransform Current;
    if(!C->GetInstanceTransform(P.Instance,Current,true)||!Current.Equals(P.InstanceTransform,.001))return false;
    const auto* Body=C->GetStaticMesh()->GetBodySetup();
    return Body&&Body->CollisionTraceFlag==CTF_UseComplexAsSimple&&Body->bDoubleSidedGeometry;
}

TSharedRef<FJsonObject> ZeppelinInvariantReport(const FZeppelinContactProbeState& P)
{
    auto Report=MakeShared<FJsonObject>();TArray<TSharedPtr<FJsonValue>> Failed;
    auto Check=[&](const TCHAR* Name,bool Passed)
    {
        Report->SetBoolField(Name,Passed);
        if(!Passed)Failed.Add(MakeShared<FJsonValueString>(Name));
    };
    auto* C=P.Component.Get();auto* Owner=C?C->GetOwner():nullptr;
    UStaticMesh* Mesh=C?C->GetStaticMesh().Get():nullptr;const auto* Body=Mesh?Mesh->GetBodySetup():nullptr;
    Check(TEXT("component_valid"),C!=nullptr);Check(TEXT("owner_valid"),Owner!=nullptr);
    Check(TEXT("owner_ownership_tag"),Owner&&Owner->ActorHasTag(ZeppelinOwnerTag));
    Check(TEXT("owner_fixed_tag"),Owner&&Owner->ActorHasTag(ZeppelinFixedTag));
    Check(TEXT("component_ownership_tag"),C&&C->ComponentHasTag(ZeppelinOwnerTag));
    Check(TEXT("component_fixed_tag"),C&&C->ComponentHasTag(ZeppelinFixedTag));
    Check(TEXT("mesh_valid"),Mesh!=nullptr);Check(TEXT("asset_identity"),Mesh&&Mesh->GetPathName()==P.Asset);
    Check(TEXT("one_instance"),C&&C->GetInstanceCount()==1);Check(TEXT("expected_instance_zero"),P.Instance==0);
    Check(TEXT("static_mobility"),C&&C->GetMobility()==EComponentMobility::Static);
    Check(TEXT("not_simulating"),C&&!C->IsSimulatingPhysics());
    Check(TEXT("query_and_physics"),C&&C->GetCollisionEnabled()==ECollisionEnabled::QueryAndPhysics);
    Check(TEXT("blocks_physics_body"),C&&C->GetCollisionResponseToChannel(ECC_PhysicsBody)==ECR_Block);
    FTransform Current;
    const bool HasTransform=C&&C->GetInstanceTransform(P.Instance,Current,true);
    Check(TEXT("instance_transform_available"),HasTransform);
    Check(TEXT("instance_transform_unchanged"),HasTransform&&Current.Equals(P.InstanceTransform,.001));
    Check(TEXT("body_setup_valid"),Body!=nullptr);
    Check(TEXT("complex_as_simple"),Body&&Body->CollisionTraceFlag==CTF_UseComplexAsSimple);
    Check(TEXT("double_sided_geometry"),Body&&Body->bDoubleSidedGeometry);
    Report->SetStringField(TEXT("expected_asset"),P.Asset);Report->SetStringField(TEXT("actual_asset"),GetPathNameSafe(Mesh));
    Report->SetStringField(TEXT("component"),GetPathNameSafe(C));Report->SetStringField(TEXT("owner"),GetPathNameSafe(Owner));
    Report->SetNumberField(TEXT("actual_instance_count"),C?C->GetInstanceCount():-1);
    Report->SetStringField(TEXT("expected_transform"),P.InstanceTransform.ToString());
    Report->SetStringField(TEXT("actual_transform"),HasTransform?Current.ToString():TEXT("unavailable"));
    Report->SetArrayField(TEXT("failed_invariants"),Failed);
    Report->SetBoolField(TEXT("all_passed"),ZeppelinInstanceUnchanged(P));
    return Report;
}

TSharedRef<FJsonObject> ZeppelinTraceHitReport(const FZeppelinContactProbeState& P,bool Found,const FHitResult& Hit)
{
    auto Report=MakeShared<FJsonObject>();
    Report->SetBoolField(TEXT("query_hit"),Found);Report->SetBoolField(TEXT("blocking_hit"),Hit.bBlockingHit);
    Report->SetBoolField(TEXT("start_penetrating"),Hit.bStartPenetrating);
    Report->SetStringField(TEXT("hit_actor"),GetPathNameSafe(Hit.GetActor()));
    Report->SetStringField(TEXT("hit_component"),GetPathNameSafe(Hit.GetComponent()));
    Report->SetNumberField(TEXT("hit_item"),Hit.Item);Report->SetNumberField(TEXT("distance_cm"),Hit.Distance);
    Report->SetArrayField(TEXT("impact_point_cm"),ZeppelinVector(Hit.ImpactPoint));
    Report->SetArrayField(TEXT("impact_normal"),ZeppelinVector(Hit.ImpactNormal));
    Report->SetNumberField(TEXT("source_point_error_cm"),Found?FVector::Dist(Hit.ImpactPoint,P.Target):-1.);
    Report->SetBoolField(TEXT("exact_source_component"),Hit.GetComponent()==P.Component.Get());
    Report->SetBoolField(TEXT("exact_source_instance"),Hit.Item==P.Instance);
    return Report;
}

bool TraceZeppelinSurface(const FZeppelinContactProbeState& P,const AActor* Vehicle,FHitResult& Hit,TSharedPtr<FJsonObject>& Audit)
{
    Audit=MakeShared<FJsonObject>();const auto Invariants=ZeppelinInvariantReport(P);
    Audit->SetObjectField(TEXT("invariants"),Invariants);
    Audit->SetArrayField(TEXT("ray_start_cm"),ZeppelinVector(P.RayStart));
    Audit->SetArrayField(TEXT("ray_end_cm"),ZeppelinVector(P.RayEnd));
    Audit->SetArrayField(TEXT("source_target_cm"),ZeppelinVector(P.Target));
    Audit->SetStringField(TEXT("ignored_vehicle_actor"),GetPathNameSafe(Vehicle));
    Audit->SetBoolField(TEXT("passed"),false);
    if(!Invariants->GetBoolField(TEXT("all_passed")))return false;
    auto* C=P.Component.Get();auto* World=C->GetWorld();
    if(!World||!Vehicle)return false;
    // The reverse route can put the real car at the source ray's start.
    // Record that occlusion, then exclude the tested car from this diagnostic
    // source-preservation query. Physical contacts keep their original filters.
    FCollisionQueryParams Unfiltered(SCENE_QUERY_STAT(ZeppelinSourceSurfaceUnfiltered),true);
    FHitResult UnfilteredHit;
    const bool UnfilteredFound=World->LineTraceSingleByChannel(UnfilteredHit,P.RayStart,P.RayEnd,ECC_Visibility,Unfiltered);
    Audit->SetObjectField(TEXT("unfiltered_world_query"),ZeppelinTraceHitReport(P,UnfilteredFound,UnfilteredHit));
    FCollisionQueryParams Params(SCENE_QUERY_STAT(ZeppelinSourceSurface),true);
    Params.AddIgnoredActor(Vehicle);
    const bool Found=World->LineTraceSingleByChannel(Hit,P.RayStart,P.RayEnd,ECC_Visibility,Params);
    Audit->SetObjectField(TEXT("vehicle_filtered_world_query"),ZeppelinTraceHitReport(P,Found,Hit));
    const bool Passed=Found&&Hit.bBlockingHit&&Hit.GetComponent()==C&&Hit.Item==P.Instance&&
        FVector::Dist(Hit.ImpactPoint,P.Target)<2.;
    Audit->SetBoolField(TEXT("passed"),Passed);
    return Passed;
}

struct FZeppelinCameraView
{
    const TCHAR* Phase;
    FVector Eye,Target;
    float Fov;
};

const FZeppelinCameraView ZeppelinViews[]={
    {TEXT("zeppelin-overview"),FVector(66000,87000,10500),FVector(51000,102500,1200),65},
    {TEXT("zeppelin-cabins"),FVector(49800,108300,1400),FVector(50800,105200,350),60},
    {TEXT("zeppelin-frame"),FVector(47587.12721,103369.90251,730.69266),
        FVector(47541.65336,103771.93749,535.69266),55}
};

void SetZeppelinCamera(USceneCaptureComponent2D* Camera,const FString& Phase)
{
    const FZeppelinCameraView* View=&ZeppelinViews[2];
    for(const auto& Candidate:ZeppelinViews)if(Phase==Candidate.Phase){View=&Candidate;break;}
    Camera->SetWorldLocationAndRotation(View->Eye,(View->Target-View->Eye).Rotation());
    Camera->FOVAngle=View->Fov;
}
}

void AImportLabGameMode::TickZeppelinContactProbe(double DeltaSeconds)
{
    if(!Rig||!Rig->IsContactPhysics()||!ControlReport.IsValid()||!OverviewCapture||
        StageMap.Id!=TEXT("wt_vlaanderen")||FParse::Param(FCommandLine::Get(),TEXT("NullRHI")))
    {Abort(TEXT("zeppelin probe requires the real Flanders World, ContactProbe, native vehicle, rendering and EvidenceDir"));return;}
    if(!ZeppelinContactProbe.IsValid())
    {
        ZeppelinContactProbe=MakeShared<FZeppelinContactProbeState>();
        // CaptureControlView -> CapturePngs explicitly calls CaptureScene for each
        // camera. Keep normal physics ticks without rendering duplicate scene views.
        OverviewCapture->bCaptureEveryFrame=false;OverviewCapture->bCaptureOnMovement=false;
        for(const auto& Camera:Rig->GetCameras())if(Camera.Value)
        {Camera.Value->bCaptureEveryFrame=false;Camera.Value->bCaptureOnMovement=false;}
        auto& P=*ZeppelinContactProbe;
        FParse::Value(FCommandLine::Get(),TEXT("ZeppelinSpeedMps="),P.Speed);
        FString Text;TSharedPtr<FJsonObject> Fixture;FVector TargetRos;FString Schema;double Index=-1;
        if(!FParse::Value(FCommandLine::Get(),TEXT("ZeppelinFixture="),P.Fixture)||
            !FFileHelper::LoadFileToString(Text,*P.Fixture)||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Fixture)||!Fixture.IsValid()||
            !Fixture->TryGetStringField(TEXT("schema"),Schema)||Schema!=TEXT("wt_zeppelin_vehicle_contact_fixture_v1")||
            !Fixture->TryGetStringField(TEXT("expected_mesh"),P.Mesh)||
            !Fixture->TryGetStringField(TEXT("expected_asset"),P.Asset)||
            !Fixture->TryGetNumberField(TEXT("expected_instance"),Index)||Index!=0||
            !ReadZeppelinVector(Fixture,TEXT("target_ros_m"),TargetRos)||
            !ReadZeppelinVector(Fixture,TEXT("ray_start_ue_cm"),P.RayStart)||
            !ReadZeppelinVector(Fixture,TEXT("ray_end_ue_cm"),P.RayEnd)||
            !P.Mesh.StartsWith(TEXT("col_zep_frame_truss_a_damaged_"))||
            P.Asset!=TEXT("/Game/RMSim/Maps/wt_vlaanderen/MissionObjects/Collision/")+P.Mesh+TEXT(".")+P.Mesh||
            !FMath::IsFinite(P.Speed)||P.Speed<.1||P.Speed>.6)
        {Abort(TEXT("invalid audited original zeppelin fixture"));return;}
        P.Target=FVector(TargetRos.X*100,-TargetRos.Y*100,TargetRos.Z*100);
        if(P.RayStart.GetAbsMax()>2000000||P.RayEnd.GetAbsMax()>2000000||
            FVector::Dist(P.RayStart,P.RayEnd)<20||FVector::Dist(P.RayStart,P.RayEnd)>500)
        {Abort(TEXT("invalid source zeppelin ray envelope"));return;}
        P.Start=Rig->GetActorLocation();P.Forward=Rig->GetActorForwardVector().GetSafeNormal2D();
        P.Side=FVector::CrossProduct(FVector::UpVector,P.Forward);
        P.MotionGeneration=Rig->GetMotionGeneration();
        const FVector ToTarget=P.Target-P.Start;
        if(FVector::DotProduct(ToTarget,P.Forward)<80||FVector::DotProduct(ToTarget,P.Forward)>600||
            FMath::Abs(FVector::DotProduct(ToTarget,P.Side))>20)
        {Abort(TEXT("copied-map spawn must approach the audited original zeppelin target"));return;}
        int32 Matches=0;
        for(TActorIterator<AActor> It(GetWorld());It;++It)
        {
            if(!It->ActorHasTag(ZeppelinOwnerTag)||!It->ActorHasTag(ZeppelinFixedTag))continue;
            TInlineComponentArray<UInstancedStaticMeshComponent*> Components;It->GetComponents(Components);
            for(auto* C:Components)if(C->GetStaticMesh()&&C->GetStaticMesh()->GetPathName()==P.Asset)
            {P.Component=C;++Matches;}
        }
        if(Matches!=1||!P.Component->GetInstanceTransform(0,P.InstanceTransform,true)||!ZeppelinInstanceUnchanged(P))
        {Abort(TEXT("expected one unchanged fixed original zeppelin collision instance"));return;}
        ControlReport->SetStringField(TEXT("probe"),TEXT("native_source_zeppelin_contact_v1"));
        ControlReport->SetStringField(TEXT("expected_mesh"),P.Mesh);
        ControlReport->SetStringField(TEXT("expected_asset"),P.Asset);
        ControlReport->SetNumberField(TEXT("expected_instance"),0);
        ControlReport->SetStringField(TEXT("source_fixture"),P.Fixture);
        ControlReport->SetStringField(TEXT("zeppelin_component"),P.Component->GetPathName());
        ControlReport->SetArrayField(TEXT("source_target_ros_m"),ZeppelinVector(TargetRos));
        ControlReport->SetArrayField(TEXT("source_ray_start_ue_cm"),ZeppelinVector(P.RayStart));
        ControlReport->SetArrayField(TEXT("source_ray_end_ue_cm"),ZeppelinVector(P.RayEnd));
        ControlReport->SetNumberField(TEXT("command_speed_m_s"),P.Speed);
        ControlReport->SetBoolField(TEXT("pose_overrides_used"),false);
        ControlReport->SetBoolField(TEXT("vehicle_cameras_repositioned"),false);
        ControlReport->SetNumberField(TEXT("initial_motion_generation"),static_cast<double>(P.MotionGeneration));
        ControlReport->SetStringField(TEXT("contact_evidence_semantics"),
            TEXT("Exact component GetRecentHitAgainst plus 5mm observation-point match; body/timestamp deduplicated OnComponentHit observations; repeated recent frames are not unique events."));
        TArray<TSharedPtr<FJsonValue>> Views;
        for(const auto& View:ZeppelinViews)
        {
            auto Item=MakeShared<FJsonObject>();Item->SetStringField(TEXT("phase"),View.Phase);
            Item->SetArrayField(TEXT("eye_ue_cm"),ZeppelinVector(View.Eye));
            Item->SetArrayField(TEXT("target_ue_cm"),ZeppelinVector(View.Target));
            Item->SetNumberField(TEXT("fov_degrees"),View.Fov);
            Item->SetStringField(TEXT("aim_camera"),TEXT("unchanged physical vehicle-mounted camera"));
            Views.Add(MakeShared<FJsonValueObject>(Item));
        }
        ControlReport->SetArrayField(TEXT("visual_capture_views"),Views);
        UE_LOG(LogRMSim,Display,TEXT("WT_ZEPPELIN_CONTACT_BEGIN mesh=%s instance=0 target_cm=%s speed_mps=%.3f"),
            *P.Mesh,*P.Target.ToString(),P.Speed);
    }
    auto& P=*ZeppelinContactProbe;
    P.Seconds+=DeltaSeconds;
    const FVector Position=Rig->GetActorLocation(),Displacement=Position-P.Start;
    const double Progress=FVector::DotProduct(Displacement,P.Forward),Drift=FVector::DotProduct(Displacement,P.Side);
    const double SpeedCmS=Rig->GetCommandOriginVelocity().Size(),UpZ=Rig->GetActorUpVector().Z;
    P.MaximumRise=FMath::Max(P.MaximumRise,Displacement.Z);P.MaximumDrift=FMath::Max(P.MaximumDrift,FMath::Abs(Drift));
    if(ControlProbePhase==1)P.PeakApproachSpeed=FMath::Max(P.PeakApproachSpeed,SpeedCmS/100.);
    if(Rig->GetActorTransform().ContainsNaN()||Rig->GetMotionGeneration()!=P.MotionGeneration||
        ContactRecoveryCount!=0||Displacement.Z < -100||Displacement.Z > 150||
        UpZ<.65||FMath::Abs(Drift)>60||P.Seconds>100||!ZeppelinInstanceUnchanged(P))
    {Rig->SetContactDrive(0,0,true);Abort(TEXT("zeppelin probe lost its fixed instance, physical route envelope or unchanged motion generation"));return;}

    SetZeppelinCamera(OverviewCapture,ControlPendingCapture);
    auto* Obstacle=P.Component.Get();double RecentImpulse=0;
    const bool Recent=Rig->GetRecentHitAgainst(Obstacle,P.Target,125.,.15,RecentImpulse)&&RecentImpulse>.0001;
    const auto Native=Rig->GetContactState();
    for(const auto& Value:Native->GetArrayField(TEXT("last_contact_per_body")))
    {
        const auto Observation=Value->AsObject();FVector Point,Normal;
        if(!ReadZeppelinVector(Observation,TEXT("point_cm"),Point)||!ReadZeppelinVector(Observation,TEXT("normal"),Normal))continue;
        const double Age=Observation->GetNumberField(TEXT("age_seconds"));double ExactImpulse=0;
        const double BlockingDot=FMath::Abs(FVector::DotProduct(Normal,P.Forward));
        if(Age<0||Age>.15||FVector::Dist2D(Point,P.Target)>125||FMath::Abs(Point.Z-P.Target.Z)>70||
            !Rig->GetRecentHitAgainst(Obstacle,Point,.5,.15,ExactImpulse)||ExactImpulse<=.0001||BlockingDot<.25)continue;
        const FString Body=Observation->GetStringField(TEXT("body"));
        const double HitWorld=GetWorld()->GetTimeSeconds()-Age;
        P.BodyNames.Add(Body);P.PeakImpulse=FMath::Max(P.PeakImpulse,ExactImpulse);
        if(ControlProbePhase==2&&ControlPendingCapture.IsEmpty())
        {
            if(P.FirstHoldContactTime<0)P.FirstHoldContactTime=HitWorld;
            P.LastHoldContactTime=FMath::Max(P.LastHoldContactTime,HitWorld);
        }
        const double* Previous=P.LastRecordedHit.Find(Body);
        if(Previous&&HitWorld<=*Previous+1.e-5)continue;
        P.LastRecordedHit.Add(Body,HitWorld);++P.PositiveContacts;++P.BlockingContacts;
        auto Event=MakeShared<FJsonObject>();
        Event->SetNumberField(TEXT("phase"),ControlProbePhase);Event->SetNumberField(TEXT("world_seconds"),HitWorld);
        Event->SetStringField(TEXT("body"),Body);Event->SetStringField(TEXT("exact_component"),Obstacle->GetPathName());
        Event->SetNumberField(TEXT("instance"),0);Event->SetNumberField(TEXT("impulse_n_s"),ExactImpulse);
        Event->SetNumberField(TEXT("blocking_normal_abs_dot_approach"),BlockingDot);
        Event->SetBoolField(TEXT("exact_component_and_point_matched"),true);
        Event->SetArrayField(TEXT("point_cm"),ZeppelinVector(Point));Event->SetArrayField(TEXT("surface_normal"),ZeppelinVector(Normal));
        Event->SetArrayField(TEXT("vehicle_position_cm"),ZeppelinVector(Position));
        Event->SetNumberField(TEXT("observed_vehicle_speed_m_s"),SpeedCmS/100.);
        if(P.Events.Num()<4096)P.Events.Add(MakeShared<FJsonValueObject>(Event));
    }
    ControlReport->SetArrayField(TEXT("actual_zeppelin_hit_observations"),P.Events);
    ControlReport->SetNumberField(TEXT("positive_target_contacts"),P.PositiveContacts);
    ControlReport->SetNumberField(TEXT("forward_blocking_contacts"),P.BlockingContacts);
    ControlReport->SetNumberField(TEXT("peak_impulse_n_s"),P.PeakImpulse);
    ControlReport->SetNumberField(TEXT("peak_approach_speed_m_s"),P.PeakApproachSpeed);
    ControlReport->SetNumberField(TEXT("maximum_body_rise_cm"),P.MaximumRise);
    ControlReport->SetNumberField(TEXT("maximum_route_drift_cm"),P.MaximumDrift);
    ControlReport->SetNumberField(TEXT("current_progress_cm"),Progress);
    ControlReport->SetNumberField(TEXT("current_speed_cm_s"),SpeedCmS);
    ControlReport->SetNumberField(TEXT("final_motion_generation"),static_cast<double>(Rig->GetMotionGeneration()));
    ControlReport->SetNumberField(TEXT("contact_recovery_count"),ContactRecoveryCount);
    TArray<TSharedPtr<FJsonValue>> Bodies;
    for(const auto& Body:P.BodyNames)Bodies.Add(MakeShared<FJsonValueString>(Body));
    ControlReport->SetArrayField(TEXT("contacting_vehicle_bodies"),Bodies);
    if(P.Seconds>=P.NextSample)
    {
        P.NextSample=P.Seconds+.1;auto Sample=MakeShared<FJsonObject>();
        Sample->SetNumberField(TEXT("phase"),ControlProbePhase);Sample->SetNumberField(TEXT("elapsed_seconds"),P.Seconds);
        Sample->SetNumberField(TEXT("progress_cm"),Progress);Sample->SetNumberField(TEXT("drift_cm"),Drift);
        Sample->SetNumberField(TEXT("actual_speed_cm_s"),SpeedCmS);Sample->SetBoolField(TEXT("recent_exact_contact"),Recent);
        Sample->SetObjectField(TEXT("native_contact_state"),Native);ContactSamples.Add(MakeShared<FJsonValueObject>(Sample));
        ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    }
    if(!ControlPendingCapture.IsEmpty())
    {
        Rig->SetContactDrive(0,0,true);
        const bool VisualView=ControlPendingCapture==TEXT("zeppelin-overview")||ControlPendingCapture==TEXT("zeppelin-cabins")||
            ControlPendingCapture==TEXT("zeppelin-frame");
        if(++ControlCaptureWarmFrames<(VisualView?90:24))return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("zeppelin PNG capture failed"));return;}
        ++P.Captures;UE_LOG(LogRMSim,Display,TEXT("WT_ZEPPELIN_CONTACT_CAPTURE phase=%s"),*ControlPendingCapture);
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    const bool PhaseBeginning=ContactPhaseTime==0;ContactPhaseTime+=DeltaSeconds;
    const auto Before=ManualControlState();
    auto Check=[&](const TCHAR* Name,bool Passed,const FString& Detail)
    {
        if(!Passed){Rig->SetContactDrive(0,0,true);CaptureControlView(TEXT("zeppelin-failed"));}
        return RecordControlCheck(Name,Passed,Before,Detail);
    };
    auto Next=[&](const TCHAR* Capture)
    {
        Rig->SetContactDrive(0,0,true);++ControlProbePhase;ContactPhaseTime=0;
        if(Capture)ControlPendingCapture=Capture;
    };
    const double Direction=ControlProbePhase==4?-1.:1.;
    const double DesiredYaw=ContactStart.Rotator().Yaw+FMath::RadiansToDegrees(FMath::Atan2(-Direction*Drift,100.));
    const double YawError=FMath::FindDeltaAngleDegrees(Rig->GetActorRotation().Yaw,DesiredYaw);
    const double Steer=FMath::Clamp(-FMath::DegreesToRadians(YawError)*3,-.7,.7);
    const double Drive=P.Speed*FMath::Clamp(1.-FMath::Abs(YawError)/40.,0.,1.);
    switch(ControlProbePhase)
    {
    case 0:
    {
        Rig->SetContactDrive(0,0,true);if(ContactPhaseTime<3)return;
        if(!Check(TEXT("physical_settle"),SpeedCmS<15&&UpZ>.9&&P.Events.IsEmpty(),
            FString::Printf(TEXT("Native settle %.4fcm/s upZ %.5f target observations %d"),SpeedCmS,UpZ,P.Events.Num())))return;
        FHitResult Hit;TSharedPtr<FJsonObject> SurfaceAudit;
        const bool Aligned=TraceZeppelinSurface(P,Rig,Hit,SurfaceAudit);
        ControlReport->SetObjectField(TEXT("source_surface_initial"),SurfaceAudit);
        ControlReport->SetNumberField(TEXT("source_surface_error_cm"),FVector::Dist(Hit.ImpactPoint,P.Target));
        ControlReport->SetNumberField(TEXT("source_surface_trace_item"),Hit.Item);
        ControlReport->SetBoolField(TEXT("source_surface_blocking_hit"),Hit.bBlockingHit);
        ControlReport->SetStringField(TEXT("source_surface_component"),GetPathNameSafe(Hit.GetComponent()));
        ControlReport->SetArrayField(TEXT("queried_source_surface_cm"),ZeppelinVector(Hit.ImpactPoint));
        ControlReport->SetArrayField(TEXT("source_surface_normal"),ZeppelinVector(Hit.ImpactNormal));
        if(!Check(TEXT("source_zeppelin_surface_alignment"),Aligned,
            FString::Printf(TEXT("Original source ray hits exact fixed instance %d; surface error %.6fcm"),Hit.Item,FVector::Dist(Hit.ImpactPoint,P.Target))))return;
        ContactStart=Rig->GetActorTransform();P.Start=Position;Next(TEXT("zeppelin-before"));return;
    }
    case 1:
        Rig->SetContactDrive(Drive,Steer,false);
        if(P.Events.IsEmpty()&&ContactPhaseTime<35)return;
        if(!Check(TEXT("actual_zeppelin_contact"),!P.Events.IsEmpty()&&Progress>50&&Recent&&P.PeakApproachSpeed>.1,
            FString::Printf(TEXT("Exact original truss: %d unique body/time observations after %.3fcm, peak approach %.4fm/s, impulse %.6fNs"),
                P.PositiveContacts,Progress,P.PeakApproachSpeed,P.PeakImpulse)))return;
        Next(TEXT("zeppelin-contact"));return;
    case 2:
        if(PhaseBeginning)
        {
            P.HoldStart=Position;P.HoldContactStart=P.PositiveContacts;P.HoldBlockingStart=P.BlockingContacts;
            P.FirstHoldContactTime=-1;P.LastHoldContactTime=-1;P.HoldStoppedSeconds=0;P.HoldRecentSeconds=0;
        }
        Rig->SetContactDrive(Drive,Steer,false);
        P.HoldAdvance=FVector::DotProduct(Position-P.HoldStart,P.Forward);
        if(SpeedCmS<12)P.HoldStoppedSeconds+=DeltaSeconds;if(Recent)P.HoldRecentSeconds+=DeltaSeconds;
        if(ContactPhaseTime<3)return;
        ControlReport->SetNumberField(TEXT("held_drive_advance_cm"),P.HoldAdvance);
        ControlReport->SetNumberField(TEXT("held_drive_seconds"),ContactPhaseTime);
        ControlReport->SetNumberField(TEXT("held_drive_positive_contacts"),P.PositiveContacts-P.HoldContactStart);
        ControlReport->SetNumberField(TEXT("held_drive_blocking_contacts"),P.BlockingContacts-P.HoldBlockingStart);
        ControlReport->SetNumberField(TEXT("held_stopped_seconds"),P.HoldStoppedSeconds);
        ControlReport->SetNumberField(TEXT("held_recent_contact_seconds"),P.HoldRecentSeconds);
        if(!Check(TEXT("zeppelin_blocks_low_speed_drive"),
            P.PositiveContacts-P.HoldContactStart>=3&&P.BlockingContacts-P.HoldBlockingStart>=2&&
            P.LastHoldContactTime-P.FirstHoldContactTime>=.5&&P.HoldRecentSeconds>=1&&P.HoldStoppedSeconds>=1&&
            P.HoldAdvance<10&&P.HoldAdvance>-30&&SpeedCmS<12&&UpZ>.85,
            FString::Printf(TEXT("Continued %.3fm/s wheel command: %.3fcm advance, %.3fcm/s speed, %d observations over %.3fs; stopped %.3fs, recent %.3fs"),
                P.Speed,P.HoldAdvance,SpeedCmS,P.PositiveContacts-P.HoldContactStart,P.LastHoldContactTime-P.FirstHoldContactTime,
                P.HoldStoppedSeconds,P.HoldRecentSeconds)))return;
        Next(nullptr);return;
    case 3:
        Rig->SetContactDrive(0,0,true);if(ContactPhaseTime<2)return;
        if(!Check(TEXT("physical_brake_after_zeppelin"),SpeedCmS<15,
            FString::Printf(TEXT("Wheel brake settles body to %.4fcm/s"),SpeedCmS)))return;
        P.ReverseStart=Position;Next(TEXT("zeppelin-blocked"));return;
    case 4:
        Rig->SetContactDrive(-Drive,Steer,false);
        P.ReverseClearance=FVector::DotProduct(P.ReverseStart-Position,P.Forward);
        if(P.ReverseClearance<100&&ContactPhaseTime<20)return;
        ControlReport->SetNumberField(TEXT("reverse_clearance_cm"),P.ReverseClearance);
        if(!Check(TEXT("physical_reverse_from_zeppelin"),P.ReverseClearance>=100&&FMath::Abs(Drift)<40,
            FString::Printf(TEXT("Physical wheel-driven backoff %.3fcm, lateral drift %.3fcm"),P.ReverseClearance,Drift)))return;
        Next(nullptr);return;
    case 5:
    {
        Rig->SetContactDrive(0,0,true);if(ContactPhaseTime<2)return;
        if(!Check(TEXT("physical_stop_after_reverse"),SpeedCmS<15&&!Recent,
            FString::Printf(TEXT("Final physical speed %.4fcm/s; current source contact %d"),SpeedCmS,Recent)))return;
        FHitResult Hit;TSharedPtr<FJsonObject> SurfaceAudit;
        const bool Preserved=TraceZeppelinSurface(P,Rig,Hit,SurfaceAudit);
        ControlReport->SetObjectField(TEXT("source_surface_final"),SurfaceAudit);
        ControlReport->SetNumberField(TEXT("final_source_surface_error_cm"),FVector::Dist(Hit.ImpactPoint,P.Target));
        if(!Check(TEXT("source_zeppelin_survives_drive"),Preserved,
            FString::Printf(TEXT("Fixed source invariants=%d; vehicle-filtered query actor=%s component=%s item=%d error=%.6fcm; ignored=%s"),
                ZeppelinInstanceUnchanged(P),*GetPathNameSafe(Hit.GetActor()),*GetPathNameSafe(Hit.GetComponent()),Hit.Item,
                FVector::Dist(Hit.ImpactPoint,P.Target),*GetPathNameSafe(Rig))))return;
        ControlReport->SetStringField(TEXT("physical_outcome"),TEXT("blocked_then_reversed"));
        Next(TEXT("zeppelin-reverse"));return;
    }
    case 6:Next(TEXT("zeppelin-overview"));return;
    case 7:Next(TEXT("zeppelin-cabins"));return;
    case 8:Next(TEXT("zeppelin-frame"));return;
    default:
        Rig->SetContactDrive(0,0,true);
        ControlReport->SetNumberField(TEXT("captures"),P.Captures);
        if(P.Captures!=7||ControlChecks.Num()!=8){Abort(TEXT("incomplete zeppelin checks or diagnostic/visual captures"));return;}
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot save zeppelin contact result"));return;}
        UE_LOG(LogRMSim,Display,TEXT("WT_ZEPPELIN_CONTACT_PASSED expected_mesh=%s expected_instance=0 positive_contacts=%d blocking_contacts=%d captures=%d checks=%d hold_advance_cm=%.5f peak_impulse_ns=%.6f"),
            *P.Mesh,P.PositiveContacts,P.BlockingContacts,P.Captures,ControlChecks.Num(),P.HoldAdvance,P.PeakImpulse);
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
}
