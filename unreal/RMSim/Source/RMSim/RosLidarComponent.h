#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Engine/EngineTypes.h"
#include "Sensors/ScanCadence.h"
#include "Sensors/Mid360Model.h"
#include "RosLidarComponent.generated.h"
class AVehicleRig;
class FJsonObject;

UCLASS()
class RMSIM_API URosLidarComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    bool Initialize(AVehicleRig* Vehicle,const FString& Profile,FString& Error,double RayScale=1.0);
    void Advance(double Time,bool Connected,double EpochSolverTime=0.);
    void Reset();
    void DiscardPending();
    bool UsesArticulatedMount() const { return bArticulated; }
    bool HasPendingScans() const { return !Pending.IsEmpty(); }
    const FString& GetSensorId() const { return Id; }
    TSharedRef<FJsonObject> TakeState(TArray<uint8>& Bytes,int32 Offset);
private:
    UPROPERTY() AVehicleRig* Rig=nullptr;
    FString Id,ProfileHash,ParentFrame;
    bool bArticulated=false,bProfileTiming=false,bSerialQuery=false,bVerifyQueries=false;
    bool bMid360=false,bIncidenceCosine=true;
    static constexpr int64 MaxVerificationRays=4096;
    int64 VerifiedRays=0,VerifiedQueries=0;
    double VerificationMaxErrorCm=0;
    double ProfileRayWall=0,ProfileStartSim=0;
    double ProfileSetupWall=0,ProfileQueryWall=0,ProfileOpticalWall=0,ProfilePackWall=0;
    int64 ProfileRayCount=0;
    int64 WindowPasses=0,SelfBlocked=0;
    FTransform Mount;
    FVector Origin;
    double RaysPerSecond=0,FrameHz=0,MinElevation=0,MaxElevation=0,MinRange=0,MaxRange=0,NoiseStd=0;
    double PreviousTime=-1,FrameStart=-1,FrameEnd=0,RayRemainder=0,MaxStep=0;
    RMSimSensors::ScanCadence Cadence;
    RMSimSensors::Mid360PulseClock PulseClock;
    double AngularNoiseStd=0,NearNoiseStd=0,NoiseNearDistance=.2,NoiseReferenceDistance=10;
    double DefaultNirReflectance=.1,RangeLowReflectance=.1,RangeLowMetres=40,RangeHighReflectance=.8,RangeHighMetres=70;
    TMap<FString,double> NirMaterialReflectance;
    TArray<FVector2D> RecordedDirections;
    FString DirectionSequenceHash;
    TSharedPtr<FJsonObject> ModelProvenance;
    int32 PendingScanLimit=2;
    int64 CurrentScanOrdinal=-1;
    int64 DeliveryStartOrdinal=0,DeliverySuppressedSlots=0;
    int64 NoHitRays=0,TooNearRays=0,TooFarRays=0,WeakReturnRays=0,MissingPoseRays=0;
    int64 DisconnectedRays=0,OpticalLimitRays=0,NoiseRejectedRays=0,SkippedScanWindows=0,DefaultMaterialRays=0,IncompleteScanDrops=0;
    int32 Reflectivity=0,Seed=0;
    int64 RayIndex=0,Traced=0,Hits=0,MissedRays=0,DroppedScans=0,ScanSequence=0,HistoryResets=0;
    uint64 MotionGeneration=0;
    FRandomStream Random;
    TArray<uint8> Building;
    struct FScan {int64 Sequence=0;double Start=0,End=0;TArray<uint8> Bytes;};
    TArray<FScan> Pending;
    struct FRay
    {
        int64 Index=0,StampNs=0;
        FVector Local,Direction,Start,RayStart,End;
        FHitResult Hit;
        double NirReflectance=0;
        bool bHit=false,bFound=false,bPoseValid=true,bVerify=false,bOpticalLimit=false;
    };
    void FinishScan();
    bool InitializeMid360(const TSharedPtr<FJsonObject>& Json,const FString& Profile,FString& Error);
    void AdvanceMid360(double Time,bool Connected,double EpochSolverTime);
    void TraceRays(TArray<FRay>& Rays);
    void SelectScan(int64 Ordinal);
    double Gaussian(int64 Index,uint32 Dimension) const;
    void LogTiming(double Time,double Started,int32 Count);
};
