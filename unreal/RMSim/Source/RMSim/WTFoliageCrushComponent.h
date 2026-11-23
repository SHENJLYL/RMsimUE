#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "ProceduralMeshComponent.h"
#include "WTFoliageCrushComponent.generated.h"

class AVehicleRig;
class UHierarchicalInstancedStaticMeshComponent;
class AActor;
class UMaterialInterface;

/** Local source-geometry damage driven by current, instance-exact Chaos contacts. */
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
    int32 ActiveAnimations() const;
    bool ProbeOneOfEach(TArray<FString>& Outcomes);
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
private:
    enum class EKind : uint8 { Soft, WoodRail, WoodBoard };
    struct FPressure
    {
        double Seconds=0, LastContact=-1;
        double ImpulseNs=0;
        FVector LastNormal=FVector::ZeroVector;
    };
    struct FSoftRemainder
    {
        TWeakObjectPtr<AActor> Actor;
        FTransform Original;
        TArray<FProcMeshSection> Sections;
    };
    struct FGroup
    {
        TWeakObjectPtr<UHierarchicalInstancedStaticMeshComponent> Visual;
        TWeakObjectPtr<UHierarchicalInstancedStaticMeshComponent> Collider;
        EKind Kind=EKind::Soft;
        TSet<int32> Removed;
        TMap<int32,FPressure> Pressure;
        TMap<int32,FSoftRemainder> SoftRemainders;
        TArray<FProcMeshSection> Template;
        TArray<UMaterialInterface*> Materials;
        bool bGeometryTried=false;
    };
    struct FLeafFall
    {
        TWeakObjectPtr<AActor> Actor;
        TWeakObjectPtr<UProceduralMeshComponent> Mesh;
        FTransform Original;
        TArray<FProcMeshSection> Initial;
        FVector Direction=FVector::ForwardVector;
        FVector SweepFrom=FVector::ZeroVector,SweepTo=FVector::ZeroVector;
        double GroundZ=0, Elapsed=0;
    };
    TArray<FGroup> Groups;
    TArray<FLeafFall> Leaves;
    TArray<TWeakObjectPtr<AActor>> Debris,Remainders;
    FVector PreviousVehicle=FVector::ZeroVector;
    double ScanAccumulator=0;
    uint64 ContactCursor=0,MotionGeneration=MAX_uint64;
    int32 TotalCrushed=0,TotalBroken=0;
    double RailImpactMps=3.5,BoardImpactMps=2.5;
    double RailImpulseNs=10,BoardImpulseNs=6;
    double RailPushNewtons=90,BoardPushNewtons=150;
    double RailPushSeconds=3.0,BoardPushSeconds=2.5;
    static bool IsSoftShrub(const FString& Name);
    static bool ClassifyHard(const FString& Name,EKind& Kind);
    bool EnsureGeometry(FGroup& Group);
    bool CrushPatch(FGroup& Group,int32 Index,const FVector& From,const FVector& To,
        const FBox& VehicleBounds,const FVector& Direction);
    bool BreakLocal(FGroup& Group,int32 Index,const FVector& Point,
        const FVector& Direction,double ImpulseNs,double SpeedMps,const AVehicleRig* Rig);
    void AdvanceLeaves(double Seconds);
};
