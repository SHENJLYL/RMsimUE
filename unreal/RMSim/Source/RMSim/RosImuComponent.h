#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "RosImuComponent.generated.h"
class AVehicleRig;
class FJsonObject;
class FJsonValue;

UCLASS()
class RMSIM_API URosImuComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    bool Initialize(AVehicleRig* Vehicle,const FString& Profile,FString& Error);
    void Advance(double SimTime,bool Collect,double EpochSolverTime=0.);
    void Reset();
    void DiscardPending();
    bool UsesId(const FString& Id) const;
    bool UsesArticulatedMount() const;
    TSharedRef<FJsonObject> TakeState();
private:
    struct FChannel
    {
        FString Id,ParentFrame;
        FTransform PreviousRelative;
        bool HasRelativeHistory=false;
        double PreviousRelativeTime=0;
        FTransform Mount;
        FVector OriginMetres,AccelBias,GyroBias,AccelStd,GyroStd;
        double Rate=200,NextDue=0,PreviousTime=0;
        int64 NextSampleIndex=0;
        double AccelRange=0,GyroRange=0,AccelQuantum=0,GyroQuantum=0,DeliveryNotBefore=0;
        FVector PreviousVelocity=FVector::ZeroVector;
        bool HasHistory=false;
        int32 Seed=0;
        FRandomStream Random;
        int64 Sequence=0,MissedPeriods=0,Dropped=0,HistoryResets=0,Invalid=0;
        int64 AccelSaturatedAxes=0,GyroSaturatedAxes=0,HistoryMisses=0,WarmupSamples=0,DeliverySuppressed=0;
        TArray<TSharedPtr<FJsonValue>> Pending;
    };
    UPROPERTY() AVehicleRig* Rig=nullptr;
    TArray<FChannel> Channels;
    FString RobotId,ProfileHash;
    uint64 MotionGeneration=0;
    double GravityZ=-980;
    bool bPhysicalV3=false,bProfileTiming=false;
    double LastAdvanceTime=0,ProfileStartSim=0,ProfileSampleWall=0,ProfilePackWall=0;
    int64 ProfileSampleCount=0;
    static constexpr double NativeIntegrationSeconds = .005;
    static double Gaussian(FRandomStream& Random);
    void AdvanceNative(double Time,bool Collect,double EpochSolverTime);
    int32 PendingSamplesPerChannel() const;
    void PublishSample(FChannel& Channel,double Time,double IntegrationDt,FVector Accel,FVector Gyro,bool Collect,int64 SampleIndex=-1);
};
