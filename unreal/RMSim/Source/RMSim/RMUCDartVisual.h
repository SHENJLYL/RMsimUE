#pragma once

#include "CoreMinimal.h"

class AActor;
class UPrimitiveComponent;
class UProceduralMeshComponent;
class USceneComponent;
struct FHitResult;

class FRMUCDartVisual
{
public:
    bool Spawn(AActor& Owner, bool bRed);
    void ShowAt(const FVector& Position, const FVector& Direction, double Now);
    bool Land(const FHitResult& Contact, double Now);
    void Update(double Now);
    void Finish(double Now);
    void Destroy();

private:
#if WITH_DEV_AUTOMATION_TESTS
    friend class ARMUCGameReferee;
#endif
    struct FTrailSample
    {
        double Time;
        FVector Position;
    };

    TWeakObjectPtr<AActor> Actor;
    TWeakObjectPtr<USceneComponent> Body;
    TWeakObjectPtr<UProceduralMeshComponent> Trail;
    TArray<FTrailSample> History;
    FLinearColor TeamColor = FLinearColor::White;
    double LastTrailUpdate = -1.;
    FVector LandingPoint = FVector::ZeroVector;
    FVector LandingNormal = FVector::UpVector;
    FVector IncomingTip = FVector::ZeroVector;
    FQuat IncomingRotation = FQuat::Identity;
    FQuat RestingRotation = FQuat::Identity;
    TWeakObjectPtr<UPrimitiveComponent> LandingSurface;
    double LandingAt = -1.;
#if WITH_DEV_AUTOMATION_TESTS
    bool bLandingSupported = false;
#endif

    void FadeTrail(double Now);
};
