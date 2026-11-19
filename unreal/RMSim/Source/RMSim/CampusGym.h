#pragma once
#include "CoreMinimal.h"

class UWorld;
class AStaticMeshActor;
class FJsonObject;
struct FMapStage;

// An enclosed campus gym surrounds the unchanged competition map. Doors are
// decorative and permanently shut; four continuous barriers retain the robot.
struct FCampusGym
{
    static bool Spawn(UWorld* World, const FMapStage& Stage,
        const TSharedPtr<FJsonObject>& Manifest, const FString& ManifestFile,
        TMap<FString,AStaticMeshActor*>& Actors, FString& Error);
    static bool IsActive(UWorld* World);
    static bool PreviewCamera(FVector& Position, FVector& Target, float& Fov);
};
