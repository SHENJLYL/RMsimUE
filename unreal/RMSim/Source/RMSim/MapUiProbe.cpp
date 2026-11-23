#include "ImportLabGameMode.h"
#include "MapOverview.h"
#include "VehicleRig.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"

// The normal manual-input adapter, physical rig and real game viewport are exercised.
// Screenshots include the actual Canvas HUD; scene-capture substitutes are not used.
struct FMapUiProbeState
{
    int32 Phase=0,Frames=0,CaptureFrames=0,Captures=0,InitialRecoveries=0;
    uint64 InitialGeneration=0;
    double Seconds=0,PhaseSeconds=0,CaptureStarted=0,ReferenceYaw=0;
    double ReferenceDistance=0,ReferenceFov=0,InitialSpan=0;
    FVector ReferenceDirection=FVector::ForwardVector;
    FVector2D ReferenceArrow=FVector2D(0,-1),Cursor=FVector2D::ZeroVector;
    FVector2D AnchorRos=FVector2D::ZeroVector,ExpectedCenter=FVector2D::ZeroVector;
    FMapOverviewPresentation BeforePresentation;
    TSharedPtr<FJsonObject> Before,CapturePresentation,CaptureState;
    TArray<TSharedPtr<FJsonValue>> Metrics,Screenshots;
    FString PendingCapture;
    bool CaptureRequested=false,CaptureFreeLook=false,CaptureModifier=false;
    double MaximumAnchorError=0;
};

namespace
{
TArray<TSharedPtr<FJsonValue>> MapPair(const FVector2D& V)
{
    return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y)};
}
double MapCross(const FVector2D& A,const FVector2D& B){return A.X*B.Y-A.Y*B.X;}
double MapHeadingError(const FVector& A,const FVector& B)
{
    return FMath::Abs(FMath::FindDeltaAngleDegrees(A.Rotation().Yaw,B.Rotation().Yaw));
}
TSharedPtr<FJsonObject> MapLayoutJson(const FMapOverviewPresentation& L)
{
    auto J=MakeShared<FJsonObject>();
    J->SetArrayField(TEXT("map_position_px"),MapPair(L.MapPositionPixels));
    J->SetArrayField(TEXT("map_size_px"),MapPair(L.MapSizePixels));
    J->SetArrayField(TEXT("view_min_uv"),MapPair(L.ViewMinUv));
    J->SetArrayField(TEXT("view_size_uv"),MapPair(L.ViewSizeUv));
    J->SetArrayField(TEXT("vehicle_position_px"),MapPair(L.VehiclePositionPixels));
    J->SetArrayField(TEXT("vehicle_forward_px"),MapPair(L.VehicleForwardPixels));
    J->SetNumberField(TEXT("metres_per_pixel"),L.MetresPerPixel);
    J->SetNumberField(TEXT("scale_distance_m"),L.ScaleDistanceMetres);
    J->SetNumberField(TEXT("scale_width_px"),L.ScaleWidthPixels);
    J->SetNumberField(TEXT("display_span_m"),L.DisplaySpanMetres);
    J->SetBoolField(TEXT("flip_source_v"),L.bFlipSourceV);
    J->SetBoolField(TEXT("original_font_loaded"),L.bOriginalFontLoaded);
    J->SetBoolField(TEXT("original_font_effect_loaded"),L.bOriginalFontEffectLoaded);
    J->SetBoolField(TEXT("original_panel_loaded"),L.bOriginalPanelLoaded);
    J->SetBoolField(TEXT("original_arrow_loaded"),L.bOriginalArrowLoaded);
    J->SetBoolField(TEXT("original_hull_loaded"),L.bOriginalHullLoaded);
    return J;
}
}

void AImportLabGameMode::TickMapUiProbe(double DeltaSeconds)
{
    auto* PC=GetWorld()->GetFirstPlayerController();
    auto* Aim=GetAimCapture();
    auto* Hud=PC?Cast<AImportLabHUD>(PC->GetHUD()):nullptr;
    if(!Rig||!Rig->IsContactPhysics()||!StageMap.bSavedScene||!PC||!Aim||!Hud||!ControlReport.IsValid())
    {Abort(TEXT("map UI probe requires the saved map, physical rig and native HUD"));return;}
    if(!MapUiProbe.IsValid())
    {
        MapUiProbe=MakeShared<FMapUiProbeState>();
        auto& P=*MapUiProbe;
        P.InitialGeneration=Rig->GetMotionGeneration();P.InitialRecoveries=ContactRecoveryCount;
        ControlPendingCapture.Reset();
        if(OverviewCapture)OverviewCapture->bCaptureEveryFrame=false;
        for(const auto& Camera:Rig->GetCameras())Camera.Value->bCaptureEveryFrame=false;
        ControlReport->SetStringField(TEXT("probe"),TEXT("native_map_ui_v1"));
        ControlReport->SetBoolField(TEXT("pose_overrides_used"),false);
        ControlReport->SetBoolField(TEXT("os_input_verified"),false);
        ControlReport->SetBoolField(TEXT("hud_pixels_verified"),false);
        ControlReport->SetBoolField(TEXT("real_viewport_hud_captures"),true);
        ControlReport->SetNumberField(TEXT("initial_motion_generation"),double(P.InitialGeneration));
        ControlReport->SetNumberField(TEXT("initial_recovery_count"),P.InitialRecoveries);
        IFileManager::Get().MakeDirectory(*(ControlEvidenceDirectory/TEXT("hud")),true);
        UE_LOG(LogRMSim,Display,TEXT("MAP_UI_BEGIN actual_viewport_hud=1"));
    }
    auto& P=*MapUiProbe;
    P.Seconds+=DeltaSeconds;
    if(Rig->GetActorTransform().ContainsNaN()||Rig->GetActorUpVector().Z<.65||
        Rig->GetActorLocation().Z<ContactSpawn.GetLocation().Z-150||P.Seconds>50||
        Rig->GetMotionGeneration()!=P.InitialGeneration||ContactRecoveryCount!=P.InitialRecoveries)
    {Abort(TEXT("map UI probe lost support, exceeded its simulation budget or reset"));return;}

    if(!P.PendingCapture.IsEmpty())
    {
        FManualControlInput Hold;Hold.Brake=true;
        Hold.FreeLook=P.CaptureFreeLook;Hold.MapModifier=P.CaptureModifier;
        ApplyManualInput(Hold,DeltaSeconds);
        if(++P.CaptureFrames<8)return;
        const FString File=FPaths::ConvertRelativePathToFull(
            ControlEvidenceDirectory/TEXT("hud")/(P.PendingCapture+TEXT(".png")));
        if(!P.CaptureRequested)
        {
            int32 CaptureWidth=0,CaptureHeight=0;PC->GetViewportSize(CaptureWidth,CaptureHeight);
            FMapOverviewFrame CaptureFrame;
            FMapOverviewPresentation CaptureLayout;
            if(!GetMapOverviewFrame(CaptureFrame)||!Hud->DescribeMap(CaptureFrame,
                FVector2D(CaptureWidth,CaptureHeight),CaptureLayout))
            {Abort(TEXT("capture-time map presentation unavailable"));return;}
            P.CapturePresentation=MapLayoutJson(CaptureLayout);
            P.CaptureState=ManualControlState();
            if(IFileManager::Get().FileExists(*File))
            {Abort(TEXT("refusing a pre-existing HUD capture"));return;}
            P.CaptureRequested=true;P.CaptureStarted=FPlatformTime::Seconds();
            FScreenshotRequest::RequestScreenshot(File,true,false,false,FIntRect(),true);
            return;
        }
        if(IFileManager::Get().FileSize(*File)>128&&!FScreenshotRequest::IsScreenshotRequested())
        {
            auto J=MakeShared<FJsonObject>();
            J->SetStringField(TEXT("name"),P.PendingCapture);J->SetStringField(TEXT("file"),File);
            J->SetBoolField(TEXT("includes_hud"),true);
            J->SetObjectField(TEXT("presentation"),P.CapturePresentation);
            J->SetObjectField(TEXT("state"),P.CaptureState);
            P.Screenshots.Add(MakeShared<FJsonValueObject>(J));
            ControlReport->SetArrayField(TEXT("hud_screenshots"),P.Screenshots);
            ++P.Captures;
            UE_LOG(LogRMSim,Display,TEXT("MAP_UI_CAPTURE name=%s file=%s"),*P.PendingCapture,*File);
            P.PendingCapture.Reset();P.CaptureFrames=0;P.CaptureRequested=false;
        }
        else if(FPlatformTime::Seconds()-P.CaptureStarted>15)
            Abort(TEXT("actual game-viewport HUD screenshot was not written"));
        return;
    }

    int32 Width=0,Height=0;PC->GetViewportSize(Width,Height);
    const FVector2D Viewport(Width,Height);
    FMapOverviewFrame Frame;
    FMapOverviewPresentation Layout;
    auto ReadLayout=[&](){return Width>0&&Height>0&&GetMapOverviewFrame(Frame)&&Hud->DescribeMap(Frame,Viewport,Layout);};
    if(!ReadLayout()){Abort(TEXT("map UI presentation unavailable"));return;}
    const bool First=P.Frames==0;
    if(First)
    {
        P.Before=ManualControlState();P.BeforePresentation=Layout;
        P.ReferenceDirection=Aim->GetForwardVector();P.ReferenceArrow=Layout.VehicleForwardPixels;
        P.ReferenceYaw=OrbitYaw;P.ReferenceDistance=ChaseDistanceCm;P.ReferenceFov=ScopeBaseFov;
    }
    P.PhaseSeconds+=DeltaSeconds;
    FManualControlInput Input;Input.Brake=true;
    auto Apply=[&]()
    {
        ApplyManualInput(Input,DeltaSeconds);++P.Frames;
        return ReadLayout();
    };
    auto Stable=[&](){return MapHeadingError(P.ReferenceDirection,Aim->GetForwardVector())<.15;};
    auto ScaleConsistent=[&]()
    {
        return Layout.ScaleWidthPixels>0&&Layout.ScaleDistanceMetres>0&&
            FMath::Abs(Layout.ScaleDistanceMetres-Layout.ScaleWidthPixels*Layout.MetresPerPixel)<.01;
    };
    auto Check=[&](const TCHAR* Name,bool Passed,const TCHAR* Capture=nullptr)
    {
        auto M=MakeShared<FJsonObject>();
        M->SetStringField(TEXT("name"),Name);M->SetNumberField(TEXT("phase"),P.Phase);
        M->SetNumberField(TEXT("duration_s"),P.PhaseSeconds);
        M->SetNumberField(TEXT("gimbal_delta_deg"),FMath::FindDeltaAngleDegrees(
            P.ReferenceDirection.Rotation().Yaw,Aim->GetForwardVector().Rotation().Yaw));
        M->SetNumberField(TEXT("screen_direction_cross"),MapCross(P.ReferenceArrow,Layout.VehicleForwardPixels));
        M->SetNumberField(TEXT("anchor_error_m"),P.MaximumAnchorError);
        M->SetObjectField(TEXT("presentation"),MapLayoutJson(Layout));
        M->SetObjectField(TEXT("state"),ManualControlState());
        P.Metrics.Add(MakeShared<FJsonValueObject>(M));
        ControlReport->SetArrayField(TEXT("phase_metrics"),P.Metrics);
        ControlReport->SetNumberField(TEXT("final_motion_generation"),double(Rig->GetMotionGeneration()));
        ControlReport->SetNumberField(TEXT("final_recovery_count"),ContactRecoveryCount);
        const FString Detail=FString::Printf(TEXT("gimbal delta %.6f deg; screen cross %.6f; zoom mini=%d full=%d; scale %.6f m / %.6f px; anchor error %.9f m"),
            M->GetNumberField(TEXT("gimbal_delta_deg")),M->GetNumberField(TEXT("screen_direction_cross")),
            MiniMapZoomLevel,FullMapZoomLevel,Layout.ScaleDistanceMetres,Layout.ScaleWidthPixels,P.MaximumAnchorError);
        if(!RecordControlCheck(Name,Passed,P.Before,Detail))return;
        if(Capture)
        {
            P.PendingCapture=Capture;P.CaptureFreeLook=bFreeLook;P.CaptureModifier=Input.MapModifier;
            P.CaptureFrames=0;P.CaptureRequested=false;
        }
        ++P.Phase;P.PhaseSeconds=0;P.Frames=0;P.MaximumAnchorError=0;
    };

    switch(P.Phase)
    {
    case 0:
        Apply();if(P.PhaseSeconds<1.2)return;
        P.InitialSpan=Layout.DisplaySpanMetres;
        Check(TEXT("original_map_ui_assets_and_supported_spawn"),
            bHadInputFocus&&!bSightMode&&!bTacticalMapExpanded&&MiniMapZoomLevel==0&&
            Rig->GetCommandOriginVelocity().Size()<15&&Layout.bOriginalFontLoaded&&
            Layout.bOriginalFontEffectLoaded&&Layout.bOriginalPanelLoaded&&
            Layout.bOriginalArrowLoaded&&Layout.bOriginalHullLoaded&&
            Layout.bFlipSourceV&&ScaleConsistent(),TEXT("01-minimap-baseline"));return;
    case 1:
        if(First)Input.MouseX=-35./MouseSensitivity;
        Apply();if(P.PhaseSeconds<.75)return;
        Check(TEXT("physical_left_turn_is_screen_counterclockwise"),
            FMath::FindDeltaAngleDegrees(P.ReferenceDirection.Rotation().Yaw,
                Aim->GetForwardVector().Rotation().Yaw)<-30&&
            MapCross(P.ReferenceArrow,Layout.VehicleForwardPixels)<-.45,
            TEXT("02-turn-left"));return;
    case 2:
        if(First)Input.MouseX=70./MouseSensitivity;
        Apply();if(P.PhaseSeconds<.9)return;
        Check(TEXT("physical_right_turn_is_screen_clockwise"),
            FMath::FindDeltaAngleDegrees(P.ReferenceDirection.Rotation().Yaw,
                Aim->GetForwardVector().Rotation().Yaw)>60&&
            MapCross(P.ReferenceArrow,Layout.VehicleForwardPixels)>.8,
            TEXT("03-turn-right"));return;
    case 3:
        Input.FreeLook=true;
        if(First)Input.MouseX=120./MouseSensitivity;
        Apply();if(P.PhaseSeconds<.45)return;
        Check(TEXT("c_orbit_keeps_gimbal_arrow_and_separates_view_sector"),
            bFreeLook&&Stable()&&FVector2D::DotProduct(P.ReferenceArrow,Layout.VehicleForwardPixels)>.99999&&
            FVector2D::DotProduct(Frame.VehicleForwardRos,Frame.ViewForwardRos)<-.4,
            TEXT("04-c-orbit"));return;
    case 4:
        Input.MapModifier=true;
        if(First){Input.WheelSteps=3;Input.MouseX=10000;}
        Apply();if(P.PhaseSeconds<.35)return;
        Check(TEXT("alt_wheel_zooms_minimap_without_steering_or_chase_zoom"),
            MiniMapZoomLevel==3&&FullMapZoomLevel==0&&bMapCursorActive&&Stable()&&
            FMath::IsNearlyEqual(ChaseDistanceCm,P.ReferenceDistance,.001)&&
            FMath::IsNearlyEqual(Layout.DisplaySpanMetres,P.InitialSpan/UMapOverview::ZoomFactor(3),.01)&&ScaleConsistent(),
            TEXT("05-minimap-zoom"));return;
    case 5:
        Input.MapModifier=true;if(First)Input.WheelSteps=100;
        Apply();if(P.PhaseSeconds<.15)return;
        Check(TEXT("minimap_zoom_clamps_at_source_ten_times"),
            MiniMapZoomLevel==UMapOverview::MaximumZoomLevel&&Stable()&&
            FMath::IsNearlyEqual(UMapOverview::ZoomFactor(MiniMapZoomLevel),10.,.000001)&&
            FMath::IsNearlyEqual(Layout.DisplaySpanMetres,P.InitialSpan/10.,.01)&&ScaleConsistent(),
            TEXT("06-minimap-max-zoom"));return;
    case 6:
        Input.MapModifier=true;if(First)Input.WheelSteps=-100;
        Apply();if(P.PhaseSeconds<.15)return;
        Check(TEXT("minimap_zoom_out_restores_full_extent"),
            MiniMapZoomLevel==0&&Stable()&&FMath::IsNearlyEqual(Layout.DisplaySpanMetres,P.InitialSpan,.01)&&
            FMath::IsNearlyEqual(ChaseDistanceCm,P.ReferenceDistance,.001)&&ScaleConsistent());return;
    case 7:
        if(First)Input.ToggleMap=true;
        Apply();if(P.PhaseSeconds<.2)return;
        Check(TEXT("m_opens_full_map_with_cursor"),
            bTacticalMapExpanded&&bMapCursorActive&&PC->bShowMouseCursor&&
            Layout.MapSizePixels.Y>P.BeforePresentation.MapSizePixels.Y*2&&MiniMapZoomLevel==0&&Stable(),
            TEXT("07-full-map"));return;
    case 8:
        if(First)
        {
            P.Cursor=Layout.MapPositionPixels+Layout.MapSizePixels*FVector2D(.62,.4);
            if(!Hud->MapScreenToWorld(Frame,Viewport,P.Cursor,P.AnchorRos))
            {Abort(TEXT("full-map zoom anchor unavailable"));return;}
            Input.WheelSteps=4;
        }
        Input.CursorPixels=P.Cursor;Apply();
        {
            FVector2D After;
            if(!Hud->MapScreenToWorld(Frame,Viewport,P.Cursor,After))
            {Abort(TEXT("full-map zoom anchor disappeared"));return;}
            P.MaximumAnchorError=FMath::Max(P.MaximumAnchorError,FVector2D::Distance(P.AnchorRos,After));
        }
        if(P.PhaseSeconds<.2)return;
        Check(TEXT("full_map_wheel_preserves_cursor_world_anchor"),
            FullMapZoomLevel==4&&MiniMapZoomLevel==0&&P.MaximumAnchorError<.01&&Stable()&&ScaleConsistent());return;
    case 9:
        if(First)
        {
            P.Cursor=Layout.MapPositionPixels+Layout.MapSizePixels*.5;
            const FVector2D Delta(80.,-40.);
            P.ExpectedCenter=FullMapCenterUv-FVector2D(Delta.X/Layout.MapSizePixels.X*Layout.ViewSizeUv.X,
                Delta.Y/Layout.MapSizePixels.Y*Layout.ViewSizeUv.Y);
        }
        Input.MapDrag=P.Frames<2;
        Input.CursorPixels=P.Cursor+(P.Frames==0?FVector2D::ZeroVector:FVector2D(80.,-40.));
        if(P.Frames==2){Input.MapPing=true;Input.MapPointOfInterest=true;}
        Apply();if(P.PhaseSeconds<.25)return;
        Check(TEXT("full_map_drag_ping_and_local_point_use_display_coordinates"),
            FVector2D::Distance(FullMapCenterUv,P.ExpectedCenter)<.000001&&
            Frame.Marks.Num()==2&&bLocalMapPointValid&&Stable(),
            TEXT("08-full-map-pan-marks"));return;
    case 10:
        if(First){Input.CloseMap=true;Input.MouseX=10000;Input.WheelSteps=100;}
        Apply();if(P.PhaseSeconds<.25)return;
        Check(TEXT("closing_map_discards_cursor_delta_and_preserves_zoom_levels"),
            !bTacticalMapExpanded&&!bMapCursorActive&&!PC->bShowMouseCursor&&
            MiniMapZoomLevel==0&&FullMapZoomLevel==4&&Stable()&&
            FMath::IsNearlyEqual(ChaseDistanceCm,P.ReferenceDistance,.001),
            TEXT("09-map-closed"));return;
    case 11:
        if(First)Input.ToggleSight=true;
        Input.MapModifier=true;
        if(P.Frames==1){Input.WheelSteps=2;Input.MouseX=10000;}
        Apply();if(P.PhaseSeconds<.4)return;
        Check(TEXT("scope_alt_wheel_changes_map_without_changing_fov_or_gimbal"),
            bSightMode&&MiniMapZoomLevel==2&&FullMapZoomLevel==4&&Stable()&&
            FMath::IsNearlyEqual(ScopeBaseFov,P.ReferenceFov,.0001),
            TEXT("10-scope-map-zoom"));return;
    case 12:
        if(P.Frames<2)
        {
            Input.Focused=P.Frames!=0;Input.ToggleMap=true;
            Input.WheelSteps=100;Input.MouseX=10000;
        }
        Apply();if(P.PhaseSeconds<.25)return;
        Check(TEXT("focus_restore_discards_stale_map_keys_wheel_and_mouse"),
            bHadInputFocus&&!bTacticalMapExpanded&&!bMapCursorActive&&
            MiniMapZoomLevel==2&&FullMapZoomLevel==4&&Stable()&&
            FMath::IsNearlyEqual(ScopeBaseFov,P.ReferenceFov,.0001));return;
    default:
        ControlReport->SetNumberField(TEXT("capture_sets_written"),P.Captures);
        if(ControlChecks.Num()!=13||P.Captures!=10||!WriteControlReport(TEXT("passed")))
        {Abort(TEXT("map UI evidence is incomplete"));return;}
        UE_LOG(LogRMSim,Display,TEXT("MAP_UI_PASSED checks=%d captures=%d"),ControlChecks.Num(),P.Captures);
        Rig->SetContactDrive(0,0,true);bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
}
