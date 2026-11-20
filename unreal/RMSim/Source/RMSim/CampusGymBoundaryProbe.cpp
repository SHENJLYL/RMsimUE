#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "HAL/PlatformMisc.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

// Opt-in evidence fixture. Spawn/SnapToTerrain is the ordinary initialization;
// only wheel motor commands change the vehicle after initialization.
struct FCampusGymBoundaryProbeState
{
    TWeakObjectPtr<UStaticMeshComponent> Wall;
    FTransform WallTransform;
    FBox WallBounds;
    FBox TargetBoundsMetres;
    FString PhysicsContract,BoundaryManifestFile;
    double SourceSouthEdgeCm=0,TargetSouthEdgeCm=0,WallInnerFaceCm=0,WallClearanceCm=0;
    double DeclaredClearanceCm=0,MinimumSourceMarginCm=TNumericLimits<double>::Max();
    bool CaptureEnabled=true;
    FVector Start=FVector::ZeroVector,Forward=FVector::ForwardVector,Side=FVector::RightVector;
    FVector HoldStart=FVector::ZeroVector,ReverseStart=FVector::ZeroVector;
    uint64 MotionGeneration=0;
    double Seconds=0,NextSample=0,PeakSpeedMps=0,PeakApproachSpeedMps=0,PeakImpulseNs=0;
    double FirstHitSeconds=-1,LastHitWorldSeconds=-1,HoldFirstHitWorld=-1,HoldLastHitWorld=-1;
    double HoldStoppedSeconds=0,HoldRecentSeconds=0,HoldAdvanceCm=0;
    double MinimumBoundaryMarginCm=TNumericLimits<double>::Max();
    double MinimumComponentAabbSourceMarginCm=TNumericLimits<double>::Max();
    double MinimumComponentAabbTargetMarginCm=TNumericLimits<double>::Max();
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
bool ReadGymBoundaryReference(const FMapStage& Stage,FCampusGymBoundaryProbeState& P,FString& Error)
{
    // Match ArenaShell's resolution for copied navigation/probe scene files.
    P.BoundaryManifestFile=FPaths::Combine(FPaths::GetPath(Stage.SourceFile),TEXT("arena-shell.json"));
    if(!IFileManager::Get().FileExists(*P.BoundaryManifestFile))
    {
        FString Directory=FPaths::GetPath(Stage.SourceFile);
        for(int32 Depth=0;Depth<8;++Depth)
        {
            const FString Candidate=FPaths::Combine(Directory,TEXT("assets/maps/rmuc2027/arena-shell.json"));
            if(IFileManager::Get().FileExists(*Candidate)){P.BoundaryManifestFile=Candidate;break;}
            const FString Parent=FPaths::GetPath(Directory);
            if(Parent.IsEmpty()||Parent==Directory)break;
            Directory=Parent;
        }
    }
    auto Fail=[&Error](const TCHAR* Message){Error=Message;return false;};
    FString Text;TSharedPtr<FJsonObject> Manifest;
    if(IFileManager::Get().FileSize(*P.BoundaryManifestFile)>64*1024||
       !FFileHelper::LoadFileToString(Text,*P.BoundaryManifestFile)||
       !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Manifest))
        return Fail(TEXT("boundary probe cannot read the actual perimeter manifest"));
    const TSharedPtr<FJsonObject>* Source=nullptr;
    const TSharedPtr<FJsonObject>* Perimeter=nullptr;
    FVector Lower,Upper;
    if(!Manifest.IsValid()||!Manifest->TryGetObjectField(TEXT("bounds_m"),Source)||
       !ReadGymVector(*Source,TEXT("lo"),Lower)||!ReadGymVector(*Source,TEXT("hi"),Upper)||
       !Lower.Equals(Stage.SourceBoundsMetres.Min,1.e-7)||!Upper.Equals(Stage.SourceBoundsMetres.Max,1.e-7)||
       !Manifest->TryGetObjectField(TEXT("perimeter"),Perimeter)||
       !(*Perimeter)->TryGetStringField(TEXT("physics_contract"),P.PhysicsContract)||
       (P.PhysicsContract!=TEXT("outside_source_bounds_v1")&&P.PhysicsContract!=TEXT("outside_platform_bounds_v1")))
        return Fail(TEXT("boundary probe source bounds or physical contract differ"));
    P.TargetBoundsMetres=Stage.SourceBoundsMetres;
    if(P.PhysicsContract==TEXT("outside_platform_bounds_v1"))
    {
        const TSharedPtr<FJsonObject>* Target=nullptr;
        if(!(*Perimeter)->TryGetObjectField(TEXT("target_bounds_m"),Target)||
           !ReadGymVector(*Target,TEXT("lo"),Lower)||!ReadGymVector(*Target,TEXT("hi"),Upper)||
           (Upper-Lower).GetMin()<=0.||
           FMath::Abs(Lower.X-Stage.SourceBoundsMetres.Min.X)>1.e-7||
           FMath::Abs(Upper.X-Stage.SourceBoundsMetres.Max.X)>1.e-7||
           FMath::Abs(Lower.Z-Stage.SourceBoundsMetres.Min.Z)>1.e-7||
           FMath::Abs(Upper.Z-Stage.SourceBoundsMetres.Max.Z)>1.e-7||
           Lower.Y<Stage.SourceBoundsMetres.Min.Y||Upper.Y>Stage.SourceBoundsMetres.Max.Y)
            return Fail(TEXT("boundary probe requires the declared platform target inside unchanged source X/Z bounds"));
        P.TargetBoundsMetres=FBox(Lower,Upper);
    }
    double Clearance=0.;
    if(!(*Perimeter)->TryGetNumberField(TEXT("outside_clearance_m"),Clearance)||
       !FMath::IsFinite(Clearance)||Clearance<0.||Clearance>.02)
        return Fail(TEXT("boundary probe requires the declared 0..2cm exterior clearance"));
    P.DeclaredClearanceCm=Clearance*100.;
    return true;
}
}

void AImportLabGameMode::TickCampusGymBoundaryProbe(double DeltaSeconds)
{
    const bool CaptureEnabled=!FParse::Param(FCommandLine::Get(),TEXT("NullRHI"))&&
        !FParse::Param(FCommandLine::Get(),TEXT("GymBoundaryProbeNoCapture"));
    if(!Rig||!Rig->IsContactPhysics()||!ControlReport.IsValid()||
        (CaptureEnabled&&!OverviewCapture)||StageMap.Id!=TEXT("rmuc2027"))
    {Abort(TEXT("boundary probe requires ContactProbe, actual RMUC perimeter, native vehicle and EvidenceDir"));return;}
    if(!CampusGymBoundaryProbe.IsValid())
    {
        CampusGymBoundaryProbe=MakeShared<FCampusGymBoundaryProbeState>();
        auto& P=*CampusGymBoundaryProbe;
        AStaticMeshActor* WallActor=MapBoxes.FindRef(TEXT("arena_guard_south"));
        if(!WallActor){Abort(TEXT("real south perimeter wall is missing"));return;}
        P.Wall=WallActor->GetStaticMeshComponent();
        P.WallTransform=P.Wall->GetComponentTransform();
        if(!P.Wall->GetStaticMesh()){Abort(TEXT("real perimeter has no mesh bounds"));return;}
        P.WallBounds=P.Wall->GetStaticMesh()->GetBoundingBox().TransformBy(P.WallTransform);
        FString ReferenceError;
        if(!ReadGymBoundaryReference(StageMap,P,ReferenceError)){Abort(ReferenceError);return;}
        P.SourceSouthEdgeCm=-StageMap.SourceBoundsMetres.Min.Y*100.;
        P.TargetSouthEdgeCm=-P.TargetBoundsMetres.Min.Y*100.;
        P.WallInnerFaceCm=P.WallBounds.Min.Y;
        P.WallClearanceCm=P.WallInnerFaceCm-P.TargetSouthEdgeCm;
        P.CaptureEnabled=CaptureEnabled;
        if(!P.WallBounds.IsValid||FMath::Abs(P.WallClearanceCm-P.DeclaredClearanceCm)>1.e-4)
        {Abort(TEXT("south collision slab must match the declared target edge and clearance within 1 micrometre"));return;}
        P.Start=Rig->GetActorLocation();
        P.Forward=Rig->GetActorForwardVector().GetSafeNormal2D();
        P.Side=FVector::CrossProduct(FVector::UpVector,P.Forward);
        P.MotionGeneration=Rig->GetMotionGeneration();
        // This source-audited approach is beside the southern decorative door.
        // UE +Y is ROS -Y, so the southward approach is UE +Y.
        if(FMath::Abs(P.Start.X+1100)>15||FMath::Abs(P.Start.Y-660)>15||
            FVector::DotProduct(P.Forward,FVector(0,1,0))<.99)
        {Abort(TEXT("use the audited copied-map spawn ROS(-11,-6.6), yaw=-pi/2"));return;}
        ControlReport->SetStringField(TEXT("probe"),P.PhysicsContract==TEXT("outside_platform_bounds_v1")?
            TEXT("campus_gym_actual_platform_boundary_v1"):TEXT("campus_gym_actual_outside_boundary_v2"));
        ControlReport->SetStringField(TEXT("physics_contract"),P.PhysicsContract);
        ControlReport->SetStringField(TEXT("boundary_manifest_file"),P.BoundaryManifestFile);
        ControlReport->SetStringField(TEXT("expected_wall"),TEXT("arena_guard_south"));
        ControlReport->SetStringField(TEXT("wall_component"),P.Wall->GetPathName());
        ControlReport->SetNumberField(TEXT("approach_command_speed_m_s"),2.0);
        ControlReport->SetNumberField(TEXT("approach_timeout_seconds"),12.0);
        ControlReport->SetNumberField(TEXT("total_timeout_seconds"),40.0);
        ControlReport->SetBoolField(TEXT("capture_enabled"),CaptureEnabled);
        ControlReport->SetBoolField(TEXT("null_rhi"),FParse::Param(FCommandLine::Get(),TEXT("NullRHI")));
        ControlReport->SetStringField(TEXT("boundary_envelope_semantics"),TEXT("Exact world-axis support of source chassis and four-wheel convex vertices; hull-local then component transform; excludes gimbal and cosmetic meshes"));
        ControlReport->SetStringField(TEXT("collision_envelope_geometry_source"),TEXT("UBodySetup::AggGeom.ConvexElems[].VertexData; unavailable or non-convex source geometry fails closed"));
        ControlReport->SetStringField(TEXT("component_aabb_envelope_semantics"),TEXT("GetFoliageContactBounds: original chassis and wheel component bounds, retained as a diagnostic"));
        ControlReport->SetStringField(TEXT("rendering_evidence_scope"),CaptureEnabled?TEXT("physics plus optional diagnostic screenshots"):TEXT("physical contacts only; rendering not exercised"));
        ControlReport->SetArrayField(TEXT("source_bounds_ros_lo_m"),GymVector(StageMap.SourceBoundsMetres.Min));
        ControlReport->SetArrayField(TEXT("source_bounds_ros_hi_m"),GymVector(StageMap.SourceBoundsMetres.Max));
        ControlReport->SetArrayField(TEXT("target_bounds_ros_lo_m"),GymVector(P.TargetBoundsMetres.Min));
        ControlReport->SetArrayField(TEXT("target_bounds_ros_hi_m"),GymVector(P.TargetBoundsMetres.Max));
        ControlReport->SetArrayField(TEXT("wall_world_bounds_lo_cm"),GymVector(P.WallBounds.Min));
        ControlReport->SetArrayField(TEXT("wall_world_bounds_hi_cm"),GymVector(P.WallBounds.Max));
        ControlReport->SetArrayField(TEXT("wall_ros_bounds_lo_m"),GymVector(FVector(P.WallBounds.Min.X,-P.WallBounds.Max.Y,P.WallBounds.Min.Z)/100.));
        ControlReport->SetArrayField(TEXT("wall_ros_bounds_hi_m"),GymVector(FVector(P.WallBounds.Max.X,-P.WallBounds.Min.Y,P.WallBounds.Max.Z)/100.));
        ControlReport->SetNumberField(TEXT("source_south_edge_world_y_cm"),P.SourceSouthEdgeCm);
        ControlReport->SetNumberField(TEXT("wall_inner_face_world_y_cm"),P.WallInnerFaceCm);
        ControlReport->SetNumberField(TEXT("wall_outside_clearance_cm"),P.WallInnerFaceCm-P.SourceSouthEdgeCm);
        ControlReport->SetNumberField(TEXT("target_south_edge_world_y_cm"),P.TargetSouthEdgeCm);
        ControlReport->SetNumberField(TEXT("wall_target_outside_clearance_cm"),P.WallClearanceCm);
        ControlReport->SetNumberField(TEXT("declared_target_clearance_cm"),P.DeclaredClearanceCm);
        ControlReport->SetNumberField(TEXT("wall_target_inner_face_error_cm"),P.WallClearanceCm-P.DeclaredClearanceCm);
        ControlReport->SetNumberField(TEXT("reverse_command_speed_m_s"),.75);
        ControlReport->SetBoolField(TEXT("pose_overrides_used"),false);
        ControlReport->SetBoolField(TEXT("os_keyboard_test"),false);
        ControlReport->SetNumberField(TEXT("initial_motion_generation"),static_cast<double>(P.MotionGeneration));
        ControlReport->SetStringField(TEXT("physics_backend"),Rig->GetPhysicsBackendName());
        ControlReport->SetStringField(TEXT("contact_evidence_semantics"),Rig->IsMujocoPhysics()?
            TEXT("Native MuJoCo solved contact impulses; short UE queries identify the exact existing wall component; repeated recent frames are not unique solver events"):
            TEXT("Exact component OnComponentHit observations via GetRecentHitAgainst; repeated recent frames are not unique solver events; no preimpact velocity estimate claimed"));
    }
    auto& P=*CampusGymBoundaryProbe;
    P.Seconds+=DeltaSeconds;
    UStaticMeshComponent* Wall=P.Wall.Get();
    auto Fail=[&](const FString& Reason)
    {
        // Capture the failing physical frame before braking, including frames
        // rejected before the periodic sample below.
        auto Frame=MakeShared<FJsonObject>();
        Frame->SetNumberField(TEXT("elapsed_seconds"),P.Seconds);
        Frame->SetNumberField(TEXT("phase"),ControlProbePhase);
        Frame->SetArrayField(TEXT("actor_position_cm"),GymVector(Rig->GetActorLocation()));
        const FQuat Rotation=Rig->GetActorQuat();
        Frame->SetArrayField(TEXT("actor_rotation_xyzw"),{MakeShared<FJsonValueNumber>(Rotation.X),
            MakeShared<FJsonValueNumber>(Rotation.Y),MakeShared<FJsonValueNumber>(Rotation.Z),
            MakeShared<FJsonValueNumber>(Rotation.W)});
        FBox ExactBounds(ForceInit);int32 Vertices=0;
        const bool ExactAvailable=Rig->GetChassisWheelCollisionEnvelope(ExactBounds,Vertices);
        Frame->SetBoolField(TEXT("collision_envelope_available"),ExactAvailable);
        Frame->SetNumberField(TEXT("collision_envelope_vertex_count"),Vertices);
        if(ExactAvailable)
        {
            Frame->SetArrayField(TEXT("collision_envelope_world_lo_cm"),GymVector(ExactBounds.Min));
            Frame->SetArrayField(TEXT("collision_envelope_world_hi_cm"),GymVector(ExactBounds.Max));
        }
        const FBox ComponentBounds=Rig->GetFoliageContactBounds();
        if(ComponentBounds.IsValid)
        {
            Frame->SetArrayField(TEXT("component_aabb_world_lo_cm"),GymVector(ComponentBounds.Min));
            Frame->SetArrayField(TEXT("component_aabb_world_hi_cm"),GymVector(ComponentBounds.Max));
        }
        Frame->SetObjectField(TEXT("native_contact_state"),Rig->GetContactState());
        ControlReport->SetObjectField(TEXT("failure_frame_state"),Frame);
        Rig->SetContactDrive(0,0,true);
        if(P.CaptureEnabled)CaptureControlView(TEXT("gym-boundary-failed"));
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
    FBox BodyBounds(ForceInit);int32 EnvelopeVertexCount=0;
    if(!Rig->GetChassisWheelCollisionEnvelope(BodyBounds,EnvelopeVertexCount))
    {Fail(TEXT("chassis/four-wheel source convex collision envelope unavailable"));return;}
    const FBox ComponentBounds=Rig->GetFoliageContactBounds();
    if(!ComponentBounds.IsValid||ComponentBounds.Min.ContainsNaN()||ComponentBounds.Max.ContainsNaN())
    {Fail(TEXT("chassis/wheel component bounds unavailable"));return;}
    ControlReport->SetNumberField(TEXT("collision_envelope_vertex_count"),EnvelopeVertexCount);
    const FBox& Source=StageMap.SourceBoundsMetres;
    const double GapToWallCm=P.WallInnerFaceCm-BodyBounds.Max.Y;
    const double CentreToWallCm=P.WallInnerFaceCm-Position.Y;
    auto MarginTo=[](const FBox& Envelope,const FBox& Boundary)
    {
        return FMath::Min(
            FMath::Min(Envelope.Min.X-Boundary.Min.X*100.,Boundary.Max.X*100.-Envelope.Max.X),
            FMath::Min(Envelope.Min.Y+Boundary.Max.Y*100.,-Boundary.Min.Y*100.-Envelope.Max.Y));
    };
    const double SourceMargin=MarginTo(BodyBounds,Source),Margin=MarginTo(BodyBounds,P.TargetBoundsMetres);
    const double ComponentSourceMargin=MarginTo(ComponentBounds,Source);
    const double ComponentTargetMargin=MarginTo(ComponentBounds,P.TargetBoundsMetres);
    const double ComponentGapToWallCm=P.WallInnerFaceCm-ComponentBounds.Max.Y;
    P.MinimumComponentAabbSourceMarginCm=FMath::Min(P.MinimumComponentAabbSourceMarginCm,ComponentSourceMargin);
    P.MinimumComponentAabbTargetMarginCm=FMath::Min(P.MinimumComponentAabbTargetMarginCm,ComponentTargetMargin);
    ControlReport->SetNumberField(TEXT("minimum_component_aabb_boundary_margin_cm"),P.MinimumComponentAabbSourceMarginCm);
    ControlReport->SetNumberField(TEXT("minimum_component_aabb_target_margin_cm"),P.MinimumComponentAabbTargetMarginCm);
    ControlReport->SetNumberField(TEXT("current_component_aabb_target_margin_cm"),ComponentTargetMargin);
    ControlReport->SetNumberField(TEXT("current_component_aabb_to_wall_gap_cm"),ComponentGapToWallCm);
    P.MinimumBoundaryMarginCm=FMath::Min(P.MinimumBoundaryMarginCm,Margin);
    P.MinimumSourceMarginCm=FMath::Min(P.MinimumSourceMarginCm,SourceMargin);
    ControlReport->SetNumberField(TEXT("minimum_vehicle_envelope_boundary_margin_cm"),P.MinimumSourceMarginCm);
    ControlReport->SetNumberField(TEXT("minimum_vehicle_envelope_target_margin_cm"),P.MinimumBoundaryMarginCm);
    ControlReport->SetNumberField(TEXT("current_chassis_wheels_to_wall_gap_cm"),GapToWallCm);
    ControlReport->SetNumberField(TEXT("current_origin_to_wall_distance_cm"),CentreToWallCm);
    ControlReport->SetNumberField(TEXT("current_actual_speed_cm_s"),SpeedCmS);
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
        UpZ<.65||FMath::Abs(Drift)>60||P.Seconds>40)
    {Fail(TEXT("vehicle left physical boundary/route envelope, changed motion generation or timed out"));return;}

    // A fixed oblique interior camera includes the real glass door, its wall
    // contact and the complete robot; the vehicle cameras are captured too.
    const FVector Target(-1100,Wall->GetComponentLocation().Y,55);
    const FVector Eye=Target-P.Forward*300+P.Side*250+FVector(0,0,160);
    if(P.CaptureEnabled)
    {
        OverviewCapture->SetWorldLocationAndRotation(Eye,(Target-Eye).Rotation());
        OverviewCapture->FOVAngle=62;
    }
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
        Event->SetNumberField(TEXT("wall_inner_face_distance_cm"),FMath::Abs(Point.Y-P.WallInnerFaceCm));
        Event->SetNumberField(TEXT("source_south_edge_signed_distance_cm"),Point.Y-P.SourceSouthEdgeCm);
        Event->SetNumberField(TEXT("target_south_edge_signed_distance_cm"),Point.Y-P.TargetSouthEdgeCm);
        Event->SetBoolField(TEXT("exact_wall_pointer_and_point_matched"),true);
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
        Sample->SetNumberField(TEXT("vehicle_envelope_boundary_margin_cm"),SourceMargin);
        Sample->SetNumberField(TEXT("vehicle_envelope_target_margin_cm"),Margin);
        Sample->SetNumberField(TEXT("chassis_wheels_to_wall_gap_cm"),GapToWallCm);
        Sample->SetNumberField(TEXT("collision_envelope_vertex_count"),EnvelopeVertexCount);
        Sample->SetArrayField(TEXT("collision_envelope_world_lo_cm"),GymVector(BodyBounds.Min));
        Sample->SetArrayField(TEXT("collision_envelope_world_hi_cm"),GymVector(BodyBounds.Max));
        Sample->SetNumberField(TEXT("component_aabb_boundary_margin_cm"),ComponentSourceMargin);
        Sample->SetNumberField(TEXT("component_aabb_target_margin_cm"),ComponentTargetMargin);
        Sample->SetNumberField(TEXT("component_aabb_to_wall_gap_cm"),ComponentGapToWallCm);
        Sample->SetArrayField(TEXT("component_aabb_world_lo_cm"),GymVector(ComponentBounds.Min));
        Sample->SetArrayField(TEXT("component_aabb_world_hi_cm"),GymVector(ComponentBounds.Max));
        Sample->SetNumberField(TEXT("origin_to_wall_distance_cm"),CentreToWallCm);
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
            if(P.CaptureEnabled)CaptureControlView(TEXT("gym-boundary-failed"));
        }
        return RecordControlCheck(Name,Passed,Before,Detail);
    };
    auto Next=[&](const TCHAR* Capture)
    {
        ++ControlProbePhase;ContactPhaseTime=0;
        if(Capture&&P.CaptureEnabled){Rig->SetContactDrive(0,0,true);ControlPendingCapture=Capture;}
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
        if(P.Events.IsEmpty()&&ContactPhaseTime<12)return;
        if(!Check(TEXT("actual_closed_perimeter_contact"),
            !P.Events.IsEmpty()&&Progress>50&&P.PeakApproachSpeedMps>.5&&Recent,
            FString::Printf(TEXT("Real wall contact after %.3fcm; actual peak approach %.4fm/s; target 2m/s; peak impulse %.6fNs"),
                Progress,P.PeakApproachSpeedMps,P.PeakImpulseNs)))return;
        ControlReport->SetNumberField(TEXT("first_accepted_wall_contact_progress_cm"),Progress);
        ControlReport->SetNumberField(TEXT("first_accepted_wall_contact_origin_to_wall_cm"),CentreToWallCm);
        ControlReport->SetNumberField(TEXT("first_accepted_wall_contact_chassis_wheels_gap_cm"),GapToWallCm);
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
        ControlReport->SetNumberField(TEXT("held_drive_final_speed_cm_s"),SpeedCmS);
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
        if(!Check(P.PhysicsContract==TEXT("outside_platform_bounds_v1")?TEXT("final_stop_inside_platform_boundary"):TEXT("final_stop_inside_original_field"),SpeedCmS<10&&UpZ>.85&&
            P.MinimumBoundaryMarginCm>=-1.&&ContactRecoveryCount==0&&Rig->GetMotionGeneration()==P.MotionGeneration,
            FString::Printf(TEXT("Final speed %.4fcm/s; every-frame minimum field margin %.3fcm; unchanged motion generation %llu; resets %d"),
                SpeedCmS,P.MinimumBoundaryMarginCm,static_cast<unsigned long long>(P.MotionGeneration),ContactRecoveryCount)))return;
        ControlReport->SetNumberField(TEXT("final_stop_speed_cm_s"),SpeedCmS);
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
