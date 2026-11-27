#pragma once

#include "CoreMinimal.h"
#include "Kismet/KismetMathLibrary.h"
#include "Rhine3DHud.generated.h"

class SWidget;
class SRhine3DHudViewport;
class UTexture2D;

USTRUCT(BlueprintType)
struct RMSIM_API FRhine3DRobotUnitState
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    FString Code = TEXT("S7");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    int32 Hp = 400;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    int32 MaxHp = 400;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    bool bIsOwnUnit = false;
};

USTRUCT(BlueprintType)
struct RMSIM_API FRhine3DOutpostState
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    int32 Hp = 1000;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    int32 MaxHp = 1000;

    // Structure owner (flips when destroyed & rebuilt by opponent)
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    bool bOwnerRed = true;

    // Zone controller (flips when captured by opponent)
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    bool bControllerRed = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    bool bFullControl = true;

    // 0..1 capture / rebuild transition progress
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    float TransitionPct = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    FString PhaseLabel = TEXT("CONTROLLED");

    // Arena coordinates ([-14..14], [-8..8]) for minimap outpost tower center
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    float ArenaX = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    float ArenaY = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Outpost")
    bool bHasMapPos = false;

    TArray<FVector2f> ZonePolyArena;
    TArray<FVector2f> HighlandPolyArena;
};

USTRUCT(BlueprintType)
struct RMSIM_API FRhine3DBroadcastItem
{
    GENERATED_BODY()

    // 1 = RED, -1 = BLUE, 0 = SYSTEM
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Broadcast")
    int32 ActorTeam = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Broadcast")
    FString ActorVehicle = TEXT("SYSTEM");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Broadcast")
    FString ActionText;

    // 1 = RED, -1 = BLUE, 0 = NEUTRAL
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Broadcast")
    int32 TargetTeam = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Broadcast")
    FString TargetName;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Broadcast")
    FString TimeStr;
};

USTRUCT(BlueprintType)
struct RMSIM_API FRhine3DMapPin
{
    GENERATED_BODY()

    // Arena coordinates in meters: X in [-14, 14] (Red left -X, Blue right +X), Y in [-8, 8]
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    float ArenaX = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    float ArenaY = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    bool bIsRed = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    bool bIsSelf = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    float HeadingRad = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rhine3DHud")
    FString Label = TEXT("7");
};

/**
 * Decoupled RMUC 2026/2027 Combat Telemetry State Contract for the Rhine Lab 3D Optical HUD.
 */
USTRUCT(BlueprintType)
struct RMSIM_API FRhine3DHudState
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    bool bFriendlyRed = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    float StageRemainSeconds = 420.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    FString MatchPhaseText = TEXT("COMBAT");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    FString RuneClientText;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    bool bRuneClientAvailable = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    FString DartClientText;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    FString DartSuppressionText;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    int32 RedBaseHp = 10000;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    int32 BlueBaseHp = 10000;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    int32 BaseMaxHp = 10000;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    bool bRedBaseProtected = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    bool bBlueBaseProtected = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    bool bRedBaseLargeArmorExposed = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    bool bBlueBaseLargeArmorExposed = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    FRhine3DOutpostState RedOutpost;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    FRhine3DOutpostState BlueOutpost;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    TArray<FRhine3DRobotUnitState> RedUnits;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Match")
    TArray<FRhine3DRobotUnitState> BlueUnits;

    // Own Robot Vitals & Ballistics
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    int32 OwnHp = 400;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    int32 OwnMaxHp = 400;

    int32 OwnRobotNumber = 7;
    int32 OwnLevel = 1;
    int32 OwnExperienceTenths = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    bool bOwnAlive = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    int32 Ammo17mm = 300;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    float MuzzleSpeedMs = 30.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    float BarrelHeatPct = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    float SupercapPct = 1.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    float GimbalYawRad = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    float GimbalPitchRad = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    float GyroRadS = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    bool bAutoAimLocked = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    FString LockTargetLabel = TEXT("BLUE-07");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Own")
    float LockTargetDistM = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Feedback")
    int32 TotalShotsFired = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Feedback")
    float OwnHitFlash = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Feedback")
    uint8 LastArmorId = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Feedback")
    float HitConfirmFlash = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Feedback")
    int32 LastHitDamage = 0;

    // 7 Hardware Statuses: REF / GIM / CHS / SHT / VIS / COM / CAP
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Hardware")
    uint8 ModuleStatusMask = 0x7F;

    // 4 Standard Buffs: HEAL / ARMOR / COOLING / POWER
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Buffs")
    bool bBuffHeal = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Buffs")
    bool bBuffArmor = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Buffs")
    bool bBuffCooling = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Buffs")
    bool bBuffPower = false;

    // Live tactical gain-zone status badge (e.g. "FORTRESS STANDBY // UNLOCKS AFTER OUTPOST LOSS")
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Buffs")
    FString ZoneStatusText;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Buffs")
    bool bZoneStatusLocked = false;

    // RMUC 2027 V1.0.0 Penalty & Respawn States
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Penalties")
    bool bFpvVideoCut = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Penalties")
    bool bOverheatMatchLocked = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Penalties")
    float OverspeedLockSeconds = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Penalties")
    bool bOverspeedMatchLocked = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Penalties")
    float ChassisPowerCutSeconds = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Respawn")
    float RespawnProgress = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Respawn")
    float RespawnRemainSeconds = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Respawn")
    int32 BuybackGoldCost = 50;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Respawn")
    int32 RemainingGold = 800;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Respawn")
    float WeakRemainSeconds = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Respawn")
    bool bWeakUntilGainCard = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Respawn")
    float InvulnerableRemainSeconds = 0.0f;

    // Events & Structured Broadcast Feed
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Events")
    FString BannerEventText;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Events")
    float BannerEventTimer = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Events")
    TArray<FRhine3DBroadcastItem> BroadcastFeed;

    // High-resolution architectural RMUC2027 minimap texture & live pins
    UPROPERTY(Transient)
    UTexture2D* MinimapTexture = nullptr;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Map")
    TArray<FRhine3DMapPin> MapPins;
};

struct FRhine3DMapCell
{
    float ArenaX = 0.0f;
    float ArenaY = 0.0f;
    float HeightM = 0.0f;
    uint8 CellType = 0;
};

/**
 * Runtime controller owning the Slate SBackgroundBlur + 3D Curved Optical HUD Viewport overlay.
 */
class RMSIM_API FRhine3DHudController
{
public:
    FRhine3DHudController();
    ~FRhine3DHudController();

    void SetEnabled(UWorld* World, bool bEnable);
    bool IsEnabled() const { return bEnabled; }
    void Destroy(UWorld* World);

    void UpdateFrame(UWorld* World, const FRhine3DHudState& InState, float DeltaSeconds);

    // Interactive debug / test actions
    void TriggerBootSequence();
    void TriggerSimulatedHit();
    void ToggleSimulatedOverheat();
    void ToggleAutoAimPreview();
    void ToggleMinimap3D();
    void TriggerSampleEventToast();
    void CycleSimulatedOutpostCapture();
    void ToggleTestConsoleExpanded();
    void RequestSwitchUiMode();

    bool ConsumeUiSwitchRequest();
    bool IsTestConsoleExpanded() const { return bConsoleExpanded; }
    bool IsMinimap3D() const { return bMinimap3D; }
    bool IsAutoAimPreview() const { return bAutoAimPreview; }
    bool IsOverheatPreview() const { return bOverheatPreview; }
    int32 GetSimOutpostMode() const { return SimOutpostMode; }

    const FRhine3DHudState& GetDisplayState() const { return DisplayState; }
    const TArray<FRhine3DMapCell>& GetArenaCells() const { return ArenaCells; }
    const TArray<float>& GetEcgSamples() const { return EcgSamples; }
    const TArray<float>& GetPinPulses() const { return PinPulses; }

    float ElapsedSec = 0.0f;
    float BootProgress = 0.0f;
    bool bBootActive = true;

    float ParX = 0.0f;
    float ParY = 0.0f;
    float Recoil = 0.0f;
    float LockBlend = 0.0f;
    float MapTiltRad = 0.56f;
    float ShakeX = 0.0f;
    float ShakeY = 0.0f;
    float ChassisYawRad = 0.35f;
    float RadarAngleRad = 0.0f;
    float FpsSmooth = 60.0f;

    float OwnHpGhostPct = 1.0f;
    float RedBaseGhostPct = 1.0f;
    float BlueBaseGhostPct = 1.0f;
    float RedOutpostGhostPct = 1.0f;
    float BlueOutpostGhostPct = 1.0f;

    float ToastWipeProgress = 0.0f;
    float PhaseSweepTimer = 0.0f;
    FString PrevPhaseText;

    float SimHitFlash = 0.0f;
    uint8 SimHitArmorId = 0;
    float SimToastTimer = 0.0f;
    FString SimToastText;
    bool bSimBuffBoost = false;
    // 0 = Real Match State, 1 = Blue Capturing Red Outpost (60%), 2 = Red Outpost Captured & Rebuilt by Blue!
    int32 SimOutpostMode = 0;

private:
    void EnsureMounted(UWorld* World);
    void LoadArenaHeightfieldOnce();

    bool bEnabled = false;
    bool bMounted = false;
    bool bHeightfieldLoaded = false;
    bool bConsoleExpanded = false;
    bool bMinimap3D = true;
    bool bAutoAimPreview = false;
    bool bOverheatPreview = false;
    bool bSwitchUiRequested = false;

    bool bPrevB = false;
    bool bPrevX = false;
    bool bPrevN = false;
    bool bPrevM = false;
    bool bPrevE = false;
    bool bPrevO = false;
    bool bPrevF10 = false;

    int32 PrevShotsFired = 0;
    float PrevGimbalYaw = 0.0f;
    float PrevGimbalPitch = 0.0f;
    float EcgPhase = 0.0f;

    FFloatSpringState ParXSpring;
    FFloatSpringState ParYSpring;
    FFloatSpringState RecoilSpring;
    FFloatSpringState MapTiltSpring;

    FRhine3DHudState DisplayState;
    TArray<FRhine3DMapCell> ArenaCells;
    TArray<float> EcgSamples;
    TArray<float> PinPulses;

    TSharedPtr<SRhine3DHudViewport> ViewportWidget;
};
