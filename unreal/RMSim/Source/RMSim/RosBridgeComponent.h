#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "RosBridgeComponent.generated.h"

class AVehicleRig;
class USceneCaptureComponent2D;
class UTextureRenderTarget2D;
class FSocket;
class ISocketSubsystem;
class FJsonObject;
class FJsonValue;
class ATrainingRange;
class URosAimController;
class URosImuComponent;
class URosLidarComponent;

UCLASS()
class RMSIM_API URosBridgeComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    bool Initialize(AVehicleRig* Vehicle,USceneCaptureComponent2D* Aim,ATrainingRange* Training,bool Control,FString& Error);
    void Advance();
    void ResetEpoch();
    void SetOperatorBrake(bool Brake);
    FString StatusText() const;
    bool HasClient() const { return Client!=nullptr; }
    void SetMapIdentity(const FString& Id,const FString& Source,bool Mesh,bool SavedScene=false);
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
private:
    UPROPERTY() AVehicleRig* Rig=nullptr;
    UPROPERTY() USceneCaptureComponent2D* Camera=nullptr;
    UPROPERTY() UTextureRenderTarget2D* Target=nullptr;
    UPROPERTY() URosAimController* AimController=nullptr;
    UPROPERTY() URosImuComponent* Imu=nullptr;
    UPROPERTY() URosLidarComponent* Lidar=nullptr;
    ISocketSubsystem* Sockets=nullptr;
    FSocket* Listener=nullptr;
    FSocket* Client=nullptr;
    FString RobotId,Session,CommandStatus=TEXT("waiting"),LastRejection;
    FString LastDisconnect;
    FString MapId,MapHash,MapKind;
    int64 SlowReaderDisconnects=0;
    bool bControl=false,bOperatorBrake=false,bMotorControl=false;
    bool bStallProbe=false,bStallInjected=false,bProfileSensorTiming=false;
    double ProfileCameraWall=0,ProfileCameraStartSim=0;
    int64 ProfileCameraCount=0;
    int32 Port=9451,Width=1280,Height=720,SendOffset=0;
    int64 Epoch=0,TickIndex=0,AcceptedSeq=-1,RejectedCommands=0,Images=0;
    // Generic ROS retains 10Hz RGB; zero disables pixels while preserving TF/calibration.
    double CameraHz=10,HFovDegrees=60,EpochSolverTime=0,LastCommandWall=-1,LastPollWall=0,LastStateWall=0,LastSendProgressWall=0,NextImageSim=0;
    TArray<TSharedPtr<FJsonValue>> SensorMountSamples;
    int64 DroppedSensorMountSamples=0;
    TArray<uint8> Inbound,Outbound;
    TArray<FColor> Pixels;
    double LastSimTime=0,RealTimeFactor=0,RateWindowWall=0,RateWindowSim=0;
    double SolverTime() const;
    void CloseSocket(FSocket*& Socket);
    void Disconnect(const FString& Reason);
    void Reject(const FString& Reason);
    bool ReadCommand(const TArray<uint8>& Line,double SimTime);
    void Receive(double SimTime);
    void SendPending();
    bool QueueState(double SimTime,bool Capture);
};
