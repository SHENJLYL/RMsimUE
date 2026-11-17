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
    void Advance(double SimTime,bool Collect);
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
        FVector PreviousVelocity=FVector::ZeroVector;
        bool HasHistory=false;
        int32 Seed=0;
        FRandomStream Random;
        int64 Sequence=0,MissedPeriods=0,Dropped=0,HistoryResets=0,Invalid=0;
        TArray<TSharedPtr<FJsonValue>> Pending;
    };
    UPROPERTY() AVehicleRig* Rig=nullptr;
    TArray<FChannel> Channels;
    FString RobotId,ProfileHash;
    uint64 MotionGeneration=0;
    double GravityZ=-980;
    static double Gaussian(FRandomStream& Random);
};
