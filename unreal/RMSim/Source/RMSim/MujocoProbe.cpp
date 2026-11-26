#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/Parse.h"
#include "Misc/CommandLine.h"

bool AImportLabGameMode::TickDedicatedMujocoProof()
{
#if WITH_DEV_AUTOMATION_TESTS
    if(FParse::Param(FCommandLine::Get(),TEXT("ManualGyroAimProof")))
    {
        auto* Aim = GetAimCapture();
        if (!Rig || Rig->GetDedicatedMujocoPeriod() <= 0 || !Rig->HasSteeringModules() || !Aim)
        { Abort(TEXT("Manual gyro aim proof requires dedicated MuJoCo, swerve and a gimbal")); return true; }

        const double Time = Rig->GetSimulationTimeSeconds();
        const double Step = FMath::Max(0., Time - DedicatedPreviousSimTime);
        DedicatedPreviousSimTime = Time;
        FManualControlInput Input;
        Input.ToggleSight = Time > .5 && !bSightMode;
        Input.ToggleGyro = Time > 1. && !bGyroMode;
        if (Time > 3.5 && Time < 5.5)
            Input.Throttle = .6 / Rig->GetDriveParameter(TEXT("MaxForwardSpeedMps"));
        if (Time > 3.5 && Time < 4.) Input.MouseY = 220. * Step;
        Input.FreeLook = Time > 5.5;
        if (Input.FreeLook && Time < 6.) Input.MouseX = 500. * Step;
        ApplyManualInput(Input, Step);
        Rig->SubmitDedicatedMujocoInputs();

        constexpr double SampleTimes[] = {2.7, 3., 3.3, 4.7, 5., 5.3, 6.1, 6.4, 6.7};
        if (Time >= SampleTimes[DedicatedMujocoProofPhase])
        {
            const FVector Target = Input.FreeLook ? HeldManualAimDirection : FRotator(OrbitPitch, OrbitYaw, 0).Vector();
            const double Error = FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(
                FVector::DotProduct(Target, Aim->GetForwardVector()), -1., 1.)));
            UE_LOG(LogRMSim, Display, TEXT("MANUAL_GYRO_AIM_PROOF sample=%d error_deg=%.6f chassis_rad_s=%.6f speed_cm_s=%.6f view_pitch_deg=%.6f"),
                DedicatedMujocoProofPhase, Error, Rig->GetMeasuredYawRate(), Rig->GetCommandOriginVelocity().Size2D(), OrbitPitch);
            if (++DedicatedMujocoProofPhase == UE_ARRAY_COUNT(SampleTimes))
            {
                bReady = false;
                FPlatformMisc::RequestExitWithStatus(false, 0);
            }
        }
        return true;
    }
    if(!FParse::Param(FCommandLine::Get(),TEXT("MujocoThreadProof")))return false;
    if(!Rig||Rig->GetDedicatedMujocoPeriod()<=0)
    {
        Abort(TEXT("MuJoCo thread proof requires the production dedicated solver"));return true;
    }
    const double Time=Rig->GetSimulationTimeSeconds();
    switch(DedicatedMujocoProofPhase)
    {
    case 0:
        if(Time>.5)Rig->SetContactDrive(.35,0,false);
        Rig->SubmitDedicatedMujocoInputs();
        if(Time<2.)return true;
        {
            uint64 Before=0,After=0,BeforeFailsafe=0,AfterFailsafe=0;
            const double Start=FPlatformTime::Seconds();
            if(!Rig->ReadDedicatedMujocoProgress(Before,BeforeFailsafe)||Rig->GetCommandOriginVelocity().Size2D()<15)
            {Abort(TEXT("Dedicated worker proof did not start from actual motion"));return true;}
            FPlatformProcess::SleepNoStats(.35f);
            const double Wall=FPlatformTime::Seconds()-Start;
            const bool Running=Rig->ReadDedicatedMujocoProgress(After,AfterFailsafe);
            const bool SnapshotHeld=Rig->GetSimulationTimeSeconds()==Time;
            const double Advanced=After>=Before?double(After-Before)*Rig->GetDynamicsStepSeconds():0.;
            UE_LOG(LogRMSim,Display,TEXT("MUJOCO_THREAD_PROOF_STALL wall_s=%.6f solved_s=%.6f steps=%llu snapshot_held=%s failsafes=%llu"),
                Wall,Advanced,After>=Before?After-Before:0,SnapshotHeld?TEXT("true"):TEXT("false"),AfterFailsafe-BeforeFailsafe);
            if(!Running||!SnapshotHeld||Advanced<Wall*.85||Advanced>Wall*1.15||AfterFailsafe!=BeforeFailsafe+1)
            {Abort(TEXT("Worker clock, snapshot or independent wall lease failed during the blocked game thread"));return true;}
        }
        Rig->SetContactDrive(0,0,true);
        ++DedicatedMujocoProofPhase;
        return true;
    case 1:
        Rig->SubmitDedicatedMujocoInputs();
        if(Time<3.2)return true;
        {
            const uint64 Before=Rig->GetMotionGeneration();
            ResetContactVehicle(TEXT("dedicated_thread_proof"),false);
            if(Rig->GetMotionGeneration()!=Before+1||Rig->GetSimulationTimeSeconds()!=0.)
            {Abort(TEXT("MuJoCo reset exposed time from a prior worker generation"));return true;}
        }
        ++DedicatedMujocoProofPhase;
        return true;
    default:
        Rig->SubmitDedicatedMujocoInputs();
        if(Time<.8)return true;
        {
            const auto State=Rig->GetContactState();
            double Oldest=0,Newest=0;int32 Count=0;
            const bool History=Rig->GetSensorPoseHistoryRange(Oldest,Newest,Count);
            const double Error=State->GetNumberField(TEXT("max_visual_body_position_error_cm"));
            if(!History||Count<480||Newest!=Time||Oldest<0||Rig->GetCommandOriginVelocity().Size2D()>2.||Error>.001)
            {Abort(TEXT("Post-reset history or body snapshot is inconsistent"));return true;}
            UE_LOG(LogRMSim,Display,TEXT("MUJOCO_THREAD_PROOF_PASSED generation=%llu solver_s=%.6f history_samples=%d visual_error_cm=%.9f"),
                Rig->GetMotionGeneration(),Time,Count,Error);
        }
        bReady=false;
        FPlatformMisc::RequestExitWithStatus(false,0);
        return true;
    }
#else
    return false;
#endif
}

void AImportLabGameMode::TickMujocoProbe(double DeltaSeconds)
{
    if(!Rig||!Rig->IsMujocoPhysics()){Abort(TEXT("MuJoCo probe requires the native MuJoCo backend"));return;}
    UpdatePlayerView(GetWorld()->GetFirstPlayerController());
    if(!ControlPendingCapture.IsEmpty())
    {
        Rig->SetContactDrive(0,0,true);
        if(++ControlCaptureWarmFrames<12)return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("MuJoCo probe capture failed"));return;}
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    ContactPhaseTime+=DeltaSeconds;
    const auto Dynamics=Rig->GetContactState();
    const auto State=ManualControlState();
    if(ContactSamples.IsEmpty()||ContactPhaseTime-FMath::Floor((ContactPhaseTime-DeltaSeconds)*4)/4>=.25)
    {
        auto Sample=MakeShared<FJsonObject>();Sample->SetNumberField(TEXT("phase"),ControlProbePhase);
        Sample->SetNumberField(TEXT("phase_time_s"),ContactPhaseTime);
        Sample->SetNumberField(TEXT("solver_time_s"),Rig->GetSimulationTimeSeconds());
        Sample->SetObjectField(TEXT("state"),State);Sample->SetObjectField(TEXT("dynamics"),Dynamics);
        ContactSamples.Add(MakeShared<FJsonValueObject>(Sample));ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    }
    double Warnings=0,ChaosBodies=0;
    if(!Dynamics->TryGetNumberField(TEXT("warning_count"),Warnings)||Warnings!=0||
       !Dynamics->TryGetNumberField(TEXT("chaos_simulating_bodies"),ChaosBodies)||ChaosBodies!=0)
    {Abort(TEXT("MuJoCo warnings or competing Chaos bodies detected"));return;}
    if(Rig->GetActorTransform().ContainsNaN()||Rig->GetVelocity().ContainsNaN()||
       Rig->GetActorUpVector().Z<.6||Rig->GetActorLocation().Z<ContactSpawn.GetLocation().Z-80)
    {Abort(TEXT("MuJoCo vehicle escaped its physical envelope"));return;}
    double RouteCm=100;FParse::Value(FCommandLine::Get(),TEXT("MujocoRouteCm="),RouteCm);
    if(!FMath::IsFinite(RouteCm)||RouteCm<20||RouteCm>1200){Abort(TEXT("invalid MuJoCo route length"));return;}
    const FVector Forward=ContactStart.GetRotation().GetForwardVector().GetSafeNormal2D();
    const FVector Side=FVector::CrossProduct(FVector::UpVector,Forward);
    const FVector Delta=Rig->GetActorLocation()-ContactStart.GetLocation();
    const double Progress=FVector::DotProduct(Delta,Forward),Drift=FVector::DotProduct(Delta,Side);
    const double Direction=ControlProbePhase==3?-1.:1.;
    const double DesiredYaw=ContactStart.Rotator().Yaw+FMath::RadiansToDegrees(FMath::Atan2(-Direction*Drift,60.));
    const double YawError=FMath::FindDeltaAngleDegrees(Rig->GetActorRotation().Yaw,DesiredYaw);
    const double Steer=FMath::Clamp(-FMath::DegreesToRadians(YawError)*4,-1.5,1.5);
    const double Drive=.8*FMath::Clamp(1-FMath::Abs(YawError)/45.,0.,1.);
    FString Name,Detail;bool Passed=false;
    if(ControlProbePhase>=9)
    {
        ControlReport->SetStringField(TEXT("physics_backend"),Rig->GetPhysicsBackendName());
        ControlReport->SetNumberField(TEXT("dynamics_step_s"),Rig->GetDynamicsStepSeconds());
        ControlReport->SetBoolField(TEXT("os_input_verified"),false);
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot write MuJoCo probe result"));return;}
        UE_LOG(LogRMSim,Display,TEXT("MUJOCO_PROBE_COMPLETE checks=%d"),ControlChecks.Num());
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
    switch(ControlProbePhase)
    {
    case 0:
    {
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<3.)return;
        double Contacts=0,NormalForce=0,Steps=0;
        Dynamics->TryGetNumberField(TEXT("contact_count"),Contacts);
        Dynamics->TryGetNumberField(TEXT("normal_force_N"),NormalForce);
        Dynamics->TryGetNumberField(TEXT("step_count"),Steps);
        Name=TEXT("mujoco_settle_with_real_contact");
        Detail=FString::Printf(TEXT("steps=%.0f contacts=%.0f normal_force_N=%.6f speed_cm_s=%.6f up_z=%.6f"),
            Steps,Contacts,NormalForce,Rig->GetVelocity().Size(),Rig->GetActorUpVector().Z);
        Passed=Steps>100&&Contacts>0&&NormalForce>5&&Rig->GetVelocity().Size()<5&&Rig->GetActorUpVector().Z>.95;
        ContactStart=Rig->GetActorTransform();ControlPendingCapture=TEXT("mujoco-rest");break;
    }
    case 1:
        Rig->SetContactDrive(Drive,Steer,false);
        if(Progress<RouteCm&&ContactPhaseTime<25)return;
        Rig->SetContactDrive(0,0,true);
        Name=TEXT("mujoco_wheel_contact_forward");
        Detail=FString::Printf(TEXT("progress_cm=%.6f target_cm=%.6f drift_cm=%.6f duration_s=%.6f"),Progress,RouteCm,Drift,ContactPhaseTime);
        Passed=Progress>=RouteCm&&FMath::Abs(Drift)<25;
        ControlPendingCapture=TEXT("mujoco-forward");break;
    case 2:
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<1.5)return;
        Name=TEXT("mujoco_physical_brake");
        Detail=FString::Printf(TEXT("measured_speed_cm_s=%.6f"),Rig->GetVelocity().Size());
        Passed=Rig->GetVelocity().Size()<5;break;
    case 3:
        Rig->SetContactDrive(-Drive,Steer,false);
        if(Progress>8&&ContactPhaseTime<25)return;
        Rig->SetContactDrive(0,0,true);
        Name=TEXT("mujoco_wheel_contact_reverse");
        Detail=FString::Printf(TEXT("remaining_cm=%.6f drift_cm=%.6f"),Progress,Drift);
        Passed=Progress<=8&&FMath::Abs(Drift)<25;ControlPendingCapture=TEXT("mujoco-return");break;
    case 4:
    case 5:
    {
        const double Sign=ControlProbePhase==4?1.:-1.;
        Rig->SetContactPlanarDrive(FVector2D::ZeroVector,Sign*.8,false,false);
        if(ContactPhaseTime<1.5)return;
        const double Turn=-FMath::DegreesToRadians(FMath::FindDeltaAngleDegrees(ContactStageStart.Rotator().Yaw,Rig->GetActorRotation().Yaw));
        Rig->SetContactDrive(0,0,true);
        Name=ControlProbePhase==4?TEXT("mujoco_positive_yaw"):TEXT("mujoco_negative_yaw");
        Detail=FString::Printf(TEXT("actual_ros_yaw_change_rad=%.6f requested_sign=%.0f"),Turn,Sign);
        Passed=Sign*Turn>.15&&Sign*Turn<2.5;break;
    }
    case 6:
        Rig->SetContactPlanarDrive(FVector2D(0,.4),0,true,false);
        if(ContactPhaseTime<1.6)return;
        Rig->SetContactDrive(0,0,true);
        Name=TEXT("mujoco_swerve_lateral_contact");
        Detail=FString::Printf(TEXT("actual_ros_left_cm=%.6f"),-(Rig->GetActorLocation().Y-ContactStageStart.GetLocation().Y));
        Passed=-(Rig->GetActorLocation().Y-ContactStageStart.GetLocation().Y)>8;break;
    case 7:
    {
        Rig->SetContactDrive(0,0,true);FString Error;
        if(!Rig->SetGimbal(.4,-.1,Error)){Abort(Error);return;}
        if(ContactPhaseTime<1.5)return;
        double Yaw=0,Pitch=0;Rig->GetGimbalAngles(Yaw,Pitch);
        FTransform Parent;const bool HasParent=Rig->GetSensorParentWorld(TEXT("yaw_link"),Parent);
        Name=TEXT("mujoco_articulated_gimbal_and_sensor_mount");
        Detail=FString::Printf(TEXT("measured_yaw_rad=%.6f measured_pitch_rad=%.6f parent_valid=%d"),Yaw,Pitch,HasParent?1:0);
        Passed=HasParent&&FMath::Abs(Yaw-.4)<.035&&FMath::Abs(Pitch+.1)<.035;
        ControlPendingCapture=TEXT("mujoco-gimbal");break;
    }
    case 8:
    {
        FManualControlInput Input;Input.Brake=true;Input.RestartRound=ContactRecoveryCount==0;
        ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<3.)return;
        const double Distance=FVector::Dist2D(Rig->GetActorLocation(),ContactSpawn.GetLocation());
        Name=TEXT("mujoco_shared_R_resets_solver_and_commands");
        Detail=FString::Printf(TEXT("resets=%d distance_cm=%.6f measured_speed_cm_s=%.6f motion_generation=%llu"),
            ContactRecoveryCount,Distance,Rig->GetVelocity().Size(),Rig->GetMotionGeneration());
        Passed=ContactRecoveryCount==1&&Rig->GetMotionGeneration()==1&&Distance<5&&Rig->GetVelocity().Size()<5;
        ControlPendingCapture=TEXT("mujoco-reset");break;
    }
    }
    if(!RecordControlCheck(Name,Passed,State,Detail))return;
    ++ControlProbePhase;ContactPhaseTime=0;ContactStageStart=Rig->GetActorTransform();
}
