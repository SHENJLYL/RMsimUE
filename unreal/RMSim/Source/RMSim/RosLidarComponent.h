#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "RosLidarComponent.generated.h"
class AVehicleRig;
class FJsonObject;

UCLASS()
class RMSIM_API URosLidarComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    bool Initialize(AVehicleRig* Vehicle,const FString& Profile,FString& Error);
    void Advance(double Time,bool Connected);
    void Reset();
    void DiscardPending();
    bool UsesArticulatedMount() const { return bArticulated; }
    const FString& GetSensorId() const { return Id; }
    TSharedRef<FJsonObject> TakeState(TArray<uint8>& Bytes,int32 Offset);
private:
    UPROPERTY() AVehicleRig* Rig=nullptr;
    FString Id,ProfileHash,ParentFrame;
    bool bArticulated=false;
    int64 WindowPasses=0,SelfBlocked=0;
    FTransform Mount;
    FVector Origin;
    double RaysPerSecond=0,FrameHz=0,MinElevation=0,MaxElevation=0,MinRange=0,MaxRange=0,NoiseStd=0;
    double PreviousTime=-1,FrameStart=-1,FrameEnd=0,RayRemainder=0,MaxStep=0;
    int32 Reflectivity=0,Seed=0;
    int64 RayIndex=0,Traced=0,Hits=0,MissedRays=0,DroppedScans=0,ScanSequence=0,HistoryResets=0;
    uint64 MotionGeneration=0;
    FRandomStream Random;
    TArray<uint8> Building;
    struct FScan {int64 Sequence=0;double Start=0,End=0;TArray<uint8> Bytes;};
    TArray<FScan> Pending;
    void FinishScan();
};
