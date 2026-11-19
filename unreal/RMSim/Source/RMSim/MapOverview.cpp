#include "MapOverview.h"

#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "CanvasItem.h"
#include "Dom/JsonObject.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "GameFramework/HUD.h"
#include "GameFramework/PlayerController.h"
#include "GlobalRenderResources.h"
#include "InputCoreTypes.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UnrealClient.h"

namespace
{
const FLinearColor PanelColor(0.025f, 0.04f, 0.055f, 0.96f);
const FLinearColor MutedColor(0.70f, 0.77f, 0.81f, 1.0f);
const FLinearColor VehicleColor(0.15f, 0.94f, 1.0f, 1.0f);
const FLinearColor SpawnColor(1.0f, 0.72f, 0.18f, 1.0f);

bool ReadPoint(const TSharedPtr<FJsonObject>& Object, const TCHAR* Key, FVector2D& Point)
{
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    double X = 0.0, Y = 0.0;
    if (!Object.IsValid() || !Object->TryGetArrayField(Key, Values) || !Values || Values->Num() != 2 ||
        !(*Values)[0].IsValid() || !(*Values)[1].IsValid() ||
        !(*Values)[0]->TryGetNumber(X) || !(*Values)[1]->TryGetNumber(Y) ||
        !FMath::IsFinite(X) || !FMath::IsFinite(Y)) return false;
    Point = FVector2D(X, Y);
    return true;
}

void Text(AHUD* Hud, const FString& Value, float X, float Y, float Scale,
    const FLinearColor& Color = FLinearColor::White, float MaxWidth = 0.0f)
{
    UFont* Font = GEngine ? GEngine->GetSmallFont() : nullptr;
    if (MaxWidth > 0.0f)
    {
        float Width = 0.0f, Height = 0.0f;
        Hud->GetTextSize(Value, Width, Height, Font, Scale);
        if (Width > MaxWidth) Scale *= MaxWidth / Width;
    }
    Hud->DrawText(Value, Color, X, Y, Font, Scale);
}

void Border(AHUD* Hud, const FVector2D& Position, const FVector2D& Size, const FLinearColor& Color, float Thickness = 1.0f)
{
    Hud->DrawLine(Position.X, Position.Y, Position.X + Size.X, Position.Y, Color, Thickness);
    Hud->DrawLine(Position.X + Size.X, Position.Y, Position.X + Size.X, Position.Y + Size.Y, Color, Thickness);
    Hud->DrawLine(Position.X + Size.X, Position.Y + Size.Y, Position.X, Position.Y + Size.Y, Color, Thickness);
    Hud->DrawLine(Position.X, Position.Y + Size.Y, Position.X, Position.Y, Color, Thickness);
}

void Triangle(UCanvas* Canvas, const FVector2D& A, const FVector2D& B, const FVector2D& C, const FLinearColor& Color)
{
    FCanvasTriangleItem Item(A, B, C, GWhiteTexture);
    Item.SetColor(Color);
    Item.BlendMode = SE_BLEND_Translucent;
    Canvas->DrawItem(Item);
}

void VehicleArrow(UCanvas* Canvas, const FVector2D& Position, FVector2D Forward, float Radius)
{
    if (!Forward.Normalize()) Forward = FVector2D(1.0, 0.0);
    const FVector2D Right(-Forward.Y, Forward.X);
    auto Arrow = [&](float R, const FLinearColor& Color)
    {
        Triangle(Canvas, Position + Forward * R, Position - Forward * R * 0.72 + Right * R * 0.63,
            Position - Forward * R * 0.72 - Right * R * 0.63, Color);
    };
    Arrow(Radius + 3.0f, FLinearColor(0.0f, 0.01f, 0.015f, 0.95f));
    Arrow(Radius, VehicleColor);
}

void SpawnSymbol(AHUD* Hud, const FVector2D& Position, float Radius)
{
    const FVector2D Vertices[] = {Position + FVector2D(0, -Radius), Position + FVector2D(Radius, 0),
        Position + FVector2D(0, Radius), Position + FVector2D(-Radius, 0)};
    for (int32 I = 0; I < 4; ++I)
    {
        Hud->DrawLine(Vertices[I].X, Vertices[I].Y, Vertices[(I + 1) % 4].X, Vertices[(I + 1) % 4].Y,
            FLinearColor::Black, 4.0f);
        Hud->DrawLine(Vertices[I].X, Vertices[I].Y, Vertices[(I + 1) % 4].X, Vertices[(I + 1) % 4].Y,
            SpawnColor, 2.0f);
    }
}

double ScaleDistance(double MaximumMetres)
{
    if (MaximumMetres <= 0.0) return 1.0;
    const double Power = FMath::Pow(10.0, FMath::FloorToDouble(FMath::LogX(10.0, MaximumMetres)));
    const double Mantissa = MaximumMetres / Power;
    return (Mantissa >= 5.0 ? 5.0 : Mantissa >= 2.0 ? 2.0 : 1.0) * Power;
}

FString DistanceText(double Metres)
{
    return Metres >= 1000.0 ? FString::Printf(TEXT("%.1f km"), Metres / 1000.0) :
        FString::Printf(TEXT("%.0f m"), Metres);
}
}

bool AImportLabGameMode::GetMapOverviewFrame(FMapOverviewFrame& OutFrame) const
{
    if (!bReady || !StageMap.bSavedScene || !IsValid(Rig)) return false;
    OutFrame.MapId = StageMap.Id;
    OutFrame.MapName = StageMap.DisplayName;
    OutFrame.SourceFile = StageMap.SourceFile;
    OutFrame.SourceSha256 = StageMap.SceneSourceHash;
    const FVector Position = Rig->GetActorLocation();
    // Inverse of RosCoordinates::Position: UE cm = (ROS x, -ROS y, ROS z) * 100.
    OutFrame.VehicleRosMetres = FVector(Position.X, -Position.Y, Position.Z) * 0.01;
    // Share the actual turret heading used by manual W, independent of C's orbit view.
    OutFrame.VehicleForwardRos = GetGimbalPlanarForwardRos();
    OutFrame.SpawnRosMetres = FVector2D(SelectedSpawn.GroundMetres.X, SelectedSpawn.GroundMetres.Y);
    return true;
}

FVector2D UMapOverview::WorldDirectionToUv(const FVector2D& Direction) const
{
    return FVector2D(Direction.X * ImageAxisV.Y - Direction.Y * ImageAxisV.X,
        ImageAxisU.X * Direction.Y - ImageAxisU.Y * Direction.X) * InverseDeterminant;
}

FVector2D UMapOverview::WorldToUv(const FVector2D& WorldMetres) const
{
    return WorldDirectionToUv(WorldMetres - ImageTopLeft);
}

bool UMapOverview::LoadDefinition(const FMapOverviewFrame& Frame)
{
    const FString Filename = FPaths::Combine(FPaths::GetPath(Frame.SourceFile), TEXT("overview.json"));
    if (bDefinitionAttempted && LoadedDefinition == Filename && LoadedSourceSha256 == Frame.SourceSha256)
        return bDefinitionValid;
    bDefinitionAttempted = true;
    bDefinitionValid = false;
    LoadedDefinition = Filename;
    LoadedSourceSha256 = Frame.SourceSha256;
    MapTexture = nullptr;
    DefinitionError.Reset();
    auto Fail = [&](const FString& Reason)
    {
        DefinitionError = Reason;
        UE_LOG(LogTemp, Warning, TEXT("MAP_OVERVIEW_UNAVAILABLE map=%s file=%s reason=%s"),
            *Frame.MapId, *Filename, *Reason);
        return false;
    };
    FString Json;
    if (!FFileHelper::LoadFileToString(Json, *Filename)) return Fail(TEXT("overview.json is unavailable"));
    TSharedPtr<FJsonObject> Root;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Root) || !Root.IsValid())
        return Fail(TEXT("invalid overview JSON"));
    FString Schema, MapId, SourceHash, TextureAsset;
    if (!Root->TryGetStringField(TEXT("schema"), Schema) || Schema != TEXT("rmsim_map_overview_v1") ||
        !Root->TryGetStringField(TEXT("map_id"), MapId) || MapId != Frame.MapId ||
        !Root->TryGetStringField(TEXT("source_sha256"), SourceHash) || SourceHash != Frame.SourceSha256)
        return Fail(TEXT("map/source identity does not match the loaded scene"));
    if (!Root->TryGetStringField(TEXT("texture_asset"), TextureAsset) || !TextureAsset.StartsWith(TEXT("/Game/")))
        return Fail(TEXT("missing map Texture2D asset"));
    const TSharedPtr<FJsonObject>* Corners = nullptr;
    FVector2D TopRight, BottomLeft;
    if (!Root->TryGetObjectField(TEXT("image_ros_corners_m"), Corners) || !Corners ||
        !ReadPoint(*Corners, TEXT("top_left"), ImageTopLeft) ||
        !ReadPoint(*Corners, TEXT("top_right"), TopRight) ||
        !ReadPoint(*Corners, TEXT("bottom_left"), BottomLeft))
        return Fail(TEXT("three calibrated source-image corners are required"));
    ImageAxisU = TopRight - ImageTopLeft;
    ImageAxisV = BottomLeft - ImageTopLeft;
    const double Determinant = ImageAxisU.X * ImageAxisV.Y - ImageAxisU.Y * ImageAxisV.X;
    if (!FMath::IsFinite(Determinant) || FMath::Abs(Determinant) < 1.e-6 ||
        ImageAxisU.Size() < 1.0 || ImageAxisV.Size() < 1.0)
        return Fail(TEXT("source-image calibration is singular"));
    InverseDeterminant = 1.0 / Determinant;
    MinimapSpanMetres = 512.0;
    Root->TryGetNumberField(TEXT("minimap_span_m"), MinimapSpanMetres);
    if (!FMath::IsFinite(MinimapSpanMetres) || MinimapSpanMetres < 16.0 || MinimapSpanMetres > 32768.0)
        return Fail(TEXT("invalid minimap span"));
    double ExpectedWidth = 0.0, ExpectedHeight = 0.0;
    if (!Root->TryGetNumberField(TEXT("image_width"), ExpectedWidth) ||
        !Root->TryGetNumberField(TEXT("image_height"), ExpectedHeight) || ExpectedWidth < 1.0 || ExpectedHeight < 1.0)
        return Fail(TEXT("source image dimensions are required"));
    MapTexture = LoadObject<UTexture2D>(nullptr, *TextureAsset);
#if WITH_EDITOR
    // A first uncooked load may still be compiling the texture platform data.
    // Retry after the asynchronous build; its temporary size is not a bad source image.
    if (IsValid(MapTexture) && MapTexture->IsCompiling())
    {
        bDefinitionAttempted = false;
        DefinitionError = TEXT("Map image is loading");
        return false;
    }
#endif
    if (!IsValid(MapTexture) || MapTexture->GetSizeX() != ExpectedWidth || MapTexture->GetSizeY() != ExpectedHeight)
        return Fail(TEXT("map texture or its source dimensions do not match"));
    bDefinitionValid = true;
    const FVector2D VehicleUv = WorldToUv(FVector2D(Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y));
    UE_LOG(LogTemp, Display, TEXT("MAP_OVERVIEW_READY map=%s texture=%s image=%dx%d span_m=%.3f source=%s "
        "top_left_ros_m=(%.6f,%.6f) axis_u_m=(%.6f,%.6f) axis_v_m=(%.6f,%.6f) vehicle_uv=(%.9f,%.9f)"),
        *Frame.MapId, *TextureAsset, MapTexture->GetSizeX(), MapTexture->GetSizeY(), MinimapSpanMetres,
        *SourceHash, ImageTopLeft.X, ImageTopLeft.Y, ImageAxisU.X, ImageAxisU.Y, ImageAxisV.X, ImageAxisV.Y,
        VehicleUv.X, VehicleUv.Y);
    return true;
}

void UMapOverview::UpdateInput(AHUD* Hud, const FMapOverviewFrame& Frame)
{
    APlayerController* PC = Hud->GetOwningPlayerController();
    UGameViewportClient* Viewport = Hud->GetWorld() ? Hud->GetWorld()->GetGameViewport() : nullptr;
    const bool Focused = PC && Viewport && Viewport->Viewport && Viewport->Viewport->HasFocus() &&
        Viewport->Viewport->IsForegroundWindow();
    const bool Down = PC && PC->IsInputKeyDown(EKeys::M);
    if (!Focused || !bHadInputFocus)
    {
        bHadInputFocus = Focused;
        bMapKeyWasDown = Down;
        return;
    }
    if (Down && !bMapKeyWasDown)
    {
        bExpanded = !bExpanded;
        const FVector2D Uv = bDefinitionValid ? WorldToUv(FVector2D(Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y)) :
            FVector2D::ZeroVector;
        UE_LOG(LogTemp, Display, TEXT("MAP_OVERVIEW_TOGGLE expanded=%d map=%s vehicle_ros_m=(%.3f,%.3f,%.3f) vehicle_uv=(%.9f,%.9f)"),
            bExpanded ? 1 : 0, *Frame.MapId, Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y, Frame.VehicleRosMetres.Z, Uv.X, Uv.Y);
    }
    bMapKeyWasDown = Down;
}

bool UMapOverview::Draw(AHUD* Hud, UCanvas* Canvas, const FMapOverviewFrame& Frame)
{
    if (!Hud || !Canvas || Canvas->SizeX < 240 || Canvas->SizeY < 240) return false;
    const bool Available = LoadDefinition(Frame);
    UpdateInput(Hud, Frame);
    const float Scale = FMath::Clamp(FMath::Min(Canvas->SizeX / 1600.0f, Canvas->SizeY / 900.0f), 0.75f, 1.25f);
    const float Padding = 12.0f * Scale;
    const float HeaderHeight = 38.0f * Scale;
    const float FooterHeight = (bExpanded ? 76.0f : 70.0f) * Scale;
    const float MinimapSide = FMath::Max(96.0f, FMath::Min(268.0f * Scale,
        FMath::Min(Canvas->SizeX - Padding * 2.0f - 40.0f * Scale,
            Canvas->SizeY - HeaderHeight - FooterHeight - 40.0f * Scale)));
    FVector2D MapSize(MinimapSide, MinimapSide);
    if (bExpanded)
    {
        Hud->DrawRect(FLinearColor(0.0f, 0.005f, 0.012f, 0.72f), 0, 0, Canvas->SizeX, Canvas->SizeY);
        const double AvailableWidth = FMath::Max(120.0, Canvas->SizeX - 80.0 * Scale);
        const double AvailableHeight = FMath::Max(120.0, Canvas->SizeY - HeaderHeight - FooterHeight - 64.0 * Scale);
        const double Aspect = Available ? ImageAxisU.Size() / ImageAxisV.Size() : 1.0;
        MapSize.Y = FMath::Min(AvailableHeight, AvailableWidth / Aspect);
        MapSize.X = MapSize.Y * Aspect;
    }
    const FVector2D PanelSize(MapSize.X + Padding * 2.0, MapSize.Y + HeaderHeight + FooterHeight);
    const FVector2D PanelPosition = bExpanded ? FVector2D((Canvas->SizeX - PanelSize.X) * 0.5, (Canvas->SizeY - PanelSize.Y) * 0.5) :
        FVector2D(Canvas->SizeX - PanelSize.X - 20.0 * Scale, Canvas->SizeY - PanelSize.Y - 20.0 * Scale);
    const FVector2D MapPosition = PanelPosition + FVector2D(Padding, HeaderHeight);
    Hud->DrawRect(PanelColor, PanelPosition.X, PanelPosition.Y, PanelSize.X, PanelSize.Y);
    Border(Hud, PanelPosition, PanelSize, FLinearColor(0.24f, 0.36f, 0.42f, 0.9f));
    Text(Hud, Frame.MapName, PanelPosition.X + Padding, PanelPosition.Y + 11.0f * Scale, 1.08f * Scale,
        FLinearColor::White, PanelSize.X - 122.0f * Scale);
    const float BadgeWidth = 86.0f * Scale;
    const FVector2D BadgePosition(PanelPosition.X + PanelSize.X - BadgeWidth - Padding, PanelPosition.Y + 8.0f * Scale);
    Hud->DrawRect(FLinearColor(0.08f, 0.22f, 0.27f, 1.0f), BadgePosition.X, BadgePosition.Y, BadgeWidth, 23.0f * Scale);
    Text(Hud, bExpanded ? TEXT("M  CLOSE") : TEXT("M  MAP"), BadgePosition.X + 10.0f * Scale,
        BadgePosition.Y + 4.0f * Scale, 0.95f * Scale, VehicleColor);
    Hud->DrawRect(FLinearColor(0.035f, 0.055f, 0.065f, 1.0f), MapPosition.X, MapPosition.Y, MapSize.X, MapSize.Y);

    const FVector2D PositionRos(Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y);
    const FVector2D VehicleUv = Available ? WorldToUv(PositionRos) : FVector2D(0.5, 0.5);
    FVector2D UvMin = FVector2D::ZeroVector, UvSize(1.0, 1.0);
    if (!bExpanded && Available)
    {
        UvSize = FVector2D(MinimapSpanMetres / ImageAxisU.Size(), MinimapSpanMetres / ImageAxisV.Size());
        UvMin = VehicleUv - UvSize * 0.5;
    }
    auto ScreenPoint = [&](const FVector2D& Uv)
    {
        return MapPosition + FVector2D((Uv.X - UvMin.X) / UvSize.X * MapSize.X,
            (Uv.Y - UvMin.Y) / UvSize.Y * MapSize.Y);
    };
    auto ScreenDirection = [&](const FVector2D& RosDirection)
    {
        const FVector2D Uv = WorldDirectionToUv(RosDirection);
        return FVector2D(Uv.X / UvSize.X * MapSize.X, Uv.Y / UvSize.Y * MapSize.Y).GetSafeNormal();
    };
    const bool VehicleInsideSource = VehicleUv.X >= 0.0 && VehicleUv.X <= 1.0 && VehicleUv.Y >= 0.0 && VehicleUv.Y <= 1.0;
    if (Available)
    {
        // Clip to real image coverage; the area beyond the source image stays explicitly blank.
        const FVector2D ClipMin(FMath::Max(0.0, UvMin.X), FMath::Max(0.0, UvMin.Y));
        const FVector2D ClipMax(FMath::Min(1.0, UvMin.X + UvSize.X), FMath::Min(1.0, UvMin.Y + UvSize.Y));
        if (ClipMax.X > ClipMin.X && ClipMax.Y > ClipMin.Y)
        {
            const FVector2D DrawPosition = ScreenPoint(ClipMin), DrawEnd = ScreenPoint(ClipMax);
            Hud->DrawTexture(MapTexture, DrawPosition.X, DrawPosition.Y, DrawEnd.X - DrawPosition.X, DrawEnd.Y - DrawPosition.Y,
                ClipMin.X, ClipMin.Y, ClipMax.X - ClipMin.X, ClipMax.Y - ClipMin.Y, FLinearColor::White, BLEND_Opaque);
        }
        if (bExpanded)
        {
            for (int32 I = 1; I < 4; ++I)
            {
                const float X = MapPosition.X + MapSize.X * I / 4.0, Y = MapPosition.Y + MapSize.Y * I / 4.0;
                Hud->DrawLine(X, MapPosition.Y, X, MapPosition.Y + MapSize.Y, FLinearColor(1, 1, 1, 0.10f), 1.0f);
                Hud->DrawLine(MapPosition.X, Y, MapPosition.X + MapSize.X, Y, FLinearColor(1, 1, 1, 0.10f), 1.0f);
            }
        }
        const FVector2D SpawnPosition = ScreenPoint(WorldToUv(Frame.SpawnRosMetres));
        const double Inset = 14.0 * Scale;
        auto InsideMap = [&](const FVector2D& Point)
        {
            return Point.X >= MapPosition.X + Inset && Point.X <= MapPosition.X + MapSize.X - Inset &&
                Point.Y >= MapPosition.Y + Inset && Point.Y <= MapPosition.Y + MapSize.Y - Inset;
        };
        if (InsideMap(SpawnPosition)) SpawnSymbol(Hud, SpawnPosition, 6.0f * Scale);
        FVector2D Marker = ScreenPoint(VehicleUv);
        const FVector2D TrueMarker = Marker;
        Marker.X = FMath::Clamp(Marker.X, MapPosition.X + Inset, MapPosition.X + MapSize.X - Inset);
        Marker.Y = FMath::Clamp(Marker.Y, MapPosition.Y + Inset, MapPosition.Y + MapSize.Y - Inset);
        VehicleArrow(Canvas, Marker, ScreenDirection(Frame.VehicleForwardRos), 10.5f * Scale);
        if (bExpanded && !VehicleInsideSource)
        {
            const FVector2D Direction = (TrueMarker - Marker).GetSafeNormal();
            const FVector2D Edge = Marker + Direction * (8.0 * Scale);
            Hud->DrawLine(Marker.X, Marker.Y, Edge.X, Edge.Y, SpawnColor, 3.0f * Scale);
        }

        // Source axes follow the calibrated image transform, including swaps or reflections.
        const FVector2D Compass = MapPosition + FVector2D(41.0 * Scale, 41.0 * Scale);
        Hud->DrawRect(FLinearColor(0.01f, 0.025f, 0.03f, 0.72f), MapPosition.X + 5.0f * Scale,
            MapPosition.Y + 5.0f * Scale, 78.0f * Scale, 78.0f * Scale);
        for (int32 Axis = 0; Axis < 2; ++Axis)
        {
            const FVector2D Direction = ScreenDirection(Axis == 0 ? FVector2D(1, 0) : FVector2D(0, 1));
            const FVector2D End = Compass + Direction * 22.0 * Scale;
            const FVector2D Side(-Direction.Y, Direction.X);
            Hud->DrawLine(Compass.X, Compass.Y, End.X, End.Y, MutedColor, 1.5f * Scale);
            const FVector2D ArrowA = End - Direction * 5.0 * Scale + Side * 3.0 * Scale;
            const FVector2D ArrowB = End - Direction * 5.0 * Scale - Side * 3.0 * Scale;
            Hud->DrawLine(End.X, End.Y, ArrowA.X, ArrowA.Y, MutedColor, 1.5f * Scale);
            Hud->DrawLine(End.X, End.Y, ArrowB.X, ArrowB.Y, MutedColor, 1.5f * Scale);
            const FVector2D Label = Compass + Direction * 31.0 * Scale - FVector2D(7.0 * Scale, 5.0 * Scale);
            Text(Hud, Axis == 0 ? TEXT("+X") : TEXT("+Y"), Label.X, Label.Y, 0.75f * Scale, MutedColor);
        }
        const double MetresPerPixel = ImageAxisU.Size() * UvSize.X / MapSize.X;
        const double ScaleMetres = ScaleDistance(MetresPerPixel * MapSize.X * 0.25);
        const float BarWidth = ScaleMetres / MetresPerPixel;
        const FVector2D Bar(MapPosition.X + 12.0 * Scale, MapPosition.Y + MapSize.Y - 13.0 * Scale);
        Hud->DrawRect(FLinearColor(0.01f, 0.02f, 0.025f, 0.78f), Bar.X - 5.0f * Scale, Bar.Y - 24.0f * Scale,
            FMath::Max(BarWidth + 10.0f * Scale, 69.0f * Scale), 32.0f * Scale);
        Text(Hud, DistanceText(ScaleMetres), Bar.X, Bar.Y - 22.0f * Scale, 0.85f * Scale);
        Hud->DrawLine(Bar.X, Bar.Y, Bar.X + BarWidth, Bar.Y, FLinearColor::White, 2.0f * Scale);
        Hud->DrawLine(Bar.X, Bar.Y - 4.0f * Scale, Bar.X, Bar.Y + 2.0f * Scale, FLinearColor::White, 2.0f * Scale);
        Hud->DrawLine(Bar.X + BarWidth, Bar.Y - 4.0f * Scale, Bar.X + BarWidth, Bar.Y + 2.0f * Scale, FLinearColor::White, 2.0f * Scale);
    }
    else
    {
        Text(Hud, bDefinitionAttempted ? TEXT("Map image unavailable") : TEXT("Loading map image"), MapPosition.X + Padding, MapPosition.Y + Padding, 1.05f * Scale);
        Text(Hud, bDefinitionAttempted ? TEXT("Check overview calibration") : TEXT("Preparing source map"), MapPosition.X + Padding, MapPosition.Y + Padding + 22.0f * Scale,
            0.85f * Scale, MutedColor, MapSize.X - Padding * 2.0f);
    }
    Border(Hud, MapPosition, MapSize, FLinearColor(0.34f, 0.45f, 0.49f, 0.9f));
    const float FooterY = MapPosition.Y + MapSize.Y + 9.0f * Scale;
    Text(Hud, FString::Printf(TEXT("X %.1f m    Y %.1f m"), Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y),
        MapPosition.X, FooterY, 0.95f * Scale, FLinearColor::White, MapSize.X);
    const float LegendY = FooterY + 23.0f * Scale;
    VehicleArrow(Canvas, FVector2D(MapPosition.X + 7.0 * Scale, LegendY + 5.0 * Scale), FVector2D(0, -1), 5.0f * Scale);
    Text(Hud, TEXT("Vehicle"), MapPosition.X + 19.0f * Scale, LegendY, 0.88f * Scale, VehicleColor);
    SpawnSymbol(Hud, FVector2D(MapPosition.X + 108.0 * Scale, LegendY + 5.0 * Scale), 4.5f * Scale);
    Text(Hud, TEXT("Spawn"), MapPosition.X + 119.0f * Scale, LegendY, 0.88f * Scale, SpawnColor);
    const FString Coverage = !Available ? TEXT("Source image required") : !VehicleInsideSource ? TEXT("Vehicle outside source map") :
        bExpanded ? TEXT("Full source map") : FString::Printf(TEXT("Near view: %s"), *DistanceText(MinimapSpanMetres));
    Text(Hud, Coverage, MapPosition.X, LegendY + 20.0f * Scale, 0.78f * Scale,
        VehicleInsideSource ? MutedColor : SpawnColor, MapSize.X);
    return bExpanded;
}
