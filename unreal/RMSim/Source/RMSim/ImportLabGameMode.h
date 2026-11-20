#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "MapStage.h"
#include "ImportLabGameMode.generated.h"

class AVehicleRig;
class ATrainingRange;
class ACameraActor;
class USceneCaptureComponent2D;
class AStaticMeshActor;
class FJsonObject;
class FJsonValue;
class URosBridgeComponent;
class UWTFoliageCrushComponent;
class UWTGeneratedCollisionComponent;
class UMapOverview;
struct FMapOverviewFrame;
struct FMapOverviewPresentation;
struct FMapUiProbeState;
struct FZeppelinContactProbeState;
struct FManualRigState;
struct FRailContactProbeState;
struct FCampusGymBoundaryProbeState;
struct FCameraHeadingProbeState;

// The live player-controller adapter and deterministic control probe share raw mouse counts.
struct FManualControlInput
{
    bool Focused = true;
    double Throttle = 0.0;
    double Steer = 0.0;
    double Strafe = 0.0;
    double Turn = 0.0;
    bool Precision = false;
    double MouseX = 0.0;
    double MouseY = 0.0;
    int32 WheelSteps = 0;
    bool MapModifier = false;
    bool ToggleMap = false;
    bool CloseMap = false;
    bool MapDrag = false;
    bool MapPing = false;
    bool MapPointOfInterest = false;
    FVector2D CursorPixels = FVector2D(-1, -1);
    bool Brake = false;
    bool FreeLook = false;
    bool ToggleSight = false;
    bool Zoom = false;
    bool Fire = false;
    bool RestartRound = false;
    bool ToggleGyro = false;
    int32 GyroSpeedSteps = 0;
};

UCLASS()
class RMSIM_API AImportLabGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    AImportLabGameMode();
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    FString GetControlHudText() const;
    bool GetGunWorldPoint(FVector& OutPoint) const;
    bool GetMapOverviewFrame(FMapOverviewFrame& OutFrame) const;
    bool IsControlHudVisible() const { return bReady && !bProbeActive; }
    bool IsGunReticleVisible() const { return bSightMode && !bFreeLook; }
private:
    UPROPERTY() AVehicleRig* Rig = nullptr;
    UPROPERTY() ATrainingRange* Training = nullptr;
    UPROPERTY() URosBridgeComponent* RosBridge=nullptr;
    UPROPERTY() UWTFoliageCrushComponent* SoftFoliage=nullptr;
    UPROPERTY() UWTGeneratedCollisionComponent* GeneratedCollision=nullptr;
    bool bRosBridgeEnabled=false,bRosControl=false,bRosAimControl=false;
    FString ImuFixture;
    double ImuFixtureElapsed=0;
    bool bImuFixtureApplied=false;
    void TickImuFixture(double Seconds);
    bool bTrainingMode=false,bCombatProbeActive=false;
    bool bContactMode=false,bContactProbeActive=false;
    FString ContactRigFile;
    double ContactPhaseTime=0;
    FTransform ContactStart;
    FTransform ContactStageStart;
    FTransform ContactSpawn;
    int32 ContactRecoveryCount=0;
    bool bGyroMode=false;
    bool bPrecisionDrive=false;
    double GyroSpeedRadS=2;
    bool bGyroProbeActive=false;
    double GyroAccumulatedAngle=0,GyroMinimumUp=1,GyroPeakCommandAcceleration=0;
    double SteeringTargetResponseSeconds=-1,SteeringActualResponseSeconds=-1,WheelCommandResponseSeconds=-1;
    double SteeringCommandPhaseSeconds=-1;
    double MovingCommandPhaseSeconds=-1,MovingTargetResponseSeconds=-1,MovingActualResponseSeconds=-1;
    double FrameAverageSeconds=0,LastFrameWallSeconds=0;
    double RealTimeStepSeconds=0,NextPhysicsFrameWall=-1;
    FDelegateHandle RealTimePaceHandle;
    void PacePhysicsFrame();
    FVector2D GyroPreviousFiltered=FVector2D::ZeroVector;
    bool bGyroPreviousValid=false;
    void TickGyroProbe(double DeltaSeconds);
    void TickCameraHeadingProbe(double DeltaSeconds);
    TSharedPtr<FCameraHeadingProbeState> CameraHeadingProbe;
    bool bTractionProbeActive=false;
    UPROPERTY() AStaticMeshActor* TractionSlope=nullptr;
    FTransform TractionPose;
    double TractionParallelDistanceCm=0;
    double TractionStationarySeconds=0;
    double TractionExternalPeakSpeedCm=0;
    void TickTractionProbe(double DeltaSeconds);
    void ResetContactVehicle(const FString& Reason,bool ResetTraining);
    void RecoverContactVehicleIfOutsideMap();
    UPROPERTY() AStaticMeshActor* ContactProbeWall=nullptr;
    TArray<TSharedPtr<FJsonValue>> ContactSamples;
    UPROPERTY() ACameraActor* Overview = nullptr;
    UPROPERTY() USceneCaptureComponent2D* OverviewCapture = nullptr;
    UPROPERTY() AStaticMeshActor* ProbeObstacle = nullptr;
    UPROPERTY() TMap<FString, AStaticMeshActor*> MapBoxes;
    FMapStage StageMap;
    FMapSpawn SelectedSpawn;
    FString MapReportFile;
    int32 SpawnValidationFrames = 0;
    double SpawnValidationElapsed = 0;
    bool bSpawnValidated = false;
    bool UpdateSceneRenderingReadiness();
    bool bSceneRenderReady = false;
    double SceneRenderStartSeconds = 0;
    int32 SceneRenderStableFrames = 0;
    int32 SceneStreamingStartId = INDEX_NONE;
    TSharedPtr<FJsonObject> SceneRenderReport;
    UPROPERTY() ACameraActor* ChaseCamera = nullptr;
    UPROPERTY() ACameraActor* SightCamera = nullptr;
    FString CaptureDirectory;
    double CaptureDelay = 3.0;
    double Elapsed = 0.0;
    bool bCaptured = false;
    bool bExitAfterCapture = false;
    bool bReady = false;
    bool bDestructionProbeStarted = false;
    double DestructionProbeSeconds = 0;
    bool bMapOverview = false;
    bool bTacticalMapExpanded = false;
    bool bMapCursorActive = false;
    bool bMapDragging = false;
    int32 MiniMapZoomLevel = 0;
    int32 FullMapZoomLevel = 0;
    FVector2D FullMapCenterUv = FVector2D(0.5, 0.5);
    FVector2D LastMapCursor = FVector2D::ZeroVector;
    FVector2D LocalMapPingRos = FVector2D::ZeroVector;
    FVector2D LocalMapPointRos = FVector2D::ZeroVector;
    double LocalMapPingTime = -1000.0;
    bool bLocalMapPointValid = false;
    void UpdateManualMapInput(const FManualControlInput& Input, bool AllowActions,
        int32& CameraWheelSteps, bool& ConsumeMouse);
    void FillMapInteractionFrame(FMapOverviewFrame& Frame) const;
    void TickMapUiProbe(double DeltaSeconds);
    TSharedPtr<FMapUiProbeState> MapUiProbe;
    void TickZeppelinContactProbe(double DeltaSeconds);
    TSharedPtr<FZeppelinContactProbeState> ZeppelinContactProbe;
    bool bProbeActive = false;
    int32 ProbePhase = 0;
    FString EvidenceDirectory;
    FString PendingProbeCapture;
    int32 ProbeWarmFrames = 0;
    double SpeedCmPerSecond = 0.0;
    double TurnDegreesPerSecond = 0.0;
    double OrbitYaw = 0.0;
    double OrbitPitch = -12.0;
    double ChaseDistanceCm = 500.0;
    double ChasePivotHeightCm = 120.0;
    double ScopeBaseFov = 30.0;
    double MouseSensitivity = 0.04; // Degrees per raw count; FOV scaling is applied exactly once.
    double GimbalYawSpeedDeg = 540.0;
    double GimbalPitchSpeedDeg = 360.0;
    bool bSightMode = false;
    bool bSightZoom = false;
    bool bFreeLook = false;
    bool bHadInputFocus = false;
    bool bAimTargetValid = false;
    // Manual observation holds a world direction, never a hit object's position.
    bool bHoldManualAimDirection = false;
    FVector HeldManualAimDirection = FVector::ForwardVector;
    // Only opt-in ballistic probes may supply a fixed world-space aim point.
    bool bProbeAimWorldTargetOverride = false;
    FVector AimWorldTarget = FVector::ZeroVector;
    FString InputEvidencePath;
    TMap<FString, int32> InputEventCounts;
    TSharedPtr<FJsonObject> ProbeReport;
    TArray<TSharedPtr<FJsonValue>> ProbeChecks;
    TArray<TSharedPtr<FJsonValue>> ProbeCaptures;
    bool bControlProbeActive = false;
    bool bTerrainProbeActive = false;
    int32 ControlProbePhase = 0;
    int32 ControlCaptureWarmFrames = 0;
    FString ControlEvidenceDirectory;
    FString ControlPendingCapture;
    TSharedPtr<FJsonObject> ControlReport;
    TArray<TSharedPtr<FJsonValue>> ControlChecks;
    bool CreateStage();
    void UpdateOverview();
    void TickManualControl(float DeltaSeconds);
    void ApplyManualInput(const FManualControlInput& Input, double DeltaSeconds);
    void UpdatePlayerView(APlayerController* PC);
    USceneCaptureComponent2D* GetAimCapture() const;
    FVector2D GetGimbalPlanarForwardRos() const;
    FVector TraceViewPoint(const FVector& Start, const FVector& Direction) const;
    TSharedRef<FJsonObject> ManualControlState() const;
    void WriteInputEvidence(const TSharedPtr<FJsonObject>& Before);
    bool CapturePngs(const FString& Phase = FString());
    void Abort(const FString& Reason);
    void TickProbe();
    void TickControlProbe();
    void TickTerrainProbe();
    void TickCombatProbe();
    void TickMujocoProbe(double DeltaSeconds);
    void TickContactProbe(double DeltaSeconds);
    void TickRailContactProbe(double DeltaSeconds);
    void TickCampusGymBoundaryProbe(double DeltaSeconds);
    TSharedPtr<FCampusGymBoundaryProbeState> CampusGymBoundaryProbe;
    TSharedPtr<FRailContactProbeState> RailContactProbe;
    void TickSuspensionProbe(double DeltaSeconds);
    bool RecordControlCheck(const FString& Name, bool Passed, const TSharedPtr<FJsonObject>& Before, const FString& Detail);
    bool WriteControlReport(const FString& Status, const FString& Reason = FString());
    bool CaptureControlView(const FString& Phase);
    bool WriteProbeReport(const FString& Status, const FString& Reason = FString());
    bool RecordProbe(const FString& Name, const FManualRigState& Before, const FManualRigState& After,
        bool bMoveAccepted, bool bPassed, const FString& Detail, const FVector& RequestedWorldCm = FVector::ZeroVector,
        double RequestedYawDegrees = 0.0);
};

UCLASS()
class RMSIM_API AImportLabHUD : public AHUD
{
    GENERATED_BODY()
public:
    virtual void DrawHUD() override;
    bool DescribeMap(const FMapOverviewFrame& Frame, const FVector2D& ViewportPixels,
        FMapOverviewPresentation& Out);
    bool MapScreenToWorld(const FMapOverviewFrame& Frame, const FVector2D& ViewportPixels,
        const FVector2D& Pixel, FVector2D& OutRos);
private:
    UPROPERTY() UMapOverview* MapOverview = nullptr;
};
