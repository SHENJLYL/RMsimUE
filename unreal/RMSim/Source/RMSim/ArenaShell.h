#pragma once
#include "CoreMinimal.h"

class UWorld;
class AStaticMeshActor;
struct FMapStage;

// The RMUC off-field shell is separate from the CAD collision and the Nav2
// raster. Only four hidden, continuous barrier slabs have physics enabled.
// All spectator structure is instanced, no-collision, no-tick visual content.
struct FArenaShell
{
    static bool Spawn(UWorld* World, const FMapStage& Stage,
                      TMap<FString,AStaticMeshActor*>& MapActors, FString& Error);
};
