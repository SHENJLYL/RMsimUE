#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "WTFoliageCrushComponent.generated.h"

class AVehicleRig;
class UHierarchicalInstancedStaticMeshComponent;
class AStaticMeshActor;

/** Source-preserving instance-local vegetation and lightweight breakable-prop responses. */
UCLASS()
class RMSIM_API UWTFoliageCrushComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    void Initialize(UWorld* World);
    void Advance(const AVehicleRig* Rig, double Seconds);
    int32 GroupCount() const { return Groups.Num(); }
    int32 CrushCount() const { return TotalCrushed; }
    int32 BreakCount() const { return TotalBroken; }
    int32 ActiveAnimations() const { return Falling.Num(); }
    // Disposable native scene fixture; verifies instance-local removal and animation lifecycle.
    bool ProbeOneOfEach(TArray<FString>& Outcomes);
private:
    enum class EKind : uint8 { Soft, WoodBend, WoodFracture, MetalBend };
    struct FGroup
    {
        TWeakObjectPtr<UHierarchicalInstancedStaticMeshComponent> Visual;
        TWeakObjectPtr<UHierarchicalInstancedStaticMeshComponent> Collider;
        EKind Kind=EKind::Soft;
        TSet<int32> Removed;
        TMap<int32,float> PressureSeconds;
        TMap<int32,float> ApproachPeakMps;
    };
    struct FFalling
    {
        TWeakObjectPtr<AStaticMeshActor> Actor;
        FTransform OriginalWorld;
        FVector Root=FVector::ZeroVector;
        FVector Axis=FVector::RightVector;
        FVector Travel=FVector::ForwardVector;
        float Elapsed=0;
        float AngleAmplitude=1.2f;
        float Duration=4.3f;
        float Hold=3.1f;
        bool bWood=false;
    };
    TArray<FGroup> Groups;
    TArray<FFalling> Falling;
    double ScanAccumulator=0;
    int32 TotalCrushed=0,TotalBroken=0;
    double WoodImpactMps=2.2,MetalImpactMps=3.3;
    double WoodPressureSeconds=.72,MetalPressureSeconds=1.4;
    static bool IsSoftShrub(const FString& MeshName);
    static bool ClassifyHard(const FString& MeshName,EKind& Kind);
    void Trigger(FGroup& Group,int32 Index,const FVector& VehicleLocationCm,
        const FVector& Direction,bool HasHit);
    void SpawnSplinters(const FGroup& Group,const FTransform& Original,
        const FVector& Direction,int32 Count);
};
