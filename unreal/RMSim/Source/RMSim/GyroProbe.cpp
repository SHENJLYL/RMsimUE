#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

void AImportLabGameMode::TickGyroProbe(double DeltaSeconds)
{
    if(!Rig||!Rig->IsContactPhysics()||!Rig->HasSteeringModules()){Abort(TEXT("gyro probe requires the physical swerve rig"));return;}
    if(FParse::Param(FCommandLine::Get(),TEXT("SteeringResponse"))&&ControlProbePhase==0)
    {ControlProbePhase=14;ContactPhaseTime=0;ContactStageStart=Rig->GetActorTransform();}
    auto* PC=GetWorld()->GetFirstPlayerController();
    UpdatePlayerView(PC);
    if(!ControlPendingCapture.IsEmpty())
    {
        if(++ControlCaptureWarmFrames<24)return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("gyro capture failed"));return;}
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    const bool Route=FParse::Param(FCommandLine::Get(),TEXT("GyroRoute"));
    const bool PrecisionTrace=FParse::Param(FCommandLine::Get(),TEXT("PrecisionTrace"));
    double ResponseSpeed=.6,ResponseSpin=0;
    FParse::Value(FCommandLine::Get(),TEXT("ResponseSpeedMps="),ResponseSpeed);
    FParse::Value(FCommandLine::Get(),TEXT("ResponseSpinRadS="),ResponseSpin);
    if(!FMath::IsFinite(ResponseSpeed)||ResponseSpeed<.1||ResponseSpeed>Rig->GetDriveParameter(TEXT("MaxLateralSpeedMps"))||
        !FMath::IsFinite(ResponseSpin)||ResponseSpin<0||ResponseSpin>Rig->GetDriveParameter(TEXT("GyroMaxRadS")))
    {Abort(TEXT("response probe speed outside vehicle profile"));return;}
    ContactPhaseTime+=DeltaSeconds;
    GyroAccumulatedAngle+=Rig->GetMeasuredYawRate()*DeltaSeconds;
    GyroMinimumUp=FMath::Min(GyroMinimumUp,Rig->GetActorUpVector().Z);
    const auto State=ManualControlState();
    const auto Contact=State->GetObjectField(TEXT("contact"));
    const auto Filtered=Contact->GetArrayField(TEXT("filtered_world_linear_m_s"));
    const FVector2D Velocity(Filtered[0]->AsNumber(),Filtered[1]->AsNumber());
    if(bGyroPreviousValid&&DeltaSeconds>1.e-5)GyroPeakCommandAcceleration=FMath::Max(GyroPeakCommandAcceleration,(Velocity-GyroPreviousFiltered).Size()/DeltaSeconds);
    GyroPreviousFiltered=Velocity;bGyroPreviousValid=true;
    if(ControlProbePhase==15||ControlProbePhase==17||(PrecisionTrace&&ControlProbePhase==18)||ContactSamples.IsEmpty()||ContactPhaseTime-FMath::Floor((ContactPhaseTime-DeltaSeconds)*4)/4>=.25)
    {
        auto Sample=MakeShared<FJsonObject>();Sample->SetNumberField(TEXT("phase"),ControlProbePhase);Sample->SetNumberField(TEXT("phase_time"),ContactPhaseTime);
        Sample->SetObjectField(TEXT("state"),State);ContactSamples.Add(MakeShared<FJsonValueObject>(Sample));ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    }
    if(Rig->GetActorTransform().ContainsNaN()||Rig->GetActorLocation().Z<ContactSpawn.GetLocation().Z-200||GyroMinimumUp<.4)
    {CaptureControlView(TEXT("gyro-failed"));Abort(TEXT("gyro vehicle lost upright support"));return;}
    const FVector Forward=ContactSpawn.GetRotation().GetForwardVector().GetSafeNormal2D();
    const FVector Left=-FVector::CrossProduct(FVector::UpVector,Forward);
    const FVector StageDelta=Rig->GetActorLocation()-ContactStageStart.GetLocation();
    const FVector RouteDelta=Rig->GetActorLocation()-ContactStart.GetLocation();
    const double Along=FVector::DotProduct(StageDelta,Forward),Across=FVector::DotProduct(StageDelta,Left);
    const double Progress=FVector::DotProduct(RouteDelta,Forward),Drift=FVector::DotProduct(RouteDelta,Left);
    FManualControlInput Input;FString Name,Detail;bool Passed=false;
    auto Finish=[&](){if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot save gyro result"));return;}
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);};
    if(Route)
    {
        double Distance=340,Speed=.35,Spin=2;FParse::Value(FCommandLine::Get(),TEXT("ContactDistanceCm="),Distance);FParse::Value(FCommandLine::Get(),TEXT("ContactSpeedMps="),Speed);
        const bool PrecisionKeys=FParse::Param(FCommandLine::Get(),TEXT("PrecisionKeys"));
        if(PrecisionKeys)Speed=Rig->GetDriveParameter(TEXT("PrecisionSpeedMps"));
        ControlReport->SetNumberField(TEXT("gyro_route_speed_m_s"),Speed);ControlReport->SetBoolField(TEXT("precision_keys"),PrecisionKeys);
        FParse::Value(FCommandLine::Get(),TEXT("GyroSpinRadS="),Spin);
        if(!FMath::IsFinite(Spin)||Spin<Rig->GetDriveParameter(TEXT("GyroMinRadS"))||Spin>Rig->GetDriveParameter(TEXT("GyroMaxRadS")))
        {Abort(TEXT("gyro route spin outside vehicle profile"));return;}
        ControlReport->SetNumberField(TEXT("gyro_route_spin_rad_s"),Spin);
        if(!FMath::IsFinite(Speed)||Speed<=0||Speed>3){Abort(TEXT("invalid gyro route speed"));return;}
        switch(ControlProbePhase)
        {
        case 0:
            Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<3)return;
            Name=TEXT("gyro_supported_spawn");Passed=Rig->GetVelocity().Size()<15&&GyroMinimumUp>.9;
            ContactStart=Rig->GetActorTransform();ControlPendingCapture=TEXT("gyro-start");break;
        case 1:
            Input.ToggleGyro=!bGyroMode;Input.GyroSpeedSteps=GyroSpeedRadS<Spin-.01?1:GyroSpeedRadS>Spin+.01?-1:0;
            ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2.5)return;
            Name=TEXT("gyro_spin_up");Passed=bGyroMode&&Rig->GetMeasuredYawRate()>Spin*.75&&GyroMinimumUp>.8;break;
        case 2:
        case 4:
        {
            const bool Reverse=ControlProbePhase==4;
            Input.Precision=PrecisionKeys;
            Input.Throttle=PrecisionKeys?(Reverse?-1:1):(Reverse?-1:1)*Speed/Rig->GetDriveParameter(Reverse?TEXT("MaxReverseSpeedMps"):TEXT("MaxForwardSpeedMps"));
            Input.Strafe=FMath::Clamp(-Drift*.006/Rig->GetDriveParameter(PrecisionKeys?TEXT("PrecisionSpeedMps"):TEXT("MaxLateralSpeedMps")),-.3,.3);
            ApplyManualInput(Input,DeltaSeconds);
            if((Reverse?Progress>10:Progress<Distance)&&ContactPhaseTime<40)return;
            Name=Reverse?TEXT("gyro_reverse_wave_route"):TEXT("gyro_forward_wave_route");
            Detail=FString::Printf(TEXT("progress %.3fcm lateral %.3fcm body spin %.3frad minimum upright %.4f"),Progress,Drift,GyroAccumulatedAngle,GyroMinimumUp);
            Passed=(Reverse?Progress<=10:Progress>=Distance)&&FMath::Abs(Drift)<35&&GyroAccumulatedAngle>2*PI&&GyroMinimumUp>.7;
            Rig->SetContactDrive(0,0,true);ControlPendingCapture=Reverse?TEXT("gyro-return"):TEXT("gyro-end");break;
        }
        case 3:
            Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
            Name=TEXT("gyro_route_brake");Passed=Rig->GetVelocity().Size()<10&&FMath::Abs(Rig->GetMeasuredYawRate())<.15;break;
        default:Finish();return;
        }
    }
    else switch(ControlProbePhase)
    {
    case 0:
        Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<3)return;
        Name=TEXT("gyro_supported_spawn");Passed=Rig->GetVelocity().Size()<15&&GyroMinimumUp>.9;ControlPendingCapture=TEXT("gyro-start");break;
    case 1:
        Input.Throttle=.3;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        Name=TEXT("normal_camera_forward_smooth");
        Detail=FString::Printf(TEXT("forward %.3fcm lateral %.3fcm peak command acceleration %.4fm/s2"),Along,Across,GyroPeakCommandAcceleration);
        Passed=Along>65&&FMath::Abs(Across)<15&&GyroPeakCommandAcceleration<.86;break;
    case 2:
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<1.2)return;
        Name=TEXT("released_keys_active_brake");Detail=FString::Printf(TEXT("coast distance %.3fcm, speed %.3fcm/s"),StageDelta.Size2D(),Rig->GetVelocity().Size());
        Passed=StageDelta.Size2D()<40&&Rig->GetVelocity().Size()<5;break;
    case 3:
        Input.Strafe=.3;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        Name=TEXT("normal_camera_left_strafe");Detail=FString::Printf(TEXT("left %.3fcm forward %.3fcm"),Across,Along);
        Passed=Across>50&&FMath::Abs(Along)<20;break;
    case 4:
        Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<1.5)return;
        Name=TEXT("normal_strafe_brake");Passed=Rig->GetVelocity().Size()<5;break;
    case 5:
        Input.ToggleGyro=!bGyroMode;Input.Throttle=.2;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<6)return;
        Name=TEXT("gyro_camera_forward_direction");Detail=FString::Printf(TEXT("forward %.3fcm lateral %.3fcm body spin %.3frad"),Along,Across,GyroAccumulatedAngle);
        Passed=bGyroMode&&Along>200&&FMath::Abs(Across)<35&&GyroAccumulatedAngle>2*PI&&GyroMinimumUp>.85;
        ControlPendingCapture=TEXT("gyro-moving");break;
    case 6:
        Input.GyroSpeedSteps=GyroSpeedRadS<2.4?1:0;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        Name=TEXT("gyro_faster_key");Detail=FString::Printf(TEXT("setpoint %.3f actual %.3frad/s"),GyroSpeedRadS,Rig->GetMeasuredYawRate());
        Passed=FMath::IsNearlyEqual(GyroSpeedRadS,2.5,.01)&&Rig->GetMeasuredYawRate()>2.0;break;
    case 7:
        Input.GyroSpeedSteps=-1;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        Name=TEXT("gyro_slower_minimum");Detail=FString::Printf(TEXT("setpoint %.3f actual %.3frad/s"),GyroSpeedRadS,Rig->GetMeasuredYawRate());
        Passed=FMath::IsNearlyEqual(GyroSpeedRadS,Rig->GetDriveParameter(TEXT("GyroMinRadS")),.001)&&FMath::Abs(Rig->GetMeasuredYawRate()-GyroSpeedRadS)<.25;break;
    case 8:
        Input.GyroSpeedSteps=1;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2.5)return;
        Name=TEXT("gyro_faster_maximum");Detail=FString::Printf(TEXT("setpoint %.3f actual %.3frad/s"),GyroSpeedRadS,Rig->GetMeasuredYawRate());
        Passed=FMath::IsNearlyEqual(GyroSpeedRadS,Rig->GetDriveParameter(TEXT("GyroMaxRadS")),.001)&&Rig->GetMeasuredYawRate()>3.;break;
    case 9:
    {
        Input.MouseY=ContactPhaseTime<.5?200*DeltaSeconds:0;
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        const FVector Wanted=(AimWorldTarget-GetAimCapture()->GetComponentLocation()).GetSafeNormal();
        const double Error=FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(FVector::DotProduct(Wanted,GetAimCapture()->GetForwardVector()),-1.,1.)));
        Name=TEXT("gyro_mouse_pitch_world_aim");Detail=FString::Printf(TEXT("pitch %.3fdeg, muzzle direction error %.3fdeg"),OrbitPitch,Error);
        Passed=OrbitPitch>-9&&Error<2.5;ControlPendingCapture=TEXT("gyro-aim");break;
    }
    case 10:
        Input.ToggleGyro=bGyroMode;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        Name=TEXT("gyro_toggle_off_brakes_spin");Passed=!bGyroMode&&FMath::Abs(Rig->GetMeasuredYawRate())<.1&&Rig->GetVelocity().Size()<5;break;
    case 11:
        Input.MouseX=OrbitYaw<80?90/MouseSensitivity:0;Input.Throttle=.2;
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        Name=TEXT("normal_forward_after_camera_turn");Detail=FString::Printf(TEXT("Camera yaw %.1f; original forward %.3fcm original left %.3fcm"),OrbitYaw,Along,Across);
        Passed=FMath::Abs(OrbitYaw-90)<.01&&Across< -50&&FMath::Abs(Along)<20;break;
    case 12:
        ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<1.2)return;
        Name=TEXT("camera_turn_release_brake");Passed=Rig->GetVelocity().Size()<5;break;
    case 13:
        Input.Strafe=-.3;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        Name=TEXT("normal_right_after_camera_turn");Detail=FString::Printf(TEXT("Camera-relative right: original forward %.3fcm original left %.3fcm"),Along,Across);
        Passed=Along< -50&&FMath::Abs(Across)<20;ControlPendingCapture=TEXT("gyro-camera-relative");break;
    case 14:
        if(ContactPhaseTime<=DeltaSeconds)ResetContactVehicle(TEXT("response_probe_setup"),false);
        Input.Brake=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<1.5)return;
        Name=TEXT("steering_response_rest_pose");Passed=Rig->GetVelocity().Size()<5;break;
    case 15:
    {
        if(SteeringCommandPhaseSeconds<0)
        {SteeringCommandPhaseSeconds=ContactPhaseTime;ControlReport->SetNumberField(TEXT("steering_command_phase_time_s"),SteeringCommandPhaseSeconds);}
        const double ResponseElapsed=ContactPhaseTime-SteeringCommandPhaseSeconds;
        Input.Strafe=.3;ApplyManualInput(Input,DeltaSeconds);
        double MinimumTarget=PI,MinimumActual=PI,MaximumWheelCommand=0;
        for(const auto& Value:Contact->GetArrayField(TEXT("wheels")))
        {
            const auto Wheel=Value->AsObject();
            MinimumTarget=FMath::Min(MinimumTarget,FMath::Abs(Wheel->GetNumberField(TEXT("steering_target_rad"))));
            MinimumActual=FMath::Min(MinimumActual,FMath::Abs(Wheel->GetNumberField(TEXT("steering_measured_rad"))));
            MaximumWheelCommand=FMath::Max(MaximumWheelCommand,FMath::Abs(Wheel->GetNumberField(TEXT("wheel_feedforward_rad_s"))));
        }
        if(SteeringTargetResponseSeconds<0&&MinimumTarget>.01)SteeringTargetResponseSeconds=ResponseElapsed;
        if(SteeringActualResponseSeconds<0&&MinimumActual>FMath::DegreesToRadians(80.))SteeringActualResponseSeconds=ResponseElapsed;
        if(WheelCommandResponseSeconds<0&&MaximumWheelCommand>1.e-5)WheelCommandResponseSeconds=ResponseElapsed;
        if(ResponseElapsed<.5)return;
        Name=TEXT("steering_step_response");Detail=FString::Printf(TEXT("90deg lateral step: target start %.1fms, wheel command %.1fms, all modules >=80deg %.1fms; actual world ticks, not OS latency"),
            SteeringTargetResponseSeconds*1000,WheelCommandResponseSeconds*1000,SteeringActualResponseSeconds*1000);
        Passed=SteeringTargetResponseSeconds>0&&SteeringTargetResponseSeconds<.06&&WheelCommandResponseSeconds>0&&WheelCommandResponseSeconds<.06&&
            SteeringActualResponseSeconds>0&&SteeringActualResponseSeconds<.18;
        ControlPendingCapture=TEXT("gyro-response");break;
    }
    case 16:
        Input.Strafe=ResponseSpeed/Rig->GetDriveParameter(TEXT("MaxLateralSpeedMps"));
        Input.ToggleGyro=ResponseSpin>0&&!bGyroMode;
        if(ResponseSpin>0)Input.GyroSpeedSteps=GyroSpeedRadS<ResponseSpin-.01?1:GyroSpeedRadS>ResponseSpin+.01?-1:0;
        ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<FMath::Max(1.,(ResponseSpeed-.6)/Rig->GetDriveParameter(TEXT("LinearAccelerationMps2"))+2))return;
        Name=TEXT("moving_turn_initial_velocity");
        Passed=Velocity.Y>ResponseSpeed-.05&&Rig->GetCommandOriginVelocity().Size2D()/100>ResponseSpeed*.85&&
            (ResponseSpin==0||FMath::Abs(Rig->GetMeasuredYawRate()-ResponseSpin)<.3);break;
    case 17:
    {
        if(MovingCommandPhaseSeconds<0)MovingCommandPhaseSeconds=ContactPhaseTime;
        const double ResponseElapsed=ContactPhaseTime-MovingCommandPhaseSeconds;
        Input.Throttle=ResponseSpeed/Rig->GetDriveParameter(TEXT("MaxForwardSpeedMps"));ApplyManualInput(Input,DeltaSeconds);
        const FVector Actual=Rig->GetCommandOriginVelocity()/100;
        if(MovingTargetResponseSeconds<0&&Velocity.X>.3&&FMath::Abs(FMath::Atan2(Velocity.Y,Velocity.X))<FMath::DegreesToRadians(10.))MovingTargetResponseSeconds=ResponseElapsed;
        if(MovingActualResponseSeconds<0&&Actual.X>.3&&FMath::Abs(FMath::Atan2(Actual.Y,Actual.X))<FMath::DegreesToRadians(15.))MovingActualResponseSeconds=ResponseElapsed;
        if(ResponseElapsed<1.5)return;
        const double FinalHeadingError=FMath::RadiansToDegrees(FMath::Abs(FMath::Atan2(Actual.Y,Actual.X)));
        Name=TEXT("moving_direction_response");Detail=FString::Printf(TEXT("%.2fm/s, %.2frad/s spin, moving 90deg input change: target heading within 10deg %.1fms; actual velocity within 15deg %.1fms; settled error %.2fdeg, speed %.3fm/s"),ResponseSpeed,ResponseSpin,MovingTargetResponseSeconds*1000,MovingActualResponseSeconds*1000,FinalHeadingError,Actual.Size2D());
        ControlReport->SetNumberField(TEXT("moving_target_response_s"),MovingTargetResponseSeconds);
        ControlReport->SetNumberField(TEXT("moving_actual_response_s"),MovingActualResponseSeconds);
        ControlReport->SetNumberField(TEXT("response_speed_m_s"),ResponseSpeed);
        ControlReport->SetNumberField(TEXT("response_spin_rad_s"),ResponseSpin);
        Passed=MovingTargetResponseSeconds>0&&MovingTargetResponseSeconds<.4&&MovingActualResponseSeconds>0&&MovingActualResponseSeconds<.5&&
            FinalHeadingError<5&&FMath::Abs(Actual.Size2D()-ResponseSpeed)<ResponseSpeed*.15;
        ControlPendingCapture=TEXT("moving-response");break;
    }
    case 18:
    {
        Input.Throttle=1;Input.Precision=true;ApplyManualInput(Input,DeltaSeconds);if(ContactPhaseTime<2)return;
        if(!ControlReport->HasField(TEXT("precision_at_2s_speed_m_s")))
            ControlReport->SetNumberField(TEXT("precision_at_2s_speed_m_s"),Rig->GetCommandOriginVelocity().Size2D()/100);
        // Diagnostic extension retains the original two-second acceptance point.
        if(PrecisionTrace&&ContactPhaseTime<6)return;
        const double AtTwoSeconds=ControlReport->GetNumberField(TEXT("precision_at_2s_speed_m_s"));
        Name=TEXT("precision_key_speed_cap");Detail=FString::Printf(TEXT("Ctrl + full throttle: actual at 2s %.4fm/s, configured cap %.4fm/s"),AtTwoSeconds,Rig->GetDriveParameter(TEXT("PrecisionSpeedMps")));
        Passed=bPrecisionDrive&&FMath::Abs(Velocity.Size()-Rig->GetDriveParameter(TEXT("PrecisionSpeedMps")))<.01&&
            FMath::Abs(AtTwoSeconds-Rig->GetDriveParameter(TEXT("PrecisionSpeedMps")))<.03;break;
    }
    case 19:
    {
        if(!PrecisionTrace){Finish();return;}
        double PeakSpeedError=0,VectorErrorSquared=0,FirstTime=-1,LastTime=0,MaximumGap=0;
        int32 Count=0;
        const double Cap=Rig->GetDriveParameter(TEXT("PrecisionSpeedMps"));
        for(const auto& Value:ContactSamples)
        {
            const auto Sample=Value->AsObject();const double Time=Sample->GetNumberField(TEXT("phase_time"));
            if(Sample->GetNumberField(TEXT("phase"))!=18||Time<2)continue;
            const auto Chassis=Sample->GetObjectField(TEXT("state"))->GetObjectField(TEXT("contact"))->GetObjectField(TEXT("chassis"));
            const auto V=Chassis->GetArrayField(TEXT("origin_velocity_cm_s"));
            const FVector2D Actual(V[0]->AsNumber()/100,V[1]->AsNumber()/100);
            PeakSpeedError=FMath::Max(PeakSpeedError,FMath::Abs(Actual.Size()-Cap));
            VectorErrorSquared+=(Actual-FVector2D(Cap,0)).SizeSquared();
            if(FirstTime<0)FirstTime=Time;else MaximumGap=FMath::Max(MaximumGap,Time-LastTime);
            LastTime=Time;++Count;
        }
        const double VectorRms=Count?FMath::Sqrt(VectorErrorSquared/Count):MAX_dbl;
        Name=TEXT("precision_steady_spin");
        Detail=FString::Printf(TEXT("%d real frames over %.3fs: peak speed error %.5fm/s, world velocity RMS error %.5fm/s, max gap %.4fs"),Count,LastTime-FirstTime,PeakSpeedError,VectorRms,MaximumGap);
        Passed=Count>=30&&LastTime-FirstTime>3.9&&MaximumGap<.1&&PeakSpeedError<.03&&VectorRms<.025;
        break;
    }
    default:Finish();return;
    }
    if(Detail.IsEmpty())Detail=FString::Printf(TEXT("phase %.3fs speed %.3fcm/s yaw %.3frad/s upright %.4f"),ContactPhaseTime,Rig->GetVelocity().Size(),Rig->GetMeasuredYawRate(),GyroMinimumUp);
    if(!Passed){Rig->SetContactDrive(0,0,true);CaptureControlView(TEXT("gyro-failed"));}
    if(RecordControlCheck(Name,Passed,State,Detail))
    {
        ++ControlProbePhase;ContactPhaseTime=0;ContactStageStart=Rig->GetActorTransform();
        GyroAccumulatedAngle=0;GyroMinimumUp=1;GyroPeakCommandAcceleration=0;bGyroPreviousValid=false;
    }
}
