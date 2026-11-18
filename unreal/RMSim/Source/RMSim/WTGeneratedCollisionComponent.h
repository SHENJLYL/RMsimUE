#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Engine/AssetUserData.h"
#include "WTGeneratedCollisionComponent.generated.h"

class AVehicleRig;
class UStaticMesh;
class UHierarchicalInstancedStaticMeshComponent;

// Binds each cooked, derived proxy to the exact local FBX and generation recipe.
UCLASS()
class RMSIM_API UWTGeneratedCollisionData : public UAssetUserData
{
    GENERATED_BODY()
public:
    UPROPERTY() FString SourceFbxSha256;
    UPROPERTY() FString RecipeSha256;
    UPROPERTY() FString CollisionMode;
    UPROPERTY() int32 SourceTriangles = 0;
};

// Optional Flanders supplement. Source visual actors and source collider counts
// remain unchanged; these static bodies are resident around the rig and spawn.
UCLASS(ClassGroup=(RMSim))
class RMSIM_API UWTGeneratedCollisionComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UWTGeneratedCollisionComponent();
    bool Initialize(AVehicleRig* Vehicle, const FString& MapFile,
        const FString& SourceSha256, FString& Error);
    bool Advance(FString& Error);
    bool InitializeAudit(const FString& ManifestFile, const FVector& AnchorWorldCm,
        double QueryRangeMetres, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|GeneratedCollision")
    bool RefreshAtWorldPoint(const FVector& WorldCm, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|GeneratedCollision")
    FString Describe() const;
    bool IsReady() const;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;

    static bool ValidateMesh(UStaticMesh* Mesh, FString& Error);
private:
    struct FCell
    {
        FString Id;
        int32 MeshIndex = INDEX_NONE;
        int32 SourceInstances = 0;
        int32 NativeBodies = 0, NativeShapes = 0;
        FBox BoundsWorldCm = FBox(ForceInit);
        TArray<FTransform> Transforms;
        TWeakObjectPtr<UHierarchicalInstancedStaticMeshComponent> Component;
    };
    UPROPERTY() AVehicleRig* Rig = nullptr;
    UPROPERTY() TArray<TObjectPtr<UStaticMesh>> Meshes;
    TArray<FCell> Cells;
    FVector SpawnAnchor = FVector::ZeroVector;
    FVector LastPosition = FVector::ZeroVector;
    double QueryRangeM = 0;
    double LoadRadiusCm = 0;
    double RetainRadiusCm = 0;
    int64 SourceInstances = 0;
    int64 PeakBodies = 0;
    int32 ActivationPasses = 0;
    int32 CellsCreated = 0;
    int32 CellsReleased = 0;
    double InitialLoadSeconds = 0;
    double MaxRefreshSeconds = 0;
    FString ManifestSha1, RecipeSha256, MapSourceSha256;
    bool bLoaded = false;
    bool Load(const FString& ManifestFile, const FString& ExpectedSource, FString& Error);
    bool Refresh(const FVector& Position, bool Force, FString& Error);
};
