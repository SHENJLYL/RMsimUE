#pragma once
#include "RMUCTacticalRules.h"
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "RMUCFieldRules.h"
#include "RMUCFieldDeviceVisuals.h"
#include "RMUCDartStation.h"
#include "RMUCDartVisual.h"
#include "RMUCRuneRules.h"
#include "RMUCRuneClientBridge.h"
#include "RMUCSupplyRules.h"
#include "RMUCFieldZones.h"
#include "Rhine3DHud.h"
#include "RMUCGameReferee.generated.h"

class AVehicleRig;
class ACameraActor;
class AStaticMeshActor;
class UStaticMeshComponent;
class UMaterialInstanceDynamic;
class AHUD;
class UCanvas;
class UTexture2D;
class FJsonObject;

enum class ERMUCGameProgress : uint8
{
    NotStart = 0,
    Preparation = 1,
    SelfChecking = 2,
    CountDown = 3,
    Running = 4,
    GameOver = 5
};

enum class ERMUCHpDeductionReason : uint8
{
    ArmorHit = 0,
    SystemOffline = 1,
    OverShootSpeed = 2,
    OverHeat = 3,
    OverPower = 4,
    ArmorCollision = 5
};

UCLASS()
class RMSIM_API ARMUCGameReferee : public AActor
{
    GENERATED_BODY()
public:
    ARMUCGameReferee();
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    bool Initialize(
        AVehicleRig* Vehicle,
        const FString& VehicleRigFilename,
        const FString& MapFilename,
        const FString& SpawnTeam,
        const TMap<FString, AStaticMeshActor*>& ArenaMapBoxes,
        FString& Error, AVehicleRig* Opponent = nullptr);
    void Advance(
        double DeltaSeconds,
        bool bFirePressed,
        const FTransform& MuzzleTransform,
        const FVector& AimTargetWorldCm,
        bool bHasAimTarget,
        bool bHasMuzzle);
    void DrawMatchHud(AHUD* Hud, UCanvas* Canvas, double GyroRadS) const;
    TSharedRef<FJsonObject> State() const;
    TSharedRef<FJsonObject> StateFor(const AVehicleRig* Robot) const;
    FString RequestRosShot(AVehicleRig* Robot, const FTransform& MuzzleTransform);
    void SetRosFlywheels(AVehicleRig* Robot, bool bRequested);
    void BindRosCompetitor(AVehicleRig* Robot) { RosCompetitor = Robot; }
    bool IsChassisPoweredFor(const AVehicleRig* Robot) const;
    bool IsSimMatchAutonomyActive(const AVehicleRig* Robot) const;
    bool SetSimMatchStance(const AVehicleRig* Robot, const FString& Stance);
    bool IsFpvVideoCutFor(const AVehicleRig* Robot) const;
    FString CompactStatusText() const;

    // Interactive referee controls (F6 Start, F7 Reset, F8 Swap Team, F9 Test Self-Hit, F10 Toggle Debug)
    void StartMatchNow();
    void ResetMatch(bool bWithCountdown = false);
    void ToggleFriendlyTeam();
    void InjectSelfArmorHit(int32 ArmorId = 0, int32 Damage = 20);
    FString RequestClientRune(AVehicleRig* Vehicle);
    FString RequestInfantryClientRune(bool bRed, int32 RobotNumber);
    TSharedRef<FJsonObject> RuneClientState() const;
    void RequestOwnDartGate();
    void ToggleOwnDartTray();
    void CycleOwnDartTarget();
    void RequestOwnAmmoPurchase();
    void RequestSentryAmmoPurchase(const AVehicleRig* Vehicle, bool bRemote);
    // Consumes only an already confirmed virtual detector event.
    bool ConfirmDartDetector(bool bRed, ERMUCDartTarget Target);
    void ToggleDebugOverlay() { bShowDebugOverlay = !bShowDebugOverlay; }
    bool IsDebugOverlayVisible() const { return bShowDebugOverlay; }

    bool IsFriendlyRed() const { return bFriendlyRed; }
    int32 GetOwnHp() const { return Own().Hp; }
    int32 GetOwnMaxHp() const { return SentryMaxHp; }
    bool IsOwnAlive() const { return GetOwnHp() > 0; }
    bool IsChassisPowered() const { return IsChassisPoweredFor(Rig); }
    bool IsShooterLocked() const { return Own().ShooterLocked(); }
    bool IsFpvVideoCut() const { return IsFpvVideoCutFor(Rig); }
    bool RequestInstantRespawn();
    int32 GetInstantRespawnCost() const { return 50 + Own().OwnBuybackCount * 50; }
    AVehicleRig* GetEnemyRig() const { return EnemyRig; }
#if WITH_DEV_AUTOMATION_TESTS
    AActor* GetRuneProbeCamera() const;
#endif

private:
    enum class EProjectileHit : uint8 { None, Armor, Rune };

    struct FProjectile
    {
        FVector Position = FVector::ZeroVector;
        FVector Velocity = FVector::ZeroVector;
        double Age = 0.0;
        int32 BounceCount = 0;
        bool bFromRed = true;
        bool bDamageCredited = false;
        UStaticMeshComponent* Visual = nullptr;
        TWeakObjectPtr<class UPointLightComponent> Light;
    };

    struct FArenaMatBinding
    {
        UMaterialInstanceDynamic* Mat = nullptr;
        FName EmissiveParam = NAME_None;
        FName ColorParam = NAME_None;
        FLinearColor BaseEmissive = FLinearColor::Black;
        FLinearColor BaseColor = FLinearColor::White;
    };

    UPROPERTY() AVehicleRig* Rig = nullptr;
    UPROPERTY() AVehicleRig* EnemyRig = nullptr;
    UPROPERTY() AVehicleRig* RosCompetitor = nullptr;

    // Direct bindings to REAL RMUC2027 arena actors from FMapStage::MapBoxes
    UPROPERTY() AStaticMeshActor* VisualBaseActor = nullptr;
    UPROPERTY() AStaticMeshActor* CollisionBaseActor = nullptr;
    UPROPERTY() AStaticMeshActor* CollisionCentrePlatform = nullptr;
    UPROPERTY() AStaticMeshActor* VisualRedFortress = nullptr;
    UPROPERTY() AStaticMeshActor* VisualBlueFortress = nullptr;
    UPROPERTY() AStaticMeshActor* VisualRedResource = nullptr;
    UPROPERTY() AStaticMeshActor* VisualBlueResource = nullptr;

    // Dynamic LED materials bound directly to the real aim_sentry vehicle armor LED slots
    TArray<UMaterialInstanceDynamic*> OwnLedMats;
    TArray<UMaterialInstanceDynamic*> EnemyLedMats;
    struct FVehicleArmorLight
    {
        TWeakObjectPtr<UMaterialInstanceDynamic> NativeBars;
        TArray<TWeakObjectPtr<class UPointLightComponent>> SceneLights;
    };
    FVehicleArmorLight OwnArmor, EnemyArmor;
    UPROPERTY() UTexture2D* SentrySticker = nullptr;
    TArray<FArenaMatBinding> ArenaBaseMats;
    TArray<FArenaMatBinding> ArenaFortressMats[2];
    TWeakObjectPtr<class UPointLightComponent> BaseIndicatorLights[2];
    TWeakObjectPtr<class UPointLightComponent> BaseGuideLights[2];
    TWeakObjectPtr<class UPointLightComponent> FortressLights[2];

    // 1:1 Official RMUC 2026 Client UI & Architectural RMUC2027 Minimap Textures (loaded from assets/ui/rmuc2026/)
    UPROPERTY() UTexture2D* TexTopChrome = nullptr;
    UPROPERTY() UTexture2D* TexCenterReticle = nullptr;
    UPROPERTY() UTexture2D* TexBottomLeftRed = nullptr;
    UPROPERTY() UTexture2D* TexBottomLeftBlue = nullptr;
    UPROPERTY() UTexture2D* TexBottomCenter = nullptr;
    UPROPERTY() UTexture2D* TexMinimap = nullptr;
    UPROPERTY() UTexture2D* TexDigitsAtlas = nullptr;

    TArray<FProjectile> Projectiles;
    bool bShowDebugOverlay = false;

    // Match state (Protocol V1.7.0 0x0001)
    ERMUCGameProgress GameProgress = ERMUCGameProgress::Running;
    double StageRemainSeconds = 420.0;
    double MatchElapsedSeconds = 0.0;
    int32 RedScore = 0;
    int32 BlueScore = 0;
    double RedControlSeconds = 0.;
    double BlueControlSeconds = 0.;
    int32 RedAttackDamage = 0;
    int32 BlueAttackDamage = 0;
    FString WinnerText;
    FString BannerEventText;
    double BannerEventTimer = 0.0;
    TArray<FString> RecentEvents;
    TArray<FRhine3DBroadcastItem> StructuredBroadcasts;

    // Friendly team & IDs
    bool bFriendlyRed = true; // true = Red 7 (id=7), false = Blue 7 (id=107)
    static constexpr int32 SentryMaxHp = 400;
    using FieldTeam = FRMUCFieldRules::Team;
    static constexpr int32 OutpostMaxHp = FRMUCFieldRules::OutpostMaxHp;
    static constexpr int32 BaseMaxHp = FRMUCFieldRules::BaseMaxHp;
    FRMUCFieldRules FieldRules;
    FRMUCRuneRules RuneRules;
    TUniquePtr<FRMUCRuneClientBridge> RuneClientBridge;
    FRMUCDartStation RedDart, BlueDart;
    struct FDartFlight
    {
        FRMUCDartVisual Visual;
        FVector Start = FVector::ZeroVector, Previous = FVector::ZeroVector;
        FVector Destination = FVector::ZeroVector, DetectorStart = FVector::ZeroVector;
        FVector DetectorAnchor = FVector::ZeroVector, DetectorPosition = FVector::ZeroVector;
        ERMUCDartTarget Target = ERMUCDartTarget::Outpost, DisplayTarget = ERMUCDartTarget::Outpost;
        double LaunchedAt = 0., NextLaunch = 0., SelectedAt = 0., FixedOffset = 0.;
        double PlacementStart = 0., BaseHeadOffset = 0.;
        bool bDetectorPrepared = false;
        bool bFlying = false;
    };
    std::array<FDartFlight, 2> DartFlights;
    void StepDartFlights();
    double DartTrayFraction[2]{};
    bool bDartTrayOut[2]{};
    void ClearDartFlights();
    FRMUCFieldDeviceVisuals FieldVisuals;
    FRMUCFieldZones FieldZones;
    std::array<FRMUCFieldPointLatch, 2> FieldPointLatches;
    std::array<FRMUCFieldPointEffects, 2> FieldPointEffects;
    std::array<FRMUCFieldContactHold, 2> FieldContactHolds;
    std::array<FRMUCFieldPoints, 2> FieldPhysicalContacts;
    std::array<std::optional<FRMUCFieldRules::FActivation>, 2> OutpostControlSignals;
    std::array<double, 2> BaseArmorTravel{};
#if WITH_DEV_AUTOMATION_TESTS
    struct FRuneLifecycleProbe
    {
        TWeakObjectPtr<ACameraActor> Camera;
        double BegunAt = 0., ActiveAt = -1., ExpiredAt = -1., NextShot = 0.;
        double MinFlow = 1., MaxFlow = 0.;
        bool bBig = false, bStarted = false, bBlackout = false, bConfirmed = false;
        bool bTargetScreenshot = false, bProgressScreenshot = false, bActiveScreenshot = false;
    } RuneLifecycleProbe;
    void StepRuneLifecycleProbe();
    bool VerifyArmorPresentation(const FString& Directory);
    bool bLogisticsProbeTriggered = false;
    bool bOutpostRebuildProbeTriggered = false;
    bool bTacticalHpFixtureTriggered = false;
    int32 TacticalFixtureStage = 0;
#endif

    // Full-field HP (RMUC 2027 V1.0.0 Ground Roster: 1 Heavy=400, 3/4 Infantry=200, 7 Auto Sentry=400; 2 Engineer=0 legacy compat only)
    int32 Red1Hp = 400, Red2Hp = 0, Red3Hp = 200, Red4Hp = 200;
    int32 Blue1Hp = 400, Blue2Hp = 0, Blue3Hp = 200, Blue4Hp = 200;

    // Own robot performance & ballistics (RMUC 2027 V1.0.0 Auto Sentry: 260 Heat / 30/s Cooling / <=25 m/s)
    int32 RobotLevel = 1;
    double BarrelHeatLimit = 260.0;
    double BarrelCoolingPerSec = 30.0;
    double HeatPerShot = 10.0;
    double ShotInterval = 0.08;
    double LaunchSpeedCmS = 2450.0; // 24.5 m/s legal 17mm muzzle speed (<= 25 m/s RMUC 2027 limit)
    static constexpr double SpeedLimit17mmCmS = 2500.0;
    static constexpr double SevereOverheat17mmOffset = 100.0;
    double GravityCmS2 = 981.0;
    double GoldTimer = 0.0;
    double ChassisPowerLimitW = 100.0;
    double DisplayedOwnHp = 400.0; // Smooth damage trail for RMUC2026 HUD
    mutable bool bUseRhineLab3DHud = true; // Default: Rhine Lab 3D Transparent HUD (F11 toggles Official UI)
    mutable bool bPrevF11Down = false;
    mutable TUniquePtr<FRhine3DHudController> Rhine3DHud;
    mutable double ReticleTimeSec = 0.0;
    mutable double ReticleAutoAimBlend = 0.0; // 0 = Free-Aim tilted 3D rings, 1 = Auto-Aim locked
    mutable double ReticleRecoil = 0.0;
    mutable int32 PrevShotsForReticle = 0;

    // Local HUD effects
    double BaseHitFlash = 0.0;
    int32 HudAudioHits = 0;
    int32 HudAudioCountdownSecond = 0;
    double NextHudHeatSoundSeconds = 0.0;
    double NextHudHitSoundSeconds = 0.0;

    bool bBigEnergyMode = false;
    struct FRMUCCompetitorState
    {
        int32 Hp = SentryMaxHp;
        double BarrelHeat = 0.0;
        double NextShotTime = 0.0;
        int32 ProjectileAllowance17mm = 300;
        int32 RemainingGoldCoin = 800;
        double ChassisPowerW = 0.0;
        double ChassisBufferEnergyJ = 60.0;
        double Referee10HzAccum = 0.0;
        double ChassisPowerCutTimer = 0.0;
        bool bOverheatLocked = false;
        bool bOverheatMatchLocked = false;
        double OverspeedLockTimer = 0.0;
        bool bOverspeedMatchLocked = false;
        double RespawnTimer = 0.0;
        double OwnRespawnDuration = 10.0;
        int32 OwnBuybackCount = 0;
        double WeakTimer = 0.0;
        double InvulnerableTimer = 0.0;
        FRMUCSupplyRules OwnLogistics;
        std::optional<FRMUCSupplyRules::EPurchaseMode> AmmoPurchaseRequest;
        uint8 LastArmorId = 0;
        ERMUCHpDeductionReason LastDeductionReason = ERMUCHpDeductionReason::ArmorHit;
        bool bHpDeducedThisStep = false;
        double OwnHitHudFlash = 0.0;
        double OwnLedFlash = 0.0;
        double HitConfirmHudFlash = 0.0;
        int32 LastHitDamageShown = 0;
        int32 TotalShotsFired = 0;
        int32 TotalHitsLanded = 0;
        uint8 EvNonOverlapSupply = 0;
        uint8 EvOverlapSupply = 0;
        uint8 EvSupplyZone = 0;
        uint8 EvSmallEnergy = 0;
        uint8 EvBigEnergy = 0;
        uint8 EvCentralHighland = 0;
        uint8 EvTrapezoidalHighland = 0;
        uint8 EvCenterGainZone = 0;
        uint8 BuffRecovery = 0;
        uint8 BuffCooling = 1;
        uint8 BuffDefence = 0;
        uint8 BuffVulnerability = 0;
        FRMUCSentryStanceState SimStance;
        uint16 BuffAttack = 0;
        uint8 BuffRemainingEnergy = 0x32;
        bool bRfidBase = false;
        bool bRfidCentralHighland = false;
        bool bRfidFortress = false;
        bool bRfidOutpost = false;
        bool bRfidSupplyNonExchange = false;
        bool bRfidSupplyExchange = false;
        bool bRfidCenterGain = false;
        double EnergyBuffTimer = 0.0;
        bool ShooterLocked() const
        {
            return Hp <= 0 || bOverheatLocked || bOverheatMatchLocked || bOverspeedMatchLocked ||
                OverspeedLockTimer > 0.0 || WeakTimer > 0.0;
        }
    };

    std::array<FRMUCCompetitorState, 2> Competitors;
    bool bPhysicalMatch = false;
    double EnemyPatrolPhase = 0.0;

    FieldTeam FriendlyTeam() const { return bFriendlyRed ? FieldTeam::Red : FieldTeam::Blue; }
    std::optional<FieldTeam> TeamFor(const AVehicleRig* Vehicle) const;
    AVehicleRig* VehicleFor(FieldTeam Team) const { return Team == FriendlyTeam() ? Rig : EnemyRig; }
    FRMUCCompetitorState& Competitor(FieldTeam Team) { return Competitors[FRMUCFieldRules::Index(Team)]; }
    const FRMUCCompetitorState& Competitor(FieldTeam Team) const { return Competitors[FRMUCFieldRules::Index(Team)]; }
    FRMUCCompetitorState& Own() { return Competitor(FriendlyTeam()); }
    const FRMUCCompetitorState& Own() const { return Competitor(FriendlyTeam()); }
    static const TCHAR* SentryName(FieldTeam Team)
    { return Team == FieldTeam::Red ? TEXT("RED #7 SENTRY") : TEXT("BLUE #7 SENTRY"); }
    static int32 BroadcastTeam(FieldTeam Team) { return Team == FieldTeam::Red ? 1 : -1; }

    // Helpers
    void LoadUiTextures();
    void BindVehicleLedMaterials(AVehicleRig* Vehicle, TArray<UMaterialInstanceDynamic*>& OutMats,
        FVehicleArmorLight& Armor);
    void BindSentryArmorPresentation(AVehicleRig& Vehicle, FVehicleArmorLight& Armor);
    void BindProjectileVehicleSurfaces(AVehicleRig* Vehicle);
    void BindRealArenaMeshes(const TMap<FString, AStaticMeshActor*>& ArenaMapBoxes);
    void SpawnRealEnemySentry(const FString& VehicleRigFilename);
    void RefreshDynamicMaterials();
    void StepMatchClock(double DeltaSeconds);
    FString StartRuneAttempt(bool bRed, int32 RobotNumber);
    void AdvanceHudAudio(ERMUCGameProgress PreviousProgress);
    bool IsHudFinalCountdown() const;
    int32 HudClockSeconds() const;
    void StepFieldDevices(double DeltaSeconds);
    void StepFieldObserver(double DeltaSeconds);
    FRMUCFieldPoints ObserveFieldPoints(const AVehicleRig& Vehicle) const;
    TSharedRef<FJsonObject> FieldDeviceState() const;
    void StepFieldControls(double DeltaSeconds, const std::array<FRMUCFieldRules::FSiteActivation, 2>& Activation = {});
    int32 ApplyFieldStructureHit(bool bTargetRed, bool bOutpost, bool bAttackerRed, int32 Damage, bool bDart = false);
    static bool ClassifyFieldStructureHit(const FHitResult& Hit, const AStaticMeshActor* BaseCollision,
        const AStaticMeshActor* CentrePlatformCollision, bool& bTargetRed, bool& bOutpost,
        const UPrimitiveComponent* CentreVisibility);
    static int32 ClassifyVehicleArmorHit(const FHitResult& Hit, const AVehicleRig* Vehicle);
    void StepDynamicArenaAndEnemy(double DeltaSeconds);
    void StepRfidAndBuffs(FieldTeam Team, double DeltaSeconds);
    void StepPowerAndHeat(FieldTeam Team, double DeltaSeconds);
    void RespawnCompetitor(FieldTeam Team, bool bBuyback);
    void FireProjectile(const FTransform& MuzzleTransform);
    void StepProjectiles(double DeltaSeconds);
    EProjectileHit ApplyProjectileDamage(const FProjectile& Pellet, const FHitResult& Hit, double NormalSpeedCmS);
    bool VerifyProjectileQueries();
    bool ApplySentryDamage(FieldTeam Target, std::optional<FieldTeam> Attacker, int32 ArmorId, int32 RawDamage,
        ERMUCHpDeductionReason Reason);
    void RecordHit(FieldTeam Attacker, int32 Damage);
    void PushBannerEvent(const FString& Text, double DurationSec = 4.0);
    void PushCombatBroadcast(int32 ActorTeam, const FString& ActorVehicle, const FString& Action,
        int32 TargetTeam, const FString& TargetName, double BannerDurationSec = 0.0);
    FLinearColor TeamLedColor(bool bIsRed, bool bAlive, double FlashTimer) const;
    void DrawTacticalMinimap(AHUD* Hud, UCanvas* Canvas, float X, float Y, float W, float H) const;
    void DrawRhineLab3DHud(AHUD* Hud, UCanvas* Canvas, double GyroRadS) const;
    const TCHAR* OutpostControlPhase(FieldTeam Site) const;
};
