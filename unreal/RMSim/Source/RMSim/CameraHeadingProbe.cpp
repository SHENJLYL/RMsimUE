#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "MapOverview.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

// Opt-in shared-input regression. Chaos, existing motors and constraints own
// the vehicle pose. The final R check alone invokes the normal user reset path.
struct FCameraHeadingProbeState
{
    int32 Phase=0,Frames=0,DriveFrames=0,MapFrames=0,Captures=0;
    int32 InitialRecoveries=0;
    uint64 InitialGeneration=0;
    double Seconds=0,PhaseSeconds=0,NextSample=0,CaptureSeconds=0;
    double PeakHeadingError=0,MinimumDriveDot=1,MinimumMapDot=1,PeakOrbitOffset=0;
    double StartViewYaw=0,StartHullYaw=0,NearTargetDistance=0;
    FVector StartPosition=FVector::ZeroVector,ReferenceDirection=FVector::ForwardVector;
    TSharedPtr<FJsonObject> Before;
    TArray<TSharedPtr<FJsonValue>> Samples,Metrics;
    bool bMapOnly=false,bSetupMouseSent=false,bToggleSent=false,bFocusLost=false,bRefocused=false;
    bool bOcclusion=false,bCrossedYawSeam=false,bOcclusionNoCapture=false;
    TWeakObjectPtr<AStaticMeshActor> Occluder;
    int32 BehindRayFrames=0,FrontRayFrames=0,IntendedDriveFrames=0;
    double PreviousOrbitYaw=0,PeakViewYawError=0,MinimumIntendedDriveDot=1;
    double MinimumRawPlanarDepth=1.e9,MaximumRawPlanarDepth=-1.e9;
    double MaximumRawTargetYawError=0,PeakFrameYawDelta=0;
    FVector PreviousAimDirection=FVector::ForwardVector;
};

namespace
{
TArray<TSharedPtr<FJsonValue>> HeadingVector(const FVector& V)
{
    return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),
        MakeShared<FJsonValueNumber>(V.Z)};
}
double HeadingError(const FVector& A,const FVector& B)
{
    return FMath::Abs(FMath::FindDeltaAngleDegrees(A.Rotation().Yaw,B.Rotation().Yaw));
}
}

void AImportLabGameMode::TickCameraHeadingProbe(double DeltaSeconds)
{
    auto* PC=GetWorld()->GetFirstPlayerController();
    auto* Aim=GetAimCapture();
    if(!Rig||!Rig->IsContactPhysics()||!Rig->HasSteeringModules()||!PC||!Aim||
        !OverviewCapture||!ControlReport.IsValid())
    {Abort(TEXT("camera heading probe needs native swerve rig, cameras and EvidenceDir"));return;}
    if(!CameraHeadingProbe.IsValid())
    {
        CameraHeadingProbe=MakeShared<FCameraHeadingProbeState>();
        auto& P=*CameraHeadingProbe;
        P.bMapOnly=FParse::Param(FCommandLine::Get(),TEXT("CameraHeadingMapProbe"));
        P.bOcclusion=FParse::Param(FCommandLine::Get(),TEXT("CameraAimOcclusionProbe"));
        P.bOcclusionNoCapture=P.bOcclusion&&FParse::Param(FCommandLine::Get(),TEXT("OcclusionNoCapture"));
        if(P.bOcclusionNoCapture)
        {
            ControlPendingCapture.Reset();
            OverviewCapture->bCaptureEveryFrame=false;
            for(const auto& Camera:Rig->GetCameras())Camera.Value->bCaptureEveryFrame=false;
        }
        if(P.bOcclusion&&(P.bMapOnly||bRosControl||bRosAimControl))
        {Abort(TEXT("camera occlusion fixture requires manual authority and its dedicated flat map"));return;}
        P.InitialGeneration=Rig->GetMotionGeneration();P.InitialRecoveries=ContactRecoveryCount;
        ControlReport->SetStringField(TEXT("probe"),P.bOcclusion?TEXT("native_camera_aim_occlusion_v1"):TEXT("native_camera_heading_v1"));
        ControlReport->SetBoolField(TEXT("camera_occlusion_fixture"),P.bOcclusion);
        ControlReport->SetBoolField(TEXT("capture_disabled"),P.bOcclusionNoCapture);
        if(P.bOcclusion)ControlReport->SetNumberField(TEXT("capture_sets_expected"),P.bOcclusionNoCapture?0:3);
        ControlReport->SetStringField(TEXT("occlusion_fixture_scope"),
            P.bOcclusion?TEXT("native Visibility trace + shared manual input; Camera Ignore; physical chassis braked except a <=0.1m drive; no runtime pose override"):TEXT("none"));
        ControlReport->SetBoolField(TEXT("map_only"),P.bMapOnly);
        ControlReport->SetBoolField(TEXT("pose_overrides_used"),false);
        ControlReport->SetBoolField(TEXT("os_input_verified"),false);
        ControlReport->SetBoolField(TEXT("hud_pixels_verified"),false);
        ControlReport->SetNumberField(TEXT("initial_motion_generation"),double(P.InitialGeneration));
        ControlReport->SetNumberField(TEXT("initial_recovery_count"),P.InitialRecoveries);
        ControlReport->SetNumberField(TEXT("heading_gate_degrees"),.5);
        ControlReport->SetNumberField(TEXT("same_frame_drive_dot_gate"),.9999);
        UE_LOG(LogRMSim,Display,TEXT("CAMERA_HEADING_BEGIN map_only=%d"),P.bMapOnly);
    }
    auto& P=*CameraHeadingProbe;
    P.Seconds+=DeltaSeconds;
    const bool TerminalReset=!P.bMapOnly&&!P.bOcclusion&&P.Phase>=14;
    if(Rig->GetActorTransform().ContainsNaN()||Rig->GetActorUpVector().Z<.65||
        Rig->GetActorLocation().Z<ContactSpawn.GetLocation().Z-150||P.Seconds>65||
        (!TerminalReset&&(Rig->GetMotionGeneration()!=P.InitialGeneration||
            ContactRecoveryCount!=P.InitialRecoveries)))
    {Abort(TEXT("camera heading probe lost support or unexpectedly reset"));return;}

    // Captures are scene views, not a claim of HUD pixel verification.
    if(!ControlPendingCapture.IsEmpty())
    {
        FManualControlInput Hold;Hold.Brake=true;Hold.FreeLook=bFreeLook;
        ApplyManualInput(Hold,DeltaSeconds);
        if(auto* View=Cast<ACameraActor>(PC->GetViewTarget()))
        {
            OverviewCapture->SetWorldTransform(View->GetActorTransform());
            OverviewCapture->FOVAngle=View->GetCameraComponent()->FieldOfView;
        }
        P.CaptureSeconds+=DeltaSeconds;
        if(++ControlCaptureWarmFrames<24||P.CaptureSeconds<.45)return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("camera heading capture failed"));return;}
        ++P.Captures;
        UE_LOG(LogRMSim,Display,TEXT("CAMERA_HEADING_CAPTURE phase=%s"),*ControlPendingCapture);
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;P.CaptureSeconds=0;
        return;
    }
    const bool First=P.Frames==0;
    if(First)
    {
        P.Before=ManualControlState();P.StartPosition=Rig->GetActorLocation();
        P.ReferenceDirection=Aim->GetForwardVector();P.StartViewYaw=OrbitYaw;
        P.StartHullYaw=Rig->GetActorRotation().Yaw;
        P.PreviousOrbitYaw=OrbitYaw;P.PreviousAimDirection=Aim->GetForwardVector();
    }
    P.PhaseSeconds+=DeltaSeconds;
    FManualControlInput Input;
    auto SetMotion=[&](double ForwardMps,double LeftMps)
    {
        Input.Throttle=ForwardMps/Rig->GetDriveParameter(ForwardMps>=0?
            TEXT("MaxForwardSpeedMps"):TEXT("MaxReverseSpeedMps"));
        Input.Strafe=LeftMps/Rig->GetDriveParameter(TEXT("MaxLateralSpeedMps"));
    };
    auto MouseYaw=[&](double DegreesPerSecond)
    {
        const double Fov=bSightMode&&!Input.FreeLook?
            (Input.Zoom?15.:ScopeBaseFov)/55.:1.;
        Input.MouseX=DegreesPerSecond*DeltaSeconds/(MouseSensitivity*Fov);
    };
    auto Apply=[&]()
    {
        ApplyManualInput(Input,DeltaSeconds);++P.Frames;
        const FVector Actual=Aim->GetForwardVector();
        P.PeakHeadingError=FMath::Max(P.PeakHeadingError,HeadingError(P.ReferenceDirection,Actual));
        P.PeakOrbitOffset=FMath::Max(P.PeakOrbitOffset,
            FMath::Abs(FMath::FindDeltaAngleDegrees(P.ReferenceDirection.Rotation().Yaw,OrbitYaw)));
        const FVector2D Forward(Actual.X,-Actual.Y);
        const FVector2D Unit=Forward.GetSafeNormal(),Left(-Unit.Y,Unit.X);
        if(Input.Focused&&!Input.Brake&&(FMath::Abs(Input.Throttle)+FMath::Abs(Input.Strafe)>.001))
        {
            const FVector2D Expected=Unit*Input.Throttle*Rig->GetDriveParameter(
                Input.Throttle>=0?TEXT("MaxForwardSpeedMps"):TEXT("MaxReverseSpeedMps"))+
                Left*Input.Strafe*Rig->GetDriveParameter(TEXT("MaxLateralSpeedMps"));
            const auto Values=Rig->GetVelocityCommandState()->GetArrayField(TEXT("requested_linear_m_s"));
            const FVector2D Requested(Values[0]->AsNumber(),Values[1]->AsNumber());
            P.MinimumDriveDot=FMath::Min(P.MinimumDriveDot,
                FVector2D::DotProduct(Expected.GetSafeNormal(),Requested.GetSafeNormal()));
            ++P.DriveFrames;
        }
        FMapOverviewFrame Map;
        const bool MapValid=GetMapOverviewFrame(Map);
        if(MapValid)
        {
            P.MinimumMapDot=FMath::Min(P.MinimumMapDot,
                FVector2D::DotProduct(Unit,Map.VehicleForwardRos.GetSafeNormal()));++P.MapFrames;
        }
        if(P.Seconds>=P.NextSample||First)
        {
            P.NextSample=P.Seconds+.1;
            auto Sample=MakeShared<FJsonObject>();
            Sample->SetNumberField(TEXT("phase"),P.Phase);
            Sample->SetNumberField(TEXT("seconds"),P.Seconds);
            Sample->SetNumberField(TEXT("phase_seconds"),P.PhaseSeconds);
            Sample->SetArrayField(TEXT("actual_gimbal_world_forward"),HeadingVector(Actual));
            Sample->SetArrayField(TEXT("reference_gimbal_world_forward"),HeadingVector(P.ReferenceDirection));
            Sample->SetBoolField(TEXT("map_frame_available"),MapValid);
            if(MapValid)Sample->SetArrayField(TEXT("map_arrow_ros"),HeadingVector(
                FVector(Map.VehicleForwardRos.X,Map.VehicleForwardRos.Y,0)));
            Sample->SetObjectField(TEXT("state"),ManualControlState());
            P.Samples.Add(MakeShared<FJsonValueObject>(Sample));
            ControlReport->SetArrayField(TEXT("samples"),P.Samples);
        }
    };
    auto Check=[&](const TCHAR* Name,bool Passed,const TCHAR* Capture=nullptr)
    {
        const double Distance=FVector::Dist2D(P.StartPosition,Rig->GetActorLocation());
        const double HullTurn=FMath::Abs(FMath::FindDeltaAngleDegrees(P.StartHullYaw,Rig->GetActorRotation().Yaw));
        const double ActualTurn=HeadingError(P.ReferenceDirection,Aim->GetForwardVector());
        auto Metric=MakeShared<FJsonObject>();
        Metric->SetStringField(TEXT("name"),Name);Metric->SetNumberField(TEXT("phase"),P.Phase);
        Metric->SetNumberField(TEXT("duration_s"),P.PhaseSeconds);
        Metric->SetNumberField(TEXT("displacement_cm"),Distance);
        Metric->SetNumberField(TEXT("peak_heading_error_deg"),P.PeakHeadingError);
        Metric->SetNumberField(TEXT("actual_gimbal_turn_deg"),ActualTurn);
        Metric->SetNumberField(TEXT("hull_turn_deg"),HullTurn);
        Metric->SetNumberField(TEXT("peak_orbit_offset_deg"),P.PeakOrbitOffset);
        Metric->SetNumberField(TEXT("minimum_same_frame_drive_dot"),P.MinimumDriveDot);
        Metric->SetNumberField(TEXT("drive_samples"),P.DriveFrames);
        Metric->SetNumberField(TEXT("minimum_map_arrow_dot"),P.MinimumMapDot);
        Metric->SetNumberField(TEXT("map_samples"),P.MapFrames);
        if(P.bOcclusion)
        {
            Metric->SetNumberField(TEXT("behind_muzzle_raw_trace_frames"),P.BehindRayFrames);
            Metric->SetNumberField(TEXT("front_muzzle_raw_trace_frames"),P.FrontRayFrames);
            Metric->SetNumberField(TEXT("minimum_raw_planar_depth_cm"),P.MinimumRawPlanarDepth);
            Metric->SetNumberField(TEXT("maximum_raw_planar_depth_cm"),P.MaximumRawPlanarDepth);
            Metric->SetNumberField(TEXT("maximum_legacy_raw_target_yaw_error_deg"),P.MaximumRawTargetYawError);
            Metric->SetNumberField(TEXT("peak_actual_view_yaw_error_deg_after_settle"),P.PeakViewYawError);
            Metric->SetNumberField(TEXT("peak_frame_actual_yaw_delta_deg"),P.PeakFrameYawDelta);
            Metric->SetNumberField(TEXT("minimum_requested_drive_vs_view_dot"),P.MinimumIntendedDriveDot);
            Metric->SetNumberField(TEXT("intended_drive_frames"),P.IntendedDriveFrames);
            Metric->SetBoolField(TEXT("crossed_view_yaw_180_seam"),P.bCrossedYawSeam);
        }
        P.Metrics.Add(MakeShared<FJsonValueObject>(Metric));
        ControlReport->SetArrayField(TEXT("phase_metrics"),P.Metrics);
        ControlReport->SetNumberField(TEXT("final_motion_generation"),double(Rig->GetMotionGeneration()));
        ControlReport->SetNumberField(TEXT("final_recovery_count"),ContactRecoveryCount);
        const FString Detail=FString::Printf(TEXT("moved %.3f cm; peak heading error %.6f deg; actual turn %.3f deg; hull turn %.3f deg; drive dot %.9f (%d); map dot %.9f (%d)"),
            Distance,P.PeakHeadingError,ActualTurn,HullTurn,P.MinimumDriveDot,P.DriveFrames,P.MinimumMapDot,P.MapFrames);
        if(P.bMapOnly)Passed=Passed&&P.MapFrames>0&&P.MinimumMapDot>.999999;
        if(!RecordControlCheck(Name,Passed,P.Before,Detail))
        {Rig->SetContactDrive(0,0,true);return;}
        ++P.Phase;P.PhaseSeconds=0;P.Frames=0;P.PeakHeadingError=0;P.MinimumDriveDot=1;
        P.MinimumMapDot=1;P.PeakOrbitOffset=0;P.DriveFrames=0;P.MapFrames=0;
        P.bSetupMouseSent=false;P.bToggleSent=false;P.bFocusLost=false;P.bRefocused=false;
        P.BehindRayFrames=0;P.FrontRayFrames=0;P.IntendedDriveFrames=0;P.PeakViewYawError=0;
        P.MinimumIntendedDriveDot=1;P.MinimumRawPlanarDepth=1.e9;P.MaximumRawPlanarDepth=-1.e9;
        P.MaximumRawTargetYawError=0;P.PeakFrameYawDelta=0;P.bCrossedYawSeam=false;
        if(Capture&&!P.bOcclusionNoCapture){ControlPendingCapture=Capture;Rig->SetContactDrive(0,0,true);}
    };
    auto Finish=[&]()
    {
        const int32 Expected=P.bOcclusion?10:P.bMapOnly?4:16;
        if(P.bOcclusion)ControlReport->SetNumberField(TEXT("capture_sets_written"),P.Captures);
        if(ControlChecks.Num()!=Expected||!WriteControlReport(TEXT("passed")))
        {Abort(TEXT("camera heading probe incomplete"));return;}
        UE_LOG(LogRMSim,Display,TEXT("CAMERA_HEADING_PASSED map_only=%d checks=%d captures=%d"),
            P.bMapOnly,ControlChecks.Num(),P.Captures);
        if(P.Occluder.IsValid())P.Occluder->Destroy();
        Rig->SetContactDrive(0,0,true);bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);
    };
    const auto Moved=[&](){return FVector::Dist2D(P.StartPosition,Rig->GetActorLocation());};
    const auto StableMoving=[&](){return P.PeakHeadingError<.5&&Moved()>30&&
        P.DriveFrames>20&&P.MinimumDriveDot>.9999;};

    if(P.bOcclusion)
    {
        Input.Brake=true;
        auto PlacePlate=[&](double DepthCm)
        {
            if(!P.Occluder.IsValid())
            {
                auto* Plate=GetWorld()->SpawnActor<AStaticMeshActor>();
                auto* Mesh=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));
                if(!Plate||!Mesh)return false;
                P.Occluder=Plate;
                auto* C=Plate->GetStaticMeshComponent();
                C->SetMobility(EComponentMobility::Movable);C->SetStaticMesh(Mesh);
                C->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
                C->SetCollisionResponseToAllChannels(ECR_Ignore);
                C->SetCollisionResponseToChannel(ECC_Visibility,ECR_Block);
                C->SetCollisionResponseToChannel(ECC_Camera,ECR_Ignore);
                C->SetGenerateOverlapEvents(false);
                Plate->SetActorScale3D(FVector(.04,8.,6.));
            }
            const FVector Flat=ChaseCamera->GetActorForwardVector().GetSafeNormal2D();
            FVector Center=Aim->GetComponentLocation()+Flat*DepthCm;
            Center.Z=Rig->GetActorLocation().Z+150.;
            P.Occluder->SetActorLocationAndRotation(Center,FRotator(0,Flat.Rotation().Yaw,0));
            return true;
        };
        auto Observe=[&]()
        {
            const FVector View=ChaseCamera->GetActorForwardVector();
            const FVector Actual=Aim->GetForwardVector();
            P.PeakFrameYawDelta=FMath::Max(P.PeakFrameYawDelta,
                HeadingError(P.PreviousAimDirection,Actual));
            P.PreviousAimDirection=Actual;
            if(FMath::Abs(OrbitYaw-P.PreviousOrbitYaw)>180.)P.bCrossedYawSeam=true;
            P.PreviousOrbitYaw=OrbitYaw;
            if(P.PhaseSeconds>.3)
                P.PeakViewYawError=FMath::Max(P.PeakViewYawError,HeadingError(View,Actual));
            FHitResult Hit;
            FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimOcclusionFixture),false,Rig);
            const FVector Start=ChaseCamera->GetActorLocation();
            if(GetWorld()->LineTraceSingleByChannel(Hit,Start,Start+View*100000.,ECC_Visibility,Query)&&
                Hit.GetActor()==P.Occluder.Get())
            {
                const FVector Raw=Hit.ImpactPoint-Aim->GetComponentLocation();
                const double Depth=FVector::DotProduct(Raw,View.GetSafeNormal2D());
                P.MinimumRawPlanarDepth=FMath::Min(P.MinimumRawPlanarDepth,Depth);
                P.MaximumRawPlanarDepth=FMath::Max(P.MaximumRawPlanarDepth,Depth);
                if(Depth< -25.)++P.BehindRayFrames;
                if(Depth>100.)++P.FrontRayFrames;
                P.MaximumRawTargetYawError=FMath::Max(P.MaximumRawTargetYawError,HeadingError(View,Raw));
                if(!P.Samples.IsEmpty()&&P.Samples.Last()->AsObject()->GetNumberField(TEXT("seconds"))==P.Seconds)
                {
                    const auto Sample=P.Samples.Last()->AsObject();
                    Sample->SetArrayField(TEXT("raw_camera_hit_cm"),HeadingVector(Hit.ImpactPoint));
                    Sample->SetArrayField(TEXT("muzzle_position_cm"),HeadingVector(Aim->GetComponentLocation()));
                    Sample->SetArrayField(TEXT("chase_view_direction"),HeadingVector(View));
                    Sample->SetNumberField(TEXT("raw_planar_depth_cm"),Depth);
                    Sample->SetNumberField(TEXT("legacy_raw_target_yaw_error_deg"),HeadingError(View,Raw));
                }
            }
            if(!Input.Brake&&Input.Throttle>0&&P.PhaseSeconds>.3)
            {
                const auto V=Rig->GetVelocityCommandState()->GetArrayField(TEXT("requested_linear_m_s"));
                const FVector2D Requested(V[0]->AsNumber(),V[1]->AsNumber());
                const FVector2D Wanted(View.X,-View.Y);
                P.MinimumIntendedDriveDot=FMath::Min(P.MinimumIntendedDriveDot,
                    FVector2D::DotProduct(Requested.GetSafeNormal(),Wanted.GetSafeNormal()));
                ++P.IntendedDriveFrames;
            }
        };
        auto ApplyObserved=[&](){Apply();Observe();};
        const auto BehindAligned=[&](){return P.BehindRayFrames>30&&
            P.MaximumRawTargetYawError>150.&&P.PeakViewYawError<.5&&P.PeakFrameYawDelta<3.;};
        switch(P.Phase)
        {
        case 0:
            ApplyObserved();if(P.PhaseSeconds<1.5)return;
            Check(TEXT("occlusion_supported_manual_spawn"),bHadInputFocus&&!bRosControl&&!bRosAimControl&&
                Rig->GetCommandOriginVelocity().Size()<10&&Rig->GetActorUpVector().Z>.95);return;
        case 1:
            if(First)
            {
                Input.MouseX=FMath::FindDeltaAngleDegrees(OrbitYaw,170.)/MouseSensitivity;
                Input.MouseY=-OrbitPitch/MouseSensitivity;
            }
            ApplyObserved();if(P.PhaseSeconds<.8)return;
            Check(TEXT("occlusion_precondition_view_170_degrees"),!bSightMode&&!bFreeLook&&
                HeadingError(FRotator(0,170.,0).Vector(),Aim->GetForwardVector())<2.&&
                FMath::Abs(FMath::FindDeltaAngleDegrees(OrbitYaw,170.))<.001);return;
        case 2:
            if(First&&!PlacePlate(-150.)){Abort(TEXT("cannot build native occlusion fixture"));return;}
            if(First)Input.MouseX=.01/MouseSensitivity;
            ApplyObserved();if(P.PhaseSeconds<.8)return;
            Check(TEXT("behind_muzzle_camera_hit_preserves_forward_aim"),BehindAligned(),TEXT("occlusion-behind"));return;
        case 3:
            MouseYaw(30.);Input.MouseY=-31.25*DeltaSeconds/MouseSensitivity;
            ApplyObserved();if(P.PhaseSeconds<.8)return;
            Check(TEXT("occluded_mouse_right_down_crosses_yaw_seam"),BehindAligned()&&
                P.bCrossedYawSeam&&OrbitPitch< -24.&&OrbitYaw< -160.);return;
        case 4:
            MouseYaw(-40.);Input.MouseY=30.*DeltaSeconds/MouseSensitivity;
            ApplyObserved();if(P.PhaseSeconds<1.2)return;
            Check(TEXT("occluded_mouse_left_up_crosses_yaw_seam"),BehindAligned()&&
                P.bCrossedYawSeam&&OrbitPitch>10.&&OrbitYaw>140.);return;
        case 5:
            Input.FreeLook=true;MouseYaw(100.);
            if(P.PhaseSeconds>.2&&!P.bToggleSent){Input.ToggleSight=true;P.bToggleSent=true;}
            ApplyObserved();if(P.PhaseSeconds<.8)return;
            Check(TEXT("occluded_c_shift_keeps_gimbal_direction"),bFreeLook&&bSightMode&&
                PC->GetViewTarget()==ChaseCamera&&P.PeakHeadingError<.5&&P.PeakOrbitOffset>70.);return;
        case 6:
            ApplyObserved();if(P.PhaseSeconds<.6)return;
            Check(TEXT("occluded_c_release_returns_scope_without_flip"),!bFreeLook&&bSightMode&&
                PC->GetViewTarget()==SightCamera&&P.PeakHeadingError<.5);return;
        case 7:
            Input.ToggleSight=First;
            if(P.PhaseSeconds>.25){MouseYaw(10.);Input.MouseY=-10.*DeltaSeconds/MouseSensitivity;}
            ApplyObserved();if(P.PhaseSeconds<.8)return;
            Check(TEXT("occluded_scope_exit_mouse_resumes_without_flip"),!bFreeLook&&!bSightMode&&
                PC->GetViewTarget()==ChaseCamera&&BehindAligned(),TEXT("occlusion-return"));return;
        case 8:
            Input.Brake=false;SetMotion(.1,0);MouseYaw(2.);
            ApplyObserved();if(P.PhaseSeconds<.8)return;
            Check(TEXT("occluded_wasd_follows_forward_camera_intent"),BehindAligned()&&
                P.IntendedDriveFrames>30&&P.MinimumIntendedDriveDot>.9999&&Moved()>1.&&Moved()<15.);return;
        case 9:
            if(First&&!PlacePlate(300.)){Abort(TEXT("cannot place forward parallax fixture"));return;}
            if(First)Input.MouseX=.01/MouseSensitivity;
            ApplyObserved();if(P.PhaseSeconds<.8)return;
            {
                const FVector Desired=(TraceViewPoint(ChaseCamera->GetActorLocation(),
                    ChaseCamera->GetActorForwardVector())-Aim->GetComponentLocation()).GetSafeNormal();
                Check(TEXT("front_occluder_retains_real_parallax_aim"),P.FrontRayFrames>30&&
                    FVector::DotProduct(Desired,Aim->GetForwardVector())>FMath::Cos(FMath::DegreesToRadians(1.))&&
                    Rig->GetCommandOriginVelocity().Size()<10,TEXT("occlusion-front"));
            }
            return;
        default:Finish();return;
        }
    }

    if(P.bMapOnly)
    {
        Input.Brake=true;
        switch(P.Phase)
        {
        case 0:
            if(P.PhaseSeconds>1.5&&!P.bSetupMouseSent)
            {Input.MouseX=35./MouseSensitivity;P.bSetupMouseSent=true;}
            Apply();if(P.PhaseSeconds<2.2)return;
            Check(TEXT("map_arrow_tracks_actual_gimbal"),
                Rig->GetCommandOriginVelocity().Size()<10&&HeadingError(Rig->GetActorForwardVector(),Aim->GetForwardVector())>25,
                TEXT("map-gimbal"));return;
        case 1:
            Input.FreeLook=true;MouseYaw(100.);Apply();if(P.PhaseSeconds<1.2)return;
            Check(TEXT("map_arrow_ignores_c_orbit"),bFreeLook&&PC->GetViewTarget()==ChaseCamera&&
                P.PeakHeadingError<.5&&P.PeakOrbitOffset>90,TEXT("map-orbit"));return;
        case 2:
            Input.ToggleSight=First;
            if(!First)MouseYaw(25.);
            Apply();if(P.PhaseSeconds<.9)return;
            Check(TEXT("map_arrow_follows_scope_mouse"),bSightMode&&!bFreeLook&&
                PC->GetViewTarget()==SightCamera&&HeadingError(P.ReferenceDirection,Aim->GetForwardVector())>15,
                TEXT("map-scope"));return;
        case 3:
            Input.FreeLook=P.PhaseSeconds<.8;
            if(P.PhaseSeconds>.2&&!P.bToggleSent){Input.ToggleSight=true;P.bToggleSent=true;}
            if(Input.FreeLook)MouseYaw(-100.);
            Apply();if(P.PhaseSeconds<1.2)return;
            Check(TEXT("map_arrow_survives_nested_scope_exit"),!bFreeLook&&!bSightMode&&
                PC->GetViewTarget()==ChaseCamera&&P.PeakHeadingError<.5);return;
        default:Finish();return;
        }
    }

    switch(P.Phase)
    {
    case 0:
        Input.Brake=true;Apply();if(P.PhaseSeconds<1.5)return;
        Check(TEXT("camera_supported_spawn"),bHadInputFocus&&Rig->GetCommandOriginVelocity().Size()<10&&
            Rig->GetActorUpVector().Z>.95);return;
    case 1:
        Input.Brake=true;
        if(First){Input.MouseX=35./MouseSensitivity;Input.MouseY=(-25.-OrbitPitch)/MouseSensitivity;}
        Apply();if(P.PhaseSeconds<.6)return;
        P.NearTargetDistance=FVector::Distance(AimWorldTarget,Aim->GetComponentLocation());
        ControlReport->SetNumberField(TEXT("near_target_distance_cm"),P.NearTargetDistance);
        Check(TEXT("near_target_gimbal_fixture"),P.NearTargetDistance>100&&P.NearTargetDistance<700&&
            HeadingError(Rig->GetActorForwardVector(),Aim->GetForwardVector())>25,TEXT("heading-near-target"));return;
    case 2:
        Input.FreeLook=true;SetMotion(.35,.55);MouseYaw(70.);
        Apply();if(P.PhaseSeconds<2.)return;
        Check(TEXT("c_orbit_preserves_moving_gimbal_direction"),bFreeLook&&
            PC->GetViewTarget()==ChaseCamera&&StableMoving()&&P.PeakOrbitOffset>90,
            TEXT("heading-c-orbit"));return;
    case 3:
        SetMotion(.35,.55);Apply();if(P.PhaseSeconds<1.5)return;
        Check(TEXT("c_release_has_no_heading_snap"),!bFreeLook&&!bSightMode&&
            PC->GetViewTarget()==ChaseCamera&&StableMoving());return;
    case 4:
        SetMotion(.35,.55);Input.ToggleSight=First;Apply();if(P.PhaseSeconds<1.3)return;
        Check(TEXT("scope_entry_does_not_lock_world_point"),bSightMode&&!bFreeLook&&
            PC->GetViewTarget()==SightCamera&&StableMoving());return;
    case 5:
        SetMotion(.35,0);MouseYaw(30.);Apply();if(P.PhaseSeconds<1.)return;
        Check(TEXT("scope_mouse_continuously_turns_gimbal"),bSightMode&&
            HeadingError(P.ReferenceDirection,Aim->GetForwardVector())>25&&
            P.DriveFrames>20&&P.MinimumDriveDot>.9999,TEXT("heading-scope"));return;
    case 6:
        SetMotion(.3,.55);Apply();if(P.PhaseSeconds<1.4)return;
        Check(TEXT("scope_mouse_stop_keeps_angular_heading"),bSightMode&&StableMoving());return;
    case 7:
        SetMotion(.3,.55);Input.ToggleSight=First;Apply();if(P.PhaseSeconds<1.3)return;
        Check(TEXT("scope_exit_keeps_angular_heading"),!bSightMode&&!bFreeLook&&
            PC->GetViewTarget()==ChaseCamera&&StableMoving());return;
    case 8:
        Input.Brake=true;MouseYaw(-25.);Apply();if(P.PhaseSeconds<.8)return;
        Check(TEXT("chase_mouse_resumes_live_aim"),!bSightMode&&!bFreeLook&&
            HeadingError(P.ReferenceDirection,Aim->GetForwardVector())>12);return;
    case 9:
        Input.Brake=true;Input.FreeLook=true;MouseYaw(100.);
        if(P.PhaseSeconds>.2&&!P.bToggleSent){Input.ToggleSight=true;P.bToggleSent=true;}
        Apply();if(P.PhaseSeconds<1.1)return;
        Check(TEXT("c_then_shift_keeps_observation_independent"),bFreeLook&&bSightMode&&
            PC->GetViewTarget()==ChaseCamera&&P.PeakHeadingError<.5&&P.PeakOrbitOffset>90);return;
    case 10:
        Input.Brake=true;Apply();if(P.PhaseSeconds<.5)return;
        Check(TEXT("c_release_returns_to_live_scope"),!bFreeLook&&bSightMode&&
            PC->GetViewTarget()==SightCamera&&P.PeakHeadingError<.5);return;
    case 11:
        Input.Brake=true;Input.FreeLook=true;MouseYaw(-110.);
        if(P.PhaseSeconds>.2&&!P.bToggleSent){Input.ToggleSight=true;P.bToggleSent=true;}
        Apply();if(P.PhaseSeconds<1.)return;
        Check(TEXT("scope_then_c_shift_exit_keeps_heading"),bFreeLook&&!bSightMode&&
            PC->GetViewTarget()==ChaseCamera&&P.PeakHeadingError<.5);return;
    case 12:
        Input.FreeLook=true;Input.Turn=.2;MouseYaw(60.);
        Apply();if(P.PhaseSeconds<1.8)return;
        Check(TEXT("c_stabilizes_heading_during_hull_turn"),bFreeLook&&P.PeakHeadingError<.5&&
            FMath::Abs(FMath::FindDeltaAngleDegrees(P.StartHullYaw,Rig->GetActorRotation().Yaw))>15);return;
    case 13:
        Input.Brake=true;
        if(First){Apply();return;} // Release C through the shared live path.
        if(!P.bFocusLost)
        {
            P.StartViewYaw=OrbitYaw;Input.Focused=false;Input.MouseX=10000;Input.ToggleSight=true;
            Apply();P.bFocusLost=true;return;
        }
        if(!P.bRefocused)
        {
            Input.MouseX=10000;Apply();P.bRefocused=true;
            Check(TEXT("focus_restore_discards_stale_mouse"),bHadInputFocus&&!bFreeLook&&!bSightMode&&
                FMath::Abs(FMath::FindDeltaAngleDegrees(P.StartViewYaw,OrbitYaw))<.001);return;
        }
        return;
    case 14:
        Input.Brake=true;Input.FreeLook=true;Input.RestartRound=First;MouseYaw(110.);
        Apply();
        if(First)
        {
            // R creates a fresh pose before C snapshots its new actual direction.
            P.ReferenceDirection=Aim->GetForwardVector();P.PeakHeadingError=0;P.PeakOrbitOffset=0;
        }
        if(P.PhaseSeconds<1.2)return;
        Check(TEXT("reset_while_c_held_captures_new_direction"),
            ContactRecoveryCount==P.InitialRecoveries+1&&bFreeLook&&!bSightMode&&
            P.PeakHeadingError<.5&&P.PeakOrbitOffset>90&&
            !ManualControlState()->GetBoolField(TEXT("probe_world_aim_override")));return;
    case 15:
        Input.Brake=true;Input.RestartRound=First;
        Apply();if(P.PhaseSeconds<1.5)return;
        Check(TEXT("shared_reset_clears_camera_heading_state"),
            ContactRecoveryCount==P.InitialRecoveries+2&&!bFreeLook&&!bSightMode&&
            !ManualControlState()->GetBoolField(TEXT("hold_manual_aim_direction"))&&
            !ManualControlState()->GetBoolField(TEXT("probe_world_aim_override"))&&
            FVector::Dist2D(Rig->GetActorLocation(),ContactSpawn.GetLocation())<5&&
            Rig->GetCommandOriginVelocity().Size()<10,TEXT("heading-reset"));return;
    default:Finish();return;
    }
}
