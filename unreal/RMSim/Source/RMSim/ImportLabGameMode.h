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
struct FManualRigState;

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
    FString GetControlHudText() const;
    bool GetGunWorldPoint(FVector& OutPoint) const;
    bool IsControlHudVisible() const { return bReady && !bProbeActive; }
private:
    UPROPERTY() AVehicleRig* Rig = nullptr;
    UPROPERTY() ATrainingRange* Training = nullptr;
    UPROPERTY() URosBridgeComponent* RosBridge=nullptr;
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
    FVector2D GyroPreviousFiltered=FVector2D::ZeroVector;
    bool bGyroPreviousValid=false;
    void TickGyroProbe(double DeltaSeconds);
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
    UPROPERTY() ACameraActor* ChaseCamera = nullptr;
    UPROPERTY() ACameraActor* SightCamera = nullptr;
    FString CaptureDirectory;
    double CaptureDelay = 3.0;
    double Elapsed = 0.0;
    bool bCaptured = false;
    bool bExitAfterCapture = false;
    bool bReady = false;
    bool bMapOverview = false;
    bool bProbeActive = false;
    int32 ProbePhase = 0;
    FString EvidenceDirectory;
    FString PendingProbeCapture;
    int32 ProbeWarmFrames = 0;
    double SpeedCmPerSecond = 0.0;
    double TurnDegreesPerSecond = 0.0;
    double OrbitYaw = 0.0;
    double OrbitPitch = -12.0;
    double SavedOrbitYaw = 0.0;
    double SavedOrbitPitch = -12.0;
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
    bool bHoldAimAfterFreeLook = false;
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
    FVector TraceViewPoint(const FVector& Start, const FVector& Direction) const;
    TSharedRef<FJsonObject> ManualControlState() const;
    void WriteInputEvidence(const TSharedPtr<FJsonObject>& Before);
    bool CapturePngs(const FString& Phase = FString());
    void Abort(const FString& Reason);
    void TickProbe();
    void TickControlProbe();
    void TickTerrainProbe();
    void TickCombatProbe();
    void TickContactProbe(double DeltaSeconds);
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
};
