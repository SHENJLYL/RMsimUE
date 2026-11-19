#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "MapOverview.generated.h"

class AHUD;
class UCanvas;
class UTexture2D;

// Local presentation data only. This snapshot has no route into the ROS bridge.
struct FMapOverviewFrame
{
    FString MapId;
    FString MapName;
    FString SourceFile;
    FString SourceSha256;
    FVector VehicleRosMetres = FVector::ZeroVector;
    // Actual gimbal forward projected onto ROS XY; also the manual W drive basis.
    FVector2D VehicleForwardRos = FVector2D(1.0, 0.0);
    FVector2D SpawnRosMetres = FVector2D::ZeroVector;
};

UCLASS()
class RMSIM_API UMapOverview : public UObject
{
    GENERATED_BODY()
public:
    // Returns true while the full map covers the normal driving HUD.
    bool Draw(AHUD* Hud, UCanvas* Canvas, const FMapOverviewFrame& Frame);

private:
    UPROPERTY() TObjectPtr<UTexture2D> MapTexture = nullptr;

    bool LoadDefinition(const FMapOverviewFrame& Frame);
    void UpdateInput(AHUD* Hud, const FMapOverviewFrame& Frame);
    FVector2D WorldToUv(const FVector2D& WorldMetres) const;
    FVector2D WorldDirectionToUv(const FVector2D& Direction) const;

    FString LoadedDefinition;
    FString LoadedSourceSha256;
    FString DefinitionError;
    bool bDefinitionAttempted = false;
    bool bDefinitionValid = false;
    bool bExpanded = false;
    bool bHadInputFocus = false;
    bool bMapKeyWasDown = false;
    FVector2D ImageTopLeft = FVector2D::ZeroVector;
    FVector2D ImageAxisU = FVector2D::ZeroVector;
    FVector2D ImageAxisV = FVector2D::ZeroVector;
    double InverseDeterminant = 0.0;
    double MinimapSpanMetres = 512.0;
};
