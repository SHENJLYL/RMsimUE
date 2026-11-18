#pragma once
#include "CoreMinimal.h"

class AStaticMeshActor;
class UWorld;
class FJsonObject;

struct FMapMesh
{
    FString Id, VisualAsset, CollisionAsset;
};

struct FMapBox
{
    FString Id;
    FVector CenterMetres;
    FVector SizeMetres;
    double YawRadians = 0;
    FVector Color;
};
struct FMapSpawn
{
    FString Id, Team;
    FVector GroundMetres;
    double YawRadians = 0;
};
struct FMapRegion
{
    FString Id, Kind, Team;
    FVector2D CenterMetres, SizeMetres;
};
struct FMapStage
{
    FString Id, DisplayName, SourceFile, InspectionObstacleId;
    FVector2D FloorSizeMetres;
    double FloorZ = 0, FloorThickness = 0.2, WallHeight = 1.5, WallThickness = 0.2;
    FVector FloorColor;
    TArray<FMapBox> Obstacles;
    TArray<FMapSpawn> Spawns;
    TArray<FMapRegion> Regions;
    bool bMeshTerrain = false;
    bool bSavedScene = false;
    FString LevelAsset, SceneSourceHash;
    TArray<FMapMesh> Meshes;
    FBox SourceBoundsMetres = FBox(ForceInit);

    bool Load(const FString& Filename, FString& Error);
    bool LoadMeshMap(const TSharedPtr<FJsonObject>& Root, FString& Error);
    bool LoadSceneMap(const TSharedPtr<FJsonObject>& Root, FString& Error);
    bool ValidateLoadedScene(UWorld* World, FString& Error) const;
    bool IsSceneCollisionReady(UWorld* World) const;
    void AddSceneReport(UWorld* World, const TSharedRef<FJsonObject>& Report) const;
    bool SpawnGeometry(UWorld* World, TMap<FString, AStaticMeshActor*>& Boxes, FString& Error) const;
    TArray<FString> RegionsAt(const FVector& WorldCentimetres) const;
    bool WriteReport(const FString& Filename, const FMapSpawn& Spawn,
        const TMap<FString, AStaticMeshActor*>& Boxes, const FVector& VehicleLocation, bool Overlapping, UWorld* World = nullptr) const;
};
