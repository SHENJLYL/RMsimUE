#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "TrainingRange.generated.h"

class AVehicleRig;
class UStaticMeshComponent;
class UMaterialInstanceDynamic;
class FJsonObject;
class FJsonValue;

UCLASS()
class RMSIM_API ATrainingRange : public AActor
{
    GENERATED_BODY()
public:
    ATrainingRange();
    bool Initialize(AVehicleRig* Vehicle,FString& Error);
    void Advance(double Seconds,bool Fire,bool Reset,const FTransform& Muzzle);
    void AdvanceInterval(double Seconds,bool Fire,bool Reset,const FTransform& StartMuzzle,const FTransform& EndMuzzle);
    FString Hud() const;
    TSharedRef<FJsonObject> State(bool IncludeEvents=true) const;
    FVector GetTargetPoint(int32 Index) const;
    int32 GetTargetHealth(int32 Index) const;
    FVector PredictImpact(const FTransform& Muzzle) const;
    const FString& GetLastHitActor() const { return LastHitActor; }
    double GetLaunchSpeed() const { return LaunchSpeedCm; }
    double GetGravity() const { return GravityCm; }
    int32 GetAmmo() const { return Ammo; }
    int32 GetShots() const { return Shots; }
    int32 GetHits() const { return Hits; }
    int32 GetEpoch() const { return Epoch; }
    int32 GetLiveProjectiles() const { return Projectiles.Num(); }
    double GetHeat() const { return Heat; }
    const FString& GetStatus() const { return Status; }
    void ResetRound();
private:
    struct FTarget
    {
        UStaticMeshComponent* Armor=nullptr;
        UMaterialInstanceDynamic* Material=nullptr;
        int32 Health=0;
    };
    struct FShot
    {
        FVector Position,Velocity;
        double Age=0;
        UStaticMeshComponent* Visual=nullptr;
        int32 Number=0;
    };
    UPROPERTY() AVehicleRig* Rig=nullptr;
    TArray<FTarget> Targets;
    TArray<FShot> Projectiles;
    double SimTime=0,NextShotTime=0,RoundSeconds=180,Heat=0;
    double LaunchSpeedCm=2500,GravityCm=981,ShotInterval=.1,HeatPerShot=10,MaxHeat=100,Cooling=20;
    int32 StartingAmmo=100,TargetMaxHealth=100,Damage=20;
    int32 Ammo=0,Shots=0,Hits=0,Score=0,Epoch=0;
    FString Status=TEXT("ready"),LastEvent;
    FString LastHitActor;
    TArray<TSharedPtr<FJsonValue>> Events;
    UStaticMeshComponent* Visual(const TCHAR* MeshPath,const FVector& Scale,const FLinearColor& Color,bool Blocking);
    void FireShot(const FTransform& Muzzle);
    void StepProjectiles(double Seconds);
    void Finish(const FString& Result);
    void Event(const FString& Type,int32 Target=-1,int32 Shot=-1);
};
