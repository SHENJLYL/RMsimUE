#pragma once

#include "CoreMinimal.h"
#include "ProceduralMeshComponent.h"

class UStaticMesh;
class UMaterialInterface;
class UWorld;
class AActor;

/** Source-geometry wood damage; callers retire the original instance only on success. */
namespace WTBreakableGeometry
{
    // Requires authoring-time CPU access. Preserves material slots, colors and UV0..3.
    bool CopySections(UStaticMesh* Source, TArray<FProcMeshSection>& Sections,
        TArray<UMaterialInterface*>& Materials);
    // Only the lowest rail or an actual standalone board is eligible, never a post or upper rail.
    bool CanBreakAt(UStaticMesh* Source, const FTransform& Instance,
        const FVector& HitWorld, bool Fence);
    bool Create(UWorld* World, UStaticMesh* Source, const FTransform& Instance,
        const FVector& HitWorld, const FVector& IntoObstacleWorld,
        double ImpulseNs, double NormalSpeedMps, bool Fence, AActor* VehicleToIgnore,
        TArray<TWeakObjectPtr<AActor>>& DebrisOut,
        TArray<TWeakObjectPtr<AActor>>& RemaindersOut);
}
