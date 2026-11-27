#include "ImportLabGameMode.h"
#include "MapOverview.h"
#include "RMUCGameReferee.h"
#include "VehicleRig.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"

bool AImportLabHUD::DescribeMap(const FMapOverviewFrame& Frame, const FVector2D& ViewportPixels,
    FMapOverviewPresentation& Out)
{
    if (!MapOverview) MapOverview = NewObject<UMapOverview>(this);
    return MapOverview->Describe(Frame, ViewportPixels, Out);
}

bool AImportLabHUD::MapScreenToWorld(const FMapOverviewFrame& Frame, const FVector2D& ViewportPixels,
    const FVector2D& Pixel, FVector2D& OutRos)
{
    if (!MapOverview) MapOverview = NewObject<UMapOverview>(this);
    return MapOverview->ScreenToWorld(Frame, ViewportPixels, Pixel, OutRos);
}

void AImportLabGameMode::FillMapInteractionFrame(FMapOverviewFrame& Frame) const
{
    Frame.bExpanded = bTacticalMapExpanded;
    Frame.MinimapZoomLevel = MiniMapZoomLevel;
    Frame.FullMapZoomLevel = FullMapZoomLevel;
    Frame.FullMapCenterUv = FullMapCenterUv;
    if (Rig)
    {
        const FVector Body = Rig->GetActorForwardVector();
        Frame.HullForwardRos = FVector2D(Body.X, -Body.Y).GetSafeNormal();
    }
    FVector View = FRotator(OrbitPitch, OrbitYaw, 0).Vector();
    Frame.ViewFovDegrees = 55.0;
    if (const auto* PC = GetWorld()->GetFirstPlayerController())
    {
        if (const auto* Camera = Cast<ACameraActor>(PC->GetViewTarget()))
        {
            View = Camera->GetActorForwardVector();
            Frame.ViewFovDegrees = Camera->GetCameraComponent()->FieldOfView;
        }
    }
    Frame.ViewForwardRos = FVector2D(View.X, -View.Y).GetSafeNormal();
    Frame.Marks.Reset();
    const double Age = GetWorld()->GetTimeSeconds() - LocalMapPingTime;
    if (Age >= 0.0 && Age < 5.0)
    {
        FMapOverviewMark Mark;
        Mark.RosMetres = LocalMapPingRos;
        Mark.Kind = TEXT("ping");
        Mark.Color = FLinearColor(1.0f, 0.72f, 0.12f, 1.0f);
        Mark.AgeSeconds = Age;
        Frame.Marks.Add(Mark);
    }
    if (bLocalMapPointValid)
    {
        FMapOverviewMark Mark;
        Mark.RosMetres = LocalMapPointRos;
        Mark.Kind = TEXT("poi");
        Mark.Color = FLinearColor(1.0f, 0.72f, 0.12f, 1.0f);
        Frame.Marks.Add(Mark);
    }
}

void AImportLabGameMode::UpdateManualMapInput(const FManualControlInput& Input, bool AllowActions,
    int32& CameraWheelSteps, bool& ConsumeMouse)
{
    CameraWheelSteps = Input.WheelSteps;
    ConsumeMouse = false;
    APlayerController* PC = GetWorld()->GetFirstPlayerController();
    const bool bDartBlackout = Referee && Referee->GetOwnDartBlackoutRemaining() > 0.0;
    if (!PC || (!StageMap.bSavedScene && !bDartBlackout && !bMapCursorActive)) return;
    const bool HadCursor = bMapCursorActive;
    const bool PreviousExpanded = bTacticalMapExpanded;
    if (!bDartBlackout)
    {
        DartBlackoutDestinationRos.Reset();
        LastDartBlackoutMoveTime = -3.0;
        if (StageMap.bSavedScene && AllowActions && Input.ToggleMap) bTacticalMapExpanded = !bTacticalMapExpanded;
        if (StageMap.bSavedScene && AllowActions && Input.CloseMap) bTacticalMapExpanded = false;
    }
    bMapCursorActive = bDartBlackout || (StageMap.bSavedScene && (bTacticalMapExpanded || Input.MapModifier));
    // Also discard the frame which returns from UI to captured mouse-look.
    ConsumeMouse = bMapCursorActive || HadCursor;
    if (HadCursor || !AllowActions) CameraWheelSteps = 0;
    if (bMapCursorActive != HadCursor)
    {
        PC->bShowMouseCursor = bMapCursorActive;
        if (bMapCursorActive)
        {
            FInputModeGameAndUI Mode;
            Mode.SetHideCursorDuringCapture(false);
            Mode.SetLockMouseToViewportBehavior(EMouseLockMode::LockAlways);
            PC->SetInputMode(Mode);
        }
        else
        {
            FInputModeGameOnly Mode;
            Mode.SetConsumeCaptureMouseDown(true);
            PC->SetInputMode(Mode);
        }
        bMapDragging = false;
    }
    if (bTacticalMapExpanded != PreviousExpanded)
    {
        bMapDragging = false;
        UE_LOG(LogRMSim, Display, TEXT("MAP_OVERVIEW_TOGGLE expanded=%d map=%s"),
            bTacticalMapExpanded, *StageMap.Id);
    }

    int32 Width = 0, Height = 0;
    PC->GetViewportSize(Width, Height);
    const FVector2D ViewportPixels(Width, Height);
    if (bDartBlackout)
    {
        bMapDragging = false;
        CameraWheelSteps = 0;
        if (AllowActions && Input.CloseMap) DartBlackoutDestinationRos.Reset();
        const double Now = GetWorld()->GetTimeSeconds();
        FVector2D RosPoint;
        if (AllowActions && !Input.CloseMap && Input.MapPing &&
            !bRosControl && Rig && Rig->IsContactPhysics() && Rig->HasSteeringModules() &&
            Referee->IsChassisPowered() && Now >= LastDartBlackoutMoveTime + 3.0 &&
            Referee->DartBlackoutMapScreenToWorld(ViewportPixels, Input.CursorPixels, RosPoint))
        {
            DartBlackoutDestinationRos = RosPoint;
            LastDartBlackoutMoveTime = Now;
            UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_BLACKOUT_MOVE sim_s=%.6f ros_m=(%.6f,%.6f)"),
                Now, RosPoint.X, RosPoint.Y);
        }
        return;
    }
    auto* Hud = Cast<AImportLabHUD>(PC->GetHUD());
    FMapOverviewFrame Frame;
    FMapOverviewPresentation Presentation;
    const bool Available = Hud && Width > 0 && Height > 0 &&
        GetMapOverviewFrame(Frame) && Hud->DescribeMap(Frame, ViewportPixels, Presentation);
    const bool CursorFinite = FMath::IsFinite(Input.CursorPixels.X) && FMath::IsFinite(Input.CursorPixels.Y);
    const auto InsideMap = [&]()
    {
        if (!Available || !CursorFinite) return false;
        const FVector2D Pixel = Input.CursorPixels - Presentation.MapPositionPixels;
        return Pixel.X >= 0 && Pixel.Y >= 0 &&
            Pixel.X <= Presentation.MapSizePixels.X && Pixel.Y <= Presentation.MapSizePixels.Y;
    };
    const auto ClampCenter = [](FVector2D Center, const FVector2D& ViewSize)
    {
        const FVector2D Half(FMath::Min(0.5, ViewSize.X * 0.5), FMath::Min(0.5, ViewSize.Y * 0.5));
        Center.X = FMath::Clamp(Center.X, Half.X, 1.0 - Half.X);
        Center.Y = FMath::Clamp(Center.Y, Half.Y, 1.0 - Half.Y);
        return Center;
    };

    if (bMapCursorActive)
    {
        CameraWheelSteps = 0;
        if (AllowActions && Input.WheelSteps != 0)
        {
            int32& Level = bTacticalMapExpanded ? FullMapZoomLevel : MiniMapZoomLevel;
            const int32 BeforeLevel = Level;
            Level = FMath::Clamp(Level + Input.WheelSteps, 0, UMapOverview::MaximumZoomLevel);
            if (bTacticalMapExpanded && Available && Level != BeforeLevel)
            {
                const double Ratio = UMapOverview::ZoomFactor(BeforeLevel) / UMapOverview::ZoomFactor(Level);
                const FVector2D OldCenter = Presentation.ViewMinUv + Presentation.ViewSizeUv * 0.5;
                FVector2D Anchor = OldCenter;
                if (InsideMap())
                {
                    const FVector2D Fraction(
                        (Input.CursorPixels.X - Presentation.MapPositionPixels.X) / Presentation.MapSizePixels.X,
                        (Input.CursorPixels.Y - Presentation.MapPositionPixels.Y) / Presentation.MapSizePixels.Y);
                    Anchor = Presentation.ViewMinUv + Fraction * Presentation.ViewSizeUv;
                }
                FullMapCenterUv = ClampCenter(Anchor + (OldCenter - Anchor) * Ratio, Presentation.ViewSizeUv * Ratio);
            }
            UE_LOG(LogRMSim, Display, TEXT("MAP_OVERVIEW_ZOOM expanded=%d level=%d factor=%.6f"),
                bTacticalMapExpanded, Level, UMapOverview::ZoomFactor(Level));
        }

        if (AllowActions && bTacticalMapExpanded && Input.MapDrag && InsideMap())
        {
            if (bMapDragging)
            {
                const FVector2D Delta = Input.CursorPixels - LastMapCursor;
                const FVector2D UvDelta(
                    Delta.X / Presentation.MapSizePixels.X * Presentation.ViewSizeUv.X,
                    Delta.Y / Presentation.MapSizePixels.Y * Presentation.ViewSizeUv.Y);
                FullMapCenterUv = ClampCenter(FullMapCenterUv - UvDelta, Presentation.ViewSizeUv);
            }
            LastMapCursor = Input.CursorPixels;
            bMapDragging = true;
        }
        else bMapDragging = false;

        if (AllowActions && InsideMap() && (Input.MapPing || Input.MapPointOfInterest))
        {
            // Refresh after a wheel/drag event; marks are local presentation state only.
            GetMapOverviewFrame(Frame);
            FVector2D Point;
            if (Hud->MapScreenToWorld(Frame, ViewportPixels, Input.CursorPixels, Point))
            {
                if (Input.MapPing)
                {
                    LocalMapPingRos = Point;
                    LocalMapPingTime = GetWorld()->GetTimeSeconds();
                    UE_LOG(LogRMSim, Display, TEXT("MAP_LOCAL_PING ros_m=(%.6f,%.6f)"), Point.X, Point.Y);
                }
                if (Input.MapPointOfInterest)
                {
                    bLocalMapPointValid = !bLocalMapPointValid || FVector2D::Distance(LocalMapPointRos, Point) > 5.0;
                    LocalMapPointRos = Point;
                    UE_LOG(LogRMSim, Display, TEXT("MAP_LOCAL_POI active=%d ros_m=(%.6f,%.6f)"),
                        bLocalMapPointValid, Point.X, Point.Y);
                }
            }
        }
    }
    else bMapDragging = false;
}
