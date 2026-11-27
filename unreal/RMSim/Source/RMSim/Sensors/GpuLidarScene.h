#pragma once

#include "CoreMinimal.h"

class AVehicleRig;
class URosLidarComponent;
class UPrimitiveComponent;
class UWorld;
class USceneComponent;
struct FGpuLidarRenderState;

struct FGpuLidarNode
{
    FVector3f Min;
    uint32 FirstAndCount = 0;
    FVector3f Max;
    uint32 Escape = 0;
};

struct FGpuLidarPrimitive
{
    FVector3f A, B, C;
    uint32 Material = 0;
    uint32 Flags = 0;
    uint32 PlaneCount = 0;
};

struct FGpuLidarInstance
{
    FVector4f ToLocalX, ToLocalY, ToLocalZ;
    uint32 Begin = 0, End = 0, Vehicle = 0, Mirrored = 0;
};

struct FGpuLidarRay
{
    FVector3f Origin;
    uint32 Snapshot = 0;
    FVector3f Direction;
    uint32 Vehicle = 0;
    float RangeCm = 0;
    uint32 Padding[3] = {};
};

struct FGpuLidarHit
{
    FVector3f Normal;
    float DistanceCm = 0;
    uint32 Material = 0, Flags = 0, Windows = 0, Primitive = 0;
    uint32 Instance = MAX_uint32, Plane = MAX_uint32, Padding[2] = {};
};

static_assert(sizeof(FGpuLidarNode) == 32 && sizeof(FGpuLidarPrimitive) == 48);
static_assert(sizeof(FGpuLidarInstance) == 64 && sizeof(FGpuLidarRay) == 48 && sizeof(FGpuLidarHit) == 48);

class FGpuLidarScene final
{
public:
    using FResults = TUniqueFunction<void(TConstArrayView<FGpuLidarHit>, TConstArrayView<FTransform>)>;

    FGpuLidarScene();
    ~FGpuLidarScene();
    bool Initialize(UWorld& World, const TMap<const USceneComponent*, int32>& NativeBodyIndices, FString& Error);
    void CaptureSnapshot(const TArray<FTransform>& NativeBodies,bool bReplayedChaos);
    bool HasReferenceSnapshot() const { return bHasReferenceSnapshot; }
#if WITH_DEV_AUTOMATION_TESTS
    TArray<FGpuLidarRay> MakeVehicleCoverageRays(uint32 Vehicle) const;
#endif
    void AddRays(TArray<FGpuLidarRay>&& Rays, FResults&& Complete);
    void Drain();
    void Submit();
    uint32 VehicleId(const AVehicleRig* Rig) const;
    FVector RefineImpactPoint(const FGpuLidarHit& Hit,const FVector& Origin,const FVector& Direction,
        TConstArrayView<FTransform> InstancePoses) const;
    TConstArrayView<FName> GetMaterials() const { return Materials; }

private:
    struct FGeometry;
    struct FDynamic;
    struct FFrame;

    TArray<FName> Materials{NAME_None};
    TArray<FPlane> ExactTrianglePlanes,ExactSolidPlanes;
    TMap<const AVehicleRig*, uint32> VehicleIds;
    TUniquePtr<FGeometry> Geometry;
    TArray<FDynamic> Dynamic;
    TSharedPtr<FGpuLidarRenderState, ESPMode::ThreadSafe> RenderState;
    TUniquePtr<FFrame> Building;
    TArray<TSharedPtr<FFrame, ESPMode::ThreadSafe>> InFlight;
    uint32 NextSequence = 0;
    bool bHasReferenceSnapshot=false;
};
