#include "ImportLabGameMode.h"
#include "ArenaShell.h"
#include "CampusGym.h"
#include "MapOverview.h"
#include "TrainingRange.h"
#include "VehicleRig.h"
#include "RosBridgeComponent.h"
#include "RMSimViewportClient.h"
#include "WTFoliageCrushComponent.h"
#include "WTGeneratedCollisionComponent.h"
#include "RosCoordinates.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Engine/RectLight.h"
#include "Components/RectLightComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/SkyLightComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/TextureCube.h"
#include "Engine/World.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "UnrealClient.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "PhysicsEngine/BodyInstance.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/PlayerInput.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProperties.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformProcess.h"
#if PLATFORM_WINDOWS
#include "Timing/WindowsDeadlineWait.h"
#endif
#include "Misc/CoreDelegates.h"
#include "ImageUtils.h"
#include "ImageCore.h"
#include "InputCoreTypes.h"
#include "Misc/CommandLine.h"
#include "Misc/App.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "RenderingThread.h"
#include "Serialization/BufferArchive.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/EngineVersion.h"
#include "Misc/Guid.h"

namespace
{
TArray<TSharedPtr<FJsonValue>> VectorJson(const FVector& Value)
{
    return {MakeShared<FJsonValueNumber>(Value.X), MakeShared<FJsonValueNumber>(Value.Y), MakeShared<FJsonValueNumber>(Value.Z)};
}
TSharedRef<FJsonObject> TransformJson(const FTransform& Value)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    const FQuat Rotation = Value.GetRotation();
    Result->SetArrayField(TEXT("position_cm"), VectorJson(Value.GetLocation()));
    Result->SetArrayField(TEXT("rotation_xyzw"), {MakeShared<FJsonValueNumber>(Rotation.X), MakeShared<FJsonValueNumber>(Rotation.Y),
        MakeShared<FJsonValueNumber>(Rotation.Z), MakeShared<FJsonValueNumber>(Rotation.W)});
    return Result;
}
TSharedRef<FJsonObject> StateJson(const FManualRigState& State)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetObjectField(TEXT("actor"), TransformJson(State.Actor));
    Result->SetArrayField(TEXT("world_bounds_min_cm"), VectorJson(State.WorldBounds.Min));
    Result->SetArrayField(TEXT("world_bounds_max_cm"), VectorJson(State.WorldBounds.Max));
    Result->SetStringField(TEXT("world_bounds_semantics"), TEXT("collision_query_box_world_aabb"));
    Result->SetArrayField(TEXT("collision_bounds_min_cm"), VectorJson(State.WorldBounds.Min));
    Result->SetArrayField(TEXT("collision_bounds_max_cm"), VectorJson(State.WorldBounds.Max));
    Result->SetArrayField(TEXT("collision_box_local_center_cm"), VectorJson(State.LocalCollisionBounds.GetCenter()));
    Result->SetArrayField(TEXT("collision_box_local_extent_cm"), VectorJson(State.LocalCollisionBounds.GetExtent()));
    Result->SetArrayField(TEXT("visual_bounds_min_cm"), VectorJson(State.VisualWorldBounds.Min));
    Result->SetArrayField(TEXT("visual_bounds_max_cm"), VectorJson(State.VisualWorldBounds.Max));
    Result->SetBoolField(TEXT("blocking_overlap"), State.bBlockingOverlap);
    TSharedRef<FJsonObject> Sweep = MakeShared<FJsonObject>();
    Sweep->SetBoolField(TEXT("blocking_hit"), State.bLastSweepHit);
    Sweep->SetStringField(TEXT("actor"), State.LastBlockingActor);
    Sweep->SetArrayField(TEXT("impact_point_cm"), VectorJson(State.LastImpactPoint));
    Sweep->SetNumberField(TEXT("fraction"), State.LastSweepFraction);
    Result->SetObjectField(TEXT("last_move_sweep"), Sweep);
    TSharedRef<FJsonObject> Joints = MakeShared<FJsonObject>(), Cameras = MakeShared<FJsonObject>();
    for (const auto& Joint : State.JointLocal) Joints->SetObjectField(Joint.Key, TransformJson(Joint.Value));
    for (const auto& Camera : State.CameraWorld) Cameras->SetObjectField(Camera.Key, TransformJson(Camera.Value));
    Result->SetObjectField(TEXT("joint_local_transforms"), Joints);
    Result->SetObjectField(TEXT("camera_world_transforms"), Cameras);
    return Result;
}
bool WheelsUnchanged(const FManualRigState& Before, const FManualRigState& After)
{
    if (Before.WheelNames.IsEmpty()) return false;
    for (const FString& Name : Before.WheelNames)
    {
        const FTransform* A = Before.JointLocal.Find(Name);
        const FTransform* B = After.JointLocal.Find(Name);
        if (!A || !B || !A->Equals(*B, 1.e-5)) return false;
    }
    return true;
}
}

AImportLabGameMode::AImportLabGameMode()
{
    DefaultPawnClass = nullptr;
    HUDClass = AImportLabHUD::StaticClass();
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PostPhysics; // Render cameras and evidence observe the completed physical pose.
}

void AImportLabGameMode::Abort(const FString& Reason)
{
    UE_LOG(LogRMSim, Error, TEXT("IMPORT_LAB_FAILED %s"), *Reason);
    if (bProbeActive) WriteProbeReport(TEXT("failed"), Reason);
    if (bControlProbeActive) WriteControlReport(TEXT("failed"), Reason);
    if (Rig) Rig->Destroy();
    bReady = false;
    // Probe JSON was synchronously saved above. Windows graceful engine exit can discard WM_QUIT's code;
    // force this disposable probe process to preserve the failure status (the engine flushes its log).
    FPlatformMisc::RequestExitWithStatus(bProbeActive || bControlProbeActive, 2);
}

void AImportLabGameMode::BeginPlay()
{
    Super::BeginPlay();
    bMapOverview = FParse::Param(FCommandLine::Get(), TEXT("MapOverview"));
    GConfig->GetDouble(TEXT("RMSim.ManualControl"), TEXT("MouseSensitivity"), MouseSensitivity, GGameIni);
    GConfig->GetDouble(TEXT("RMSim.ManualControl"), TEXT("GimbalYawSpeedDeg"), GimbalYawSpeedDeg, GGameIni);
    GConfig->GetDouble(TEXT("RMSim.ManualControl"), TEXT("GimbalPitchSpeedDeg"), GimbalPitchSpeedDeg, GGameIni);
    if (!FMath::IsFinite(MouseSensitivity) || MouseSensitivity <= 0.0 || MouseSensitivity > 1.0 ||
        !FMath::IsFinite(GimbalYawSpeedDeg) || GimbalYawSpeedDeg <= 0.0 ||
        !FMath::IsFinite(GimbalPitchSpeedDeg) || GimbalPitchSpeedDeg <= 0.0)
    { Abort(TEXT("Invalid manual-control sensitivity or gimbal speed config")); return; }
    bTerrainProbeActive = FParse::Param(FCommandLine::Get(), TEXT("TerrainProbe"));
    bGyroProbeActive=FParse::Param(FCommandLine::Get(),TEXT("GyroProbe"));
    bTractionProbeActive=FParse::Param(FCommandLine::Get(),TEXT("TractionProbe"));
    bContactProbeActive = bTractionProbeActive||bGyroProbeActive||FParse::Param(FCommandLine::Get(), TEXT("ContactProbe"));
    bRosAimControl=FParse::Param(FCommandLine::Get(),TEXT("RosAimControl"));
    bRosControl=FParse::Param(FCommandLine::Get(),TEXT("RosControl"))||FParse::Param(FCommandLine::Get(),TEXT("RosMotorControl"));
    FString PhysicsBackend=TEXT("chaos");
    FParse::Value(FCommandLine::Get(),TEXT("PhysicsBackend="),PhysicsBackend);
    if(PhysicsBackend!=TEXT("chaos")&&PhysicsBackend!=TEXT("mujoco"))
    {Abort(TEXT("PhysicsBackend must be chaos or mujoco"));return;}
    const bool MujocoRequested=PhysicsBackend==TEXT("mujoco");
    double FixedStep=MujocoRequested?(FParse::Param(FCommandLine::Get(),TEXT("RosMotorControl"))?.001:.005):0;
    FParse::Value(FCommandLine::Get(),TEXT("SimStepSeconds="),FixedStep);
    if(MujocoRequested&&(FixedStep<=0||FixedStep>.005))
    {Abort(TEXT("MuJoCo requires a fixed engine step of 0.0005..0.005 seconds"));return;}
    if(!FMath::IsFinite(FixedStep)||FixedStep<0||(FixedStep>0&&(FixedStep<.0005||FixedStep>.02)))
    {Abort(TEXT("SimStepSeconds must be0 or0.0005..0.02"));return;}
    if(FParse::Param(FCommandLine::Get(),TEXT("RosMotorControl")))
    {
        if(FixedStep>0&&!FMath::IsNearlyEqual(FixedStep,.001,1.e-9)){Abort(TEXT("original LK motor mode requires1ms physics"));return;}
        FixedStep=.001;
    }
    const bool PaceRequested=FParse::Param(FCommandLine::Get(),TEXT("SimRealTimePace"));
    const bool Dedicated=MujocoRequested&&PaceRequested&&!bContactProbeActive&&
        !FParse::Param(FCommandLine::Get(),TEXT("RosMotorControl"))&&
        !FParse::Param(FCommandLine::Get(),TEXT("MujocoSynchronous"));
    if(Dedicated)
    {
        DedicatedMujocoStep=FixedStep;
        FApp::SetUseFixedTimeStep(false);
        double RequestedHz=0;
        FParse::Value(FCommandLine::Get(),TEXT("RMSimViewportHz="),RequestedHz);
        if(GEngine&&FMath::IsFinite(RequestedHz)&&RequestedHz>=10&&RequestedHz<=144)
            GEngine->SetMaxFPS(float(RequestedHz));
        UE_LOG(LogRMSim,Display,TEXT("SIM_REALTIME_PACE fixed_step_s=%.6f mode=dedicated_native_solver"),FixedStep);
    }
    else if(FixedStep>0)
    {
        FApp::SetFixedDeltaTime(FixedStep);FApp::SetUseFixedTimeStep(true);
        if(PaceRequested)
        {
            // Fixed-time engine updates bypass the normal MaxFPS sleep path.
            // Pace actual solver ticks against a monotonic absolute deadline.
            // Overruns retain every physical step and remain visible in /clock.
            RealTimeStepSeconds=FixedStep;
            bMujocoRealTimePace=MujocoRequested;
            if(GEngine)GEngine->SetMaxFPS(0);
            RealTimePaceHandle=FCoreDelegates::OnBeginFrame.AddUObject(this,&AImportLabGameMode::PacePhysicsFrame);
            UE_LOG(LogRMSim,Display,TEXT("SIM_REALTIME_PACE fixed_step_s=%.6f mode=monotonic_deadline"),FixedStep);
        }
    }
#if WITH_DEV_AUTOMATION_TESTS
    FParse::Value(FCommandLine::Get(),TEXT("ImuFixture="),ImuFixture);
    if(!ImuFixture.IsEmpty()&&ImuFixture!=TEXT("freefall")&&ImuFixture!=TEXT("incline"))
    {Abort(TEXT("unknown development IMU fixture"));return;}
#endif
    bRosBridgeEnabled=bRosControl||bRosAimControl||FParse::Param(FCommandLine::Get(),TEXT("RosBridge"))||FParse::Param(FCommandLine::Get(),TEXT("RosImu"))||FParse::Param(FCommandLine::Get(),TEXT("RosLidar"));
    bContactMode = MujocoRequested||bRosBridgeEnabled||bContactProbeActive || FParse::Param(FCommandLine::Get(), TEXT("ContactPhysics"));
    bCombatProbeActive = FParse::Param(FCommandLine::Get(), TEXT("CombatProbe"));
    bTrainingMode = bCombatProbeActive || FParse::Param(FCommandLine::Get(), TEXT("TrainingMode"));
    bControlProbeActive = bContactProbeActive || bTerrainProbeActive || bCombatProbeActive || FParse::Param(FCommandLine::Get(), TEXT("ControlProbe"));
    if(bRosControl&&bControlProbeActive){Abort(TEXT("ROS control cannot compete with an internal control probe"));return;}
    if(bContactMode && bControlProbeActive && !bContactProbeActive)
    {Abort(TEXT("ContactPhysics uses ContactProbe with actual world ticks; kinematic probes are separate"));return;}
    if (bControlProbeActive)
    {
        FParse::Value(FCommandLine::Get(), TEXT("EvidenceDir="), ControlEvidenceDirectory);
        ControlReport = MakeShared<FJsonObject>();
        ControlReport->SetStringField(TEXT("run_id"), FGuid::NewGuid().ToString());
        ControlReport->SetStringField(TEXT("started_utc"), FDateTime::UtcNow().ToIso8601());
        ControlReport->SetStringField(TEXT("requested_physics_backend"),PhysicsBackend);
        ControlReport->SetStringField(TEXT("scope"),bContactProbeActive?(MujocoRequested?TEXT("native_MuJoCo_world_ticks_and_wheel_motor_commands"):TEXT("native_Chaos_world_ticks_and_wheel_motor_commands")):
            bTerrainProbeActive ? TEXT("mesh_terrain_shared_controls_not_vehicle_dynamics") : TEXT("shared_manual_input_pipeline_not_OS_input"));
        ControlReport->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());
        ControlReport->SetBoolField(TEXT("os_keyboard_test"), false);
        if (ControlEvidenceDirectory.IsEmpty() || !WriteControlReport(TEXT("running")))
        { Abort(TEXT("ControlProbe requires writable EvidenceDir")); return; }
    }
    FString ProbeName;
    FParse::Value(FCommandLine::Get(), TEXT("ManualProbe="), ProbeName);
    bProbeActive = !ProbeName.IsEmpty();
    if(bRosBridgeEnabled&&(bProbeActive||bControlProbeActive)){Abort(TEXT("ROS bridge uses external integration probes, not internal motion probes"));return;}
    if (bProbeActive && bControlProbeActive) { Abort(TEXT("Choose ManualProbe or ControlProbe, not both")); return; }
    if (bProbeActive)
    {
        FParse::Value(FCommandLine::Get(), TEXT("EvidenceDir="), EvidenceDirectory);
        ProbeReport = MakeShared<FJsonObject>();
        ProbeReport->SetStringField(TEXT("run_id"), FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens));
        ProbeReport->SetStringField(TEXT("started_utc"), FDateTime::UtcNow().ToIso8601());
        ProbeReport->SetStringField(TEXT("scope"), TEXT("kinematic_manual_control_probe"));
        ProbeReport->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());
        ProbeReport->SetStringField(TEXT("platform"), FPlatformProperties::PlatformName());
        ProbeReport->SetBoolField(TEXT("manual_keyboard_verified"), false);
        ProbeReport->SetBoolField(TEXT("visual_review_verified"), false);
        ProbeReport->SetBoolField(TEXT("dynamics_verified"), false);
        if (ProbeName != TEXT("basic") || EvidenceDirectory.IsEmpty())
        {
            Abort(TEXT("ManualProbe supports basic and requires EvidenceDir")); return;
        }
        if (!WriteProbeReport(TEXT("running"))) { Abort(TEXT("cannot create probe evidence file")); return; }
    }
    FString VehicleFile, MeshRigFile;
    FParse::Value(FCommandLine::Get(), TEXT("VehicleFile="), VehicleFile);
    FParse::Value(FCommandLine::Get(), TEXT("MeshRigFile="), MeshRigFile);
    ContactRigFile=MeshRigFile;
    if (ProbeReport.IsValid()) ProbeReport->SetStringField(TEXT("input_file"), MeshRigFile.IsEmpty() ? VehicleFile : MeshRigFile);
    if (VehicleFile.IsEmpty() == MeshRigFile.IsEmpty())
    {
        Abort(TEXT("provide exactly one -VehicleFile=<canonical.json> or -MeshRigFile=<ue_mesh_rig.json>"));
        return;
    }
    Rig = GetWorld()->SpawnActor<AVehicleRig>();
    FString Error;
    if (!Rig || !(MeshRigFile.IsEmpty() ? Rig->LoadCanonical(VehicleFile, Error) : Rig->LoadMeshRig(MeshRigFile, Error)))
    {
        Abort(Error.IsEmpty() ? TEXT("cannot spawn vehicle rig") : Error); return;
    }
    double Yaw = 0.0, Pitch = 0.0;
    FParse::Value(FCommandLine::Get(), TEXT("GimbalYaw="), Yaw);
    FParse::Value(FCommandLine::Get(), TEXT("GimbalPitch="), Pitch);
    if (!FMath::IsFinite(Yaw) || !FMath::IsFinite(Pitch) || !Rig->SetGimbal(Yaw, Pitch, Error))
    {
        Abort(Error.IsEmpty() ? TEXT("nonfinite gimbal angle") : Error); return;
    }
    // Place visual bounds above floor. This is presentation, not a contact solve.
    const FBox Bounds = Rig->GetVisualBounds();
    if (Bounds.IsValid) Rig->AddActorWorldOffset(FVector(0.0, 0.0, 2.0 - Bounds.Min.Z));
    // Stable hull-relative pivot above the roof: turret motion must not move the camera pivot.
    ChasePivotHeightCm = Bounds.IsValid ? Bounds.Max.Z + 40.0 : 120.0;
    if (!CreateStage()) return;
    if (ProbeReport.IsValid()) ProbeReport->SetStringField(TEXT("vehicle_id"), Rig->GetVehicleId());
    FParse::Value(FCommandLine::Get(), TEXT("CaptureDir="), CaptureDirectory);
    if (bProbeActive) CaptureDirectory = EvidenceDirectory;
    FParse::Value(FCommandLine::Get(), TEXT("CaptureDelay="), CaptureDelay);
    if (!FMath::IsFinite(CaptureDelay) || CaptureDelay < 0.1 || CaptureDelay > 300.0)
    {
        Abort(TEXT("CaptureDelay must be 0.1..300 seconds")); return;
    }
    bExitAfterCapture = FParse::Param(FCommandLine::Get(), TEXT("ExitAfterCapture"));
    // Interactive driving renders the player view only. These unused offscreen
    // targets previously rendered two additional views every frame and added latency.
    const bool ContinuousCapture=bProbeActive||bControlProbeActive||!CaptureDirectory.IsEmpty();
    for(const auto& Camera:Rig->GetCameras())Camera.Value->bCaptureEveryFrame=ContinuousCapture;
    if(OverviewCapture)OverviewCapture->bCaptureEveryFrame=ContinuousCapture;
    FParse::Value(FCommandLine::Get(), TEXT("InputEvidence="), InputEvidencePath);
    if (bExitAfterCapture && CaptureDirectory.IsEmpty())
    {
        Abort(TEXT("ExitAfterCapture requires CaptureDir")); return;
    }
    if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
    {
        if (!bProbeActive)
        {
            ChaseCamera = GetWorld()->SpawnActor<ACameraActor>();
            SightCamera = GetWorld()->SpawnActor<ACameraActor>();
            ChaseCamera->GetCameraComponent()->SetFieldOfView(55.0f);
            SightCamera->GetCameraComponent()->SetFieldOfView(30.0f);
            OrbitYaw = Rig->GetActorRotation().Yaw;
            UpdatePlayerView(PC);
        }
        else PC->SetViewTarget(Overview);
        PC->bShowMouseCursor = false;
        PC->SetInputMode(FInputModeGameOnly());
    }
    bReady = true;
    UE_LOG(LogRMSim, Display, TEXT("IMPORT_LAB_INITIALIZING waiting for map collision registration"));
}

bool AImportLabGameMode::CreateStage()
{
    FString MapFile, MapError, SpawnId = TEXT("default");
    FParse::Value(FCommandLine::Get(), TEXT("MapFile="), MapFile);
    FParse::Value(FCommandLine::Get(), TEXT("SpawnId="), SpawnId);
    FParse::Value(FCommandLine::Get(), TEXT("MapReport="), MapReportFile);
    if (MapFile.IsEmpty()) MapFile = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(), TEXT("../../assets/maps/rmuc2027/map.json")));
    if (!StageMap.Load(MapFile, MapError)) { Abort(MapError); return false; }
    if (bTerrainProbeActive && !StageMap.bMeshTerrain) { Abort(TEXT("TerrainProbe requires a mesh map")); return false; }
    const FMapSpawn* Spawn = StageMap.Spawns.FindByPredicate([&SpawnId](const FMapSpawn& S) { return S.Id == SpawnId; });
    if (!Spawn) { Abort(TEXT("requested spawn id does not exist in map")); return false; }
    SelectedSpawn = *Spawn;
    if (!StageMap.SpawnGeometry(GetWorld(), MapBoxes, MapError)) { Abort(MapError); return false; }
    if (StageMap.bMeshTerrain && !FArenaShell::Spawn(GetWorld(),StageMap,MapBoxes,MapError)) { Abort(MapError); return false; }
    ProbeObstacle = MapBoxes.FindRef(TEXT("obstacle_") + StageMap.InspectionObstacleId);
    const FBox LocalBounds = Rig->GetLocalCollisionBounds();
    Rig->SetActorLocationAndRotation(RosCoordinates::Position(Spawn->GroundMetres) + FVector(0,0,2.0-LocalBounds.Min.Z),
        RosCoordinates::Rotation(FVector(0,0,Spawn->YawRadians)));
    if (StageMap.bSavedScene && StageMap.Id == TEXT("wt_vlaanderen"))
    {
        GeneratedCollision = NewObject<UWTGeneratedCollisionComponent>(this);
        AddInstanceComponent(GeneratedCollision);
        GeneratedCollision->RegisterComponent();
        if (!GeneratedCollision->Initialize(Rig, StageMap.SourceFile, StageMap.SceneSourceHash, MapError))
        { Abort(MapError); return false; }
    }
    if (ProbeReport.IsValid()) { ProbeReport->SetStringField(TEXT("map_id"),StageMap.Id); ProbeReport->SetStringField(TEXT("spawn_id"),SpawnId); }
    if (ControlReport.IsValid()) { ControlReport->SetStringField(TEXT("map_id"),StageMap.Id); ControlReport->SetStringField(TEXT("spawn_id"),SpawnId); }
    if(!FCampusGym::IsActive(GetWorld()))
    {
    const FVector FloorOffset=StageMap.bSavedScene ? RosCoordinates::Position(Spawn->GroundMetres) : FVector(0,0,StageMap.FloorZ*100.0);
    ADirectionalLight* Sun = GetWorld()->SpawnActor<ADirectionalLight>(FVector(0, 0, 500)+FloorOffset, FRotator(-45, -30, 0));
    Sun->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Sun->GetLightComponent()->SetIntensity(StageMap.bSavedScene ? 4.0f : 8.0f);
    if (StageMap.bSavedScene)
    {
        // Outdoor scene lighting is an explicit UE adaptation. Keep the existing
        // indoor presentation unchanged and anchor the atmosphere below source terrain.
        UDirectionalLightComponent* SunComponent = CastChecked<UDirectionalLightComponent>(Sun->GetLightComponent());
        SunComponent->SetAtmosphereSunLight(true);
        SunComponent->SetAtmosphereSunLightIndex(0);
        ASkyAtmosphere* Atmosphere = GetWorld()->SpawnActor<ASkyAtmosphere>(
            FVector(0,0,StageMap.SourceBoundsMetres.Min.Z*100.0-100.0),FRotator::ZeroRotator);
        Atmosphere->GetComponent()->TransformMode = ESkyAtmosphereTransformMode::PlanetTopAtComponentTransform;
        Atmosphere->GetComponent()->MarkRenderStateDirty();
        UE_LOG(LogRMSim,Display,TEXT("SCENE_LIGHTING sky_atmosphere=true sun=4 ambient=0.8 studio_fills=0"));
    }
    ASkyLight* Sky = GetWorld()->SpawnActor<ASkyLight>();
    Sky->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Sky->GetLightComponent()->SourceType = ESkyLightSourceType::SLS_SpecifiedCubemap;
    Sky->GetLightComponent()->SetCubemap(LoadObject<UTextureCube>(nullptr,
        TEXT("/Engine/MapTemplates/Sky/DaylightAmbientCubemap.DaylightAmbientCubemap")));
    Sky->GetLightComponent()->SetIntensity(StageMap.bSavedScene ? 0.8f : 2.0f);
    Sky->GetLightComponent()->SetLowerHemisphereColor(FLinearColor(0.25f, 0.28f, 0.32f));
    if (!StageMap.bSavedScene)
    for (const FVector Position : {FVector(200, -250, 250), FVector(100, 300, 200), FVector(-250, 50, 250)})
    {
        ARectLight* Fill = GetWorld()->SpawnActor<ARectLight>(Position+FloorOffset, (FVector(0, 0, 45) - Position).Rotation());
        URectLightComponent* Light = CastChecked<URectLightComponent>(Fill->GetLightComponent());
        Light->SetMobility(EComponentMobility::Movable);
        Light->SetIntensity(5000.0f);
        Light->SetSourceWidth(200.0f);
        Light->SetSourceHeight(200.0f);
        Light->SetAttenuationRadius(1800.0f);
    }
    } // Map-owned indoor lighting
    Overview = GetWorld()->SpawnActor<ACameraActor>();
    Overview->GetCameraComponent()->SetFieldOfView(55.0f);
    UpdateOverview();
    OverviewCapture = NewObject<USceneCaptureComponent2D>(Overview);
    OverviewCapture->SetupAttachment(Overview->GetRootComponent());
    OverviewCapture->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;
    OverviewCapture->FOVAngle = Overview->GetCameraComponent()->FieldOfView;
    if (bMapOverview)
    {
        OverviewCapture->ProjectionType = ECameraProjectionMode::Orthographic;
        OverviewCapture->OrthoWidth = Overview->GetCameraComponent()->OrthoWidth;
    }
    OverviewCapture->bCaptureEveryFrame = true; OverviewCapture->bCaptureOnMovement = false;
    OverviewCapture->bAlwaysPersistRenderingState = true;
    UTextureRenderTarget2D* Target = NewObject<UTextureRenderTarget2D>(Overview);
    Target->RenderTargetFormat = ETextureRenderTargetFormat::RTF_RGBA8_SRGB;
    Target->InitAutoFormat(1600, 1000); Target->UpdateResourceImmediate(true);
    OverviewCapture->TextureTarget = Target;
    Overview->AddInstanceComponent(OverviewCapture); OverviewCapture->RegisterComponent();
    FString GymRenderProfile;
    if (StageMap.Id == TEXT("rmuc2027") &&
        FParse::Value(FCommandLine::Get(), TEXT("CustomConfig="), GymRenderProfile) &&
        GymRenderProfile == TEXT("RMSimGymLumen"))
    {
        // SceneCapture defaults explicitly opt out of Lumen in UE 5.8.
        // Match this venue's player viewport for visual QA; sensor cameras retain their settings.
        auto& CapturePost = OverviewCapture->PostProcessSettings;
        CapturePost.bOverride_DynamicGlobalIlluminationMethod = true;
        CapturePost.DynamicGlobalIlluminationMethod = EDynamicGlobalIlluminationMethod::Lumen;
        CapturePost.bOverride_ReflectionMethod = true;
        CapturePost.ReflectionMethod = EReflectionMethod::Lumen;
        CapturePost.bOverride_LumenSurfaceCacheResolution = true;
        CapturePost.LumenSurfaceCacheResolution = 1.0f;
        OverviewCapture->ShowFlags.SetTemporalAA(true);
        OverviewCapture->ShowFlags.SetAntiAliasing(true);
        UE_LOG(LogTemp, Display, TEXT("RMSIM_GYM_OVERVIEW_LUMEN enabled=1 temporal_aa=1"));
    }
    return true;
}

void AImportLabGameMode::UpdateOverview()
{
    if (!Rig || !Overview) return;
    FVector GymPosition,GymTarget;float GymFov=72;
    if(StageMap.Id==TEXT("rmuc2027")&&FCampusGym::PreviewCamera(GymPosition,GymTarget,GymFov))
    {
        Overview->SetActorLocation(GymPosition);
        Overview->SetActorRotation((GymTarget-GymPosition).Rotation());
        Overview->GetCameraComponent()->SetProjectionMode(ECameraProjectionMode::Perspective);
        Overview->GetCameraComponent()->SetFieldOfView(GymFov);
        return;
    }
    if (bMapOverview)
    {
        FVector OverviewLocation(0,0,(StageMap.FloorZ+50.0)*100);
        if(StageMap.bSavedScene)
        {
            OverviewLocation=RosCoordinates::Position(StageMap.SourceBoundsMetres.GetCenter());
            OverviewLocation.Z=(StageMap.SourceBoundsMetres.Max.Z+500.0)*100.0;
        }
        Overview->SetActorLocation(OverviewLocation);
        Overview->SetActorRotation(FRotator(-90,-90,0));
        Overview->GetCameraComponent()->SetProjectionMode(ECameraProjectionMode::Orthographic);
        Overview->GetCameraComponent()->SetOrthoWidth(FMath::Max(StageMap.FloorSizeMetres.X,
            StageMap.FloorSizeMetres.Y*1.6)*105.0);
        return;
    }
    const FVector Focus = Rig->GetActorLocation() + FVector(0, 0, 45);
    Overview->SetActorLocation(Focus + FVector(220, -260, 160));
    Overview->SetActorRotation((Focus - Overview->GetActorLocation()).Rotation());
}

bool AImportLabGameMode::GetMujocoRenderDeadline(double& OutWallDeadline,double& OutSolverSeconds,
    double& OutStepSeconds,uint64& OutGeneration) const
{
    const UWorld* World=GetWorld();
    if(!bMujocoRealTimePace||!bSpawnValidated||!IsValid(Rig)||!Rig->IsMujocoPhysics()||
        !World||World->IsPaused()||NextPhysicsFrameWall<0.||MujocoPaceLastSimSeconds<0.||
        !FMath::IsFinite(RealTimeStepSeconds)||RealTimeStepSeconds<=0.)return false;
    const double SolverSeconds=Rig->GetSimulationTimeSeconds();
    const uint64 Generation=Rig->GetMotionGeneration();
    if(!FMath::IsFinite(SolverSeconds)||SolverSeconds<MujocoPaceLastSimSeconds||
        Generation!=MujocoPaceMotionGeneration)return false;
    const double Deadline=NextPhysicsFrameWall+(SolverSeconds-MujocoPaceLastSimSeconds);
    if(!FMath::IsFinite(Deadline))return false;
    OutWallDeadline=Deadline;OutSolverSeconds=SolverSeconds;
    OutStepSeconds=RealTimeStepSeconds;OutGeneration=Generation;
    return true;
}

void AImportLabGameMode::PacePhysicsFrame()
{
    if(RealTimeStepSeconds<=0)return;
    // Optional wall-clock accounting only. The native deadline follows actual
    // solver time; these diagnostics are off in production gates.
    static const bool bTimingRequested=FParse::Param(FCommandLine::Get(),TEXT("ProfileMujocoTiming"));
    const bool bProfilePace=bTimingRequested&&bMujocoRealTimePace;
    const double ProfileEntryWall=bProfilePace?FPlatformTime::Seconds():0.;
    struct FPaceTimingWindow
    {
        TWeakObjectPtr<AImportLabGameMode> Owner;
        double StartWall=-1.,StartSim=0.,PreviousExitWall=-1.;
        double PaceSeconds=0.,RequestedSleepSeconds=0.,ActualSleepSeconds=0.;
        double NonzeroSleepSeconds=0.,ZeroSleepSeconds=0.;
        double WorkSeconds=0.,MaxWorkSeconds=0.;
        uint64 Calls=0,NonzeroSleepCalls=0,ZeroSleepCalls=0;
        uint64 TimerWaitCalls=0,FallbackSleepCalls=0,WaitFailures=0;
        double MaxDeadlineOvershootSeconds=0.;
        uint64 WorkSamples=0,WorkOver5ms=0,AnchorsAtStart=0,LagWarningsAtStart=0;
    };
    static FPaceTimingWindow PaceTiming;
    auto ResetPaceTiming=[&](double Wall,double Sim)
    {
        PaceTiming=FPaceTimingWindow{};
        PaceTiming.Owner=this;PaceTiming.StartWall=Wall;PaceTiming.StartSim=Sim;
        PaceTiming.AnchorsAtStart=MujocoPaceAnchorCount;
        PaceTiming.LagWarningsAtStart=MujocoPaceLagWarningCount;
    };
    auto FinishPaceTiming=[&]()
    {
        if(!bProfilePace)return;
        const double EndWall=FPlatformTime::Seconds();
        PaceTiming.PaceSeconds+=EndWall-ProfileEntryWall;
        const double WallElapsed=EndWall-PaceTiming.StartWall;
        if(WallElapsed>=5.)
        {
            UE_LOG(LogRMSim,Display,TEXT("RMSIM_PACE_TIMING wall_elapsed=%.6f sim_elapsed=%.6f calls=%llu ")
                TEXT("pace_total_ms=%.3f pace_mean_ms=%.6f ")
                TEXT("sleep_requested_nonzero_ms=%.3f sleep_actual_ms=%.3f ")
                TEXT("sleep_actual_nonzero_ms=%.3f sleep_actual_zero_ms=%.3f ")
                TEXT("sleep_nonzero_calls=%llu sleep_zero_calls=%llu ")
                TEXT("high_res_timer_wait_calls=%llu fallback_sleep_calls=%llu wait_failures=%llu max_wait_overshoot_ms=%.6f ")
                TEXT("work_samples=%llu work_total_ms=%.3f work_mean_ms=%.6f work_max_ms=%.3f work_gt_5ms_calls=%llu ")
                TEXT("anchors_delta=%llu lag_warnings_delta=%llu late_debt_s=%.6f max_lag_s=%.6f generation=%llu"),
                WallElapsed,MujocoPaceLastSimSeconds-PaceTiming.StartSim,PaceTiming.Calls,
                PaceTiming.PaceSeconds*1000.,PaceTiming.Calls?PaceTiming.PaceSeconds*1000./double(PaceTiming.Calls):0.,
                PaceTiming.RequestedSleepSeconds*1000.,PaceTiming.ActualSleepSeconds*1000.,
                PaceTiming.NonzeroSleepSeconds*1000.,PaceTiming.ZeroSleepSeconds*1000.,
                PaceTiming.NonzeroSleepCalls,PaceTiming.ZeroSleepCalls,
                PaceTiming.TimerWaitCalls,PaceTiming.FallbackSleepCalls,PaceTiming.WaitFailures,
                PaceTiming.MaxDeadlineOvershootSeconds*1000.,
                PaceTiming.WorkSamples,PaceTiming.WorkSeconds*1000.,
                PaceTiming.WorkSamples?PaceTiming.WorkSeconds*1000./double(PaceTiming.WorkSamples):0.,
                PaceTiming.MaxWorkSeconds*1000.,PaceTiming.WorkOver5ms,
                MujocoPaceAnchorCount-PaceTiming.AnchorsAtStart,
                MujocoPaceLagWarningCount-PaceTiming.LagWarningsAtStart,
                MujocoPaceCurrentLagSeconds,MujocoPaceMaxLagSeconds,MujocoPaceMotionGeneration);
            // Optional endpoint diagnostics share this five-second profiling window.
            // Contacts retain per-substep times and accumulated impulse, never a sum of forces.
            if(bSpawnValidated&&IsValid(Rig)&&Rig->IsMujocoPhysics())
            {
                const auto NativeContactState=Rig->GetContactState();
                auto Snapshot=MakeShared<FJsonObject>();
                Snapshot->SetStringField(TEXT("schema"),TEXT("rmsim_native_drive_snapshot_v1"));
                Snapshot->SetStringField(TEXT("coordinates"),TEXT("UE world cm; native joint SI; drive world ROS ENU SI"));
                Snapshot->SetStringField(TEXT("contact_sampling_phase"),TEXT("solved contacts collected over the last complete UE physics frame"));
                Snapshot->SetNumberField(TEXT("motion_generation"),double(Rig->GetMotionGeneration()));
                Snapshot->SetNumberField(TEXT("frame_duration_s"),RealTimeStepSeconds);
                for(const TCHAR* Field:{TEXT("time_s"),TEXT("step_s"),TEXT("last_frame_substeps"),TEXT("active_contacts"),
                    TEXT("maximum_contact_penetration_m"),TEXT("chassis"),TEXT("actuator_state"),TEXT("actuator_sampling_phase")})
                    if(const auto* Value=NativeContactState->Values.Find(Field))Snapshot->SetField(Field,*Value);
                Snapshot->SetObjectField(TEXT("drive"),Rig->GetVelocityCommandState());
                const TArray<TSharedPtr<FJsonValue>>* NativeValues=nullptr;
                TArray<TSharedPtr<FJsonValue>> WheelSnapshot;
                if(NativeContactState->TryGetArrayField(TEXT("wheels"),NativeValues))
                {
                    Snapshot->SetNumberField(TEXT("wheel_count"),NativeValues->Num());
                    for(const auto& Value:*NativeValues)
                    {
                        const auto Wheel=Value->AsObject();if(!Wheel||WheelSnapshot.Num()>=4)continue;
                        auto Selected=MakeShared<FJsonObject>();
                        for(const TCHAR* Field:{TEXT("part"),TEXT("radius_m"),TEXT("wheel_feedforward_rad_s"),TEXT("motor_target_rev_s"),
                            TEXT("wheel_measured_rad_s"),TEXT("motor_integral_bias_rad_s"),TEXT("steering_target_rad"),
                            TEXT("steering_measured_rad"),TEXT("steering_measured_rate_rad_s"),
                            TEXT("suspension_displacement_m"),TEXT("suspension_velocity_m_s")})
                            if(const auto* Item=Wheel->Values.Find(Field))Selected->SetField(Field,*Item);
                        WheelSnapshot.Add(MakeShared<FJsonValueObject>(Selected));
                    }
                }
                Snapshot->SetArrayField(TEXT("wheels"),WheelSnapshot);
                TMap<FString,TSharedPtr<FJsonObject>> ContactGroups;
                if(NativeContactState->TryGetArrayField(TEXT("mujoco_contacts"),NativeValues))
                {
                    Snapshot->SetNumberField(TEXT("contact_sample_count"),NativeValues->Num());
                    for(const auto& Value:*NativeValues)
                    {
                        const auto Contact=Value->AsObject();if(!Contact)continue;
                        const FString Body=Contact->GetStringField(TEXT("body")),Other=Contact->GetStringField(TEXT("other"));
                        const FString GroupKey=Body+TEXT("|")+Other;
                        auto Group=ContactGroups.FindRef(GroupKey);
                        if(!Group)
                        {
                            Group=MakeShared<FJsonObject>();Group->SetStringField(TEXT("body"),Body);Group->SetStringField(TEXT("other"),Other);
                            Group->SetNumberField(TEXT("samples"),0);Group->SetNumberField(TEXT("normal_impulse_sum_ns"),0);
                            Group->SetNumberField(TEXT("peak_normal_force_n"),-1);
                            Group->SetNumberField(TEXT("first_sample_time_s"),Contact->GetNumberField(TEXT("time_s")));
                            ContactGroups.Add(GroupKey,Group);
                        }
                        Group->SetNumberField(TEXT("samples"),Group->GetNumberField(TEXT("samples"))+1);
                        Group->SetNumberField(TEXT("normal_impulse_sum_ns"),Group->GetNumberField(TEXT("normal_impulse_sum_ns"))+Contact->GetNumberField(TEXT("normal_impulse_ns")));
                        Group->SetNumberField(TEXT("last_sample_time_s"),Contact->GetNumberField(TEXT("time_s")));
                        const double Force=Contact->GetNumberField(TEXT("normal_force_n"));
                        if(Force>Group->GetNumberField(TEXT("peak_normal_force_n")))
                        {
                            Group->SetNumberField(TEXT("peak_normal_force_n"),Force);
                            for(const TCHAR* Field:{TEXT("point_cm"),TEXT("into_obstacle_normal"),TEXT("other_geom"),TEXT("other_flex"),TEXT("time_s")})
                                if(const auto* Item=Contact->Values.Find(Field))Group->SetField(Field,*Item);
                        }
                    }
                }
                TArray<TSharedPtr<FJsonValue>> ContactSnapshot;
                for(const auto& Entry:ContactGroups)ContactSnapshot.Add(MakeShared<FJsonValueObject>(Entry.Value));
                ContactSnapshot.Sort([](const TSharedPtr<FJsonValue>& A,const TSharedPtr<FJsonValue>& B)
                    {return A->AsObject()->GetNumberField(TEXT("peak_normal_force_n"))>B->AsObject()->GetNumberField(TEXT("peak_normal_force_n"));});
                Snapshot->SetNumberField(TEXT("contact_group_count"),ContactSnapshot.Num());
                Snapshot->SetNumberField(TEXT("omitted_contact_groups"),FMath::Max(0,ContactSnapshot.Num()-16));
                if(ContactSnapshot.Num()>16)ContactSnapshot.SetNum(16,EAllowShrinking::No);
                Snapshot->SetArrayField(TEXT("contact_groups"),ContactSnapshot);
                FString SnapshotJson;
                FJsonSerializer::Serialize(Snapshot,TJsonWriterFactory<TCHAR,TCondensedJsonPrintPolicy<TCHAR>>::Create(&SnapshotJson));
                UE_LOG(LogRMSim,Display,TEXT("RMSIM_DRIVE_SNAPSHOT %s"),*SnapshotJson);
            }
            ResetPaceTiming(EndWall,MujocoPaceLastSimSeconds);
        }
        // Exclude diagnostic aggregation/log emission from the following work
        // interval, which measures exit-to-entry engine/renderer/ROS work.
        PaceTiming.PreviousExitWall=FPlatformTime::Seconds();
    };
    if(bMujocoRealTimePace)
    {
        // Model compilation and scene registration precede the simulation epoch.
        // Their wall time must not become a later fast-forward obligation.
        if(!bSpawnValidated||!IsValid(Rig)||!Rig->IsMujocoPhysics())
        {
            if(bProfilePace)PaceTiming=FPaceTimingWindow{};
            NextPhysicsFrameWall=-1;MujocoPaceLastSimSeconds=-1;
            MujocoPaceCurrentLagSeconds=0;MujocoPaceLastLagWarningWall=-1;return;
        }
        const double Now=FPlatformTime::Seconds(),SimTime=Rig->GetSimulationTimeSeconds();
        const uint64 Generation=Rig->GetMotionGeneration();
        const bool NewEpoch=Generation!=MujocoPaceMotionGeneration||SimTime<MujocoPaceLastSimSeconds;
        if(bProfilePace)
        {
            // Weak owner identity prevents pointer reuse across worlds. The same
            // epoch conditions as the pacer also discard pre-reset work samples.
            if(PaceTiming.Owner.Get()!=this||PaceTiming.StartWall<0||NextPhysicsFrameWall<0||NewEpoch)
                ResetPaceTiming(ProfileEntryWall,SimTime);
            ++PaceTiming.Calls;
            if(PaceTiming.PreviousExitWall>=0.)
            {
                const double Work=ProfileEntryWall-PaceTiming.PreviousExitWall;
                ++PaceTiming.WorkSamples;PaceTiming.WorkSeconds+=Work;
                PaceTiming.MaxWorkSeconds=FMath::Max(PaceTiming.MaxWorkSeconds,Work);
                if(Work>.005)++PaceTiming.WorkOver5ms;
            }
        }
        if(NextPhysicsFrameWall<0||NewEpoch)
        {
            NextPhysicsFrameWall=Now;MujocoPaceLastSimSeconds=SimTime;MujocoPaceMotionGeneration=Generation;
            MujocoPaceCurrentLagSeconds=0;MujocoPaceLastLagWarningWall=-1;++MujocoPaceAnchorCount;
            UE_LOG(LogRMSim,Display,TEXT("SIM_REALTIME_PACE_ANCHOR backend=mujoco reason=%s solver_time_s=%.6f generation=%llu anchors=%llu late_warning_s=0.050 deadline_policy=retain_absolute"),
                NewEpoch?TEXT("motion_generation_or_time_reset"):TEXT("ready"),SimTime,Generation,MujocoPaceAnchorCount);
            FinishPaceTiming();
            return;
        }
        NextPhysicsFrameWall+=SimTime-MujocoPaceLastSimSeconds;
        MujocoPaceLastSimSeconds=SimTime;
        const double Lag=Now-NextPhysicsFrameWall;
        MujocoPaceCurrentLagSeconds=FMath::Max(0.,Lag);
        MujocoPaceMaxLagSeconds=FMath::Max(MujocoPaceMaxLagSeconds,Lag);
        // Retain late-frame debt against the absolute deadline. The normal frame
        // loop completes every solver/sensor step; it simply does not wait while
        // late. Only initialization or a real simulation epoch reset reanchors.
        constexpr double LateWarningSeconds=.050,LateWarningIntervalSeconds=1.;
        if(Lag>LateWarningSeconds&&(MujocoPaceLastLagWarningWall<0||
            Now-MujocoPaceLastLagWarningWall>=LateWarningIntervalSeconds))
        {
            MujocoPaceLastLagWarningWall=Now;++MujocoPaceLagWarningCount;
            UE_LOG(LogRMSim,Display,TEXT("SIM_REALTIME_PACE_LATE backend=mujoco lag_s=%.6f lag_warnings=%llu anchors=%llu deadline_policy=retain_absolute physics_steps_skipped=0"),
                Lag,MujocoPaceLagWarningCount,MujocoPaceAnchorCount);
        }
    }
    else
    {
        // Preserve the existing Chaos pacing behavior.
        if(NextPhysicsFrameWall<0){NextPhysicsFrameWall=FPlatformTime::Seconds();return;}
        NextPhysicsFrameWall+=RealTimeStepSeconds;
    }
#if PLATFORM_WINDOWS
    if(bMujocoRealTimePace)
    {
        if(!MujocoDeadlineWait)MujocoDeadlineWait=MakeShared<FRMSimWindowsDeadlineWait>();
        const auto Wait=MujocoDeadlineWait->WaitUntil(NextPhysicsFrameWall,[]{return FPlatformTime::Seconds();});
        if(Wait.NewFailures)
        {
            const auto& Stats=MujocoDeadlineWait->GetStatistics();
            UE_LOG(LogRMSim,Warning,TEXT("SIM_REALTIME_PACE_WAIT_FALLBACK backend=mujoco win32_error=%u failures=%llu policy=positive_millisecond_sleep minimum_ms=1 deadline_policy=retain_absolute"),
                Stats.LastError,static_cast<uint64>(Stats.Failures));
        }
        if(bProfilePace)
        {
            PaceTiming.TimerWaitCalls+=Wait.TimerWaitCalls;
            PaceTiming.FallbackSleepCalls+=Wait.FallbackSleepCalls;
            PaceTiming.WaitFailures+=Wait.NewFailures;
            PaceTiming.NonzeroSleepCalls+=Wait.TimerWaitCalls+Wait.FallbackSleepCalls;
            PaceTiming.RequestedSleepSeconds+=Wait.RequestedSeconds;
            PaceTiming.ActualSleepSeconds+=Wait.ActualWaitSeconds;
            PaceTiming.NonzeroSleepSeconds+=Wait.ActualWaitSeconds;
            PaceTiming.MaxDeadlineOvershootSeconds=FMath::Max(PaceTiming.MaxDeadlineOvershootSeconds,Wait.DeadlineOvershootSeconds);
        }
        FinishPaceTiming();
        return;
    }
#endif
    // Non-Windows and the existing Chaos path keep their original wait policy.
    for(double Remaining=NextPhysicsFrameWall-FPlatformTime::Seconds();Remaining>0;
        Remaining=NextPhysicsFrameWall-FPlatformTime::Seconds())
    {
        // Sleep most of the budget, yielding the final sub-millisecond tail.
        const double SleepStartWall=bProfilePace?FPlatformTime::Seconds():0.;
        FPlatformProcess::SleepNoStats(Remaining>.001?float(Remaining-.0005):0.f);
        if(bProfilePace)
        {
            const double ActualSleep=FPlatformTime::Seconds()-SleepStartWall;
            PaceTiming.ActualSleepSeconds+=ActualSleep;
            if(Remaining>.001)
            {
                ++PaceTiming.NonzeroSleepCalls;
                PaceTiming.RequestedSleepSeconds+=float(Remaining-.0005);
                PaceTiming.NonzeroSleepSeconds+=ActualSleep;
            }
            else
            {
                ++PaceTiming.ZeroSleepCalls;
                PaceTiming.ZeroSleepSeconds+=ActualSleep;
            }
        }
    }
    FinishPaceTiming();
}

void AImportLabGameMode::EndPlay(const EEndPlayReason::Type Reason)
{
    FCoreDelegates::OnBeginFrame.Remove(RealTimePaceHandle);
#if PLATFORM_WINDOWS
    if(MujocoDeadlineWait)
    {
        MujocoDeadlineWait->CancelAndClose();
        const auto& Wait=MujocoDeadlineWait->GetStatistics();
        UE_LOG(LogRMSim,Display,TEXT("SIM_REALTIME_PACE_WAIT_SUMMARY backend=mujoco policy=high_resolution_one_shot timer_creates=%llu timer_wait_calls=%llu fallback_sleep_calls=%llu failures=%llu cancel_calls=%llu cancel_failures=%llu close_calls=%llu close_failures=%llu last_win32_error=%u"),
            static_cast<uint64>(Wait.CreateCalls),static_cast<uint64>(Wait.TimerWaitCalls),static_cast<uint64>(Wait.FallbackSleepCalls),
            static_cast<uint64>(Wait.Failures),static_cast<uint64>(Wait.CancelCalls),static_cast<uint64>(Wait.CancelFailures),
            static_cast<uint64>(Wait.CloseCalls),static_cast<uint64>(Wait.CloseFailures),Wait.LastError);
        MujocoDeadlineWait.Reset();
    }
#endif
    if(bMujocoRealTimePace)
        UE_LOG(LogRMSim,Display,TEXT("SIM_REALTIME_PACE_SUMMARY backend=mujoco anchors=%llu lag_warnings=%llu late_debt_s=%.6f max_lag_s=%.6f deadline_policy=retain_absolute physics_steps_skipped=0"),
            MujocoPaceAnchorCount,MujocoPaceLagWarningCount,MujocoPaceCurrentLagSeconds,MujocoPaceMaxLagSeconds);
    Super::EndPlay(Reason);
}

void AImportLabGameMode::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bReady) return;
    const double FrameWallNow=FPlatformTime::Seconds();
    const double FrameWallDelta=LastFrameWallSeconds>0?FrameWallNow-LastFrameWallSeconds:double(DeltaSeconds);
    LastFrameWallSeconds=FrameWallNow;
    FrameAverageSeconds=FrameAverageSeconds>0?FMath::Lerp(FrameAverageSeconds,FrameWallDelta,.05):FrameWallDelta;
    if(!IsValid(Rig)){Abort(TEXT("vehicle no longer exists"));return;}
    if (GeneratedCollision)
    {
        FString CollisionError;
        if (!GeneratedCollision->Advance(CollisionError)) { Abort(CollisionError); return; }
    }
    if (!bSpawnValidated)
    {
        SpawnValidationElapsed += DeltaSeconds;
        bool PhysicsReady = ++SpawnValidationFrames >= 3;
        for (const auto& Entry : MapBoxes)
        {
            UStaticMeshComponent* Mesh = Entry.Value->GetStaticMeshComponent();
            if (Mesh->GetCollisionEnabled() != ECollisionEnabled::NoCollision)
                PhysicsReady &= Mesh->IsPhysicsStateCreated() && Mesh->GetBodyInstance() && Mesh->GetBodyInstance()->IsValidBodyInstance();
        }
        PhysicsReady &= StageMap.IsSceneCollisionReady(GetWorld());
        if (!PhysicsReady)
        {
            if (SpawnValidationElapsed > 15.0) Abort(TEXT("map collision bodies did not become ready"));
            return;
        }
        if (!UpdateSceneRenderingReadiness()) return;
        FString TerrainError;
        if (StageMap.bMeshTerrain && !Rig->SnapToTerrain(TerrainError)) { Abort(TerrainError); return; }
        const bool Overlapping = Rig->GetManualState().bBlockingOverlap;
        if (!MapReportFile.IsEmpty() && !StageMap.WriteReport(MapReportFile,SelectedSpawn,MapBoxes,Rig->GetActorLocation(),Overlapping,GetWorld()))
        { Abort(TEXT("cannot write fresh map report")); return; }
        if (Overlapping) { Abort(TEXT("vehicle collision proxy overlaps geometry at selected spawn")); return; }
        if(bContactMode)
        {
            FString PhysicsError;
            if(!Rig->InitializeContactPhysics(ContactRigFile,PhysicsError)){Abort(PhysicsError);return;}
            ContactStart=Rig->GetActorTransform();
            ContactSpawn=ContactStart;
            GyroSpeedRadS=Rig->GetDriveParameter(TEXT("GyroDefaultRadS"));
        }
        if(bTrainingMode)
        {
            Training=GetWorld()->SpawnActor<ATrainingRange>();FString TrainingError;
            if(!Training||!Training->Initialize(Rig,TrainingError)){Abort(TrainingError);return;}
        }
        if(bRosBridgeEnabled)
        {
            RosBridge=NewObject<URosBridgeComponent>(this);AddInstanceComponent(RosBridge);RosBridge->RegisterComponent();FString BridgeError;
            if(!RosBridge->Initialize(Rig,GetAimCapture(),Training,bRosControl,BridgeError)){Abort(BridgeError);return;}
            RosBridge->SetMapIdentity(StageMap.Id,StageMap.SourceFile,StageMap.bMeshTerrain,StageMap.bSavedScene);
            if(RosBridge->SupportsOperatorGyro()&&FParse::Param(FCommandLine::Get(),TEXT("RosOperatorGyroStart")))
            {
                double StartupRate = GyroSpeedRadS;
                if (FParse::Value(FCommandLine::Get(), TEXT("RosOperatorGyroStartRate="), StartupRate) &&
                    FMath::IsFinite(StartupRate))
                {
                    GyroSpeedRadS = FMath::Clamp(StartupRate, Rig->GetDriveParameter(TEXT("GyroMinRadS")),
                        Rig->GetDriveParameter(TEXT("GyroMaxRadS")));
                }
                bGyroMode=true;
                RosBridge->SetOperatorGyro(GyroSpeedRadS);
            }
        }
        if(UsesDedicatedMujoco())
        {
            FString WorkerError;
            if(!Rig->StartDedicatedMujoco(DedicatedMujocoStep,WorkerError)){Abort(WorkerError);return;}
            DedicatedPreviousSimTime=Rig->GetSimulationTimeSeconds();
        }
        bSpawnValidated = true;
        if(StageMap.bSavedScene)
        {
            SoftFoliage=NewObject<UWTFoliageCrushComponent>(this);
            AddInstanceComponent(SoftFoliage);SoftFoliage->RegisterComponent();
            SoftFoliage->Initialize(GetWorld());
        }
        if(ControlReport.IsValid())StageMap.AddSceneReport(GetWorld(),ControlReport.ToSharedRef());
        if(ProbeReport.IsValid())StageMap.AddSceneReport(GetWorld(),ProbeReport.ToSharedRef());
        UE_LOG(LogRMSim,Display,TEXT("MAP_STAGE_READY %s spawn=%s obstacles=%d regions=%d"),*StageMap.Id,*SelectedSpawn.Id,StageMap.Obstacles.Num(),StageMap.Regions.Num());
        UE_LOG(LogRMSim,Display,TEXT("IMPORT_LAB_READY W/S throttle, A/D steer, Space brake, mouse aim, C free-look, Shift sight, RMB zoom"));
    }
    if(!bControlProbeActive&&!bProbeActive)RecoverContactVehicleIfOutsideMap();
    if(SoftFoliage)SoftFoliage->Advance(Rig,DeltaSeconds);
    if(FParse::Param(FCommandLine::Get(),TEXT("DestructionDriveFast")) ||
       FParse::Param(FCommandLine::Get(),TEXT("DestructionDriveHold")) ||
       FParse::Param(FCommandLine::Get(),TEXT("DestructionDriveCross")))
    {
        if(!SoftFoliage || !Rig->IsContactPhysics())
        {Abort(TEXT("destruction drive probe requires a saved-scene contact vehicle"));return;}
        double X=0,Y=0,Z=0;
        if(!FParse::Value(FCommandLine::Get(),TEXT("DestructionTargetX="),X) ||
           !FParse::Value(FCommandLine::Get(),TEXT("DestructionTargetY="),Y) ||
           !FParse::Value(FCommandLine::Get(),TEXT("DestructionTargetZ="),Z) ||
           !FMath::IsFinite(X)||!FMath::IsFinite(Y)||!FMath::IsFinite(Z))
        {Abort(TEXT("destruction drive probe requires finite target ROS metres"));return;}
        const bool Cross=FParse::Param(FCommandLine::Get(),TEXT("DestructionDriveCross"));
        const bool Fast=Cross||FParse::Param(FCommandLine::Get(),TEXT("DestructionDriveFast"));
        const FVector TargetUE=RosCoordinates::Position(FVector(X,Y,Z));
        const FVector Approach=(TargetUE-ContactSpawn.GetLocation()).GetSafeNormal2D();
        const FVector Extended=Cross?TargetUE+Approach*250.:TargetUE;
        const FVector From=Extended-Rig->GetActorLocation();
        const FVector2D Direction(From.X,-From.Y);
        const double TargetSpeed=Fast?FMath::Min(3.,Rig->GetDriveParameter(TEXT("MaxForwardSpeedMps"))):.85;
        if(Direction.SizeSquared()>.04)
            Rig->SetContactPlanarDrive(Direction.GetSafeNormal()*TargetSpeed,0,true,false);
        else Rig->SetContactDrive(0,0,true);
        DestructionProbeSeconds+=DeltaSeconds;
        const double BeyondCm=FVector::DotProduct(Rig->GetActorLocation()-TargetUE,Approach);
        if(SoftFoliage->BreakCount()>0 && (!Cross || BeyondCm>90.))
        {
            Rig->SetContactDrive(0,0,true);
            UE_LOG(LogRMSim,Display,
                TEXT("WT_DESTRUCTION_DRIVE_PASSED mode=%s seconds=%.3f broken=%d chassis_speed_mps=%.3f passed_fence_cm=%.2f"),
                Cross?TEXT("cross"):Fast?TEXT("fast"):TEXT("hold"),DestructionProbeSeconds,SoftFoliage->BreakCount(),
                Rig->GetCommandOriginVelocity().Size2D()/100.,BeyondCm);
            FPlatformMisc::RequestExitWithStatus(false,0);
        }
        else if(DestructionProbeSeconds>20.)
            Abort(TEXT("destruction drive probe: physical trigger absent within20s"));
        return;
    }
    if(FParse::Param(FCommandLine::Get(),TEXT("DestructionProbe")))
    {
        if(!SoftFoliage){Abort(TEXT("destruction probe requires saved-scene foliage"));return;}
        if(!bDestructionProbeStarted)
        {
            TArray<FString> Outcomes;
            if(!SoftFoliage->ProbeOneOfEach(Outcomes))
            {Abort(TEXT("destruction native instance identity/collision/animation probe failed"));return;}
            UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_PROBE_INIT %s"),*FString::Join(Outcomes,TEXT(",")));
            bDestructionProbeStarted=true;
        }
        DestructionProbeSeconds+=DeltaSeconds;
        if(DestructionProbeSeconds>=6.1)
        {
            if(SoftFoliage->ActiveAnimations()>0)
            {Abort(TEXT("destruction visual did not disappear on schedule"));return;}
            UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_PROBE_PASSED soft=%d hard=%d live=%d"),
                SoftFoliage->CrushCount(),SoftFoliage->BreakCount(),SoftFoliage->ActiveAnimations());
            FPlatformMisc::RequestExitWithStatus(false,0);
        }
        return;
    }
    UpdateOverview();
    if(bTractionProbeActive){TickTractionProbe(DeltaSeconds);return;}
    if(bGyroProbeActive){TickGyroProbe(DeltaSeconds);return;}
    if(bContactProbeActive){TickContactProbe(DeltaSeconds);return;}
    if (bProbeActive)
    {
        UpdateOverview();
        Elapsed += DeltaSeconds;
        if (Elapsed >= CaptureDelay) TickProbe();
        return; // No keyboard input can change this run's command sequence.
    }
    if (bControlProbeActive)
    {
        Elapsed += DeltaSeconds;
        if (Elapsed >= CaptureDelay) TickControlProbe();
        return;
    }
    if(UsesDedicatedMujoco())
    {
        const double Now=Rig->GetSimulationTimeSeconds();
        // Local training and gimbal input share the consumed physical clock.
        DeltaSeconds=float(FMath::Max(0.,Now-DedicatedPreviousSimTime));
        DedicatedPreviousSimTime=Now;
    }
    TickManualControl(DeltaSeconds);
    if(!ImuFixture.IsEmpty())TickImuFixture(DeltaSeconds);
    if(RosBridge)RosBridge->Advance();
    else if(UsesDedicatedMujoco())Rig->SubmitDedicatedMujocoInputs();
    Elapsed += DeltaSeconds;
    if (!bCaptured && !CaptureDirectory.IsEmpty() && Elapsed >= CaptureDelay)
    {
        bCaptured = true;
        if (!CapturePngs()) { Abort(TEXT("PNG capture/write failed")); return; }
        if (bExitAfterCapture) FPlatformMisc::RequestExitWithStatus(false, 0);
    }
}

USceneCaptureComponent2D* AImportLabGameMode::GetAimCapture() const
{
    if (!Rig) return nullptr;
    if (USceneCaptureComponent2D* const* Aim = Rig->GetCameras().Find(TEXT("aim_camera"))) return *Aim;
    for (const auto& Camera : Rig->GetCameras()) return Camera.Value;
    return nullptr;
}

FVector2D AImportLabGameMode::GetGimbalPlanarForwardRos() const
{
    if (!Rig) return FVector2D(1, 0);
    const USceneCaptureComponent2D* Aim = GetAimCapture();
    const FVector Gun = Aim ? Aim->GetForwardVector() : Rig->GetActorForwardVector();
    FVector2D Forward(Gun.X, -Gun.Y); // UE XY -> ROS left-positive XY.
    if (Forward.IsNearlyZero(1.e-6))
    {
        const FVector Body = Rig->GetActorForwardVector();
        Forward = FVector2D(Body.X, -Body.Y);
    }
    return Forward.IsNearlyZero(1.e-6) ? FVector2D(1, 0) : Forward.GetSafeNormal();
}

FVector AImportLabGameMode::TraceViewPoint(const FVector& Start, const FVector& Direction) const
{
    const FVector End = Start + Direction.GetSafeNormal() * 100000.0;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimViewAim), false, Rig);
    FHitResult Hit;
    return GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Query) ? Hit.ImpactPoint : End;
}

void AImportLabGameMode::UpdatePlayerView(APlayerController* PC)
{
    if (!ChaseCamera || !SightCamera || !Rig || !PC) return;
    // Optional presentation view uses the same main renderer as normal driving.
    // The official launcher does not pass this flag, so its controls/view stay unchanged.
    static const bool GymReviewCamera = FParse::Param(FCommandLine::Get(), TEXT("GymReviewCamera"));
    if (GymReviewCamera && StageMap.Id == TEXT("rmuc2027") && Overview)
    {
        if (PC->GetViewTarget() != Overview) PC->SetViewTarget(Overview);
        return;
    }
    const FVector Pivot = Rig->GetActorLocation() + FVector(0, 0, ChasePivotHeightCm);
    const FRotator Look(OrbitPitch, OrbitYaw, 0.0);
    const FVector Direction = Look.Vector();
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimChaseCamera), false, Rig);
    FHitResult Hit;
    const bool Obstructed = GetWorld()->SweepSingleByChannel(Hit, Pivot,
        Pivot - Direction * ChaseDistanceCm, FQuat::Identity, ECC_Camera,
        FCollisionShape::MakeSphere(12.0f), Query);
    const double ClearDistance = Obstructed ? ChaseDistanceCm * Hit.Time : ChaseDistanceCm;
    const double Now = FPlatformTime::Seconds();
    const uint64 Generation = Rig->GetMotionGeneration();
    if (LastChaseViewWall <= 0.0 || ChaseViewGeneration != Generation ||
        ClearDistance <= ChaseVisibleDistanceCm)
    {
        ChaseVisibleDistanceCm = ClearDistance;
    }
    else
    {
        const double Delta = FMath::Clamp(Now - LastChaseViewWall, 0.0, 0.05);
        ChaseVisibleDistanceCm = FMath::Lerp(ChaseVisibleDistanceCm, ClearDistance,
            1.0 - FMath::Exp(-12.0 * Delta));
    }
    LastChaseViewWall = Now;
    ChaseViewGeneration = Generation;
    ChaseCamera->SetActorLocationAndRotation(Pivot - Direction * ChaseVisibleDistanceCm, Look);
    ChaseCamera->GetCameraComponent()->SetFieldOfView(55.0f);
    if (USceneCaptureComponent2D* Aim = GetAimCapture())
    {
        SightCamera->SetActorTransform(Aim->GetComponentTransform());
        const float SightFov = bSightZoom ? 15.0f : static_cast<float>(ScopeBaseFov);
        SightCamera->GetCameraComponent()->SetFieldOfView(SightFov);
        Aim->FOVAngle = SightFov; // Manual preview optics; no calibrated sensor-rate claim.
    }
    ACameraActor* Wanted = bSightMode && !bFreeLook ? SightCamera : ChaseCamera;
    if (PC->GetViewTarget() != Wanted) PC->SetViewTarget(Wanted);
}

void AImportLabGameMode::TickManualControl(float DeltaSeconds)
{
    APlayerController* PC = GetWorld()->GetFirstPlayerController();
    if (!PC || !ChaseCamera || !SightCamera) return;
    UGameViewportClient* ViewportClient = GetWorld()->GetGameViewport();
    const bool Focused = ViewportClient && ViewportClient->Viewport &&
        ViewportClient->Viewport->HasFocus() && ViewportClient->Viewport->IsForegroundWindow();
    TSharedPtr<FJsonObject> BeforeInput;
    auto NoteEdges = [this, PC, Focused, &BeforeInput](FKey Key, const TCHAR* Name)
    {
        if (!Focused) return;
        for (const bool Pressed : {true, false})
        {
            if (Pressed ? PC->WasInputKeyJustPressed(Key) : PC->WasInputKeyJustReleased(Key))
            {
                if (!BeforeInput.IsValid()) BeforeInput = ManualControlState();
                ++InputEventCounts.FindOrAdd(FString(Name) + (Pressed ? TEXT("_press") : TEXT("_release")));
            }
        }
    };
    NoteEdges(EKeys::W, TEXT("W")); NoteEdges(EKeys::S, TEXT("S"));
    NoteEdges(EKeys::A, TEXT("A")); NoteEdges(EKeys::D, TEXT("D"));
    NoteEdges(EKeys::SpaceBar, TEXT("Space")); NoteEdges(EKeys::C, TEXT("C"));
    NoteEdges(EKeys::LeftShift, TEXT("LeftShift")); NoteEdges(EKeys::RightShift, TEXT("RightShift"));
    NoteEdges(EKeys::RightMouseButton, TEXT("RightMouseZoom"));
    NoteEdges(EKeys::LeftMouseButton, TEXT("Fire")); NoteEdges(EKeys::R, TEXT("RestartRound"));
    NoteEdges(EKeys::G,TEXT("GyroToggle"));NoteEdges(EKeys::Z,TEXT("GyroSlower"));NoteEdges(EKeys::X,TEXT("GyroFaster"));
    NoteEdges(EKeys::Q,TEXT("TurnLeft"));NoteEdges(EKeys::E,TEXT("TurnRight"));
    NoteEdges(EKeys::LeftControl,TEXT("Precision"));NoteEdges(EKeys::RightControl,TEXT("Precision"));
    NoteEdges(EKeys::MouseScrollUp, TEXT("WheelUp")); NoteEdges(EKeys::MouseScrollDown, TEXT("WheelDown"));
    NoteEdges(EKeys::M, TEXT("Map")); NoteEdges(EKeys::Escape, TEXT("MapClose"));
    NoteEdges(EKeys::LeftAlt, TEXT("MapModifier")); NoteEdges(EKeys::RightAlt, TEXT("MapModifier"));
    NoteEdges(EKeys::MiddleMouseButton, TEXT("MapPoint"));

    float MouseX = 0.0f, MouseY = 0.0f;
    if (PC->PlayerInput)
    {
        // Avoid the engine's sensitivity, FOV multiplier and smoothing being applied a second time.
        MouseX = PC->PlayerInput->GetRawKeyValue(EKeys::MouseX);
        MouseY = PC->PlayerInput->GetRawKeyValue(EKeys::MouseY);
    }
    auto Key = [PC](FKey Value) { return PC->IsInputKeyDown(Value) ? 1.0 : 0.0; };
    FManualControlInput Input;
    Input.Focused = Focused;
    Input.Throttle = Key(EKeys::W) - Key(EKeys::S);
    Input.Steer = Key(EKeys::D) - Key(EKeys::A);
    Input.Strafe=Key(EKeys::A)-Key(EKeys::D);
    Input.Turn=Key(EKeys::E)-Key(EKeys::Q);
    Input.Precision=PC->IsInputKeyDown(EKeys::LeftControl)||PC->IsInputKeyDown(EKeys::RightControl);
    Input.Brake = PC->IsInputKeyDown(EKeys::SpaceBar);
    Input.FreeLook = PC->IsInputKeyDown(EKeys::C);
    Input.ToggleSight = PC->WasInputKeyJustPressed(EKeys::LeftShift) || PC->WasInputKeyJustPressed(EKeys::RightShift);
    Input.Zoom = PC->IsInputKeyDown(EKeys::RightMouseButton);
    Input.Fire = PC->IsInputKeyDown(EKeys::LeftMouseButton);
    Input.RestartRound = PC->WasInputKeyJustPressed(EKeys::R);
    Input.ToggleGyro=PC->WasInputKeyJustPressed(EKeys::G);
    Input.GyroSpeedSteps=(PC->WasInputKeyJustPressed(EKeys::X)?1:0)-(PC->WasInputKeyJustPressed(EKeys::Z)?1:0);
    Input.WheelSteps = (PC->WasInputKeyJustPressed(EKeys::MouseScrollUp) ? 1 : 0) -
        (PC->WasInputKeyJustPressed(EKeys::MouseScrollDown) ? 1 : 0);
    Input.MapModifier = PC->IsInputKeyDown(EKeys::LeftAlt) || PC->IsInputKeyDown(EKeys::RightAlt);
    Input.ToggleMap = PC->WasInputKeyJustPressed(EKeys::M);
    Input.CloseMap = PC->WasInputKeyJustPressed(EKeys::Escape);
    Input.MapDrag = PC->IsInputKeyDown(EKeys::RightMouseButton);
    Input.MapPing = PC->WasInputKeyJustPressed(EKeys::LeftMouseButton);
    Input.MapPointOfInterest = PC->WasInputKeyJustPressed(EKeys::MiddleMouseButton);
    float CursorX = -1.0f, CursorY = -1.0f;
    if (PC->GetMousePosition(CursorX, CursorY)) Input.CursorPixels = FVector2D(CursorX, CursorY);
    Input.MouseX = MouseX; Input.MouseY = MouseY;
    ApplyManualInput(Input, DeltaSeconds);
    if (BeforeInput.IsValid() && Focused) WriteInputEvidence(BeforeInput);
}

void AImportLabGameMode::ApplyManualInput(const FManualControlInput& Input, double DeltaSeconds)
{
    APlayerController* PC = GetWorld()->GetFirstPlayerController();
    if (!PC || !Rig || !ChaseCamera || !SightCamera) return;
    if (!Input.Focused)
    {
        bPrecisionDrive=false;
        bMapDragging=false;
        if(Rig->IsContactPhysics()&&!bRosControl)Rig->SetContactDrive(0,0,true);
        if(RosBridge)RosBridge->SetOperatorBrake(false);
        if(Training&&!bRosAimControl)if(auto* Aim=GetAimCapture())Training->Advance(FMath::Clamp(DeltaSeconds,0.,.25),false,false,Aim->GetComponentTransform());
        bHadInputFocus = false;
        SpeedCmPerSecond = bRosControl?Rig->GetCommandOriginVelocity().Size2D():0.; TurnDegreesPerSecond = 0.0;
        UpdatePlayerView(PC);
        return;
    }
    const double Step = FMath::Clamp(DeltaSeconds, 0.0, 0.05);
    const bool AllowTrigger=bHadInputFocus; // Ignore recapture clicks and stale key edges.
    double MouseX = Input.MouseX, MouseY = Input.MouseY;
    if (!bHadInputFocus || !FMath::IsFinite(MouseX) || !FMath::IsFinite(MouseY)) MouseX = MouseY = 0.0;
    bHadInputFocus = true;
    int32 CameraWheelSteps = Input.WheelSteps;
    bool MapConsumesMouse = false;
    UpdateManualMapInput(Input, AllowTrigger, CameraWheelSteps, MapConsumesMouse);
    if (MapConsumesMouse) MouseX = MouseY = 0.0;
    if(Input.RestartRound&&AllowTrigger&&Rig->IsContactPhysics())
    {
        ResetContactVehicle(TEXT("manual_R"),bRosAimControl);
        MouseX = MouseY = 0.0;
    }
    bPrecisionDrive=Input.Precision&&Rig->HasSteeringModules();

    auto ActualAimDirection = [this]()
    {
        const auto* Aim = GetAimCapture();
        return (Aim ? Aim->GetForwardVector() : Rig->GetActorForwardVector()).GetSafeNormal();
    };
    auto HoldActualDirection = [&]()
    {
        HeldManualAimDirection = ActualAimDirection();
        bHoldManualAimDirection = true;
    };
    auto RecenterView = [this](const FVector& Direction)
    {
        FRotator Look = Direction.Rotation();
        if (!bSightMode)
        {
            const auto* Aim = GetAimCapture();
            const FVector Muzzle = Aim ? Aim->GetComponentLocation() : Rig->GetActorLocation();
            const FVector Target = TraceViewPoint(Muzzle, Direction);
            const FVector Pivot = Rig->GetActorLocation() + FVector(0, 0, ChasePivotHeightCm);
            Look = (Target - Pivot).Rotation();
        }
        OrbitYaw = FRotator::NormalizeAxis(Look.Yaw);
        OrbitPitch = FMath::Clamp(Look.Pitch, -60.0, 45.0);
    };
    const bool ReleasedFreeLook = !Input.FreeLook && bFreeLook;
    if (Input.FreeLook && !bFreeLook)
    {
        // Stop an unfinished previous aim slew at the actual gun direction.
        HoldActualDirection();
    }
    bFreeLook = Input.FreeLook;
    if (Input.ToggleSight && AllowTrigger)
    {
        bSightMode = !bSightMode;
        if (!bFreeLook)
        {
            HoldActualDirection();
            RecenterView(HeldManualAimDirection);
            // Scope intent is angular and is evaluated every frame, including zero mouse input.
            if (bSightMode) bHoldManualAimDirection = false;
        }
    }
    if (ReleasedFreeLook)
    {
        // C+Shift chooses the return camera without consuming the orbit angle as aim.
        RecenterView(HeldManualAimDirection);
        MouseX = MouseY = 0.0;
    }
    bSightZoom = bSightMode && Input.Zoom && !MapConsumesMouse;
    const double Sensitivity = MouseSensitivity * (bSightMode && !bFreeLook ? (bSightZoom ? 15.0 : ScopeBaseFov) / 55.0 : 1.0);
    OrbitYaw = FRotator::NormalizeAxis(OrbitYaw + MouseX * Sensitivity);
    OrbitPitch = FMath::Clamp(OrbitPitch + MouseY * Sensitivity, -60.0, 45.0);
    if (!bFreeLook && (!FMath::IsNearlyZero(MouseX) || !FMath::IsNearlyZero(MouseY)))
        bHoldManualAimDirection = false;
    if (bSightMode && !bFreeLook)
        ScopeBaseFov = FMath::Clamp(ScopeBaseFov - CameraWheelSteps * 3.0, 15.0, 30.0);
    else ChaseDistanceCm = FMath::Clamp(ChaseDistanceCm - CameraWheelSteps * 35.0, 180.0, 800.0);

    if(Rig->IsContactPhysics()&&AllowTrigger&&(!bRosControl||(RosBridge&&RosBridge->SupportsOperatorGyro())))
    {
        if(Input.ToggleGyro&&Rig->HasSteeringModules())bGyroMode=!bGyroMode;
        GyroSpeedRadS=FMath::Clamp(GyroSpeedRadS+Input.GyroSpeedSteps*Rig->GetDriveParameter(TEXT("GyroStepRadS")),
            Rig->GetDriveParameter(TEXT("GyroMinRadS")),Rig->GetDriveParameter(TEXT("GyroMaxRadS")));
    }
    const double Throttle = FMath::Clamp(Input.Throttle, -1.0, 1.0);
    const bool Brake = Input.Brake;
    if(RosBridge)
    {
        RosBridge->SetOperatorBrake(Brake);
        RosBridge->SetOperatorGyro(bGyroMode?GyroSpeedRadS:0.0);
    }
    const double TargetSpeed = Brake ? 0.0 : (Throttle > 0.0 ? 300.0 : Throttle < 0.0 ? -150.0 : 0.0);
    const bool Slowing = TargetSpeed == 0.0 || SpeedCmPerSecond * TargetSpeed < 0.0;
    SpeedCmPerSecond = FMath::FInterpConstantTo(SpeedCmPerSecond, TargetSpeed, Step, Brake ? 900.0 : Slowing ? 300.0 : 180.0);
    TurnDegreesPerSecond = FMath::FInterpConstantTo(TurnDegreesPerSecond,
        Brake ? 0.0 : FMath::Clamp(Input.Steer, -1.0, 1.0) * 60.0, Step, 180.0);
    const FTransform BeforeMove = Rig->GetActorTransform();
    if(!Rig->IsContactPhysics() && !Rig->MoveManual(FVector(SpeedCmPerSecond * Step, 0, 0), TurnDegreesPerSecond * Step) && Step > 0.0)
    {
        SpeedCmPerSecond = FVector::DotProduct(Rig->GetActorLocation() - BeforeMove.GetLocation(), BeforeMove.GetRotation().GetForwardVector()) / Step;
        TurnDegreesPerSecond = FMath::FindDeltaAngleDegrees(BeforeMove.Rotator().Yaw, Rig->GetActorRotation().Yaw) / Step;
    }

    UpdatePlayerView(PC);
    if (USceneCaptureComponent2D* Aim = GetAimCapture())
    {
        if (!bRosControl && !bRosAimControl)
        {
            FVector DesiredWorldDirection;
            if (bControlProbeActive && bProbeAimWorldTargetOverride && bAimTargetValid)
            {
                DesiredWorldDirection = (AimWorldTarget - Aim->GetComponentLocation()).GetSafeNormal();
            }
            else if (bFreeLook || bHoldManualAimDirection)
            {
                DesiredWorldDirection = HeldManualAimDirection;
                AimWorldTarget = TraceViewPoint(Aim->GetComponentLocation(), DesiredWorldDirection);
                bAimTargetValid = true;
            }
            else if (bSightMode)
            {
                DesiredWorldDirection = FRotator(OrbitPitch, OrbitYaw, 0).Vector();
                AimWorldTarget = TraceViewPoint(Aim->GetComponentLocation(), DesiredWorldDirection);
                bAimTargetValid = true;
            }
            else
            {
                const FVector ViewDirection = ChaseCamera->GetActorForwardVector().GetSafeNormal();
                const FVector Muzzle = Aim->GetComponentLocation();
                AimWorldTarget = TraceViewPoint(ChaseCamera->GetActorLocation(), ViewDirection);
                const FVector ToTarget = AimWorldTarget - Muzzle;
                const FVector2D ViewPlanar(ViewDirection.X, ViewDirection.Y);
                const FVector2D TargetPlanar(ToTarget.X, ToTarget.Y);
                // A chase-camera occluder can sit behind the muzzle. Converging
                // on it turns yaw by 180 degrees and reverses gun-relative WASD.
                // Check planar depth too: a downward ray can have positive 3D
                // depth while its horizontal target still lies behind the gun.
                const bool TargetIsForward = FVector::DotProduct(ToTarget, ViewDirection) > KINDA_SMALL_NUMBER &&
                    (ViewPlanar.IsNearlyZero() || FVector2D::DotProduct(TargetPlanar, ViewPlanar) > KINDA_SMALL_NUMBER);
                DesiredWorldDirection = TargetIsForward ? ToTarget.GetSafeNormal() : ViewDirection;
                if (!TargetIsForward)
                    AimWorldTarget = TraceViewPoint(Muzzle, DesiredWorldDirection);
                bAimTargetValid = true;
            }
            const FRotator DesiredLocal = Rig->GetActorTransform().InverseTransformVectorNoScale(DesiredWorldDirection).Rotation();
            const double DesiredYaw = -FMath::DegreesToRadians(DesiredLocal.Yaw);
            const double DesiredPitch = -FMath::DegreesToRadians(DesiredLocal.Pitch);
            double CurrentYaw, CurrentPitch;
            Rig->GetGimbalAngles(CurrentYaw, CurrentPitch);
            // Preview turn-rate limits, not measured actuator parameters.
            const double YawBudget = FMath::DegreesToRadians(GimbalYawSpeedDeg) * Step;
            const double PitchBudget = FMath::DegreesToRadians(GimbalPitchSpeedDeg) * Step;
            Rig->NudgeGimbal(
                FMath::Clamp(FMath::FindDeltaAngleRadians(CurrentYaw, DesiredYaw), -YawBudget, YawBudget),
                FMath::Clamp(DesiredPitch - CurrentPitch, -PitchBudget, PitchBudget));
        }
        if (Training && !bRosAimControl)
            Training->Advance(FMath::Clamp(DeltaSeconds,0.,.25),Input.Fire&&AllowTrigger&&!bRosControl&&!MapConsumesMouse,
                Input.RestartRound&&AllowTrigger,Aim->GetComponentTransform());
    }

    if (Rig->IsContactPhysics())
    {
        if (!bRosControl)
        {
            const double MaximumDriveSpeed=Rig->GetDriveParameter(Throttle>=0?TEXT("MaxForwardSpeedMps"):TEXT("MaxReverseSpeedMps"));
            const double DriveSpeed=Throttle*(bPrecisionDrive?FMath::Min(MaximumDriveSpeed,Rig->GetDriveParameter(TEXT("PrecisionSpeedMps"))):MaximumDriveSpeed);
            if (Rig->HasSteeringModules())
            {
                // Sample after this frame's gimbal update: driving and both map arrows share actual heading.
                const FVector2D Forward=GetGimbalPlanarForwardRos();
                const FVector2D Left(-Forward.Y,Forward.X);
                const double LateralSpeed=bPrecisionDrive?FMath::Min(Rig->GetDriveParameter(TEXT("MaxLateralSpeedMps")),Rig->GetDriveParameter(TEXT("PrecisionSpeedMps"))):Rig->GetDriveParameter(TEXT("MaxLateralSpeedMps"));
                FVector2D WorldVelocity=Forward*DriveSpeed+Left*FMath::Clamp(Input.Strafe,-1.,1.)*LateralSpeed;
                if(bPrecisionDrive&&WorldVelocity.Size()>Rig->GetDriveParameter(TEXT("PrecisionSpeedMps")))WorldVelocity=WorldVelocity.GetSafeNormal()*Rig->GetDriveParameter(TEXT("PrecisionSpeedMps"));
                const double YawRate=bGyroMode?GyroSpeedRadS:-FMath::Clamp(Input.Turn,-1.,1.)*Rig->GetDriveParameter(TEXT("ManualYawRateRadS"));
                Rig->SetContactPlanarDrive(WorldVelocity,YawRate,true,Brake);
            }
            else Rig->SetContactDrive(DriveSpeed,-FMath::Clamp(Input.Steer,-1.,1.)*Rig->GetDriveParameter(TEXT("ManualYawRateRadS")),Brake);
        }
        SpeedCmPerSecond=Rig->HasSteeringModules()?Rig->GetCommandOriginVelocity().Size2D():FVector::DotProduct(Rig->GetVelocity(),Rig->GetActorForwardVector());
    }
    UpdatePlayerView(PC);
}

TSharedRef<FJsonObject> AImportLabGameMode::ManualControlState() const
{
    TSharedRef<FJsonObject> State = MakeShared<FJsonObject>();
    State->SetNumberField(TEXT("frame_interval_s"),GetWorld()->GetDeltaSeconds());
    State->SetNumberField(TEXT("average_frame_interval_s"),FrameAverageSeconds);
    if(Training)State->SetObjectField(TEXT("training"),Training->State());
    if (Rig)
    {
        State->SetNumberField(TEXT("contact_recovery_count"),ContactRecoveryCount);
        State->SetBoolField(TEXT("contact_physics"),Rig->IsContactPhysics());
        State->SetBoolField(TEXT("blocking_overlap_query_valid"),!Rig->IsContactPhysics());
        if(Rig->IsContactPhysics())State->SetObjectField(TEXT("contact"),Rig->GetContactState());
        State->SetObjectField(TEXT("actor"), TransformJson(Rig->GetActorTransform()));
        double Yaw, Pitch; Rig->GetGimbalAngles(Yaw, Pitch);
        State->SetNumberField(TEXT("gimbal_yaw_rad"), Yaw);
        State->SetNumberField(TEXT("gimbal_pitch_rad"), Pitch);
        if (const auto* Aim = GetAimCapture())
            State->SetArrayField(TEXT("gimbal_world_forward"), VectorJson(Aim->GetForwardVector()));
        const FVector2D DriveForward = GetGimbalPlanarForwardRos();
        State->SetArrayField(TEXT("drive_forward_ros"), {
            MakeShared<FJsonValueNumber>(DriveForward.X), MakeShared<FJsonValueNumber>(DriveForward.Y)});
        const FManualRigState Actual=Rig->GetManualState();
        State->SetBoolField(TEXT("blocking_overlap"),Actual.bBlockingOverlap);
        State->SetBoolField(TEXT("terrain_following"),Actual.bTerrainFollowing);
        State->SetArrayField(TEXT("ground_normal"),VectorJson(Actual.GroundNormal));
        State->SetStringField(TEXT("terrain_block_reason"),Actual.TerrainBlockReason);
        TArray<TSharedPtr<FJsonValue>> Contacts;
        for (const FVector& Point : Actual.GroundContacts) Contacts.Add(MakeShared<FJsonValueArray>(VectorJson(Point)));
        State->SetArrayField(TEXT("ground_contacts_cm"),Contacts);
    }
    State->SetNumberField(TEXT("speed_cm_per_second"), SpeedCmPerSecond);
    State->SetNumberField(TEXT("view_yaw_deg"), OrbitYaw);
    State->SetNumberField(TEXT("view_pitch_deg"), OrbitPitch);
    State->SetNumberField(TEXT("chase_distance_cm"), ChaseDistanceCm);
    State->SetNumberField(TEXT("chase_visible_distance_cm"), ChaseVisibleDistanceCm);
    State->SetNumberField(TEXT("scope_base_fov_deg"), ScopeBaseFov);
    State->SetNumberField(TEXT("mouse_degrees_per_raw_count"), MouseSensitivity);
    if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
        if (ACameraActor* View = Cast<ACameraActor>(PC->GetViewTarget()))
            State->SetNumberField(TEXT("active_fov_deg"), View->GetCameraComponent()->FieldOfView);
    State->SetArrayField(TEXT("world_aim_target_cm"), VectorJson(AimWorldTarget));
    State->SetBoolField(TEXT("free_look"), bFreeLook);
    State->SetBoolField(TEXT("hold_manual_aim_direction"), bHoldManualAimDirection);
    State->SetArrayField(TEXT("held_aim_world_direction"), VectorJson(HeldManualAimDirection));
    State->SetBoolField(TEXT("probe_world_aim_override"), bControlProbeActive && bProbeAimWorldTargetOverride);
    State->SetBoolField(TEXT("sight_mode"), bSightMode);
    State->SetBoolField(TEXT("zoom"), bSightZoom);
    State->SetBoolField(TEXT("gyro_mode"),bGyroMode);
    State->SetBoolField(TEXT("precision_drive"),bPrecisionDrive);
    State->SetNumberField(TEXT("gyro_setpoint_rad_s"),GyroSpeedRadS);
    State->SetBoolField(TEXT("window_focused"), bHadInputFocus);
    State->SetBoolField(TEXT("map_expanded"), bTacticalMapExpanded);
    State->SetBoolField(TEXT("map_cursor_active"), bMapCursorActive);
    State->SetNumberField(TEXT("minimap_zoom_level"), MiniMapZoomLevel);
    State->SetNumberField(TEXT("full_map_zoom_level"), FullMapZoomLevel);
    State->SetArrayField(TEXT("full_map_center_uv"), {
        MakeShared<FJsonValueNumber>(FullMapCenterUv.X), MakeShared<FJsonValueNumber>(FullMapCenterUv.Y)});
    if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
        if (AActor* View = PC->GetViewTarget()) State->SetObjectField(TEXT("camera"), TransformJson(View->GetActorTransform()));
    return State;
}

void AImportLabGameMode::WriteInputEvidence(const TSharedPtr<FJsonObject>& Before)
{
    if (InputEvidencePath.IsEmpty()) return;
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>(), Counts = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("scope"), TEXT("focused_game_supported_key_edges_only"));
    Report->SetStringField(TEXT("updated_utc"), FDateTime::UtcNow().ToIso8601());
    for (const auto& Count : InputEventCounts) Counts->SetNumberField(Count.Key, Count.Value);
    Report->SetObjectField(TEXT("event_counts"), Counts);
    Report->SetObjectField(TEXT("before_last_event"), Before);
    Report->SetObjectField(TEXT("after_last_event"), ManualControlState());
    FString Json;
    const FString Directory = FPaths::GetPath(InputEvidencePath);
    if ((!Directory.IsEmpty() && !IFileManager::Get().MakeDirectory(*Directory, true)) ||
        !FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Json)) ||
        !FFileHelper::SaveStringToFile(Json, *InputEvidencePath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
        UE_LOG(LogRMSim, Warning, TEXT("Cannot write input evidence: %s"), *InputEvidencePath);
}

FString AImportLabGameMode::GetControlHudText() const
{
    const FString Mode = bFreeLook ? TEXT("FREE LOOK") : bSightMode ?
        FString::Printf(TEXT("SIGHT %.0f deg"), bSightZoom ? 15.0 : ScopeBaseFov) : TEXT("CHASE 55 deg");
    const FString Region = Rig ? FString::Join(StageMap.RegionsAt(Rig->GetActorLocation()), TEXT(", ")) : FString();
    const auto* Viewport=GEngine?Cast<URMSimViewportClient>(GEngine->GameViewport):nullptr;
    const double DrawHz=Viewport?Viewport->GetMeasuredSceneHz():0.;
    const FString DrawFps=DrawHz>0.?FString::Printf(TEXT("%.0f"),DrawHz):TEXT("--");
    const FString BlockReason=Rig?Rig->GetTerrainBlockReason():FString();
    const FString Gyro=Rig&&Rig->IsContactPhysics()?FString::Printf(TEXT("\nG gyro %s   Z/X speed   set %.1f / actual %.1f rad/s%s"),
        bGyroMode?TEXT("ON"):TEXT("OFF"),GyroSpeedRadS,Rig->GetMeasuredYawRate(),
        TEXT("  |  WASD turret-relative move")):FString();
    return FString::Printf(TEXT("%.1f km/h  |  %s%s  |  %s%s  |  scene %s fps / world %.0f Hz\nW/S turret forward/back   A/D strafe   Q/E turn   Ctrl slow   SPACE brake   R reset\nMouse aim   Hold C orbit view   Shift sight   RMB zoom   Wheel camera"),
        SpeedCmPerSecond * 0.036, *Mode, bHadInputFocus ? TEXT("") : TEXT("  |  Click to focus"),
        *StageMap.DisplayName, Region.IsEmpty() ? TEXT("") : *(TEXT(" / ")+Region),*DrawFps,FrameAverageSeconds>1.e-6?1./FrameAverageSeconds:0.) + (Training?TEXT("\n")+Training->Hud():FString()) +
        Gyro+(bPrecisionDrive?TEXT("\nPRECISION drive"):FString())+(RosBridge?TEXT("\n")+RosBridge->StatusText():FString())+
        (bRosControl?TEXT("\nROS translation / G gyro / Space operator brake / R reset"):FString())+
        (bRosAimControl?TEXT("\nROS owns aim/fire; mouse observes; Space inhibit / R reset"):FString())+
        (BlockReason.IsEmpty()?FString():TEXT("\nBlocked: ")+BlockReason);
}

bool AImportLabGameMode::GetGunWorldPoint(FVector& OutPoint) const
{
    if (USceneCaptureComponent2D* Aim = GetAimCapture())
    {
        OutPoint = Training?Training->PredictImpact(Aim->GetComponentTransform()):TraceViewPoint(Aim->GetComponentLocation(), Aim->GetForwardVector());
        return true;
    }
    return false;
}

void AImportLabHUD::DrawHUD()
{
    Super::DrawHUD();
    if (FParse::Param(FCommandLine::Get(), TEXT("GymReviewCamera"))) return;
    const AImportLabGameMode* Mode = GetWorld()->GetAuthGameMode<AImportLabGameMode>();
    if (!Canvas || !Mode || !Mode->IsControlHudVisible()) return;
    if (!MapOverview) MapOverview = NewObject<UMapOverview>(this);
    FMapOverviewFrame MapFrame;
    if (Mode->GetMapOverviewFrame(MapFrame) && MapOverview->Draw(this, Canvas, MapFrame)) return;
    DrawRect(FLinearColor(0, 0, 0, 0.55f), 16, 16, FMath::Min(900.0f, Canvas->SizeX - 32.0f), 112);
    DrawText(Mode->GetControlHudText(), FLinearColor::White, 26, 24, GEngine ? GEngine->GetMediumFont() : nullptr, 1.0f);
    const float X = Canvas->SizeX * 0.5f, Y = Canvas->SizeY * 0.5f;
    DrawLine(X - 12, Y, X - 4, Y, FLinearColor::White, 1.5f); DrawLine(X + 4, Y, X + 12, Y, FLinearColor::White, 1.5f);
    DrawLine(X, Y - 12, X, Y - 4, FLinearColor::White, 1.5f); DrawLine(X, Y + 4, X, Y + 12, FLinearColor::White, 1.5f);
    FVector GunPoint; FVector2D Screen;
    if (PlayerOwner && Mode->IsGunReticleVisible() && Mode->GetGunWorldPoint(GunPoint) && PlayerOwner->ProjectWorldLocationToScreen(GunPoint, Screen, true) &&
        Screen.X >= 0 && Screen.X <= Canvas->SizeX && Screen.Y >= 0 && Screen.Y <= Canvas->SizeY)
    {
        const FLinearColor GunColor(1.0f, 0.65f, 0.1f);
        DrawLine(Screen.X - 4, Screen.Y - 4, Screen.X + 4, Screen.Y - 4, GunColor, 2);
        DrawLine(Screen.X + 4, Screen.Y - 4, Screen.X + 4, Screen.Y + 4, GunColor, 2);
        DrawLine(Screen.X + 4, Screen.Y + 4, Screen.X - 4, Screen.Y + 4, GunColor, 2);
        DrawLine(Screen.X - 4, Screen.Y + 4, Screen.X - 4, Screen.Y - 4, GunColor, 2);
    }
}

bool AImportLabGameMode::CapturePngs(const FString& Phase)
{
    const FString Directory = Phase.IsEmpty() ? CaptureDirectory : FPaths::Combine(CaptureDirectory, Phase);
    if (!IFileManager::Get().MakeDirectory(*Directory, true)) return false;
    TMap<FString, USceneCaptureComponent2D*> Captures = Rig->GetCameras();
    Captures.Add(TEXT("overview"), OverviewCapture);
    for (const auto& Pair : Captures)
    {
        const bool bContinuousPreview = Pair.Value->bCaptureEveryFrame;
        Pair.Value->bCaptureEveryFrame = false;
        Pair.Value->CaptureScene();
        FlushRenderingCommands();
        Pair.Value->bCaptureEveryFrame = bContinuousPreview;
        FImage CapturedImage;
        TArray64<uint8> Buffer;
        const FString Filename = FPaths::Combine(Directory, FPaths::MakeValidFileName(Pair.Key) + TEXT(".png"));
        // Save the compressed byte count. UE 5.8's deprecated export helper
        // serializes allocation capacity and can append bytes after PNG IEND.
        if (!FImageUtils::GetRenderTargetImage(Pair.Value->TextureTarget, CapturedImage) ||
            !FImageUtils::CompressImage(Buffer, TEXT("PNG"), CapturedImage) ||
            !FFileHelper::SaveArrayToFile(Buffer, *Filename)) return false;
        UE_LOG(LogRMSim, Display, TEXT("CAPTURE_WRITTEN %s"), *Filename);
        if (bProbeActive)
        {
            TSharedRef<FJsonObject> Capture = MakeShared<FJsonObject>();
            Capture->SetStringField(TEXT("phase"), Phase);
            Capture->SetStringField(TEXT("camera"), Pair.Key);
            Capture->SetStringField(TEXT("path"), Filename);
            Capture->SetNumberField(TEXT("width"), Pair.Value->TextureTarget->SizeX);
            Capture->SetNumberField(TEXT("height"), Pair.Value->TextureTarget->SizeY);
            ProbeCaptures.Add(MakeShared<FJsonValueObject>(Capture));
        }
    }
    return true;
}

bool AImportLabGameMode::WriteProbeReport(const FString& Status, const FString& Reason)
{
    if (!ProbeReport.IsValid() || EvidenceDirectory.IsEmpty()) return false;
    ProbeReport->SetStringField(TEXT("status"), Status);
    ProbeReport->SetStringField(TEXT("updated_utc"), FDateTime::UtcNow().ToIso8601());
    ProbeReport->SetBoolField(TEXT("complete"), Status != TEXT("running"));
    ProbeReport->SetBoolField(TEXT("kinematic_probe_passed"), Status == TEXT("passed"));
    ProbeReport->SetArrayField(TEXT("checks"), ProbeChecks);
    ProbeReport->SetArrayField(TEXT("captures"), ProbeCaptures);
    if (!Reason.IsEmpty()) ProbeReport->SetStringField(TEXT("failure_reason"), Reason);
    FString Json;
    if (!IFileManager::Get().MakeDirectory(*EvidenceDirectory, true) ||
        !FJsonSerializer::Serialize(ProbeReport.ToSharedRef(), TJsonWriterFactory<>::Create(&Json))) return false;
    return FFileHelper::SaveStringToFile(Json, *FPaths::Combine(EvidenceDirectory, TEXT("probe-result.json")),
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
}

bool AImportLabGameMode::RecordProbe(const FString& Name, const FManualRigState& Before,
    const FManualRigState& After, bool bMoveAccepted, bool bPassed, const FString& Detail,
    const FVector& RequestedWorldCm, double RequestedYawDegrees)
{
    TSharedRef<FJsonObject> Check = MakeShared<FJsonObject>();
    Check->SetStringField(TEXT("name"), Name);
    Check->SetNumberField(TEXT("sequence"), ProbePhase);
    Check->SetBoolField(TEXT("call_accepted"), bMoveAccepted);
    Check->SetBoolField(TEXT("passed"), bPassed);
    Check->SetStringField(TEXT("detail"), Detail);
    Check->SetArrayField(TEXT("requested_world_translation_cm"), VectorJson(RequestedWorldCm));
    Check->SetNumberField(TEXT("requested_yaw_degrees"), RequestedYawDegrees);
    Check->SetObjectField(TEXT("before"), StateJson(Before));
    Check->SetObjectField(TEXT("after"), StateJson(After));
    ProbeChecks.Add(MakeShared<FJsonValueObject>(Check));
    UE_LOG(LogRMSim, Display, TEXT("MANUAL_PROBE_CHECK %s %s"), *Name, bPassed ? TEXT("passed") : TEXT("failed"));
    if (!bPassed) { Abort(Name + TEXT(": ") + Detail); return false; }
    if (!WriteProbeReport(TEXT("running"))) { Abort(TEXT("cannot update probe result")); return false; }
    return true;
}

void AImportLabGameMode::TickProbe()
{
    if (!PendingProbeCapture.IsEmpty())
    {
        // Continuous preview captures retain temporal history. Hold the state for warm-up frames;
        // this is image QA preparation, not a sensor publication-rate implementation.
        if (++ProbeWarmFrames < 16) return;
        if (!CapturePngs(PendingProbeCapture)) { Abort(TEXT("probe PNG capture/write failed")); return; }
        PendingProbeCapture.Reset();
        ProbeWarmFrames = 0;
        if (!WriteProbeReport(TEXT("running"))) Abort(TEXT("cannot record probe captures"));
        return;
    }
    if (!Rig || !ProbeObstacle) { Abort(TEXT("probe rig/obstacle unavailable")); return; }
    const FManualRigState Before = Rig->GetManualState();
    const FBox ObstacleBounds = ProbeObstacle->GetComponentsBoundingBox(true, false);
    auto MoveWorld = [this, &Before](const FVector& Delta, double Yaw = 0.0)
    {
        return Rig->MoveManual(Before.Actor.InverseTransformVectorNoScale(Delta), Yaw);
    };
    auto Clear = [&ObstacleBounds](const FManualRigState& State)
    {
        return !State.bBlockingOverlap && !State.WorldBounds.Intersect(ObstacleBounds);
    };
    if (ProbePhase == 0)
    {
        const bool Valid = Before.WorldBounds.IsValid && Before.WorldBounds.GetSize().GetMin() > 0.0 &&
            !Before.Actor.ContainsNaN() && Clear(Before) && !Before.CameraWorld.IsEmpty() &&
            !Before.WheelNames.IsEmpty() && Before.JointLocal.Contains(Before.YawName) && Before.JointLocal.Contains(Before.PitchName);
        ProbeReport->SetArrayField(TEXT("obstacle_bounds_min_cm"), VectorJson(ObstacleBounds.Min));
        ProbeReport->SetArrayField(TEXT("obstacle_bounds_max_cm"), VectorJson(ObstacleBounds.Max));
        ProbeReport->SetStringField(TEXT("obstacle_actor"), ProbeObstacle->GetName());
        if (!RecordProbe(TEXT("initial_state"), Before, Before, true, Valid,
            TEXT("Require finite live geometry, no initial overlap, camera, mapped yaw/pitch and explicit wheel visuals"))) return;
        PendingProbeCapture = TEXT("initial");
    }
    else if (ProbePhase == 1)
    {
        // Align with the actual obstacle centre; driving on the original y=0 line misses it.
        const FVector Delta(0.0, ObstacleBounds.GetCenter().Y - Before.WorldBounds.GetCenter().Y, 0.0);
        const bool Accepted = MoveWorld(Delta);
        const FManualRigState After = Rig->GetManualState();
        const bool Passed = Accepted && Clear(After) &&
            (After.Actor.GetLocation() - Before.Actor.GetLocation()).Equals(Delta, 0.05);
        if (!RecordProbe(TEXT("align_with_obstacle"), Before, After, Accepted, Passed,
            TEXT("Measured actor displacement must match requested lateral alignment within 0.05 cm"), Delta)) return;
    }
    else if (ProbePhase == 2)
    {
        const double Gap = ObstacleBounds.Min.X - Before.WorldBounds.Max.X;
        const FVector Delta(Gap * 0.25, 0.0, 0.0);
        const bool Accepted = Gap > 0.0 && MoveWorld(Delta);
        const FManualRigState After = Rig->GetManualState();
        const bool Passed = Accepted && Clear(After) &&
            (After.Actor.GetLocation() - Before.Actor.GetLocation()).Equals(Delta, 0.05) && !WheelsUnchanged(Before, After);
        if (!RecordProbe(TEXT("free_translation"), Before, After, Accepted, Passed,
            TEXT("Move through one quarter of actual clearance; verify actual translation and wheel component rotation"), Delta)) return;
    }
    else if (ProbePhase == 3)
    {
        const FVector Delta(ObstacleBounds.Min.X - Before.WorldBounds.Max.X +
            ObstacleBounds.GetSize().X + Before.WorldBounds.GetSize().X, 0.0, 0.0);
        const bool Accepted = MoveWorld(Delta);
        const FManualRigState After = Rig->GetManualState();
        const double RemainingGap = ObstacleBounds.Min.X - After.WorldBounds.Max.X;
        const bool Passed = !Accepted && Clear(After) && After.bLastSweepHit &&
            After.LastBlockingActor == ProbeObstacle->GetName() &&
            After.Actor.GetLocation().X > Before.Actor.GetLocation().X && RemainingGap >= -0.01 && RemainingGap <= 0.5;
        if (!RecordProbe(TEXT("long_sweep_blocks"), Before, After, Accepted, Passed,
            FString::Printf(TEXT("Actual hit must name stage obstacle; observed front gap %.6f cm must be 0..0.5 cm"), RemainingGap), Delta)) return;
        PendingProbeCapture = TEXT("blocked");
    }
    else if (ProbePhase == 4)
    {
        const FVector Delta(ObstacleBounds.GetSize().X, 0.0, 0.0);
        const bool Accepted = MoveWorld(Delta);
        const FManualRigState After = Rig->GetManualState();
        const bool Passed = !Accepted && Clear(After) && Before.Actor.Equals(After.Actor, 0.01) && WheelsUnchanged(Before, After);
        if (!RecordProbe(TEXT("blocked_motion_does_not_spin_wheels"), Before, After, Accepted, Passed,
            TEXT("A repeated forward request must leave measured actor and wheel component transforms unchanged"), Delta)) return;
    }
    else if (ProbePhase == 5)
    {
        const bool Accepted = MoveWorld(FVector::ZeroVector, 30.0);
        const FManualRigState After = Rig->GetManualState();
        const double Angle = FMath::RadiansToDegrees(Before.Actor.GetRotation().AngularDistance(After.Actor.GetRotation()));
        const bool Passed = !Accepted && Clear(After) && Angle < 29.9 &&
            Before.Actor.GetLocation().Equals(After.Actor.GetLocation(), 0.01);
        if (!RecordProbe(TEXT("rotation_blocked_near_wall"), Before, After, Accepted, Passed,
            FString::Printf(TEXT("Measured yaw %.6f deg must stop short of 30 deg with no obstacle overlap"), Angle), FVector::ZeroVector, 30.0)) return;
    }
    else if (ProbePhase == 6)
    {
        const FVector Delta(-Before.WorldBounds.GetSize().X * 1.5, 0.0, 0.0);
        const bool Accepted = MoveWorld(Delta);
        const FManualRigState After = Rig->GetManualState();
        const bool Passed = Accepted && Clear(After) &&
            (After.Actor.GetLocation() - Before.Actor.GetLocation()).Equals(Delta, 0.05);
        if (!RecordProbe(TEXT("reverse_away_from_wall"), Before, After, Accepted, Passed,
            TEXT("Retreat by 1.5 actual vehicle lengths; measured displacement must match within 0.05 cm"), Delta)) return;
    }
    else if (ProbePhase == 7)
    {
        const bool Accepted = MoveWorld(FVector::ZeroVector, 20.0);
        const FManualRigState After = Rig->GetManualState();
        const double Angle = FMath::RadiansToDegrees(Before.Actor.GetRotation().AngularDistance(After.Actor.GetRotation()));
        const bool Passed = Accepted && Clear(After) && FMath::IsNearlyEqual(Angle, 20.0, 0.01) &&
            Before.Actor.GetLocation().Equals(After.Actor.GetLocation(), 0.01) && !WheelsUnchanged(Before, After);
        if (!RecordProbe(TEXT("free_rotation"), Before, After, Accepted, Passed,
            FString::Printf(TEXT("Measured actor yaw %.6f deg and wheel transforms must change in free space"), Angle), FVector::ZeroVector, 20.0)) return;
    }
    else if (ProbePhase == 8)
    {
        Rig->NudgeGimbal(0.3, -0.2);
        const FManualRigState After = Rig->GetManualState();
        const FTransform *YawBefore = Before.JointLocal.Find(Before.YawName), *YawAfter = After.JointLocal.Find(After.YawName);
        const FTransform *PitchBefore = Before.JointLocal.Find(Before.PitchName), *PitchAfter = After.JointLocal.Find(After.PitchName);
        const double Yaw = YawBefore && YawAfter ? YawBefore->GetRotation().AngularDistance(YawAfter->GetRotation()) : -1.0;
        const double Pitch = PitchBefore && PitchAfter ? PitchBefore->GetRotation().AngularDistance(PitchAfter->GetRotation()) : -1.0;
        bool CameraChanged = false;
        for (const auto& Camera : Before.CameraWorld)
        {
            const FTransform* Current = After.CameraWorld.Find(Camera.Key);
            CameraChanged |= Current && Camera.Value.GetRotation().AngularDistance(Current->GetRotation()) > 0.05;
        }
        const bool Passed = Clear(After) && Before.Actor.Equals(After.Actor, 1.e-5) &&
            FMath::IsNearlyEqual(Yaw, 0.3, 1.e-4) && FMath::IsNearlyEqual(Pitch, 0.2, 1.e-4) && CameraChanged;
        if (!RecordProbe(TEXT("gimbal_and_camera_transform"), Before, After, true, Passed,
            FString::Printf(TEXT("Requested yaw +0.3/pitch -0.2 rad; measured local rotation magnitudes %.6f/%.6f; camera changed %s"),
                Yaw, Pitch, CameraChanged ? TEXT("yes") : TEXT("no")))) return;
        PendingProbeCapture = TEXT("gimbal");
    }
    else
    {
        if (ProbeChecks.Num() != 9 || ProbeCaptures.Num() != 3 * (Rig->GetCameras().Num() + 1))
        {
            Abort(TEXT("probe ended without all checks and three complete capture sets")); return;
        }
        if (!WriteProbeReport(TEXT("passed"))) { Abort(TEXT("cannot save final probe result")); return; }
        UE_LOG(LogRMSim, Display, TEXT("MANUAL_PROBE_PASSED %s; keyboard, appearance and dynamics remain separate acceptance"),
            *FPaths::Combine(EvidenceDirectory, TEXT("probe-result.json")));
        bReady = false;
        FPlatformMisc::RequestExitWithStatus(false, 0);
        return;
    }
    ++ProbePhase;
}
