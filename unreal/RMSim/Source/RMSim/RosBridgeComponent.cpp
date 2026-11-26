#include "RosBridgeComponent.h"
#include "RMUCGameReferee.h"
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
#include "RHIGPUReadback.h"
#include "TextureResource.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SocketSubsystem.h"
#include "Sockets.h"
#include <atomic>

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
    return Depth==0&&Roots==1&&(Keys.Num()==8||Keys.Num()==10||Keys.Num()==16||Keys.Num()==19)&&Reader->GetErrorMessage().IsEmpty()&&
        FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Result)&&Result.IsValid();
}
void LittleEndian(TArray<uint8>& Bytes,uint32 Value)
{for(int I=0;I<4;++I)Bytes.Add(uint8(Value>>(I*8)));}
}

struct URosBridgeComponent::FCameraReadback
{
    enum class EStatus { Waiting, Ready, Failed };
    std::atomic<EStatus> Status{EStatus::Waiting};
    std::atomic<bool> PollQueued{false};
    TUniquePtr<FRHIGPUTextureReadback> Copy;
    TArray<FColor> Pixels;
    FTransform OpticalMount;
    int64 Stamp=0,Connection=0;
};

double URosBridgeComponent::SolverTime() const
{
    return IsValid(Rig) ? Rig->GetSimulationTimeSeconds() : 0.;
}

bool URosBridgeComponent::Initialize(AVehicleRig* Vehicle,USceneCaptureComponent2D* Aim,ATrainingRange* Training,bool Control,FString& Error)
{
    return Initialize(Vehicle,Aim,Training,Control,FRosBridgeOptions{},Error);
}

bool URosBridgeComponent::Initialize(AVehicleRig* Vehicle,USceneCaptureComponent2D* Aim,ATrainingRange* Training,bool Control,const FRosBridgeOptions& Options,FString& Error)
{
    if(!Vehicle||!Vehicle->IsContactPhysics()||!Aim||UPhysicsSettings::Get()->bTickPhysicsAsync)
    {Error=TEXT("ROS bridge requires a physical rig and synchronous post-physics sampling");return false;}
    Rig=Vehicle;bControl=Control;RobotId=Rig->GetVehicleId();
    FParse::Value(FCommandLine::Get(),TEXT("RosRobotId="),RobotId);
    if(!Options.RobotIdOverride.IsEmpty())RobotId=Options.RobotIdOverride;
    bProfileSensorTiming=FParse::Param(FCommandLine::Get(),TEXT("ProfileSensorTiming"));
    bMotorControl=FParse::Param(FCommandLine::Get(),TEXT("RosMotorControl"));
    FString MatchRoster;
    const bool bMatchBridge=FParse::Value(FCommandLine::Get(),TEXT("MatchRoster="),MatchRoster);
    bMatchAutonomy=bControl&&!bMotorControl&&bMatchBridge&&
        FParse::Param(FCommandLine::Get(),TEXT("RmsimMatchAutonomy"));
    bOperatorGyroAvailable=bControl&&!bMotorControl&&Rig->HasSteeringModules()&&
        (bMatchAutonomy||FParse::Param(FCommandLine::Get(),TEXT("RosOperatorGyroControl")));
    bNavigationFrameAvailable=bControl&&!bMotorControl&&Rig->HasSteeringModules()&&
        (bOperatorGyroAvailable||bMatchBridge);
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
    if(Options.PortOverride>=1024&&Options.PortOverride<=65535)Port=Options.PortOverride;
    FParse::Value(FCommandLine::Get(),TEXT("RosCameraWidth="),Width);FParse::Value(FCommandLine::Get(),TEXT("RosCameraHeight="),Height);
    FParse::Value(FCommandLine::Get(),TEXT("RosCameraHz="),CameraHz);FParse::Value(FCommandLine::Get(),TEXT("RosCameraHFov="),HFovDegrees);
    CapturePhaseFraction=FMath::IsFinite(Options.CapturePhaseFraction)?FMath::Clamp(Options.CapturePhaseFraction,0.0,0.99):0.0;
    bLightweightAimCamera=Options.bLightweightAimCamera||bMatchBridge||FParse::Param(FCommandLine::Get(),TEXT("RosCameraLightweight"));
    if(FParse::Param(FCommandLine::Get(),TEXT("RosCameraFullRender")))bLightweightAimCamera=false;
    if(Port<1024||Port>65535||Width<16||Width>1920||Height<16||Height>1080||
        !FMath::IsFinite(CameraHz)||(CameraHz!=0&&(CameraHz<1||CameraHz>60))||!FMath::IsFinite(HFovDegrees)||HFovDegrees<5||HFovDegrees>150)
    {Error=TEXT("invalid ROS port or camera configuration");return false;}
    FString BindIp=TEXT("127.0.0.1");
    FParse::Value(FCommandLine::Get(),TEXT("RosBindAddress="),BindIp);
    if(!Options.BindAddress.IsEmpty())BindIp=Options.BindAddress;
    Sockets=ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    Listener=Sockets->CreateSocket(NAME_Stream,TEXT("RMSim loopback listener"),false);
    bool Valid=false;auto Address=Sockets->CreateInternetAddr();Address->SetIp(*BindIp,Valid);Address->SetPort(Port);
    if(!Listener||!Valid||!Listener->SetNonBlocking(true)||!Listener->Bind(*Address)||!Listener->Listen(1))
    {Error=FString::Printf(TEXT("cannot bind exclusive ROS port %s:%d"),*BindIp,Port);CloseSocket(Listener);return false;}
    Camera=NewObject<USceneCaptureComponent2D>(Rig,*FString::Printf(TEXT("RosCamera_%d"),Port));Camera->SetupAttachment(Aim);
    Camera->SetRelativeTransform(FTransform::Identity);Camera->FOVAngle=HFovDegrees;Camera->CaptureSource=ESceneCaptureSource::SCS_FinalColorLDR;
    Camera->bCaptureEveryFrame=false;Camera->bCaptureOnMovement=false;Camera->bAlwaysPersistRenderingState=true;
    if(bLightweightAimCamera)
    {
        // Strip expensive viewport-only passes from multi-robot aim cameras while preserving emissive lightbars and armor stickers.
        Camera->ShowFlags.SetDynamicShadows(false);
        Camera->ShowFlags.SetVolumetricFog(false);
        Camera->ShowFlags.SetBloom(false);
        Camera->ShowFlags.SetMotionBlur(false);
        Camera->ShowFlags.SetAmbientOcclusion(false);
        Camera->ShowFlags.SetScreenSpaceReflections(false);
        Camera->ShowFlags.SetAntiAliasing(false);
        Camera->ShowFlags.SetTemporalAA(false);
        Camera->ShowFlags.SetContactShadows(false);
        Camera->ShowFlags.SetAtmosphere(false);
        Camera->ShowFlags.SetFog(false);
    }
    Target=NewObject<UTextureRenderTarget2D>(this);Target->RenderTargetFormat=RTF_RGBA8_SRGB;
    Target->InitAutoFormat(Width,Height);Target->UpdateResourceImmediate(true);Camera->TextureTarget=Target;
    Rig->AddInstanceComponent(Camera);Camera->RegisterComponent();
    UE_LOG(LogRMSim,Display,TEXT("ROS_CAMERA_CAPTURE enabled=%d nominal_hz=%.3f width=%d height=%d lightweight=%d phase=%.2f"),
        CameraHz>0&&FApp::CanEverRender()?1:0,CameraHz,Width,Height,bLightweightAimCamera?1:0,CapturePhaseFraction);
    AimController=NewObject<URosAimController>(GetOwner());GetOwner()->AddInstanceComponent(AimController);AimController->RegisterComponent();
    if(!AimController->Initialize(Rig,Aim,Training,FParse::Param(FCommandLine::Get(),TEXT("RosAimControl")),Error))return false;
    AimController->SetReferee(Referee);
    if(Options.bAllowImu&&FParse::Param(FCommandLine::Get(),TEXT("RosImu")))
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
    if(Options.bAllowLidar&&FParse::Param(FCommandLine::Get(),TEXT("RosLidar")))
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
    NextImageSim=(CameraHz>0.0)?(CapturePhaseFraction/CameraHz):0.0;
    Rig->EnableExternalDriveLease(bControl);
    UE_LOG(LogRMSim,Display,TEXT("ROS_BRIDGE_READY loopback=%s:%d control=%d camera=%dx%d %.1fHz lightweight=%d phase=%.2f physics_clock=%s"),
        *BindIp,Port,bControl,Width,Height,CameraHz,bLightweightAimCamera?1:0,CapturePhaseFraction,Rig->GetPhysicsBackendName());
    return true;
}

void URosBridgeComponent::CloseSocket(FSocket*& Socket)
{if(Socket){Socket->Close();Sockets->DestroySocket(Socket);Socket=nullptr;}}

void URosBridgeComponent::Disconnect(const FString& Reason)
{
    if(Client){LastDisconnect=Reason;if(Reason==TEXT("slow_reader"))++SlowReaderDisconnects;
        UE_LOG(LogRMSim,Display,TEXT("ROS_BRIDGE_DISCONNECT reason=%s"),*Reason);}
    SensorMountSamples.Reset();
    ++CameraConnection;
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
        !(((Kind==TEXT("command")||Kind==TEXT("operator_idle"))&&J->Values.Num()==10)||(Kind==TEXT("motor_current")&&J->Values.Num()==16)||
          (Kind==TEXT("aim")&&J->Values.Num()==19)||
          ((Kind==TEXT("match_stance")||Kind==TEXT("match_ammo_purchase"))&&J->Values.Num()==8))||
        !String(J,TEXT("session"),Identity)||!Number(J,TEXT("epoch"),CommandEpoch,true)||!Number(J,TEXT("seq"),Sequence,true)||Sequence<1||
        !Number(J,TEXT("stamp_ns"),Stamp,true)||!String(J,TEXT("frame_id"),Frame))
    {Reject(TEXT("fields"));if(AimController)AimController->Reject(TEXT("fields"));return true;}
    if(Kind==TEXT("match_stance"))
    {
        FString Stance;
        const double Age=SimTime-Stamp/1.e9;
        if(bMatchAutonomy&&IsValid(Referee)&&Identity==Session&&CommandEpoch==Epoch&&
            Frame==RobotId+TEXT("/base_link")&&Sequence>MatchStanceSequence&&Age<=.2&&Age>=-.05&&
            String(J,TEXT("stance"),Stance)&&Referee->SetSimMatchStance(Rig,Stance))
            MatchStanceSequence=int64(Sequence);
        return true;
    }
    if(Kind==TEXT("match_ammo_purchase"))
    {
        double Mode;
        const double Age=SimTime-Stamp/1.e9;
        if(bMatchAutonomy&&IsValid(Referee)&&Identity==Session&&CommandEpoch==Epoch&&
            Frame==RobotId+TEXT("/base_link")&&Sequence>MatchAmmoPurchaseSequence&&Age<=.2&&Age>=-.05&&
            Number(J,TEXT("purchase_mode"),Mode,true)&&(Mode==1||Mode==2))
        {
            MatchAmmoPurchaseSequence=int64(Sequence);
            Referee->RequestSentryAmmoPurchase(Rig,Mode==2);
        }
        return true;
    }
    if(StallCommandFloor>=0. && Stamp*1.e-9<=StallCommandFloor)
    {
        Reject(TEXT("pre_stall_command"));
        if(Kind==TEXT("aim")&&AimController)AimController->Reject(TEXT("pre_stall_command"));
        return true;
    }
    if(Kind==TEXT("aim"))
    {
        if(Identity!=Session||CommandEpoch!=Epoch){AimController->Reject(TEXT("session_epoch"));return true;}
        if(Frame!=TEXT("rmsim_world")&&Frame!=RobotId+TEXT("/base_link")){AimController->Reject(TEXT("frame"));return true;}
        const double Age=SimTime-Stamp/1.e9;
        if(Age>.2||Age<-.05){AimController->Reject(TEXT("timestamp"));return true;}
        AimController->Accept(J,int64(Sequence),SimTime);return true;
    }
    if(Identity!=Session||CommandEpoch!=Epoch){Reject(TEXT("session_epoch"));return true;}
    const bool NavigationFrame=bNavigationFrameAvailable&&Kind==TEXT("command")&&
        Frame==RobotId+TEXT("/odom");
    if(Frame!=RobotId+TEXT("/base_link")&&!NavigationFrame){Reject(TEXT("frame"));return true;}
    if(Sequence<=AcceptedSeq){Reject(TEXT("sequence"));return true;}
    const double Age=SimTime-Stamp/1.e9;
    if(Age>.25||Age<-.05){Reject(TEXT("timestamp"));return true;}
    if(!bControl){Reject(TEXT("manual_mode"));return true;}
    if(bOperatorBrake){Reject(TEXT("operator_brake"));return true;}
    if((Kind==TEXT("motor_current"))!=bMotorControl){Reject(TEXT("control_mode"));return true;}
    if(IsValid(Referee)&&!Referee->IsChassisPoweredFor(Rig))
    {Reject(TEXT("referee_chassis_power_cut"));return true;}
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
        // Nav2 supplies the target; the shared vehicle profile shapes the wheel command.
        // A deliberate zero and command expiry retain the physical safety brake.
        const bool StopRequested=FMath::Abs(Vx)<1.e-4&&FMath::Abs(Vy)<1.e-4&&FMath::Abs(Wz)<1.e-4;
        const bool OperatorIdle=Kind==TEXT("operator_idle");
        if(OperatorIdle&&(!StopRequested||!bOperatorGyroAvailable))
        {Reject(TEXT("operator_idle"));return true;}
        const bool OperatorGyro=bOperatorGyroAvailable&&(!StopRequested||OperatorIdle)&&OperatorGyroRateRadS>0.0;
        const double OrdinaryYawLimit=Rig->GetDriveParameter(TEXT("ManualYawRateRadS"));
        const double YawTarget=OperatorGyro?OperatorGyroRateRadS:FMath::Clamp(Wz,-OrdinaryYawLimit,OrdinaryYawLimit);
        const bool BrakeTarget=StopRequested&&!OperatorGyro;
        FVector2D Translation(Vx,Vy);
        if(NavigationFrame)
        {
            // The odometry origin is calibrated at the stationary start of this
            // sensor session. Rotate its fixed velocity axes into the actuator frame.
            const double Delta=NavigationFrameYawRad+FMath::DegreesToRadians(Rig->GetActorRotation().Yaw);
            const double C=FMath::Cos(Delta),S=FMath::Sin(Delta);
            Translation=FVector2D(C*Vx-S*Vy,S*Vx+C*Vy);
        }
        if(!Rig->SetExternalVelocityTarget(Translation,YawTarget,BrakeTarget,OperatorGyro||NavigationFrame))
        {Reject(TEXT("drive_backend"));return true;}
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

void URosBridgeComponent::AdvanceCamera(double SimTime)
{
    if(CameraHz<=0||!FApp::CanEverRender())return;
    const FIntPoint Extent(Width,Height);
    for(const auto& Readback:CameraReadbacks)
    {
        if(Readback->PollQueued.load()||Readback->Status.load()!=FCameraReadback::EStatus::Waiting)continue;
        Readback->PollQueued.store(true);
        ENQUEUE_RENDER_COMMAND(RMSimPollCamera)([Readback,Extent](FRHICommandListImmediate&)
        {
            auto Status=Readback->Status.load();
            if(Status==FCameraReadback::EStatus::Waiting&&Readback->Copy->IsReady())
            {
                int32 Pitch=0,Rows=0;
                const auto* Source=static_cast<const FColor*>(Readback->Copy->Lock(Pitch,&Rows));
                Status=FCameraReadback::EStatus::Failed;
                if(Source&&Pitch>=Extent.X&&Rows>=Extent.Y)
                {
                    Readback->Pixels.SetNumUninitialized(Extent.X*Extent.Y);
                    for(int32 Y=0;Y<Extent.Y;++Y)
                        FMemory::Memcpy(Readback->Pixels.GetData()+Y*Extent.X,Source+Y*Pitch,Extent.X*sizeof(FColor));
                    Status=FCameraReadback::EStatus::Ready;
                }
                if(Source)Readback->Copy->Unlock();
            }
            Readback->Status.store(Status);
            Readback->PollQueued.store(false);
        });
    }
    while(!CameraReadbacks.IsEmpty())
    {
        const auto& First=CameraReadbacks[0];
        if(First->PollQueued.load()||First->Status.load()==FCameraReadback::EStatus::Waiting)break;
        if(First->Connection==CameraConnection&&First->Status.load()==FCameraReadback::EStatus::Ready)break;
        if(First->Connection==CameraConnection)++CameraReadbackMisses;
        FreeCameraReadbacks.Add(First);
        CameraReadbacks.RemoveAt(0);
    }
    if(!Client||bFpvVideoCut||SimTime+1.e-8<NextImageSim)return;
    const int64 Due=1+FMath::FloorToInt64(FMath::Max(0.,SimTime-NextImageSim)*CameraHz);
    NextImageSim+=double(Due)/CameraHz;
    CameraReadbackMisses+=Due-1;
    constexpr int32 MaxInFlight=3;
    if(CameraReadbacks.Num()>=MaxInFlight){++CameraReadbackMisses;return;}

    const double Start=bProfileSensorTiming?FPlatformTime::Seconds():0.;
    auto Readback=FreeCameraReadbacks.IsEmpty()?MakeShared<FCameraReadback,ESPMode::ThreadSafe>():FreeCameraReadbacks.Pop(EAllowShrinking::No);
    Readback->Stamp=FMath::RoundToInt64(SimTime*1.e9);
    Readback->Connection=CameraConnection;
    Readback->OpticalMount=Camera->GetComponentTransform().GetRelativeTransform(Rig->GetActorTransform());
    Readback->Status.store(FCameraReadback::EStatus::Waiting);
    FTextureRenderTargetResource* Resource=Target->GameThread_GetRenderTargetResource();
    Camera->CaptureScene();
    // Observe the same render command order as the scene capture. The staging fence is polled in later frames.
    ENQUEUE_RENDER_COMMAND(RMSimCopyCamera)([Readback,Resource](FRHICommandListImmediate& Commands)
    {
        FRHITexture* Texture=Resource->GetRenderTargetTexture().GetReference();
        if(!Texture||Texture->GetFormat()!=PF_B8G8R8A8)
        {
            Readback->Status.store(FCameraReadback::EStatus::Failed);
            return;
        }
        if(!Readback->Copy)Readback->Copy=MakeUnique<FRHIGPUTextureReadback>(TEXT("RMSimCameraRGB"));
        Readback->Copy->EnqueueCopy(Commands,Texture);
    });
    CameraReadbacks.Add(MoveTemp(Readback));
    if(bProfileSensorTiming)
    {
        ProfileCameraWall+=FPlatformTime::Seconds()-Start;++ProfileCameraCount;
        if(SimTime-ProfileCameraStartSim>=5.0)
        {
            UE_LOG(LogRMSim,Display,TEXT("RMSIM_CAMERA_TIMING samples=%lld submit_wall_total_ms=%.3f submit_mean_ms=%.3f sim_elapsed=%.3f missed_total=%lld"),
                ProfileCameraCount,ProfileCameraWall*1000,1000*ProfileCameraWall/FMath::Max(1.,double(ProfileCameraCount)),SimTime-ProfileCameraStartSim,CameraReadbackMisses);
            ProfileCameraWall=0;ProfileCameraCount=0;ProfileCameraStartSim=SimTime;
        }
    }
}

bool URosBridgeComponent::QueueState(double SimTime)
{
    const int64 Stamp=FMath::RoundToInt64(SimTime*1.e9);if(Stamp<0||Stamp>MaxInteger)return false;
    const auto Pose=RosPose(Rig->GetActorTransform());
    TSharedPtr<FCameraReadback,ESPMode::ThreadSafe> Frame;
    if(!bFpvVideoCut&&!CameraReadbacks.IsEmpty())
    {
        const auto& First=CameraReadbacks[0];
        if(!First->PollQueued.load()&&First->Status.load()==FCameraReadback::EStatus::Ready&&First->Connection==CameraConnection)
        {
            Frame=First;
            CameraReadbacks.RemoveAt(0);
            ++Images;
        }
    }
    const auto Local=RosPose(Frame?Frame->OpticalMount:Camera->GetComponentTransform().GetRelativeTransform(Rig->GetActorTransform()),true);
    auto State=MakeShared<FJsonObject>();State->SetNumberField(TEXT("v"),Lidar?2:1);State->SetStringField(TEXT("kind"),TEXT("state"));
    State->SetStringField(TEXT("session"),Session);State->SetStringField(TEXT("robot_id"),RobotId);
    State->SetNumberField(TEXT("epoch"),Epoch);State->SetNumberField(TEXT("tick"),TickIndex);State->SetNumberField(TEXT("sim_time_ns"),double(Stamp));
    State->SetObjectField(TEXT("pose"),Pose);
    auto Map=MakeShared<FJsonObject>();Map->SetStringField(TEXT("id"),MapId);Map->SetStringField(TEXT("source_md5"),MapHash);Map->SetStringField(TEXT("kind"),MapKind);State->SetObjectField(TEXT("map"),Map);
    auto Sensor=MakeShared<FJsonObject>();Sensor->SetNumberField(TEXT("width"),Width);Sensor->SetNumberField(TEXT("height"),Height);
    Sensor->SetNumberField(TEXT("hfov_rad"),FMath::DegreesToRadians(HFovDegrees));Sensor->SetNumberField(TEXT("stamp_ns"),double(Frame?Frame->Stamp:Stamp));
    Sensor->SetNumberField(TEXT("missed_captures"),CameraReadbackMisses);
    Sensor->SetNumberField(TEXT("nominal_hz"),CameraHz);
    Sensor->SetBoolField(TEXT("capture_enabled"),!bFpvVideoCut&&CameraHz>0&&FApp::CanEverRender());
    if(const auto* Observer=Cast<USceneCaptureComponent2D>(Camera->GetAttachParent()))
        Sensor->SetNumberField(TEXT("observer_hfov_rad"),FMath::DegreesToRadians(double(Observer->FOVAngle)));
    Sensor->SetStringField(TEXT("frame_id"),RobotId+TEXT("/aim_camera_optical_frame"));Sensor->SetObjectField(TEXT("pose_in_base"),Local);State->SetObjectField(TEXT("camera"),Sensor);
    auto Command=MakeShared<FJsonObject>();Command->SetNumberField(TEXT("accepted_seq"),double(AcceptedSeq));Command->SetStringField(TEXT("status"),CommandStatus);
    Command->SetNumberField(TEXT("age_s"),LastCommandWall<0?-1:FPlatformTime::Seconds()-LastCommandWall);
    Command->SetNumberField(TEXT("rejected_count"),RejectedCommands);Command->SetStringField(TEXT("last_rejection"),LastRejection);
    State->SetObjectField(TEXT("command"),Command);State->SetNumberField(TEXT("captured_images"),Images);
    auto Drive=Rig->GetVelocityCommandState();
    Drive->SetBoolField(TEXT("operator_gyro_available"),bOperatorGyroAvailable);
    Drive->SetBoolField(TEXT("match_autonomy"),bMatchAutonomy);
    Drive->SetBoolField(TEXT("navigation_frame_available"),bNavigationFrameAvailable);
    Drive->SetNumberField(TEXT("operator_gyro_setpoint_rad_s"),OperatorGyroRateRadS);
    Drive->SetNumberField(TEXT("measured_yaw_rad_s"),Rig->GetMeasuredYawRate());
    State->SetObjectField(TEXT("drive"),Drive);
    if(const auto* Viewport=Cast<URMSimViewportClient>(GetWorld()->GetGameViewport()))
        State->SetObjectField(TEXT("rendering"),Viewport->RenderState());
    if(AimController)
    {
        State->SetObjectField(TEXT("aim"),AimController->State(Stamp));
        State->SetObjectField(TEXT("training_evaluation"),AimController->Evaluation());
    }
    if(IsValid(Referee))State->SetObjectField(TEXT("referee"),Referee->StateFor(Rig));
    if(bMotorControl)State->SetObjectField(TEXT("motors"),Rig->GetMotorFeedback());
    if(!SensorMountSamples.IsEmpty()){State->SetArrayField(TEXT("sensor_mounts"),SensorMountSamples);State->SetNumberField(TEXT("dropped_sensor_mounts"),DroppedSensorMountSamples);SensorMountSamples.Reset();}
    if(Imu)State->SetObjectField(TEXT("imu"),Imu->TakeState());
    TArray<uint8> LidarBytes;const int32 CameraBytes=Frame?Width*Height*3:0;
    if(Lidar)
    {State->SetNumberField(TEXT("camera_bytes"),CameraBytes);State->SetObjectField(TEXT("lidar"),Lidar->TakeState(LidarBytes,CameraBytes));}
    if(CameraBytes+LidarBytes.Num()>16*1024*1024)return false;
    State->SetNumberField(TEXT("game_tick_stalls"),double(GameTickStalls));
    State->SetNumberField(TEXT("last_game_tick_gap_ms"),LastGameTickGapMs);
    if(Rig->IsMujocoPhysics())State->SetNumberField(TEXT("mujoco_warning_count"),Rig->GetMujocoWarningCount());
    State->SetStringField(TEXT("last_disconnect"),LastDisconnect);State->SetNumberField(TEXT("slow_reader_disconnects"),SlowReaderDisconnects);
    State->SetBoolField(TEXT("development_stall_injected"),bStallInjected);
    const double DedicatedPeriod=Rig->GetDedicatedMujocoPeriod();
    State->SetStringField(TEXT("clock_source"),DedicatedPeriod>0?TEXT("dedicated_MuJoCo_snapshot"):
        Rig->IsMujocoPhysics()?TEXT("synchronous_MuJoCo_solver"):TEXT("synchronous_Chaos_solver"));
    State->SetStringField(TEXT("physics_backend"),Rig->GetPhysicsBackendName());
    State->SetNumberField(TEXT("dynamics_step_s"),Rig->GetDynamicsStepSeconds());
    State->SetNumberField(TEXT("simulation_rate"),RealTimeFactor);
    const double EngineStep=FApp::UseFixedTimeStep()?FApp::GetFixedDeltaTime():0;
    State->SetNumberField(TEXT("fixed_step_s"),DedicatedPeriod>0?DedicatedPeriod:EngineStep);
    State->SetNumberField(TEXT("engine_fixed_step_s"),EngineStep);
    FString Json;FJsonSerializer::Serialize(State,TJsonWriterFactory<TCHAR,TCondensedJsonPrintPolicy<TCHAR>>::Create(&Json));
    const FTCHARToUTF8 Utf8(*Json);if(Utf8.Length()>65536)return false;
    bOutboundHasCameraFrame=Frame.IsValid();
    Outbound.Reset();SendOffset=0;LittleEndian(Outbound,Utf8.Length());LittleEndian(Outbound,CameraBytes+LidarBytes.Num());
    Outbound.Append(reinterpret_cast<const uint8*>(Utf8.Get()),Utf8.Length());
    if(Frame)
    {
        const int32 RGBOffset=Outbound.AddUninitialized(CameraBytes);
        uint8* Destination=Outbound.GetData()+RGBOffset;
        for(const FColor& Pixel:Frame->Pixels)
        {
            *Destination++=Pixel.R;*Destination++=Pixel.G;*Destination++=Pixel.B;
        }
        FreeCameraReadbacks.Add(MoveTemp(Frame));
    }
    Outbound.Append(LidarBytes);
    LastSendProgressWall=FPlatformTime::Seconds();return true;
}

void URosBridgeComponent::Advance()
{
    if(!Listener||!IsValid(Rig))return;
    const double Now=FPlatformTime::Seconds();

    const double SimTime=FMath::Max(0.,SolverTime()-EpochSolverTime);
    if(bMatchAutonomy)
        OperatorGyroRateRadS=bOperatorGyroAvailable&&IsValid(Referee)&&Referee->IsSimMatchAutonomyActive(Rig)
            ? FMath::Min(MatchOperatorGyroRateRadS,Rig->GetDriveParameter(TEXT("GyroMaxRadS"))) : 0.;
    const bool bChassisPowerCut=bControl&&IsValid(Referee)&&!Referee->IsChassisPoweredFor(Rig);
    if(bChassisPowerCut)
    {
        LastCommandWall=-1;
        CommandStatus=TEXT("referee_chassis_power_cut");
        Rig->RenewExternalDriveLease(0);
        Rig->SetContactDrive(0,0,true);
    }
    if(Client&&Now-LastPollWall>.25)
    {
        if(Rig->GetDedicatedMujocoPeriod()>0.)
        {
            ++GameTickStalls;
            LastGameTickGapMs=(Now-LastPollWall)*1000.;
            StallCommandFloor=SimTime;
            LastCommandWall=-1.;CommandStatus=TEXT("game_tick_stall_brake");
            if(bControl){Rig->RenewExternalDriveLease(0);Rig->SetContactDrive(0,0,true);}
            if(AimController)AimController->Stop(CommandStatus);
            UE_LOG(LogRMSim,Warning,TEXT("ROS_BRIDGE_STALL_BRAKE gap_ms=%.2f solver_delta_s=%.4f warnings=%d"),
                LastGameTickGapMs,SimTime-LastSimTime,Rig->GetMujocoWarningCount());
        }
        else Disconnect(TEXT("game_tick_stall"));
    }
    LastPollWall=Now;++TickIndex;
    if(RateWindowWall<=0||SimTime<RateWindowSim){RateWindowWall=Now;RateWindowSim=SimTime;}
    else if(Now-RateWindowWall>=1.)
    {
        RealTimeFactor=(SimTime-RateWindowSim)/(Now-RateWindowWall);
        RateWindowWall=Now;RateWindowSim=SimTime;
    }
    const double SimDelta=FMath::Max(0.,SimTime-LastSimTime);LastSimTime=SimTime;
    // Apply only the previously accepted aim command over elapsed physics time.
    if(AimController)AimController->Advance(SimDelta);
    const bool Dedicated=Rig->GetDedicatedMujocoPeriod()>0;
    if(Dedicated)
    {
        // Act on incoming brake/velocity packets before the LiDAR batch and RGB
        // readback can consume a render frame. The worker still checks the lease.
        if(Client)Receive(SimTime);
        if(bControl&&bOperatorBrake)
        {
            Rig->RenewExternalDriveLease(0);
            Rig->SetContactDrive(0,0,true);
        }
        Rig->SubmitDedicatedMujocoInputs();
    }
    const bool UsesMount=(Imu&&Imu->UsesArticulatedMount())||(Lidar&&Lidar->UsesArticulatedMount());
    const auto CaptureMount=[&](double At)
    {
        if(!Client||!UsesMount)return;
        if(SensorMountSamples.Num()>=64){SensorMountSamples.RemoveAt(0,1,EAllowShrinking::No);++DroppedSensorMountSamples;}
        SensorMountSamples.Add(MakeShared<FJsonValueObject>(Rig->GetSensorJointState(FMath::RoundToInt64(At*1.e9))));
    };
    if(Lidar&&Dedicated)
    {
        Rig->ReplayDedicatedLidarFrames(Client!=nullptr,[&](double At)
        {
            if(At<EpochSolverTime)return;
            const double Relative=At-EpochSolverTime;
            CaptureMount(Relative);
            if(Imu)Imu->Advance(Relative,Client!=nullptr,EpochSolverTime);
            Lidar->Advance(Relative,Client!=nullptr,EpochSolverTime);
        });
    }
    else
    {
        // Multiple visual updates can consume the same completed solver state.
        // Dynamic TF samples must advance strictly within their epoch.
        if(SimDelta>0)CaptureMount(SimTime);
        if(Imu)Imu->Advance(SimTime,Client!=nullptr,EpochSolverTime);
        if(Lidar)Lidar->Advance(SimTime,Client!=nullptr,EpochSolverTime);
    }
    // Retire an EOF peer before deciding whether a newly queued connection is
    // a competing client; otherwise an immediate reconnect can lose one attempt.
    if(Client&&!Dedicated)Receive(SimTime);
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
                StallCommandFloor=-1.;GameTickStalls=0;LastGameTickGapMs=0.;
                Session=FGuid::NewGuid().ToString();AcceptedSeq=-1;MatchStanceSequence=-1;MatchAmmoPurchaseSequence=-1;
                LastCommandWall=-1;CommandStatus=bControl?TEXT("waiting"):TEXT("manual_mode");
                NavigationFrameYawRad=-FMath::DegreesToRadians(Rig->GetActorRotation().Yaw);
                LastStateWall=0;NextImageSim=SimTime+((CameraHz>0.0)?(CapturePhaseFraction/CameraHz):0.0);Inbound.Reset();Outbound.Reset();SendOffset=0;
                if(AimController)AimController->Reset(false);
                if(Imu)Imu->DiscardPending();
                if(Lidar)Lidar->DiscardPending();
            }
        }
    }
    if(bControl&&!bChassisPowerCut&&(bOperatorBrake||LastCommandWall<0||Now-LastCommandWall>.25))
    {
        Rig->RenewExternalDriveLease(0);Rig->SetContactDrive(0,0,true);
        if(bOperatorBrake)CommandStatus=TEXT("operator_brake");
        else if(LastCommandWall>=0)CommandStatus=TEXT("expired");
    }
    const bool bWasFpvVideoCut=bFpvVideoCut;
    bFpvVideoCut=IsValid(Referee)&&Referee->IsFpvVideoCutFor(Rig);
    if(bFpvVideoCut&&!bWasFpvVideoCut)
    {
        ++CameraConnection; // Pending GPU readbacks will retire without being published.
        if(bOutboundHasCameraFrame&&!Outbound.IsEmpty())
        {
            if(SendOffset>0)Disconnect(TEXT("referee_fpv_video_cut")); // Preserve framing after an incomplete write.
            else Outbound.Reset();
        }
    }
    if(!bFpvVideoCut&&bWasFpvVideoCut)NextImageSim=SimTime;
    if(Client)SendPending();
    AdvanceCamera(SimTime);
    const double StateInterval=CameraHz>0?FMath::Min(.02,1./CameraHz):.02;
    // A completed sensor frame is delivered on its own simulation cadence, including100Hz clouds.
    // Existing camera/control heartbeat cadence remains available between cloud boundaries.
    if(Client&&Outbound.IsEmpty()&&(bMotorControl||(Lidar&&Lidar->HasPendingScans())||Now-LastStateWall>=StateInterval))
    {
        if(!QueueState(SimTime)){Disconnect(TEXT("state_encoding_failed"));return;}
        LastStateWall=Now;SendPending();
    }
}

void URosBridgeComponent::ResetEpoch()
{
    ++Epoch;EpochSolverTime=SolverTime();TickIndex=0;NextImageSim=(CameraHz>0.0)?(CapturePhaseFraction/CameraHz):0.0;LastSimTime=0;RealTimeFactor=0;RateWindowWall=0;RateWindowSim=0;Disconnect(TEXT("reset"));
    if(AimController)AimController->Reset(true);
    if(Imu)Imu->Reset();
    if(Lidar)Lidar->Reset();
}

void URosBridgeComponent::SetMapIdentity(const FString& Id,const FString& Source,bool Mesh,bool SavedScene)
{MapId=Id;MapHash=LexToString(FMD5Hash::HashFile(*Source));MapKind=SavedScene?TEXT("rm_scene_map"):Mesh?TEXT("rm_mesh_map"):TEXT("rm_flat_map");}

void URosBridgeComponent::SetOperatorGyro(double RateRadS)
{
    if(bMatchAutonomy)
    {
        MatchOperatorGyroRateRadS=FMath::IsFinite(RateRadS)?
            FMath::Clamp(RateRadS,0.,Rig->GetDriveParameter(TEXT("GyroMaxRadS"))):0.;
        return;
    }
    OperatorGyroRateRadS=bOperatorGyroAvailable&&FMath::IsFinite(RateRadS)?
        FMath::Clamp(RateRadS,0.0,Rig->GetDriveParameter(TEXT("GyroMaxRadS"))):0.0;
}

void URosBridgeComponent::SetReferee(ARMUCGameReferee* InReferee)
{
    Referee=InReferee;
    if(AimController)AimController->SetReferee(InReferee);
}

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
