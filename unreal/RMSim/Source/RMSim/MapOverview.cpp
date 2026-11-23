#include "MapOverview.h"
#include "MapFontRenderer.h"

#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "CanvasItem.h"
#include "Dom/JsonObject.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "Engine/Texture2D.h"
#include "GameFramework/HUD.h"
#include "GlobalRenderResources.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
// Original tacticalMap/CSS colors are sRGB bytes. Canvas expects linear color;
// passing the byte fractions directly lifts gray outlines and washes out team colors.
const FLinearColor GridColor = FLinearColor::FromSRGBColor(FColor(0, 0, 0, 80));
const FLinearColor FrameColor = FLinearColor::FromSRGBColor(FColor(96, 96, 96, 204));
const FLinearColor FriendColor = FLinearColor::FromSRGBColor(FColor(35, 91, 232, 255));
const FLinearColor EnemyColor = FLinearColor::FromSRGBColor(FColor(232, 62, 35, 255));

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

void MapText(AHUD* Hud, UMapFontRenderer* Renderer, UFont* Font,
    const FString& Value, FVector2D Position, float PixelHeight,
    const FLinearColor& Color = FLinearColor::Black, bool CenterX = false, bool CenterY = false)
{
    if (Renderer && Renderer->Draw(Hud, Value, Position, PixelHeight, Color, CenterX, CenterY)) return;
    if (!Font) return;
    // A plain text fallback keeps a missing source font readable; Describe exposes
    // the unavailable source effect so a parity probe cannot silently accept it.
    float Width = 0.0f, Height = 0.0f;
    Hud->GetTextSize(Value, Width, Height, Font, 1.0f);
    const float Scale = PixelHeight / FMath::Max(1.0f, Height);
    if (CenterX) Position.X -= Width * Scale * 0.5;
    if (CenterY) Position.Y -= Height * Scale * 0.5;
    Hud->DrawText(Value, Color, Position.X, Position.Y, Font, Scale);
}

void SourceMapPanel(AHUD* Hud, UTexture2D* Texture, const FVector2D& Position,
    const FVector2D& Size, double Ui)
{
    if (!Texture || Texture->GetSizeX() < 3 || Texture->GetSizeY() < 11)
    {
        Hud->DrawRect(FLinearColor::FromSRGBColor(FColor(0, 0, 0, 99)),
            Position.X, Position.Y, Size.X, Size.Y);
        return;
    }
    // option_select_odd uses background-position:1,5 and background-repeat:expand.
    // Preserve the original corner/edge RGBA instead of flattening its center color.
    const double BorderX = FMath::Min(1.0 * Ui, Size.X * 0.5);
    const double BorderY = FMath::Min(5.0 * Ui, Size.Y * 0.5);
    const double X[] = {Position.X, Position.X + BorderX,
        Position.X + Size.X - BorderX, Position.X + Size.X};
    const double Y[] = {Position.Y, Position.Y + BorderY,
        Position.Y + Size.Y - BorderY, Position.Y + Size.Y};
    const double U[] = {0.0, 1.0 / Texture->GetSizeX(), 1.0 - 1.0 / Texture->GetSizeX(), 1.0};
    const double V[] = {0.0, 5.0 / Texture->GetSizeY(), 1.0 - 5.0 / Texture->GetSizeY(), 1.0};
    for (int32 Row = 0; Row < 3; ++Row)
        for (int32 Column = 0; Column < 3; ++Column)
            if (X[Column + 1] > X[Column] && Y[Row + 1] > Y[Row])
                Hud->DrawTexture(Texture, X[Column], Y[Row], X[Column + 1] - X[Column],
                    Y[Row + 1] - Y[Row], U[Column], V[Row],
                    U[Column + 1] - U[Column], V[Row + 1] - V[Row],
                    FLinearColor::White, BLEND_Translucent);
}

void Border(AHUD* Hud, const FVector2D& Position, const FVector2D& Size,
    const FLinearColor& Color, float Thickness = 1.0f)
{
    // UE FCanvasLineItem forces opaque blending. Translucent tiles retain the
    // source CSS alpha; excluding corners from side tiles avoids double blending.
    Hud->DrawRect(Color, Position.X, Position.Y, Size.X, Thickness);
    Hud->DrawRect(Color, Position.X, Position.Y + Size.Y - Thickness, Size.X, Thickness);
    Hud->DrawRect(Color, Position.X, Position.Y + Thickness, Thickness, Size.Y - 2.0 * Thickness);
    Hud->DrawRect(Color, Position.X + Size.X - Thickness, Position.Y + Thickness,
        Thickness, Size.Y - 2.0 * Thickness);
}

void Triangle(UCanvas* Canvas, const FVector2D& A, const FVector2D& B, const FVector2D& C,
    const FLinearColor& Color)
{
    FCanvasTriangleItem Item(A, B, C, GWhiteTexture);
    Item.SetColor(Color);
    Item.BlendMode = SE_BLEND_Translucent;
    Canvas->DrawItem(Item);
}

void Circle(AHUD* Hud, const FVector2D& Position, double Radius, const FLinearColor& Color,
    float Thickness = 1.0f)
{
    FVector2D Previous = Position + FVector2D(Radius, 0);
    for (int32 I = 1; I <= 40; ++I)
    {
        const double Angle = I * 2.0 * PI / 40.0;
        const FVector2D Point = Position + FVector2D(FMath::Cos(Angle), FMath::Sin(Angle)) * Radius;
        Hud->DrawLine(Previous.X, Previous.Y, Point.X, Point.Y, Color, Thickness);
        Previous = Point;
    }
}

FVector2D ClampToMap(const FVector2D& Point, const FMapOverviewPresentation& Layout, double Inset = 0.0)
{
    return FVector2D(FMath::Clamp(Point.X, Layout.MapPositionPixels.X + Inset,
        Layout.MapPositionPixels.X + Layout.MapSizePixels.X - Inset),
        FMath::Clamp(Point.Y, Layout.MapPositionPixels.Y + Inset,
        Layout.MapPositionPixels.Y + Layout.MapSizePixels.Y - Inset));
}

FVector2D RayToMapEdge(const FVector2D& Origin, const FVector2D& Direction,
    const FMapOverviewPresentation& Layout)
{
    double Distance = Layout.MapSizePixels.Size() * 2.0;
    const FVector2D Max = Layout.MapPositionPixels + Layout.MapSizePixels;
    if (Direction.X > 1.e-9) Distance = FMath::Min(Distance, (Max.X - Origin.X) / Direction.X);
    if (Direction.X < -1.e-9) Distance = FMath::Min(Distance, (Layout.MapPositionPixels.X - Origin.X) / Direction.X);
    if (Direction.Y > 1.e-9) Distance = FMath::Min(Distance, (Max.Y - Origin.Y) / Direction.Y);
    if (Direction.Y < -1.e-9) Distance = FMath::Min(Distance, (Layout.MapPositionPixels.Y - Origin.Y) / Direction.Y);
    return Origin + Direction * FMath::Max(0.0, Distance);
}

void SourcePlayerArrow(AHUD* Hud, UCanvas* Canvas, UTexture2D* Texture,
    const FVector2D& Position, const FVector2D& Forward, double Scale)
{
    if (Texture)
    {
        // The original SVG points upward; rotate in screen space after the map transform.
        const float Rotation = FMath::RadiansToDegrees(FMath::Atan2(Forward.Y, Forward.X)) + 90.0;
        Hud->DrawTexture(Texture, Position.X - 9.5 * Scale, Position.Y - 16.0 * Scale,
            19.0 * Scale, 32.0 * Scale, 0, 0, 1, 1, FLinearColor::White,
            BLEND_Translucent, 1.0f, false, Rotation, FVector2D(0.5, 0.5));
        return;
    }
    // Geometric fallback preserves the source hollow glyph; asset availability is exposed to probes.
    const FVector2D Right(-Forward.Y, Forward.X);
    auto P = [&](double X, double Y) { return Position + Right * ((X - 9.5) * Scale) +
        Forward * ((16.0 - Y) * Scale); };
    const FVector2D Outer[] = {P(9.5, 1), P(17, 21), P(2, 21)};
    const FVector2D Inner[] = {P(9.5, 3.9), P(15.5, 20), P(3.5, 20)};
    for (int32 I = 0; I < 3; ++I)
    {
        const int32 J = (I + 1) % 3;
        Hud->DrawLine(Outer[I].X, Outer[I].Y, Outer[J].X, Outer[J].Y,
            FLinearColor(0, 0, 0, 0.25f), 2.0 * Scale);
        Triangle(Canvas, Outer[I], Outer[J], Inner[J], FLinearColor::White);
        Triangle(Canvas, Outer[I], Inner[J], Inner[I], FLinearColor::White);
    }
}

void SourceTankHull(AHUD* Hud, UCanvas* Canvas, UTexture2D* Texture,
    const FVector2D& Position, const FVector2D& Forward, double Scale)
{
    if (Texture)
    {
        const float Rotation = FMath::RadiansToDegrees(FMath::Atan2(Forward.Y, Forward.X)) + 90.0;
        Hud->DrawTexture(Texture, Position.X - 8.5 * Scale, Position.Y - 8.5 * Scale,
            17.0 * Scale, 17.0 * Scale, 0, 0, 1, 1, FLinearColor::White,
            BLEND_Translucent, 1.0f, false, Rotation, FVector2D(0.5, 0.5));
        return;
    }
    const FVector2D Right(-Forward.Y, Forward.X);
    auto P = [&](double X, double Y) { return Position + Right * ((X - 8.5) * Scale) +
        Forward * ((8.5 - Y) * Scale); };
    const FVector2D Points[] = {P(8.5, 3), P(12.5, 12), P(8.5, 10.917), P(4.5, 12)};
    for (int32 I = 0; I < 4; ++I)
        Hud->DrawLine(Points[I].X, Points[I].Y, Points[(I + 1) % 4].X, Points[(I + 1) % 4].Y,
            FLinearColor(0, 0, 0, 0.5f), 2.0 * Scale);
    Triangle(Canvas, Points[0], Points[1], Points[2], FLinearColor(245.0f / 255, 200.0f / 255, 48.0f / 255));
    Triangle(Canvas, Points[0], Points[2], Points[3], FLinearColor(245.0f / 255, 200.0f / 255, 48.0f / 255));
    Triangle(Canvas, P(8.5, 4), P(10.58, 11.28), P(8.5, 10.5), FLinearColor::White);
    Triangle(Canvas, P(8.5, 4), P(8.5, 10.5), P(6.42, 11.28), FLinearColor::White);
}

FString DistanceText(double Metres)
{
    return Metres >= 1000.0 ? FString::Printf(TEXT("%g km"), Metres / 1000.0) :
        FString::Printf(TEXT("%g m"), Metres);
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
    FillMapInteractionFrame(OutFrame);
    return true;
}

bool FMapOverviewPresentation::Contains(const FVector2D& Pixel) const
{
    return bAvailable && Pixel.X >= MapPositionPixels.X && Pixel.Y >= MapPositionPixels.Y &&
        Pixel.X <= MapPositionPixels.X + MapSizePixels.X && Pixel.Y <= MapPositionPixels.Y + MapSizePixels.Y;
}

FVector2D FMapOverviewPresentation::Project(const FVector2D& DisplayUv) const
{
    return MapPositionPixels + (DisplayUv - ViewMinUv) / ViewSizeUv * MapSizePixels;
}

double UMapOverview::ZoomFactor(int32 Level)
{
    static const double Factors[] = {1.0, 1.4142135623730951, 2.0, 2.8284271247461903,
        4.0, 5.6568542494923806, 8.0, 10.0};
    return Factors[FMath::Clamp(Level, 0, MaximumZoomLevel)];
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

FVector2D UMapOverview::WorldToDisplayUv(const FVector2D& WorldMetres) const
{
    const FVector2D SourceUv = WorldToUv(WorldMetres);
    return FVector2D(SourceUv.X, bFlipSourceV ? 1.0 - SourceUv.Y : SourceUv.Y);
}

FVector2D UMapOverview::DisplayDirection(const FVector2D& Direction,
    const FMapOverviewPresentation& Layout) const
{
    FVector2D Uv = WorldDirectionToUv(Direction);
    if (bFlipSourceV) Uv.Y = -Uv.Y;
    FVector2D Pixel = Uv / Layout.ViewSizeUv * Layout.MapSizePixels;
    if (!Pixel.Normalize()) Pixel = FVector2D(0.0, -1.0);
    return Pixel;
}

void UMapOverview::LoadMissionMarks(const FString& Filename, const FMapOverviewFrame& Frame)
{
    MissionMarks.Reset();
    FString Json;
    if (!FFileHelper::LoadFileToString(Json, *Filename)) return;
    TSharedPtr<FJsonObject> Root;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Root) || !Root.IsValid()) return;
    FString Schema, MapId, SourceHash;
    if (!Root->TryGetStringField(TEXT("schema"), Schema) || Schema != TEXT("rmsim_map_mission_markers_v1") ||
        !Root->TryGetStringField(TEXT("map_id"), MapId) || MapId != Frame.MapId ||
        !Root->TryGetStringField(TEXT("source_sha256"), SourceHash) || SourceHash != Frame.SourceSha256)
    {
        UE_LOG(LogTemp, Warning, TEXT("MAP_MARKERS_REJECTED map=%s reason=source_identity"), *Frame.MapId);
        return;
    }
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!Root->TryGetArrayField(TEXT("markers"), Values) || !Values || Values->Num() > 256) return;
    for (const TSharedPtr<FJsonValue>& Value : *Values)
    {
        const TSharedPtr<FJsonObject>* Item = nullptr;
        if (!Value.IsValid() || !Value->TryGetObject(Item) || !Item || !Item->IsValid()) continue;
        FMapOverviewMark Mark;
        if (!(*Item)->TryGetStringField(TEXT("kind"), Mark.Kind) ||
            (Mark.Kind != TEXT("capture") && Mark.Kind != TEXT("spawn")) ||
            !ReadPoint(*Item, TEXT("position_ros_m"), Mark.RosMetres)) continue;
        (*Item)->TryGetStringField(TEXT("label"), Mark.Label);
        FString Team;
        double TeamNumber = 0.0;
        (*Item)->TryGetStringField(TEXT("team"), Team);
        (*Item)->TryGetNumberField(TEXT("team"), TeamNumber);
        Mark.Color = (Team == TEXT("friendly") || Team == TEXT("1") || TeamNumber == 1.0) ? FriendColor :
            (Team == TEXT("enemy") || Team == TEXT("2") || TeamNumber == 2.0) ? EnemyColor : FLinearColor::White;
        MissionMarks.Add(Mark);
    }
    UE_LOG(LogTemp, Display, TEXT("MAP_MARKERS_READY map=%s count=%d"), *Frame.MapId, MissionMarks.Num());
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
    MapFont = nullptr;
    FontRenderer = nullptr;
    PanelTexture = nullptr;
    PlayerArrowTexture = nullptr;
    HullArrowTexture = nullptr;
    MarkerTextures.Reset();
    MissionMarks.Reset();
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
    // Source texture coordinates are storage coordinates. A right-handed ROS XY
    // turn must be counterclockwise on a screen whose Y points downward.
    // Reflect BOTH the sampled source texture and every displayed coordinate.
    bFlipSourceV = Determinant > 0.0;
    double Columns = 7.0, Rows = 7.0;
    Root->TryGetNumberField(TEXT("grid_columns"), Columns);
    Root->TryGetNumberField(TEXT("grid_rows"), Rows);
    GridColumns = FMath::Clamp(FMath::RoundToInt(Columns), 2, 26);
    GridRows = FMath::Clamp(FMath::RoundToInt(Rows), 2, 26);
    double ExpectedWidth = 0.0, ExpectedHeight = 0.0;
    if (!Root->TryGetNumberField(TEXT("image_width"), ExpectedWidth) ||
        !Root->TryGetNumberField(TEXT("image_height"), ExpectedHeight) || ExpectedWidth < 1.0 || ExpectedHeight < 1.0)
        return Fail(TEXT("source image dimensions are required"));
    MapTexture = LoadObject<UTexture2D>(nullptr, *TextureAsset);
#if WITH_EDITOR
    if (IsValid(MapTexture) && MapTexture->IsCompiling())
    {
        bDefinitionAttempted = false;
        DefinitionError = TEXT("Map image is loading");
        return false;
    }
#endif
    if (!IsValid(MapTexture) || MapTexture->GetSizeX() != ExpectedWidth || MapTexture->GetSizeY() != ExpectedHeight)
        return Fail(TEXT("map texture or its source dimensions do not match"));
    FString Asset;
    if (Root->TryGetStringField(TEXT("font_asset"), Asset) && Asset.StartsWith(TEXT("/Game/")))
        MapFont = LoadObject<UFont>(nullptr, *Asset);
    if (IsValid(MapFont))
    {
        FontRenderer = NewObject<UMapFontRenderer>(this);
        FontRenderer->Initialize(MapFont);
    }
    auto LoadTexture = [&](const TCHAR* Key) -> UTexture2D*
    {
        FString Path;
        return Root->TryGetStringField(Key, Path) && Path.StartsWith(TEXT("/Game/")) ?
            LoadObject<UTexture2D>(nullptr, *Path) : nullptr;
    };
    PlayerArrowTexture = LoadTexture(TEXT("player_arrow_asset"));
    HullArrowTexture = LoadTexture(TEXT("hull_arrow_asset"));
    PanelTexture = LoadTexture(TEXT("full_map_background_asset"));
    const TSharedPtr<FJsonObject>* MarkerAssets = nullptr;
    if (Root->TryGetObjectField(TEXT("marker_assets"), MarkerAssets) && MarkerAssets && MarkerAssets->IsValid())
        for (const auto& Pair : (*MarkerAssets)->Values)
        {
            FString Path;
            if (Pair.Value->TryGetString(Path) && Path.StartsWith(TEXT("/Game/")))
                if (UTexture2D* Texture = LoadObject<UTexture2D>(nullptr, *Path))
                    MarkerTextures.Add(FString(Pair.Key.ToView()), TObjectPtr<UTexture2D>(Texture));
        }
    FString MarkersFile = TEXT("mission-markers.json");
    Root->TryGetStringField(TEXT("mission_markers_file"), MarkersFile);
    // A map package may refer only to its own local marker definition.
    if (FPaths::GetCleanFilename(MarkersFile) == MarkersFile)
        LoadMissionMarks(FPaths::Combine(FPaths::GetPath(Filename), MarkersFile), Frame);
    bDefinitionValid = true;
    const FVector2D VehicleUv = WorldToDisplayUv(FVector2D(Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y));
    UE_LOG(LogTemp, Display, TEXT("MAP_OVERVIEW_READY map=%s texture=%s image=%dx%d source=%s "
        "display_flip_v=%d original_font=%d original_arrow=%d original_hull=%d grid=%dx%d "
        "top_left_ros_m=(%.6f,%.6f) axis_u_m=(%.6f,%.6f) axis_v_m=(%.6f,%.6f) vehicle_uv=(%.9f,%.9f)"),
        *Frame.MapId, *TextureAsset, MapTexture->GetSizeX(), MapTexture->GetSizeY(), *SourceHash,
        bFlipSourceV ? 1 : 0, IsValid(MapFont) ? 1 : 0, IsValid(PlayerArrowTexture) ? 1 : 0,
        IsValid(HullArrowTexture) ? 1 : 0, GridColumns, GridRows,
        ImageTopLeft.X, ImageTopLeft.Y, ImageAxisU.X, ImageAxisU.Y, ImageAxisV.X, ImageAxisV.Y,
        VehicleUv.X, VehicleUv.Y);
    return true;
}

bool UMapOverview::Describe(const FMapOverviewFrame& Frame, FVector2D ViewportPixels,
    FMapOverviewPresentation& Out)
{
    Out = FMapOverviewPresentation();
    if (ViewportPixels.X < 240 || ViewportPixels.Y < 240 || !LoadDefinition(Frame)) return false;
    Out.bAvailable = true;
    Out.bFlipSourceV = bFlipSourceV;
    Out.bUsesSourceFont = IsValid(MapFont);
    Out.bOriginalFontLoaded = IsValid(MapFont);
    Out.bOriginalFontEffectLoaded = IsValid(FontRenderer) && FontRenderer->IsReady();
    Out.bOriginalPanelLoaded = IsValid(PanelTexture);
    Out.bOriginalArrowLoaded = IsValid(PlayerArrowTexture);
    Out.bOriginalHullLoaded = IsValid(HullArrowTexture);
    Out.UiScale = ViewportPixels.Y / 1080.0;
    // The original 1440p HUD has a 14px safe-area margin (10px at 1080p).
    const double Padding = 10.0 * Out.UiScale;
    // Source HUD: sizeTacticalMap=0.3*shHud. M uses optionlist_container[type=big]
    // at 0.66 screen height with 10px@1080 internal padding; the map starts
    // at the left edge of its right-hand parent, beside the objectives column.
    const double Side = Frame.bExpanded ?
        FMath::Min(ViewportPixels.Y * 0.66 - Padding * 2.0, ViewportPixels.X * 0.5 - Padding * 2.0) :
        FMath::Min(ViewportPixels.Y * 0.3, ViewportPixels.X - Padding * 2.0);
    Out.MapSizePixels = FVector2D(Side, Side);
    Out.MapPositionPixels = Frame.bExpanded ?
        FVector2D(ViewportPixels.X * 0.5, (ViewportPixels.Y - Side) * 0.5) :
        ViewportPixels - Out.MapSizePixels - FVector2D(Padding, Padding);
    const double Zoom = ZoomFactor(Frame.bExpanded ? Frame.FullMapZoomLevel : Frame.MinimapZoomLevel);
    Out.ViewSizeUv = FVector2D(1.0 / Zoom, 1.0 / Zoom);
    const FVector2D VehicleUv = WorldToDisplayUv(FVector2D(Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y));
    const FVector2D DesiredCenter = Frame.bExpanded ? Frame.FullMapCenterUv : VehicleUv;
    Out.ViewMinUv = FVector2D(FMath::Clamp(DesiredCenter.X - Out.ViewSizeUv.X * 0.5, 0.0, 1.0 - Out.ViewSizeUv.X),
        FMath::Clamp(DesiredCenter.Y - Out.ViewSizeUv.Y * 0.5, 0.0, 1.0 - Out.ViewSizeUv.Y));
    Out.VehiclePositionPixels = Out.Project(VehicleUv);
    Out.VehicleForwardPixels = DisplayDirection(Frame.VehicleForwardRos, Out);
    Out.DisplaySpanMetres = ImageAxisU.Size() * Out.ViewSizeUv.X;
    Out.MetresPerPixel = Out.DisplaySpanMetres / Out.MapSizePixels.X;
    // Source HUD uses a roughly one-cell black scale bar, with its true metric value.
    Out.ScaleDistanceMetres = FMath::Max(1.0, FMath::RoundToDouble(Out.DisplaySpanMetres / GridColumns));
    Out.ScaleWidthPixels = Out.ScaleDistanceMetres / Out.MetresPerPixel;
    return true;
}

bool UMapOverview::ScreenToWorld(const FMapOverviewFrame& Frame, FVector2D ViewportPixels,
    FVector2D Pixel, FVector2D& OutRos)
{
    FMapOverviewPresentation Layout;
    if (!Describe(Frame, ViewportPixels, Layout) || !Layout.Contains(Pixel)) return false;
    FVector2D Uv = Layout.ViewMinUv + (Pixel - Layout.MapPositionPixels) / Layout.MapSizePixels * Layout.ViewSizeUv;
    if (bFlipSourceV) Uv.Y = 1.0 - Uv.Y;
    OutRos = ImageTopLeft + ImageAxisU * Uv.X + ImageAxisV * Uv.Y;
    return FMath::IsFinite(OutRos.X) && FMath::IsFinite(OutRos.Y);
}

bool UMapOverview::Draw(AHUD* Hud, UCanvas* Canvas, const FMapOverviewFrame& Frame)
{
    if (!Hud || !Canvas) return false;
    FMapOverviewPresentation Layout;
    if (!Describe(Frame, FVector2D(Canvas->SizeX, Canvas->SizeY), Layout)) return false;
    const FVector2D& P = Layout.MapPositionPixels;
    const FVector2D& S = Layout.MapSizePixels;
    const FVector2D& Uv = Layout.ViewMinUv;
    const FVector2D& UvSize = Layout.ViewSizeUv;
    const double Ui = Layout.UiScale;
    UFont* Font = IsValid(MapFont) ? MapFont.Get() : GEngine ? GEngine->GetSmallFont() : nullptr;
    if (Frame.bExpanded)
    {
        // tacticalmap.blk root uses shadeBackgroundColor=#AA000000; it has no
        // blur/fullScreenBgrColor layer. The panel uses the original nine-slice
        // option_select_odd image inside the 0.66H optionlist_container.
        Hud->DrawRect(FLinearColor::FromSRGBColor(FColor(0, 0, 0, 170)),
            0, 0, Canvas->SizeX, Canvas->SizeY);
        const double Padding = 10.0 * Ui;
        SourceMapPanel(Hud, PanelTexture, FVector2D(P.X - S.X - Padding, P.Y - Padding),
            FVector2D(S.X * 2.0 + Padding * 2.0, S.Y + Padding * 2.0), Ui);
    }
    // The exact same reflection is applied to texture sampling, marker positions,
    // heading vectors, and ScreenToWorld. Do not negate only the arrow angle.
    Hud->DrawTexture(MapTexture, P.X, P.Y, S.X, S.Y,
        Uv.X, bFlipSourceV ? 1.0 - Uv.Y : Uv.Y,
        UvSize.X, bFlipSourceV ? -UvSize.Y : UvSize.Y,
        FLinearColor::White, BLEND_Opaque);

    for (int32 I = 1; I < GridColumns; ++I)
    {
        const double X = Layout.Project(FVector2D(double(I) / GridColumns, 0)).X;
        if (X > P.X && X < P.X + S.X) Hud->DrawRect(GridColor, X - 0.5, P.Y, 1.0f, S.Y);
    }
    for (int32 I = 1; I < GridRows; ++I)
    {
        const double Y = Layout.Project(FVector2D(0, double(I) / GridRows)).Y;
        if (Y > P.Y && Y < P.Y + S.Y) Hud->DrawRect(GridColor, P.X, Y - 0.5, S.X, 1.0f);
    }
    const FVector2D Vehicle = ClampToMap(Layout.VehiclePositionPixels, Layout, 3.0 * Ui);
    // View boundaries follow the camera. C may rotate these without rotating the actual gimbal arrow.
    if (Layout.Contains(Layout.VehiclePositionPixels))
    {
        FVector2D View = Frame.ViewForwardRos.GetSafeNormal();
        if (View.IsNearlyZero()) View = Frame.VehicleForwardRos.GetSafeNormal();
        const double HalfFov = FMath::DegreesToRadians(FMath::Clamp(Frame.ViewFovDegrees, 5.0, 170.0) * 0.5);
        // Original screenshots show a bright viewing sector with a dark surround.
        // This contrast interprets native fovColor.a=190 as retained map intensity.
        const FLinearColor OutOfViewColor = FLinearColor::FromSRGBColor(FColor(10, 0, 0, 65));
        TArray<double> FanAngles;
        FanAngles.Add(HalfFov);
        FanAngles.Add(2.0 * PI - HalfFov);
        // Include each rectangle corner exactly; sampling a circular fan can cut
        // corners and leak unshaded slivers beyond a narrow camera view.
        for (const FVector2D& Corner : {Uv, Uv + FVector2D(UvSize.X, 0),
            Uv + UvSize, Uv + FVector2D(0, UvSize.Y)})
        {
            const FVector2D SourceCorner(Corner.X, bFlipSourceV ? 1.0 - Corner.Y : Corner.Y);
            const FVector2D WorldCorner = ImageTopLeft + ImageAxisU * SourceCorner.X + ImageAxisV * SourceCorner.Y;
            const FVector2D Direction = WorldCorner - FVector2D(Frame.VehicleRosMetres.X, Frame.VehicleRosMetres.Y);
            double Angle = FMath::Atan2(View.X * Direction.Y - View.Y * Direction.X,
                FVector2D::DotProduct(View, Direction));
            if (Angle < 0.0) Angle += 2.0 * PI;
            if (Angle > HalfFov && Angle < 2.0 * PI - HalfFov) FanAngles.Add(Angle);
        }
        FanAngles.Sort();
        auto EndAt = [&](double Angle)
        {
            const FVector2D Ray(View.X * FMath::Cos(Angle) - View.Y * FMath::Sin(Angle),
                View.X * FMath::Sin(Angle) + View.Y * FMath::Cos(Angle));
            return RayToMapEdge(Vehicle, DisplayDirection(Ray, Layout), Layout);
        };
        FVector2D Previous = EndAt(FanAngles[0]);
        for (int32 I = 1; I < FanAngles.Num(); ++I)
        {
            const FVector2D End = EndAt(FanAngles[I]);
            Triangle(Canvas, Vehicle, Previous, End, OutOfViewColor);
            Previous = End;
        }
    }

    auto DrawMark = [&](const FMapOverviewMark& Mark)
    {
        const FVector2D Point = Layout.Project(WorldToDisplayUv(Mark.RosMetres));
        if (!Layout.Contains(Point)) return;
        FString Key = Mark.Kind + TEXT("_") + Mark.Label;
        const TObjectPtr<UTexture2D>* Asset = MarkerTextures.Find(Key);
        if (!Asset) Asset = MarkerTextures.Find(Mark.Kind);
        const double Size = (Mark.Kind == TEXT("capture") ? 21.0 : Mark.Kind == TEXT("spawn") ? 30.0 : 24.0) * Ui;
        if (Asset && IsValid(Asset->Get()))
        {
            UTexture2D* Texture = Asset->Get();
            const double Width = Size * double(Texture->GetSizeX()) / FMath::Max(1, Texture->GetSizeY());
            if (Point.X - Width * 0.5 >= P.X && Point.X + Width * 0.5 <= P.X + S.X &&
                Point.Y - Size * 0.5 >= P.Y && Point.Y + Size * 0.5 <= P.Y + S.Y)
                Hud->DrawTexture(Texture, Point.X - Width * 0.5, Point.Y - Size * 0.5, Width, Size,
                    0, 0, 1, 1, Mark.Color, BLEND_Translucent);
        }
        else if (Mark.Kind == TEXT("capture"))
        {
            Circle(Hud, Point, 10.0 * Ui, FLinearColor(0, 0, 0, 0.65f), 3.0 * Ui);
            Circle(Hud, Point, 10.0 * Ui, Mark.Color, 1.5 * Ui);
            MapText(Hud, FontRenderer, Font, Mark.Label, Point, 15.0 * Ui, Mark.Color, true, true);
        }
        else if (Mark.Kind == TEXT("spawn"))
        {
            const FVector2D D[] = {Point + FVector2D(0, -11) * Ui, Point + FVector2D(8, 0) * Ui,
                Point + FVector2D(0, 11) * Ui, Point + FVector2D(-8, 0) * Ui};
            for (int32 I = 0; I < 4; ++I)
                Hud->DrawLine(D[I].X, D[I].Y, D[(I + 1) % 4].X, D[(I + 1) % 4].Y, Mark.Color, 2.0 * Ui);
        }
        else
        {
            Circle(Hud, Point, 7.0 * Ui, FLinearColor::Black, 3.0 * Ui);
            Circle(Hud, Point, 7.0 * Ui, Mark.Color, 1.5 * Ui);
            Hud->DrawLine(Point.X - 10.0 * Ui, Point.Y, Point.X + 10.0 * Ui, Point.Y, Mark.Color, 1.0f);
            Hud->DrawLine(Point.X, Point.Y - 10.0 * Ui, Point.X, Point.Y + 10.0 * Ui, Mark.Color, 1.0f);
        }
        if (Mark.Kind == TEXT("ping"))
        {
            const double Radius = (10.0 + FMath::Fmod(FMath::Max(0.0, Mark.AgeSeconds), 1.0) * 16.0) * Ui;
            if (Point.X - Radius >= P.X && Point.X + Radius <= P.X + S.X &&
                Point.Y - Radius >= P.Y && Point.Y + Radius <= P.Y + S.Y)
                Circle(Hud, Point, Radius, Mark.Color.CopyWithNewOpacity(0.7f), 1.0f);
        }
    };
    for (const FMapOverviewMark& Mark : MissionMarks) DrawMark(Mark);
    if (MissionMarks.IsEmpty())
    {
        FMapOverviewMark Spawn;
        Spawn.Kind = TEXT("spawn");
        Spawn.RosMetres = Frame.SpawnRosMetres;
        Spawn.Color = FriendColor;
        DrawMark(Spawn);
    }
    for (const FMapOverviewMark& Mark : Frame.Marks) DrawMark(Mark);

    SourceTankHull(Hud, Canvas, HullArrowTexture, Vehicle, DisplayDirection(Frame.HullForwardRos, Layout), Ui);
    SourcePlayerArrow(Hud, Canvas, PlayerArrowTexture, Vehicle, Layout.VehicleForwardPixels, Ui);

    // Source tactical-map labels: numbers above columns, lowercase letters along the left edge.
    for (int32 I = 0; I < GridColumns; ++I)
    {
        const double X = Layout.Project(FVector2D((I + 0.5) / GridColumns, 0)).X;
        if (X >= P.X + 12.0 * Ui && X <= P.X + S.X - 12.0 * Ui)
            MapText(Hud, FontRenderer, Font, FString::FromInt(I + 1), FVector2D(X, P.Y + 3.0 * Ui), 18.0 * Ui,
                FLinearColor::Black, true);
    }
    for (int32 I = 0; I < GridRows; ++I)
    {
        const double Y = Layout.Project(FVector2D(0, (I + 0.5) / GridRows)).Y;
        if (Y >= P.Y + 30.0 * Ui && Y <= P.Y + S.Y - 12.0 * Ui)
            MapText(Hud, FontRenderer, Font, FString::Chr(TCHAR('a' + I)), FVector2D(P.X + 3.0 * Ui, Y), 18.0 * Ui,
                FLinearColor::Black, false, true);
    }

    const FVector2D ScaleEnd(P.X + S.X - 6.0 * Ui, P.Y + S.Y - 2.0 * Ui);
    const FVector2D ScaleStart = ScaleEnd - FVector2D(Layout.ScaleWidthPixels, 0);
    Hud->DrawLine(ScaleStart.X, ScaleStart.Y, ScaleEnd.X, ScaleEnd.Y, FLinearColor::Black, 2.0f);
    MapText(Hud, FontRenderer, Font, DistanceText(Layout.ScaleDistanceMetres),
        FVector2D((ScaleStart.X + ScaleEnd.X) * 0.5, ScaleStart.Y - 19.0 * Ui),
        18.0 * Ui, FLinearColor::Black, true);
    Border(Hud, P, S, FrameColor, 1.0f);
    return Frame.bExpanded;
}
