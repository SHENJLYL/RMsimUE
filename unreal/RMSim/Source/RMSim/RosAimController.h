#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "RosAimController.generated.h"

class AVehicleRig;
class ATrainingRange;
class USceneCaptureComponent2D;
class FJsonObject;

UCLASS()
class RMSIM_API URosAimController : public UActorComponent
{
    GENERATED_BODY()
public:
    bool Initialize(AVehicleRig* Vehicle,USceneCaptureComponent2D* Muzzle,ATrainingRange* Range,bool Enabled,FString& Error);
    bool Accept(const TSharedPtr<FJsonObject>& Packet,int64 Sequence,double SimTime);
    void Reject(const FString& Reason);
    void Stop(const FString& Reason);
    void Reset(bool ResetPose);
    void SetOperatorBrake(bool Brake);
    void Advance(double SimSeconds);
    TSharedRef<FJsonObject> State(int64 Stamp) const;
    TSharedRef<FJsonObject> Evaluation() const;
private:
    struct FAxis
    {
        int Mode=0;
        double Position=0,Rate=0,Minimum=0,Maximum=0,Target=0,Direction=1;
    };
    UPROPERTY() AVehicleRig* Rig=nullptr;
    UPROPERTY() USceneCaptureComponent2D* Aim=nullptr;
    UPROPERTY() ATrainingRange* Training=nullptr;
    bool bEnabled=false,bLive=false,bFire=false,bPermit=false,bOperatorBrake=false,bLimited=false;
    bool bBodyFrame=false;
    FAxis Yaw,Pitch;
    FString YawName,PitchName,Status=TEXT("waiting"),LastRejection;
    double PitchMin=0,PitchMax=0,YawRateLimit=0,PitchRateLimit=0;
    double Deadline=0,LastCommandWall=-1,YawVelocity=0,PitchVelocity=0;
    bool bHaveSample=false;
    double PreviousYaw=0,PreviousPitch=0;
    FTransform PreviousMuzzle=FTransform::Identity;
    int64 AcceptedSeq=-1,RejectedCount=0;
    FVector2D WorldAngles() const;
    static void StepAxis(FAxis& Axis,double Seconds);
    void Drive(double Seconds);
    bool CanFire() const;
};
