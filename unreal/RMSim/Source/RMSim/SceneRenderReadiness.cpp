#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "Camera/PlayerCameraManager.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/RuntimeVirtualTextureComponent.h"
#include "ContentStreaming.h"
#include "CoreGlobals.h"
#include "Dom/JsonObject.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/World.h"
#include "EngineModule.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "PixelFormat.h"
#include "RendererInterface.h"
#include "RenderingThread.h"
#include "RHI.h"
#include "RHICommandList.h"
#include "VirtualTexturing.h"
#include "VT/RuntimeVirtualTexture.h"
#include "VT/RuntimeVirtualTextureVolume.h"
#if WITH_EDITOR
#include "AssetCompilingManager.h"
#include "ShaderCompiler.h"
#include "StaticMeshCompiler.h"
#include "TextureCompiler.h"
#endif

namespace
{
constexpr double SceneRenderTimeoutSeconds = 300.0;
constexpr float SceneStreamingWaitSeconds = 0.1f;
constexpr int32 RequiredCompletedFrames = 2;
constexpr double GroundDecalPreloadRadiusCm = 2000.0;
constexpr int32 MaxGroundDecalPreloadTiles = 256;
constexpr const TCHAR* GroundDecalRVTPath =
    TEXT("/Game/RMSim/Maps/wt_vlaanderen/Materials/WT_GroundDecalRVT.WT_GroundDecalRVT");

struct FSceneVTReadback
{
    bool bRenderThreadExecuted = false, bAllocated = false, bSyncReturned = false;
    uint32 Width = 0, Height = 0, WidthTiles = 0, HeightTiles = 0;
    uint32 VirtualTileSize = 0, PhysicalTileSize = 0, BorderSize = 0, SpaceId = 0;
    int32 RequestedTiles = 0;
    FIntPoint RequestMin = FIntPoint::ZeroValue, RequestMax = FIntPoint::ZeroValue;
    TArray<uint32> PhysicalSizes;
    TArray<EPixelFormat> Formats;
    uint64 PhysicalPageBytes = 0, PhysicalTextureBytes = 0;
    double SyncSeconds = 0;
    FString Error;

    TSharedRef<FJsonObject> ToJson() const
    {
        auto Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("render_thread_executed"), bRenderThreadExecuted);
        Result->SetBoolField(TEXT("allocated"), bAllocated);
        Result->SetBoolField(TEXT("sync_virtual_texture_updates_returned"), bSyncReturned);
        Result->SetNumberField(TEXT("sync_seconds"), SyncSeconds);
        Result->SetNumberField(TEXT("virtual_width_pixels"), Width);
        Result->SetNumberField(TEXT("virtual_height_pixels"), Height);
        Result->SetNumberField(TEXT("width_tiles"), WidthTiles);
        Result->SetNumberField(TEXT("height_tiles"), HeightTiles);
        Result->SetNumberField(TEXT("virtual_tile_size"), VirtualTileSize);
        Result->SetNumberField(TEXT("physical_tile_size_including_border"), PhysicalTileSize);
        Result->SetNumberField(TEXT("tile_border_size"), BorderSize);
        Result->SetNumberField(TEXT("space_id"), SpaceId);
        Result->SetNumberField(TEXT("top_mip_request_tile_coverage"), RequestedTiles);
        Result->SetNumberField(TEXT("request_tile_min_x"), RequestMin.X);
        Result->SetNumberField(TEXT("request_tile_min_y"), RequestMin.Y);
        Result->SetNumberField(TEXT("request_tile_max_x_exclusive"), RequestMax.X);
        Result->SetNumberField(TEXT("request_tile_max_y_exclusive"), RequestMax.Y);
        Result->SetNumberField(TEXT("physical_page_bytes_all_layers"), static_cast<double>(PhysicalPageBytes));
        Result->SetNumberField(TEXT("physical_texture_bytes_all_layers"), static_cast<double>(PhysicalTextureBytes));
        Result->SetStringField(TEXT("request_count_scope"),
            TEXT("exact request rectangle coverage using native allocation and UE5.8 float tile-range formula; not a GPU-produced-page counter"));
        TArray<TSharedPtr<FJsonValue>> Layers;
        for (int32 Index = 0; Index < PhysicalSizes.Num(); ++Index)
        {
            auto Layer = MakeShared<FJsonObject>();
            Layer->SetNumberField(TEXT("layer"), Index);
            Layer->SetNumberField(TEXT("physical_texture_size_pixels"), PhysicalSizes[Index]);
            Layer->SetStringField(TEXT("format"), GPixelFormats[Formats[Index]].Name);
            Layers.Add(MakeShared<FJsonValueObject>(Layer));
        }
        Result->SetArrayField(TEXT("physical_layers"), Layers);
        if (!Error.IsEmpty()) Result->SetStringField(TEXT("error"), Error);
        return Result;
    }
};

struct FSceneCompileCounts
{
    int32 Assets = 0, Textures = 0, Meshes = 0, ShaderJobs = 0, ShaderPending = 0;
    bool ShaderCompiling = false, ShaderMapsPending = false;

    bool IsComplete() const
    {
        return Assets == 0 && Textures == 0 && Meshes == 0 && ShaderJobs == 0 &&
            ShaderPending == 0 && !ShaderCompiling && !ShaderMapsPending;
    }

    TSharedRef<FJsonObject> ToJson() const
    {
        auto Result = MakeShared<FJsonObject>();
        Result->SetNumberField(TEXT("assets"), Assets);
        Result->SetNumberField(TEXT("textures"), Textures);
        Result->SetNumberField(TEXT("static_meshes"), Meshes);
        Result->SetNumberField(TEXT("shader_jobs"), ShaderJobs);
        Result->SetNumberField(TEXT("shader_pending_jobs"), ShaderPending);
        Result->SetBoolField(TEXT("shader_compiling"), ShaderCompiling);
        Result->SetBoolField(TEXT("shader_maps_pending_finalization"), ShaderMapsPending);
        return Result;
    }
};

FSceneCompileCounts SceneCompileCounts()
{
    FSceneCompileCounts Result;
#if WITH_EDITOR
    Result.Assets = FAssetCompilingManager::Get().GetNumRemainingAssets();
    Result.Textures = FTextureCompilingManager::Get().GetNumRemainingTextures();
    Result.Meshes = FStaticMeshCompilingManager::Get().GetNumRemainingMeshes();
    if (GShaderCompilingManager)
    {
        Result.ShaderJobs = GShaderCompilingManager->GetNumRemainingJobs();
        Result.ShaderPending = GShaderCompilingManager->GetNumPendingJobs();
        Result.ShaderCompiling = GShaderCompilingManager->IsCompiling();
        Result.ShaderMapsPending = GShaderCompilingManager->HasShaderJobs();
    }
#endif
    return Result;
}

TArray<TSharedPtr<FJsonValue>> SceneViewPosition(const FVector& Position)
{
    return {MakeShared<FJsonValueNumber>(Position.X), MakeShared<FJsonValueNumber>(Position.Y),
            MakeShared<FJsonValueNumber>(Position.Z)};
}
}

bool AImportLabGameMode::UpdateSceneRenderingReadiness()
{
    if (!StageMap.bSavedScene || bSceneRenderReady) return true;
    check(IsInGameThread());

    if (!SceneRenderReport.IsValid())
    {
        SceneRenderStartSeconds = FPlatformTime::Seconds();
        SceneRenderReport = MakeShared<FJsonObject>();
        SceneRenderReport->SetStringField(TEXT("schema"), TEXT("rm_scene_render_readiness_v1"));
        SceneRenderReport->SetStringField(TEXT("status"), TEXT("waiting"));
        SceneRenderReport->SetStringField(TEXT("started_utc"), FDateTime::UtcNow().ToIso8601());
        SceneRenderReport->SetNumberField(TEXT("process_id"), FPlatformProcess::GetCurrentProcessId());
        SceneRenderReport->SetNumberField(TEXT("timeout_seconds"), SceneRenderTimeoutSeconds);
        SceneRenderReport->SetNumberField(TEXT("streaming_wait_budget_seconds"), SceneStreamingWaitSeconds);
        SceneRenderReport->SetNumberField(TEXT("required_completed_render_frames"), RequiredCompletedFrames);
        SceneRenderReport->SetNumberField(TEXT("last_progress_log_seconds"), -10.0);
        SceneRenderReport->SetNumberField(TEXT("streaming_full_updates"), 0);
        SceneRenderReport->SetNumberField(TEXT("last_completed_render_frame"), -1);
        SceneRenderReport->SetBoolField(TEXT("ready"), false);
        SceneRenderReport->SetBoolField(TEXT("compilation_complete"), false);
        SceneRenderReport->SetBoolField(TEXT("blocking_finish_all_compilation_used"), false);
        SceneRenderReport->SetBoolField(TEXT("editor_compilation_counters_available"), WITH_EDITOR != 0);
        SceneRenderReport->SetBoolField(TEXT("streaming_id_change_required"), false);
        SceneRenderReport->SetStringField(TEXT("streaming_freshness_evidence"),
            TEXT("UE5.8 StreamAllResources performs UpdateResourceStreaming(0,true); legacy wanting-resource IDs are recorded and may remain unchanged"));
        SceneRenderReport->SetObjectField(TEXT("compilation_before"), SceneCompileCounts().ToJson());
        auto VT = MakeShared<FJsonObject>();
        VT->SetBoolField(TEXT("required"), StageMap.Id == TEXT("wt_vlaanderen"));
        VT->SetBoolField(TEXT("complete"), false);
        VT->SetNumberField(TEXT("sync_attempts"), 0);
        VT->SetStringField(TEXT("status"), TEXT("waiting_for_assets_and_near_view_streaming"));
        SceneRenderReport->SetObjectField(TEXT("runtime_virtual_texture"), VT);
        if (ControlReport.IsValid()) ControlReport->SetObjectField(TEXT("scene_render_readiness"), SceneRenderReport);
        if (ProbeReport.IsValid()) ProbeReport->SetObjectField(TEXT("scene_render_readiness"), SceneRenderReport);
        UE_LOG(LogRMSim, Display, TEXT("SCENE_RENDER_INITIALIZING map=%s timeout=300s"), *StageMap.Id);
    }

    if (!FApp::CanEverRender() || GUsingNullRHI)
    {
        SceneRenderReport->SetStringField(TEXT("status"), TEXT("skipped_no_rendering"));
        SceneRenderReport->SetBoolField(TEXT("rendering_enabled"), false);
        SceneRenderReport->SetBoolField(TEXT("ready"), true);
        SceneRenderReport->GetObjectField(TEXT("runtime_virtual_texture"))->SetStringField(
            TEXT("status"), TEXT("skipped_no_rendering"));
        bSceneRenderReady = true;
        return true;
    }
    SceneRenderReport->SetBoolField(TEXT("rendering_enabled"), true);

    auto CheckDeadline = [&]()
    {
        const double Elapsed = FPlatformTime::Seconds() - SceneRenderStartSeconds;
        SceneRenderReport->SetNumberField(TEXT("elapsed_seconds"), Elapsed);
        if (Elapsed <= SceneRenderTimeoutSeconds) return true;
        SceneRenderReport->SetStringField(TEXT("status"), TEXT("failed"));
        SceneRenderReport->SetStringField(TEXT("error"), TEXT("scene rendering readiness exceeded 300 seconds"));
        SceneRenderReport->SetStringField(TEXT("finished_utc"), FDateTime::UtcNow().ToIso8601());
        Abort(TEXT("scene rendering readiness exceeded 300 seconds; see scene_render_readiness counters"));
        return false;
    };
    if (!CheckDeadline()) return false;

    const double ProcessStarted = FPlatformTime::Seconds();
#if WITH_EDITOR
    // Apply completed jobs with the engine's per-frame time budgets. This keeps
    // the world responsive and lets the wall-clock deadline remain enforceable.
    FAssetCompilingManager::Get().ProcessAsyncTasks(true);
    if (GShaderCompilingManager) GShaderCompilingManager->ProcessAsyncResults(true, false);
#endif
    SceneRenderReport->SetNumberField(TEXT("last_compilation_process_seconds"),
        FPlatformTime::Seconds() - ProcessStarted);
    FSceneCompileCounts Counts = SceneCompileCounts();
    SceneRenderReport->SetObjectField(TEXT("compilation_current"), Counts.ToJson());
    if (!CheckDeadline()) return false;

    auto Waiting = [&](const TCHAR* Phase)
    {
        SceneRenderReport->SetStringField(TEXT("phase"), Phase);
        SceneRenderReport->SetNumberField(TEXT("completed_render_frames"), SceneRenderStableFrames);
        const double Elapsed = FPlatformTime::Seconds() - SceneRenderStartSeconds;
        if (Elapsed - SceneRenderReport->GetNumberField(TEXT("last_progress_log_seconds")) >= 5.0)
        {
            SceneRenderReport->SetNumberField(TEXT("last_progress_log_seconds"), Elapsed);
            UE_LOG(LogRMSim, Display, TEXT("SCENE_RENDER_WAIT phase=%s assets=%d textures=%d meshes=%d shader_jobs=%d shader_pending=%d compiling=%s elapsed=%.3fs"),
                Phase, Counts.Assets, Counts.Textures, Counts.Meshes, Counts.ShaderJobs,
                Counts.ShaderPending, Counts.ShaderCompiling ? TEXT("true") : TEXT("false"), Elapsed);
        }
        return false;
    };
    auto ResetStableFrames = [&]()
    {
        SceneRenderStableFrames = 0;
        SceneRenderReport->SetNumberField(TEXT("last_completed_render_frame"), -1);
    };
    const TSharedPtr<FJsonObject> VTReport = SceneRenderReport->GetObjectField(TEXT("runtime_virtual_texture"));
    auto ResetVTReadiness = [&]()
    {
        if (VTReport->GetBoolField(TEXT("required")))
        {
            VTReport->SetBoolField(TEXT("complete"), false);
            VTReport->SetStringField(TEXT("status"), TEXT("waiting_for_assets_and_near_view_streaming"));
        }
    };

    if (!Counts.IsComplete())
    {
        ResetStableFrames();
        ResetVTReadiness();
        SceneStreamingStartId = INDEX_NONE;
        SceneRenderReport->SetBoolField(TEXT("compilation_complete"), false);
        return Waiting(TEXT("asset_compilation"));
    }

    auto& Streaming = IStreamingManager::Get();
    if (!SceneRenderReport->GetBoolField(TEXT("compilation_complete")))
    {
        const double FlushStarted = FPlatformTime::Seconds();
        FlushRenderingCommands();
        SceneRenderReport->SetNumberField(TEXT("compilation_flush_seconds"), FPlatformTime::Seconds() - FlushStarted);
        Counts = SceneCompileCounts();
        SceneRenderReport->SetObjectField(TEXT("compilation_after"), Counts.ToJson());
        if (!Counts.IsComplete()) return Waiting(TEXT("asset_compilation"));
        SceneRenderReport->SetBoolField(TEXT("compilation_complete"), true);
        SceneStreamingStartId = Streaming.GetNumWantingResourcesID();
        SceneRenderReport->SetNumberField(TEXT("streaming_id_before"), SceneStreamingStartId);
        UE_LOG(LogRMSim, Display, TEXT("SCENE_RENDER_COMPILED assets=0 textures=0 meshes=0 shaders=0 elapsed=%.3fs"),
            FPlatformTime::Seconds() - SceneRenderStartSeconds);
    }
    if (!CheckDeadline()) return false;

    TArray<TSharedPtr<FJsonValue>> Views;
    auto AddView = [&](const TCHAR* Label, const FVector& Location, float Width, float FovDegrees)
    {
        if (Location.ContainsNaN() || Width <= 0 || !FMath::IsFinite(FovDegrees) || FovDegrees <= 0 || FovDegrees >= 180) return;
        const float FovScreenSize = Width / FMath::Tan(FMath::DegreesToRadians(FovDegrees * 0.5f));
        Streaming.AddViewInformation(Location, Width, FovScreenSize, 1.0f, false, 0.0f, Rig, GetWorld());
        auto Entry = MakeShared<FJsonObject>();
        Entry->SetStringField(TEXT("view"), Label);
        Entry->SetArrayField(TEXT("location_cm"), SceneViewPosition(Location));
        Entry->SetNumberField(TEXT("width_pixels"), Width);
        Entry->SetNumberField(TEXT("fov_degrees"), FovDegrees);
        Entry->SetNumberField(TEXT("fov_screen_size"), FovScreenSize);
        Views.Add(MakeShared<FJsonValueObject>(Entry));
    };
    if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
    {
        int32 Width = 0, Height = 0;
        PC->GetViewportSize(Width, Height);
        FVector Location; FRotator Rotation;
        PC->GetPlayerViewPoint(Location, Rotation);
        if (PC->PlayerCameraManager) AddView(TEXT("player"), Location, Width, PC->PlayerCameraManager->GetFOVAngle());
    }
    auto AddCapture = [&](const TCHAR* Label, USceneCaptureComponent2D* Capture)
    {
        if (Capture && Capture->bCaptureEveryFrame && Capture->TextureTarget)
            AddView(Label, Capture->GetComponentLocation(), Capture->TextureTarget->SizeX, Capture->FOVAngle);
    };
    AddCapture(TEXT("overview"), OverviewCapture);
    if (Rig) for (const auto& Camera : Rig->GetCameras()) AddCapture(*Camera.Key, Camera.Value);
    SceneRenderReport->SetArrayField(TEXT("submitted_views"), Views);
    SceneRenderReport->SetBoolField(TEXT("streaming_enabled"), Streaming.IsStreamingEnabled());
    SceneRenderReport->SetBoolField(TEXT("texture_streaming_enabled"), Streaming.IsTextureStreamingEnabled());
    if (Views.IsEmpty() || !Streaming.IsStreamingEnabled())
    {
        ResetStableFrames();
        ResetVTReadiness();
        return Waiting(TEXT("streaming_views"));
    }

    // UE5.8's complete update evaluates these views synchronously. The legacy
    // wanting-resource ID is not incremented by FRenderAssetStreamingManager.
    const double StreamStarted = FPlatformTime::Seconds();
    const int32 Pending = Streaming.StreamAllResources(SceneStreamingWaitSeconds);
    const double StreamElapsed = FPlatformTime::Seconds() - StreamStarted;
    const int32 Wanted = Streaming.GetNumWantingResources();
    const int32 StreamId = Streaming.GetNumWantingResourcesID();
    SceneRenderReport->SetNumberField(TEXT("streaming_full_updates"),
        SceneRenderReport->GetNumberField(TEXT("streaming_full_updates")) + 1);
    SceneRenderReport->SetNumberField(TEXT("last_streaming_update_seconds"), StreamElapsed);
    SceneRenderReport->SetNumberField(TEXT("streaming_pending_requests"), Pending);
    SceneRenderReport->SetNumberField(TEXT("streaming_wanted_resources"), Wanted);
    SceneRenderReport->SetNumberField(TEXT("streaming_id_after"), StreamId);
    SceneRenderReport->SetNumberField(TEXT("streaming_active_views"), Streaming.GetNumViews());
    SceneRenderReport->SetBoolField(TEXT("fresh_synchronous_streaming_evaluation"), true);
    Counts = SceneCompileCounts();
    SceneRenderReport->SetObjectField(TEXT("compilation_current"), Counts.ToJson());
    if (!CheckDeadline()) return false;
    if (!Counts.IsComplete())
    {
        ResetStableFrames();
        ResetVTReadiness();
        SceneRenderReport->SetBoolField(TEXT("compilation_complete"), false);
        return Waiting(TEXT("asset_compilation"));
    }
    if (Pending != 0 || Wanted != 0)
    {
        ResetStableFrames();
        ResetVTReadiness();
        return Waiting(TEXT("near_view_streaming"));
    }

    if (!VTReport->GetBoolField(TEXT("required")))
    {
        VTReport->SetBoolField(TEXT("complete"), true);
        VTReport->SetStringField(TEXT("status"), TEXT("not_required_for_map"));
    }
    else if (!VTReport->GetBoolField(TEXT("complete")))
    {
        auto FailVT = [&](const FString& Error)
        {
            VTReport->SetStringField(TEXT("status"), TEXT("failed"));
            VTReport->SetStringField(TEXT("error"), Error);
            SceneRenderReport->SetStringField(TEXT("status"), TEXT("failed"));
            SceneRenderReport->SetStringField(TEXT("error"), Error);
            SceneRenderReport->SetStringField(TEXT("finished_utc"), FDateTime::UtcNow().ToIso8601());
            Abort(Error);
            return false;
        };
        URuntimeVirtualTextureComponent* TargetComponent = nullptr;
        int32 MatchingVolumes = 0;
        for (TActorIterator<ARuntimeVirtualTextureVolume> It(GetWorld()); It; ++It)
        {
            auto* Component = It->FindComponentByClass<URuntimeVirtualTextureComponent>();
            if (Component && Component->GetVirtualTexture() &&
                Component->GetVirtualTexture()->GetPathName() == GroundDecalRVTPath)
            {
                ++MatchingVolumes;
                TargetComponent = Component;
            }
        }
        VTReport->SetNumberField(TEXT("matching_volume_count"), MatchingVolumes);
        if (MatchingVolumes != 1 || !Rig)
            return FailVT(TEXT("ground decal RVT readiness requires one source volume and the placed vehicle"));
        if (!TargetComponent->IsRegistered() || !TargetComponent->IsEnabledInScene())
            return FailVT(TEXT("ground decal RVT component is not registered/enabled in this rendering scene"));

        URuntimeVirtualTexture* VirtualTexture = TargetComponent->GetVirtualTexture();
        const FTransform VolumeTransform = TargetComponent->GetComponentTransform();
        const FVector VolumeScale = VolumeTransform.GetScale3D();
        if (!VolumeTransform.GetRotation().IsIdentity(1e-6) || VolumeScale.GetMin() <= 0)
            return FailVT(TEXT("ground decal RVT volume must have positive scale and identity rotation"));
        const FBox VolumeBox = FBox(FVector::ZeroVector, FVector::OneVector).TransformBy(VolumeTransform);
        const FVector SpawnPosition = Rig->GetActorLocation();
        if (SpawnPosition.ContainsNaN()) return FailVT(TEXT("ground decal RVT preload has invalid spawn coordinates"));
        const FVector RegionMin(SpawnPosition.X-GroundDecalPreloadRadiusCm,
            SpawnPosition.Y-GroundDecalPreloadRadiusCm, SpawnPosition.Z);
        const FVector RegionMax(SpawnPosition.X+GroundDecalPreloadRadiusCm,
            SpawnPosition.Y+GroundDecalPreloadRadiusCm, SpawnPosition.Z);
        const FVector LocalMin = VolumeTransform.InverseTransformPosition(RegionMin);
        const FVector LocalMax = VolumeTransform.InverseTransformPosition(RegionMax);
        if (LocalMin.X < 0 || LocalMin.Y < 0 || LocalMax.X > 1 || LocalMax.Y > 1)
            return FailVT(TEXT("ground decal RVT near-spawn rectangle lies outside the source projection volume"));
        const FVector2D UV0(LocalMin.X, LocalMin.Y), UV1(LocalMax.X, LocalMax.Y);
        const ERHIFeatureLevel::Type FeatureLevel = GetWorld()->GetFeatureLevel();
        VTReport->SetStringField(TEXT("status"), TEXT("synchronizing_near_spawn_pages"));
        VTReport->SetStringField(TEXT("asset"), VirtualTexture->GetPathName());
        VTReport->SetStringField(TEXT("component"), TargetComponent->GetPathName());
        VTReport->SetArrayField(TEXT("spawn_world_cm"), SceneViewPosition(SpawnPosition));
        VTReport->SetArrayField(TEXT("requested_world_min_cm"), SceneViewPosition(RegionMin));
        VTReport->SetArrayField(TEXT("requested_world_max_cm"), SceneViewPosition(RegionMax));
        VTReport->SetArrayField(TEXT("volume_world_min_cm"), SceneViewPosition(VolumeBox.Min));
        VTReport->SetArrayField(TEXT("volume_world_max_cm"), SceneViewPosition(VolumeBox.Max));
        VTReport->SetArrayField(TEXT("volume_scale_cm"), SceneViewPosition(VolumeScale));
        VTReport->SetNumberField(TEXT("requested_uv_min_x"), UV0.X);
        VTReport->SetNumberField(TEXT("requested_uv_min_y"), UV0.Y);
        VTReport->SetNumberField(TEXT("requested_uv_max_x"), UV1.X);
        VTReport->SetNumberField(TEXT("requested_uv_max_y"), UV1.Y);
        VTReport->SetNumberField(TEXT("requested_mip"), 0);
        VTReport->SetNumberField(TEXT("feature_level"), static_cast<int32>(FeatureLevel));
        VTReport->SetNumberField(TEXT("max_preload_tiles"), MaxGroundDecalPreloadTiles);
        VTReport->SetNumberField(TEXT("sync_attempts"), VTReport->GetNumberField(TEXT("sync_attempts"))+1);
        VTReport->SetStringField(TEXT("preload_api"),
            TEXT("IRendererModule.RequestVirtualTextureTiles(region,mip0); avoids component sphere/aspect expansion"));
        SceneRenderReport->SetStringField(TEXT("phase"), TEXT("runtime_virtual_texture_preload"));
        IRendererModule* Renderer = &GetRendererModule();
        FSceneVTReadback Readback;
        const double VTStarted = FPlatformTime::Seconds();
        ENQUEUE_RENDER_COMMAND(RMSimPreloadGroundDecalRVT)(
            [VirtualTexture, Renderer, FeatureLevel, UV0, UV1, &Readback](FRHICommandListImmediate& RHICmdList)
            {
                check(IsInRenderingThread());
                Readback.bRenderThreadExecuted = true;
                IAllocatedVirtualTexture* Allocated = VirtualTexture->GetAllocatedVirtualTexture();
                if (!Allocated) return;
                Readback.bAllocated = true;
                Readback.Width = Allocated->GetWidthInPixels();
                Readback.Height = Allocated->GetHeightInPixels();
                Readback.WidthTiles = Allocated->GetWidthInTiles();
                Readback.HeightTiles = Allocated->GetHeightInTiles();
                Readback.VirtualTileSize = Allocated->GetVirtualTileSize();
                Readback.PhysicalTileSize = Allocated->GetPhysicalTileSize();
                Readback.BorderSize = Allocated->GetTileBorderSize();
                Readback.SpaceId = Allocated->GetSpaceID();
                if (Allocated->GetWidthInBlocks() != 1 || Allocated->GetHeightInBlocks() != 1 ||
                    Readback.SpaceId >= VIRTUALTEXTURE_MAX_FEEDBACK_SPACES ||
                    Readback.WidthTiles == 0 || Readback.HeightTiles == 0)
                {
                    Readback.Error = TEXT("invalid source RVT allocation for bounded page requests");
                    return;
                }
                // Match RequestTilesForRegionInternal's float UV conversion and
                // floor/ceil range exactly before submitting this bounded job.
                Readback.RequestMin = FIntPoint(
                    FMath::FloorToInt(static_cast<float>(UV0.X)*Readback.WidthTiles),
                    FMath::FloorToInt(static_cast<float>(UV0.Y)*Readback.HeightTiles));
                Readback.RequestMax = FIntPoint(
                    FMath::CeilToInt(static_cast<float>(UV1.X)*Readback.WidthTiles),
                    FMath::CeilToInt(static_cast<float>(UV1.Y)*Readback.HeightTiles));
                Readback.RequestedTiles = (Readback.RequestMax.X-Readback.RequestMin.X)*
                    (Readback.RequestMax.Y-Readback.RequestMin.Y);
                if (Readback.RequestedTiles <= 0 || Readback.RequestedTiles > MaxGroundDecalPreloadTiles)
                {
                    Readback.Error = TEXT("source RVT near-spawn request exceeds the bounded tile budget");
                    return;
                }
                const double SyncStarted = FPlatformTime::Seconds();
                // Refresh this region if new source texture mips arrived during
                // readiness; other regions and other processes remain untouched.
                Renderer->FlushVirtualTextureCache(Allocated, FVector2f(UV0), FVector2f(UV1));
                Renderer->RequestVirtualTextureTiles(Allocated, FVector2D::One(), FVector2D::Zero(),
                    FVector2D::One(), UV0, UV1, 0);
                Renderer->SyncVirtualTextureUpdates(RHICmdList, FeatureLevel);
                Readback.SyncSeconds = FPlatformTime::Seconds()-SyncStarted;
                Readback.bSyncReturned = true;
                Allocated = VirtualTexture->GetAllocatedVirtualTexture();
                if (!Allocated || Allocated->GetNumTextureLayers() != 3)
                {
                    Readback.Error = TEXT("source RVT allocation/layers missing after synchronous update");
                    return;
                }
                for (uint32 Layer = 0; Layer < Allocated->GetNumTextureLayers(); ++Layer)
                {
                    const uint32 Size = Allocated->GetPhysicalTextureSize(Layer);
                    FRHITexture* Texture = Allocated->GetPhysicalTexture(Layer);
                    if (!Texture || Size == 0)
                    {
                        Readback.Error = TEXT("source RVT physical layer was not allocated after synchronous update");
                        return;
                    }
                    const EPixelFormat Format = Texture->GetFormat();
                    const EPixelFormat Expected = Layer == 1 ? PF_BC5 : PF_DXT5;
                    if (Format != Expected)
                    {
                        Readback.Error = TEXT("source RVT physical layer format differs from the Mask YCoCg contract");
                        return;
                    }
                    Readback.PhysicalSizes.Add(Size);
                    Readback.Formats.Add(Format);
                    const auto& Info = GPixelFormats[Format];
                    Readback.PhysicalPageBytes += static_cast<uint64>(
                        FMath::DivideAndRoundUp(Readback.PhysicalTileSize, static_cast<uint32>(Info.BlockSizeX))) *
                        FMath::DivideAndRoundUp(Readback.PhysicalTileSize, static_cast<uint32>(Info.BlockSizeY)) * Info.BlockBytes;
                    Readback.PhysicalTextureBytes += static_cast<uint64>(
                        FMath::DivideAndRoundUp(Size, static_cast<uint32>(Info.BlockSizeX))) *
                        FMath::DivideAndRoundUp(Size, static_cast<uint32>(Info.BlockSizeY)) * Info.BlockBytes;
                }
            });
        // The render-thread readback is stack-owned and is consumed only after
        // this command/RHI synchronization has completed.
        FlushRenderingCommands();
        VTReport->SetNumberField(TEXT("preload_and_flush_seconds"), FPlatformTime::Seconds()-VTStarted);
        VTReport->SetObjectField(TEXT("native_allocation"), Readback.ToJson());
        if (!CheckDeadline()) return false;
        if (!Readback.Error.IsEmpty()) return FailVT(Readback.Error);
        if (!Readback.bAllocated || !Readback.bSyncReturned)
        {
            ResetStableFrames();
            return Waiting(TEXT("runtime_virtual_texture_allocation"));
        }
        VTReport->SetBoolField(TEXT("complete"), true);
        VTReport->SetStringField(TEXT("status"), TEXT("synchronized"));
        VTReport->SetNumberField(TEXT("synchronized_render_frame"), static_cast<double>(GFrameCounterRenderThread));
        ResetStableFrames();
        UE_LOG(LogRMSim, Display, TEXT("SCENE_RVT_READY requested_top_mip_tiles=%d virtual=%ux%u tile=%u bordered_tile=%u layers=%d physical_bytes=%llu sync=%.3fs elapsed=%.3fs"),
            Readback.RequestedTiles, Readback.Width, Readback.Height, Readback.VirtualTileSize,
            Readback.PhysicalTileSize, Readback.PhysicalSizes.Num(), Readback.PhysicalTextureBytes,
            Readback.SyncSeconds, FPlatformTime::Seconds()-SceneRenderStartSeconds);
    }

    // A flush completes prior render/RHI submissions. Read the render-thread
    // frame counter only after this synchronization, then require two distinct
    // completed frames after streaming and the explicit near-spawn RVT sync.
    FlushRenderingCommands();
    const double CompletedFrame = static_cast<double>(GFrameCounterRenderThread);
    const double LastCompletedFrame = SceneRenderReport->GetNumberField(TEXT("last_completed_render_frame"));
    if (LastCompletedFrame < 0)
        SceneRenderReport->SetNumberField(TEXT("ready_view_render_frame_start"), CompletedFrame);
    else if (CompletedFrame > LastCompletedFrame)
        ++SceneRenderStableFrames;
    SceneRenderReport->SetNumberField(TEXT("last_completed_render_frame"), CompletedFrame);
    SceneRenderReport->SetNumberField(TEXT("completed_render_frames"), SceneRenderStableFrames);
    if (!CheckDeadline()) return false;
    if (SceneRenderStableFrames < RequiredCompletedFrames) return Waiting(TEXT("completed_render_frames"));

    bSceneRenderReady = true;
    SceneRenderReport->SetStringField(TEXT("status"), TEXT("passed"));
    SceneRenderReport->SetStringField(TEXT("phase"), TEXT("ready"));
    SceneRenderReport->SetStringField(TEXT("finished_utc"), FDateTime::UtcNow().ToIso8601());
    SceneRenderReport->SetBoolField(TEXT("ready"), true);
    SceneRenderReport->SetObjectField(TEXT("compilation_after"), Counts.ToJson());
    UE_LOG(LogRMSim, Display, TEXT("SCENE_RENDER_READY assets=0 textures=0 meshes=0 shaders=0 pending=%d wanted=%d streaming_id=%d->%d completed_frames=%d elapsed=%.3fs"),
        Pending, Wanted, SceneStreamingStartId, StreamId, SceneRenderStableFrames,
        FPlatformTime::Seconds() - SceneRenderStartSeconds);
    return true;
}
