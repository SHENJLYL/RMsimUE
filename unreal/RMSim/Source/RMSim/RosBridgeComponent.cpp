#include "RosBridgeComponent.h"
#include "RMSimViewportClient.h"
#include "VehicleRig.h"
#include "RosAimController.h"
#include "RosImuComponent.h"
#include "RosLidarComponent.h"
#include "Misc/SecureHash.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/World.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformProcess.h"
#include "IPAddress.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Misc/App.h"
#include "Physics/Experimental/PhysScene_Chaos.h"
#include "PBDRigidsSolver.h"
#include "PhysicsEngine/PhysicsSettings.h"
#include "RenderingThread.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

namespace
{
constexpr double MaxInteger=9007199254740991.;
TArray<TSharedPtr<FJsonValue>> Array(const FVector& V)
{return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)};}
TSharedRef<FJsonObject> RosPose(const FTransform& Pose,bool Optical=false)
{
    const FVector P=Pose.GetLocation()/100;const FQuat U=Pose.GetRotation();
    FQuat Q(-U.X,U.Y,-U.Z,U.W);
    if(Optical)Q=Q*FQuat(-.5,.5,-.5,.5);
    Q.Normalize();auto J=MakeShared<FJsonObject>();J->SetArrayField(TEXT("position_m"),Array(FVector(P.X,-P.Y,P.Z)));
    J->SetArrayField(TEXT("rotation_xyzw"),{MakeShared<FJsonValueNumber>(Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(Q.Z),MakeShared<FJsonValueNumber>(Q.W)});return J;
}
bool Number(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,double& Value,bool Integer=false)
{
    const auto* V=J->Values.Find(Key);
    if(!V||!V->IsValid()||(*V)->Type!=EJson::Number||!(*V)->TryGetNumber(Value)||!FMath::IsFinite(Value))return false;
    return !Integer||(Value>=0&&Value<=MaxInteger&&FMath::FloorToDouble(Value)==Value);
}
bool String(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,FString& Value)
{
    const auto* V=J->Values.Find(Key);return V&&V->IsValid()&&(*V)->Type==EJson::String&&(*V)->TryGetString(Value);
}
bool FlatJson(const FString& Text,TSharedPtr<FJsonObject>& Result)
{
    auto Reader=TJsonReaderFactory<>::Create(Text);EJsonNotation Kind;int32 Depth=0,Roots=0;TSet<FString> Keys;
    while(Reader->ReadNext(Kind))
    {
        if(Kind==EJsonNotation::ObjectStart){if(++Depth!=1||++Roots!=1)return false;}
        else if(Kind==EJsonNotation::ObjectEnd){if(--Depth!=0)return false;}
        else if(Kind==EJsonNotation::String||Kind==EJsonNotation::Number)
        {const FString Key=Reader->GetIdentifier();if(Depth!=1||Keys.Contains(Key))return false;Keys.Add(Key);}
        else return false;
    }
    return Depth==0&&Roots==1&&(Keys.Num()==10||Keys.Num()==16||Keys.Num()==19)&&Reader->GetErrorMessage().IsEmpty()&&
        FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Result)&&Result.IsValid();
}
void LittleEndian(TArray<uint8>& Bytes,uint32 Value)
{for(int I=0;I<4;++I)Bytes.Add(uint8(Value>>(I*8)));}
}

double URosBridgeComponent::SolverTime() const
{
    auto* Scene=GetWorld()?GetWorld()->GetPhysicsScene():nullptr;
    return Scene&&Scene->GetSolver()?double(Scene->GetSolver()->GetSolverTime()):0.;
}

bool URosBridgeComponent::Initialize(AVehicleRig* Vehicle,USceneCaptureComponent2D* Aim,ATrainingRange* Training,bool Control,FString& Error)
{
    if(!Vehicle||!Vehicle->IsContactPhysics()||!Aim||UPhysicsSettings::Get()->bTickPhysicsAsync)
    {Error=TEXT("ROS bridge requires a physical rig and synchronous post-physics sampling");return false;}
    Rig=Vehicle;bControl=Control;RobotId=Rig->GetVehicleId();
    bProfileSensorTiming=FParse::Param(FCommandLine::Get(),TEXT("ProfileSensorTiming"));
    bMotorControl=FParse::Param(FCommandLine::Get(),TEXT("RosMotorControl"));
    if(bMotorControl)
    {
        double WheelNmPerAmp=.14,SteerNmPerAmp=.14;
        FParse::Value(FCommandLine::Get(),TEXT("LkWheelNmPerAmp="),WheelNmPerAmp);
        FParse::Value(FCommandLine::Get(),TEXT("LkSteerNmPerAmp="),SteerNmPerAmp);
        if(!bControl||!Rig->EnableMotorCurrentControl(WheelNmPerAmp,SteerNmPerAmp))
        {Error=TEXT("LK current control needs four steering modules and valid torque constants");return false;}
    }
#if WITH_DEV_AUTOMATION_TESTS
    bStallProbe=FParse::Param(FCommandLine::Get(),TEXT("RosBridgeStallProbe"));
#endif
    if(RobotId.IsEmpty()||RobotId.Len()>64||!FChar::IsAlpha(RobotId[0])){Error=TEXT("invalid ROS robot identifier");return false;}
    for(TCHAR C:RobotId)if(C>127||!(FChar::IsAlnum(C)||C=='_')){Error=TEXT("invalid ROS robot identifier");return false;}
    FParse::Value(FCommandLine::Get(),TEXT("RosPort="),Port);
    FParse::Value(FCommandLine::Get(),TEXT("RosCameraWidth="),Width);FParse::Value(FCommandLine::Get(),TEXT("RosCameraHeight="),Height);
    FParse::Value(FCommandLine::Get(),TEXT("RosCameraHz="),CameraHz);FParse::Value(FCommandLine::Get(),TEXT("RosCameraHFov="),HFovDegrees);
    if(Port<1024||Port>65535||Width<16||Width>1920||Height<16||Height>1080||
        !FMath::IsFinite(CameraHz)||(CameraHz!=0&&(CameraHz<1||CameraHz>60))||!FMath::IsFinite(HFovDegrees)||HFovDegrees<5||HFovDegrees>150)
    {Error=TEXT("invalid ROS port or camera configuration");return false;}
    Sockets=ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    Listener=Sockets->CreateSocket(NAME_Stream,TEXT("RMSim loopback listener"),false);
    bool Valid=false;auto Address=Sockets->CreateInternetAddr();Address->SetIp(TEXT("127.0.0.1"),Valid);Address->SetPort(Port);
    if(!Listener||!Valid||!Listener->SetNonBlocking(true)||!Listener->Bind(*Address)||!Listener->Listen(1))
    {Error=TEXT("cannot bind exclusive ROS loopback port");CloseSocket(Listener);return false;}
    Camera=NewObject<USceneCaptureComponent2D>(Rig,TEXT("RosCamera"));Camera->SetupAttachment(Aim);
    Camera->SetRelativeTransform(FTransform::Identity);Camera->FOVAngle=HFovDegrees;Camera->CaptureSource=ESceneCaptureSource::SCS_FinalColorLDR;
    Camera->bCaptureEveryFrame=false;Camera->bCaptureOnMovement=false;Camera->bAlwaysPersistRenderingState=true;
    Target=NewObject<UTextureRenderTarget2D>(this);Target->RenderTargetFormat=RTF_RGBA8_SRGB;
    Target->InitAutoFormat(Width,Height);Target->UpdateResourceImmediate(true);Camera->TextureTarget=Target;
    Rig->AddInstanceComponent(Camera);Camera->RegisterComponent();
    UE_LOG(LogRMSim,Display,TEXT("ROS_CAMERA_CAPTURE enabled=%d nominal_hz=%.3f width=%d height=%d"),
        CameraHz>0&&FApp::CanEverRender()?1:0,CameraHz,Width,Height);
    AimController=NewObject<URosAimController>(GetOwner());GetOwner()->AddInstanceComponent(AimController);AimController->RegisterComponent();
    if(!AimController->Initialize(Rig,Aim,Training,FParse::Param(FCommandLine::Get(),TEXT("RosAimControl")),Error))return false;
    if(FParse::Param(FCommandLine::Get(),TEXT("RosImu")))
    {
        FString Profile;
        if(!FParse::Value(FCommandLine::Get(),TEXT("ImuProfile="),Profile))
        {
            FString RigFile;
            if(!FParse::Value(FCommandLine::Get(),TEXT("MeshRigFile="),RigFile)){Error=TEXT("RosImu needs ImuProfile or a mesh-rig package");return false;}
            Profile=FPaths::Combine(FPaths::GetPath(RigFile),TEXT("sensors/imu.json"));
        }
        Imu=NewObject<URosImuComponent>(GetOwner());GetOwner()->AddInstanceComponent(Imu);Imu->RegisterComponent();
        if(!Imu->Initialize(Rig,Profile,Error))return false;
    }
    if(FParse::Param(FCommandLine::Get(),TEXT("RosLidar")))
    {
        FString Profile;
        if(!FParse::Value(FCommandLine::Get(),TEXT("LidarProfile="),Profile))
        {
            FString RigFile;
            if(!FParse::Value(FCommandLine::Get(),TEXT("MeshRigFile="),RigFile)){Error=TEXT("RosLidar needs LidarProfile or a mesh-rig package");return false;}
            Profile=FPaths::Combine(FPaths::GetPath(RigFile),TEXT("sensors/lidar.json"));
        }
        Lidar=NewObject<URosLidarComponent>(GetOwner());GetOwner()->AddInstanceComponent(Lidar);Lidar->RegisterComponent();
        if(!Lidar->Initialize(Rig,Profile,Error))return false;
        if(Imu&&Imu->UsesId(Lidar->GetSensorId())){Error=TEXT("IMU and LiDAR sensor ids must be unique");return false;}
    }
    EpochSolverTime=SolverTime();LastPollWall=FPlatformTime::Seconds();
    Rig->EnableExternalDriveLease(bControl);
    UE_LOG(LogRMSim,Display,TEXT("ROS_BRIDGE_READY loopback=127.0.0.1:%d control=%d camera=%dx%d %.1fHz physics_clock=Chaos"),Port,bControl,Width,Height,CameraHz);
    return true;
}

void URosBridgeComponent::CloseSocket(FSocket*& Socket)
{if(Socket){Socket->Close();Sockets->DestroySocket(Socket);Socket=nullptr;}}

void URosBridgeComponent::Disconnect(const FString& Reason)
{
    if(Client){LastDisconnect=Reason;if(Reason==TEXT("slow_reader"))++SlowReaderDisconnects;
        UE_LOG(LogRMSim,Display,TEXT("ROS_BRIDGE_DISCONNECT reason=%s"),*Reason);}
    SensorMountSamples.Reset();
    CloseSocket(Client);Inbound.Reset();Outbound.Reset();SendOffset=0;LastCommandWall=-1;CommandStatus=Reason;
    if(AimController)AimController->Stop(Reason);
    if(Imu)Imu->DiscardPending();
    if(Lidar)Lidar->DiscardPending();
    if(bControl&&IsValid(Rig)){Rig->RenewExternalDriveLease(0);Rig->SetContactDrive(0,0,true);}
}

void URosBridgeComponent::Reject(const FString& Reason)
{
    ++RejectedCommands;LastRejection=Reason;CommandStatus=TEXT("rejected_")+Reason;LastCommandWall=-1;
    if(bControl){Rig->RenewExternalDriveLease(0);Rig->SetContactDrive(0,0,true);}
}

bool URosBridgeComponent::ReadCommand(const TArray<uint8>& Line,double SimTime)
{
    const FUTF8ToTCHAR Converted(reinterpret_cast<const ANSICHAR*>(Line.GetData()),Line.Num());
    const FString Text(Converted.Length(),Converted.Get());TSharedPtr<FJsonObject> J;
    if(!FlatJson(Text,J)){Reject(TEXT("malformed_json"));return false;}
    double Version,CommandEpoch,Sequence,Stamp;FString Kind,Identity,Frame;
    if(!Number(J,TEXT("v"),Version,true)||Version!=1||!String(J,TEXT("kind"),Kind)||
        !((Kind==TEXT("command")&&J->Values.Num()==10)||(Kind==TEXT("motor_current")&&J->Values.Num()==16)||(Kind==TEXT("aim")&&J->Values.Num()==19))||
        !String(J,TEXT("session"),Identity)||!Number(J,TEXT("epoch"),CommandEpoch,true)||!Number(J,TEXT("seq"),Sequence,true)||Sequence<1||
        !Number(J,TEXT("stamp_ns"),Stamp,true)||!String(J,TEXT("frame_id"),Frame))
    {Reject(TEXT("fields"));if(AimController)AimController->Reject(TEXT("fields"));return true;}
    if(Kind==TEXT("aim"))
    {
        if(Identity!=Session||CommandEpoch!=Epoch){AimController->Reject(TEXT("session_epoch"));return true;}
        if(Frame!=TEXT("rmsim_world")&&Frame!=RobotId+TEXT("/base_link")){AimController->Reject(TEXT("frame"));return true;}
        const double Age=SimTime-Stamp/1.e9;
        if(Age>.2||Age<-.05){AimController->Reject(TEXT("timestamp"));return true;}
        AimController->Accept(J,int64(Sequence),SimTime);return true;
    }
    if(Identity!=Session||CommandEpoch!=Epoch){Reject(TEXT("session_epoch"));return true;}
    if(Frame!=RobotId+TEXT("/base_link")){Reject(TEXT("frame"));return true;}
    if(Sequence<=AcceptedSeq){Reject(TEXT("sequence"));return true;}
    const double Age=SimTime-Stamp/1.e9;
    if(Age>.25||Age<-.05){Reject(TEXT("timestamp"));return true;}
    if(!bControl){Reject(TEXT("manual_mode"));return true;}
    if(bOperatorBrake){Reject(TEXT("operator_brake"));return true;}
    if((Kind==TEXT("motor_current"))!=bMotorControl){Reject(TEXT("control_mode"));return true;}
    if(bMotorControl)
    {
        double Brake;
        if(!Number(J,TEXT("brake"),Brake,true)||Brake>1){Reject(TEXT("motor_brake"));return true;}
        TArray<double> Steer,Wheel;
        for(int I=0;I<4;++I)
        {
            double S,W;
            if(!Number(J,*FString::Printf(TEXT("steer%d"),I),S)||!Number(J,*FString::Printf(TEXT("wheel%d"),I),W)||
                FMath::Abs(S)>2000||FMath::Abs(W)>2000||S!=FMath::RoundToDouble(S)||W!=FMath::RoundToDouble(W))
            {Reject(TEXT("motor_limits"));return true;}
            Steer.Add(S);Wheel.Add(W);
        }
        if(Brake>0)Rig->SetContactDrive(0,0,true);
        else if(!Rig->SetMotorCurrentCommands(Steer,Wheel)){Reject(TEXT("motor_backend"));return true;}
    }
    else
    {
        double Vx,Vy,Wz;
        if(!Number(J,TEXT("vx"),Vx)||!Number(J,TEXT("vy"),Vy)||!Number(J,TEXT("wz"),Wz)){Reject(TEXT("fields"));return true;}
        const double MaxSpeed=Rig->GetDriveParameter(TEXT("MaxForwardSpeedMps"));
        // Coordinate rotations can overshoot a saturated float velocity by a few ULPs.
        constexpr double LimitTolerance=1.e-6;
        if(FMath::Abs(Vx)>MaxSpeed+LimitTolerance||FMath::Abs(Vy)>MaxSpeed+LimitTolerance||FVector2D(Vx,Vy).Size()>MaxSpeed+LimitTolerance||FMath::Abs(Wz)>Rig->GetDriveParameter(TEXT("GyroMaxRadS"))+LimitTolerance)
        {Reject(TEXT("limits"));return true;}
        // Nav2 owns the velocity ramp. A deliberate zero requests physical
        // brake torque; command expiry still uses the independent safety brake.
        const bool StopRequested=FMath::Abs(Vx)<1.e-4&&FMath::Abs(Vy)<1.e-4&&FMath::Abs(Wz)<1.e-4;
        if(!Rig->SetExternalVelocityTarget(FVector2D(Vx,Vy),Wz,StopRequested)){Reject(TEXT("drive_backend"));return true;}
    }
    AcceptedSeq=int64(Sequence);LastCommandWall=FPlatformTime::Seconds();CommandStatus=TEXT("active");
    Rig->RenewExternalDriveLease(.25);return true;
}

void URosBridgeComponent::Receive(double SimTime)
{
    int32 Lines=0;
    for(int32 Chunk=0;Client&&Chunk<16&&Lines<64;++Chunk)
    {
        int32 Newline;
        while(Client&&Inbound.Find(uint8('\n'),Newline))
        {
            if(Newline>4096){Reject(TEXT("oversized"));Disconnect(TEXT("oversized"));return;}
            TArray<uint8> Line;Line.Append(Inbound.GetData(),Newline);Inbound.RemoveAt(0,Newline+1,EAllowShrinking::No);
            if(!ReadCommand(Line,SimTime)){Disconnect(TEXT("malformed_json"));return;}
            if(++Lines>=64)break;
        }
        if(Inbound.Num()>4096){Reject(TEXT("oversized"));Disconnect(TEXT("oversized"));return;}
        if(Lines>=64)break;
        uint8 Buffer[4096];int32 Count=0;
        if(!Client->Recv(Buffer,sizeof(Buffer),Count))
        {
            // UE's stream wrapper returns true/zero for EWOULDBLOCK and false
            // for EOF or an unrecoverable error; do not reuse a stale last-error code.
            Disconnect(TEXT("disconnected"));return;
        }
        if(Count==0)break;
        Inbound.Append(Buffer,Count);
    }
}

void URosBridgeComponent::SendPending()
{
    int32 Budget=4*1024*1024;
    while(Client&&SendOffset<Outbound.Num()&&Budget>0)
    {
        int32 Sent=0;
        if(!Client->Send(Outbound.GetData()+SendOffset,FMath::Min(Outbound.Num()-SendOffset,Budget),Sent))
        {
            if(Sockets->GetLastErrorCode()==SE_EWOULDBLOCK)break;
            Disconnect(TEXT("socket_write_error"));return;
        }
        if(Sent<=0)break;
        SendOffset+=Sent;Budget-=Sent;LastSendProgressWall=FPlatformTime::Seconds();
    }
    if(SendOffset==Outbound.Num()){Outbound.Reset();SendOffset=0;}
    else if(FPlatformTime::Seconds()-LastSendProgressWall>1)Disconnect(TEXT("slow_reader"));
}

bool URosBridgeComponent::QueueState(double SimTime,bool Capture)
{
    // RGB capture is optional; calibration and camera TF remain available.
    // NullRHI also omits pixels without dereferencing a render resource.
    if(CameraHz<=0||!FApp::CanEverRender())Capture=false;
    const int64 Stamp=FMath::RoundToInt64(SimTime*1.e9);if(Stamp<0||Stamp>MaxInteger)return false;
    const auto Pose=RosPose(Rig->GetActorTransform());
    const auto Local=RosPose(Camera->GetComponentTransform().GetRelativeTransform(Rig->GetActorTransform()),true);
    if(Capture)
    {
        const double CameraStart=bProfileSensorTiming?FPlatformTime::Seconds():0.;
        Camera->CaptureScene();FlushRenderingCommands();
        FReadSurfaceDataFlags Flags(RCM_UNorm);Flags.SetLinearToGamma(false);
        if(!Target->GameThread_GetRenderTargetResource()->ReadPixels(Pixels,Flags)||Pixels.Num()!=Width*Height)return false;
        ++Images;
        if(bProfileSensorTiming)
        {
            ProfileCameraWall+=FPlatformTime::Seconds()-CameraStart;++ProfileCameraCount;
            if(SimTime-ProfileCameraStartSim>=5.0)
            {
                UE_LOG(LogRMSim,Display,TEXT("RMSIM_CAMERA_TIMING samples=%lld wall_total_ms=%.3f mean_ms=%.3f sim_elapsed=%.3f"),
                    ProfileCameraCount,ProfileCameraWall*1000,1000*ProfileCameraWall/FMath::Max(1.,double(ProfileCameraCount)),SimTime-ProfileCameraStartSim);
                ProfileCameraWall=0;ProfileCameraCount=0;ProfileCameraStartSim=SimTime;
            }
        }
    }
    auto State=MakeShared<FJsonObject>();State->SetNumberField(TEXT("v"),Lidar?2:1);State->SetStringField(TEXT("kind"),TEXT("state"));
    State->SetStringField(TEXT("session"),Session);State->SetStringField(TEXT("robot_id"),RobotId);
    State->SetNumberField(TEXT("epoch"),Epoch);State->SetNumberField(TEXT("tick"),TickIndex);State->SetNumberField(TEXT("sim_time_ns"),double(Stamp));
    State->SetObjectField(TEXT("pose"),Pose);
    auto Map=MakeShared<FJsonObject>();Map->SetStringField(TEXT("id"),MapId);Map->SetStringField(TEXT("source_md5"),MapHash);Map->SetStringField(TEXT("kind"),MapKind);State->SetObjectField(TEXT("map"),Map);
    auto Sensor=MakeShared<FJsonObject>();Sensor->SetNumberField(TEXT("width"),Width);Sensor->SetNumberField(TEXT("height"),Height);
    Sensor->SetNumberField(TEXT("hfov_rad"),FMath::DegreesToRadians(HFovDegrees));Sensor->SetNumberField(TEXT("stamp_ns"),double(Stamp));
    Sensor->SetNumberField(TEXT("nominal_hz"),CameraHz);
    Sensor->SetBoolField(TEXT("capture_enabled"),CameraHz>0&&FApp::CanEverRender());
    if(const auto* Observer=Cast<USceneCaptureComponent2D>(Camera->GetAttachParent()))
        Sensor->SetNumberField(TEXT("observer_hfov_rad"),FMath::DegreesToRadians(double(Observer->FOVAngle)));
    Sensor->SetStringField(TEXT("frame_id"),RobotId+TEXT("/aim_camera_optical_frame"));Sensor->SetObjectField(TEXT("pose_in_base"),Local);State->SetObjectField(TEXT("camera"),Sensor);
    auto Command=MakeShared<FJsonObject>();Command->SetNumberField(TEXT("accepted_seq"),double(AcceptedSeq));Command->SetStringField(TEXT("status"),CommandStatus);
    Command->SetNumberField(TEXT("age_s"),LastCommandWall<0?-1:FPlatformTime::Seconds()-LastCommandWall);
    Command->SetNumberField(TEXT("rejected_count"),RejectedCommands);Command->SetStringField(TEXT("last_rejection"),LastRejection);
    State->SetObjectField(TEXT("command"),Command);State->SetNumberField(TEXT("captured_images"),Images);
    State->SetObjectField(TEXT("drive"),Rig->GetVelocityCommandState());
    if(const auto* Viewport=Cast<URMSimViewportClient>(GetWorld()->GetGameViewport()))
        State->SetObjectField(TEXT("rendering"),Viewport->RenderState());
    if(AimController)
    {
        State->SetObjectField(TEXT("aim"),AimController->State(Stamp));
        State->SetObjectField(TEXT("training_evaluation"),AimController->Evaluation());
    }
    if(bMotorControl)State->SetObjectField(TEXT("motors"),Rig->GetMotorFeedback());
    if(!SensorMountSamples.IsEmpty()){State->SetArrayField(TEXT("sensor_mounts"),SensorMountSamples);State->SetNumberField(TEXT("dropped_sensor_mounts"),DroppedSensorMountSamples);SensorMountSamples.Reset();}
    if(Imu)State->SetObjectField(TEXT("imu"),Imu->TakeState());
    TArray<uint8> LidarBytes;const int32 CameraBytes=Capture?Width*Height*3:0;
    if(Lidar)
    {State->SetNumberField(TEXT("camera_bytes"),CameraBytes);State->SetObjectField(TEXT("lidar"),Lidar->TakeState(LidarBytes,CameraBytes));}
    if(CameraBytes+LidarBytes.Num()>16*1024*1024)return false;
    State->SetStringField(TEXT("last_disconnect"),LastDisconnect);State->SetNumberField(TEXT("slow_reader_disconnects"),SlowReaderDisconnects);
    State->SetBoolField(TEXT("development_stall_injected"),bStallInjected);
    State->SetStringField(TEXT("clock_source"),TEXT("synchronous_Chaos_solver"));
    State->SetNumberField(TEXT("simulation_rate"),RealTimeFactor);
    State->SetNumberField(TEXT("fixed_step_s"),FApp::UseFixedTimeStep()?FApp::GetFixedDeltaTime():0);
    FString Json;FJsonSerializer::Serialize(State,TJsonWriterFactory<TCHAR,TCondensedJsonPrintPolicy<TCHAR>>::Create(&Json));
    const FTCHARToUTF8 Utf8(*Json);if(Utf8.Length()>65536)return false;
    Outbound.Reset();SendOffset=0;LittleEndian(Outbound,Utf8.Length());LittleEndian(Outbound,CameraBytes+LidarBytes.Num());
    Outbound.Append(reinterpret_cast<const uint8*>(Utf8.Get()),Utf8.Length());
    if(Capture)for(const auto& Pixel:Pixels){Outbound.Add(Pixel.R);Outbound.Add(Pixel.G);Outbound.Add(Pixel.B);}
    Outbound.Append(LidarBytes);
    LastSendProgressWall=FPlatformTime::Seconds();return true;
}

void URosBridgeComponent::Advance()
{
    if(!Listener||!IsValid(Rig))return;
    const double Now=FPlatformTime::Seconds();

    if(Client&&Now-LastPollWall>.25)Disconnect(TEXT("game_tick_stall"));
    LastPollWall=Now;++TickIndex;
    const double SimTime=FMath::Max(0.,SolverTime()-EpochSolverTime);
    if(RateWindowWall<=0||SimTime<RateWindowSim){RateWindowWall=Now;RateWindowSim=SimTime;}
    else if(Now-RateWindowWall>=1.)
    {
        RealTimeFactor=(SimTime-RateWindowSim)/(Now-RateWindowWall);
        RateWindowWall=Now;RateWindowSim=SimTime;
    }
    const double SimDelta=FMath::Max(0.,SimTime-LastSimTime);LastSimTime=SimTime;
    // Apply only the previously accepted aim command over elapsed physics time.
    if(AimController)AimController->Advance(SimDelta);
    if(Client&&((Imu&&Imu->UsesArticulatedMount())||(Lidar&&Lidar->UsesArticulatedMount())))
    {
        if(SensorMountSamples.Num()>=64){SensorMountSamples.RemoveAt(0,1,EAllowShrinking::No);++DroppedSensorMountSamples;}
        SensorMountSamples.Add(MakeShared<FJsonValueObject>(Rig->GetSensorJointState(FMath::RoundToInt64(SimTime*1.e9))));
    }
    if(Imu)Imu->Advance(SimTime,Client!=nullptr);
    if(Lidar)Lidar->Advance(SimTime,Client!=nullptr);
    // Retire an EOF peer before deciding whether a newly queued connection is
    // a competing client; otherwise an immediate reconnect can lose one attempt.
    if(Client)Receive(SimTime);
    if(bStallProbe&&!bStallInjected&&bControl&&AcceptedSeq>=5&&LastCommandWall>=0)
    {
        bStallInjected=true;UE_LOG(LogRMSim,Display,TEXT("ROS_BRIDGE_TEST_STALL injected_400ms"));
        FPlatformProcess::Sleep(.4f);return; // Development-only fault injection, never a network command.
    }
    bool Pending=false;
    if(Listener->HasPendingConnection(Pending)&&Pending)
    {
        auto* Incoming=Listener->Accept(TEXT("RMSim local ROS client"));
        if(Incoming)
        {
            if(Client)CloseSocket(Incoming);
            else
            {
                Client=Incoming;Client->SetNonBlocking(true);Client->SetNoDelay(true);int32 Actual=0;Client->SetSendBufferSize(4*1024*1024,Actual);
                Session=FGuid::NewGuid().ToString();AcceptedSeq=-1;LastCommandWall=-1;CommandStatus=bControl?TEXT("waiting"):TEXT("manual_mode");
                LastStateWall=0;NextImageSim=SimTime;Inbound.Reset();Outbound.Reset();SendOffset=0;
                if(AimController)AimController->Reset(false);
                if(Imu)Imu->DiscardPending();
                if(Lidar)Lidar->DiscardPending();
            }
        }
    }
    if(bControl&&(bOperatorBrake||LastCommandWall<0||Now-LastCommandWall>.25))
    {
        Rig->RenewExternalDriveLease(0);Rig->SetContactDrive(0,0,true);
        if(bOperatorBrake)CommandStatus=TEXT("operator_brake");
        else if(LastCommandWall>=0)CommandStatus=TEXT("expired");
    }
    if(Client)SendPending();
    const double StateInterval=CameraHz>0?FMath::Min(.02,1./CameraHz):.02;
    if(Client&&Outbound.IsEmpty()&&(bMotorControl||Now-LastStateWall>=StateInterval))
    {
        const bool Capture=CameraHz>0&&SimTime>=NextImageSim;
        if(!QueueState(SimTime,Capture)){Disconnect(TEXT("capture_failed"));return;}
        if(Capture)NextImageSim=SimTime+1/CameraHz;
        LastStateWall=Now;SendPending();
    }
}

void URosBridgeComponent::ResetEpoch()
{
    ++Epoch;EpochSolverTime=SolverTime();TickIndex=0;NextImageSim=0;LastSimTime=0;RealTimeFactor=0;RateWindowWall=0;RateWindowSim=0;Disconnect(TEXT("reset"));
    if(AimController)AimController->Reset(true);
    if(Imu)Imu->Reset();
    if(Lidar)Lidar->Reset();
}

void URosBridgeComponent::SetMapIdentity(const FString& Id,const FString& Source,bool Mesh)
{MapId=Id;MapHash=LexToString(FMD5Hash::HashFile(*Source));MapKind=Mesh?TEXT("rm_mesh_map"):TEXT("rm_flat_map");}

void URosBridgeComponent::SetOperatorBrake(bool Brake)
{
    bOperatorBrake=Brake;
    if(AimController)AimController->SetOperatorBrake(Brake);
    if(Brake&&bControl){LastCommandWall=-1;Rig->RenewExternalDriveLease(0);Rig->SetContactDrive(0,0,true);}
}

FString URosBridgeComponent::StatusText() const
{return FString::Printf(TEXT("ROS %s | sim %.2fx | %s | images %lld | rejected %lld"),bMotorControl?TEXT("LK CURRENT"):bControl?TEXT("CONTROL"):TEXT("OBSERVE"),RealTimeFactor,*CommandStatus,Images,RejectedCommands);}

void URosBridgeComponent::EndPlay(const EEndPlayReason::Type Reason)
{
    Disconnect(TEXT("closed"));CloseSocket(Listener);
    if(IsValid(Rig))Rig->EnableExternalDriveLease(false);
    if(IsValid(Camera))Camera->DestroyComponent();
    Super::EndPlay(Reason);
}
