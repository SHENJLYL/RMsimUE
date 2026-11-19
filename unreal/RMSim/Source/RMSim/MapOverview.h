#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "MapOverview.generated.h"

class AHUD;
class UCanvas;
class UTexture2D;
class UFont;
class UMapFontRenderer;

// Local presentation only; these coordinates never enter localization.
struct FMapOverviewMark
{
    FVector2D RosMetres = FVector2D::ZeroVector;
    FString Kind;
    FString Label;
    FLinearColor Color = FLinearColor::White;
    double AgeSeconds = 0.0;
};

struct FMapOverviewFrame
{
    FString MapId;
    FString MapName;
    FString SourceFile;
    FString SourceSha256;
    FVector VehicleRosMetres = FVector::ZeroVector;
    // Actual gimbal forward projected onto ROS XY; also the manual W drive basis.
    FVector2D VehicleForwardRos = FVector2D(1.0, 0.0);
    FVector2D HullForwardRos = FVector2D(1.0, 0.0);
    FVector2D ViewForwardRos = FVector2D(1.0, 0.0);
    double ViewFovDegrees = 55.0;
    FVector2D SpawnRosMetres = FVector2D::ZeroVector;
    bool bExpanded = false;
    int32 MinimapZoomLevel = 0;
    int32 FullMapZoomLevel = 0;
    // Coordinates in the displayed, handedness-corrected image.
    FVector2D FullMapCenterUv = FVector2D(0.5, 0.5);
    TArray<FMapOverviewMark> Marks;
};

// Shared display geometry for native HUD, input hit testing and independent probes.
struct FMapOverviewPresentation
{
    bool bAvailable = false;
    bool bFlipSourceV = false;
    bool bUsesSourceFont = false;
    bool bOriginalFontLoaded = false;
    bool bOriginalFontEffectLoaded = false;
    bool bOriginalPanelLoaded = false;
    bool bOriginalArrowLoaded = false;
    bool bOriginalHullLoaded = false;
    FVector2D MapPositionPixels = FVector2D::ZeroVector;
    FVector2D MapSizePixels = FVector2D::ZeroVector;
    FVector2D ViewMinUv = FVector2D::ZeroVector;
    FVector2D ViewSizeUv = FVector2D(1.0, 1.0);
    FVector2D VehiclePositionPixels = FVector2D::ZeroVector;
    FVector2D VehicleForwardPixels = FVector2D(0.0, -1.0);
    double MetresPerPixel = 1.0;
    double ScaleDistanceMetres = 100.0;
    double ScaleWidthPixels = 100.0;
    double DisplaySpanMetres = 2048.0;
    double UiScale = 1.0;
    bool Contains(const FVector2D& Pixel) const;
    FVector2D Project(const FVector2D& DisplayUv) const;
};

UCLASS()
class RMSIM_API UMapOverview : public UObject
{
    GENERATED_BODY()
public:
    static constexpr int32 MaximumZoomLevel = 7;
    static double ZoomFactor(int32 Level);

    // Input is owned by the shared GameMode path. Draw never polls or consumes keys.
    bool Draw(AHUD* Hud, UCanvas* Canvas, const FMapOverviewFrame& Frame);
    bool Describe(const FMapOverviewFrame& Frame, FVector2D ViewportPixels,
        FMapOverviewPresentation& Out);
    bool ScreenToWorld(const FMapOverviewFrame& Frame, FVector2D ViewportPixels,
        FVector2D Pixel, FVector2D& OutRos);
    FVector2D WorldToDisplayUv(const FVector2D& WorldMetres) const;

private:
    UPROPERTY() TObjectPtr<UTexture2D> MapTexture = nullptr;
    UPROPERTY() TObjectPtr<UTexture2D> PlayerArrowTexture = nullptr;
    UPROPERTY() TObjectPtr<UTexture2D> HullArrowTexture = nullptr;
    UPROPERTY() TObjectPtr<UFont> MapFont = nullptr;
    UPROPERTY() TObjectPtr<UMapFontRenderer> FontRenderer = nullptr;
    UPROPERTY() TObjectPtr<UTexture2D> PanelTexture = nullptr;
    UPROPERTY() TMap<FString, TObjectPtr<UTexture2D>> MarkerTextures;

    bool LoadDefinition(const FMapOverviewFrame& Frame);
    void LoadMissionMarks(const FString& Filename, const FMapOverviewFrame& Frame);
    FVector2D WorldToUv(const FVector2D& WorldMetres) const;
    FVector2D WorldDirectionToUv(const FVector2D& Direction) const;
    FVector2D DisplayDirection(const FVector2D& Direction,
        const FMapOverviewPresentation& Layout) const;

    FString LoadedDefinition;
    FString LoadedSourceSha256;
    FString DefinitionError;
    bool bDefinitionAttempted = false;
    bool bDefinitionValid = false;
    bool bFlipSourceV = false;
    FVector2D ImageTopLeft = FVector2D::ZeroVector;
    FVector2D ImageAxisU = FVector2D::ZeroVector;
    FVector2D ImageAxisV = FVector2D::ZeroVector;
    double InverseDeterminant = 0.0;
    int32 GridColumns = 7;
    int32 GridRows = 7;
    TArray<FMapOverviewMark> MissionMarks;
};
