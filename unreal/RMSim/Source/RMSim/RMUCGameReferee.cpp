#include "RMUCGameReferee.h"
#include "RMUCArmorPresentation.h"
#include "RMUCHudAudio.h"
#include "RMUCPhysicalAudio.h"
#include "HAL/PlatformMisc.h"
#include "VehicleRig.h"
#include "CanvasItem.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Components/SceneComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/SpotLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "GameFramework/HUD.h"
#include "GlobalRenderResources.h"
#include "ImageUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/Paths.h"

namespace
{
FVector RosMetersToUeCm(double RosX, double RosY, double RosZ)
{
    return FVector(RosX * 100.0, -RosY * 100.0, RosZ * 100.0);
}

FVector UeCmToRosMeters(const FVector& UeCm)
{
    return FVector(UeCm.X * 0.01, -UeCm.Y * 0.01, UeCm.Z * 0.01);
}

FString ProgressName(ERMUCGameProgress P)
{
    switch (P)
    {
    case ERMUCGameProgress::NotStart: return TEXT("STANDBY");
    case ERMUCGameProgress::Preparation: return TEXT("PREP");
    case ERMUCGameProgress::SelfChecking: return TEXT("CHECK");
    case ERMUCGameProgress::CountDown: return TEXT("COUNTDOWN");
    case ERMUCGameProgress::Running: return TEXT("IN MATCH");
    case ERMUCGameProgress::GameOver: return TEXT("GAME OVER");
    default: return TEXT("UNKNOWN");
    }
}
} // namespace

ARMUCGameReferee::ARMUCGameReferee()
{
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    PrimaryActorTick.bCanEverTick = false;
}

FLinearColor ARMUCGameReferee::TeamLedColor(bool bIsRed, bool bAlive, double FlashTimer) const
{
    return RMUCArmorPresentation::TeamColor(bIsRed) * RMUCArmorPresentation::LampLevel(bAlive, FlashTimer);
}

void ARMUCGameReferee::BindVehicleLedMaterials(AVehicleRig* Vehicle, TArray<UMaterialInstanceDynamic*>& OutMats,
    FVehicleArmorLight& Armor)
{
    OutMats.Reset();
    Armor = {};
    if (!Vehicle) return;
    BindSentryArmorPresentation(*Vehicle, Armor);
    UMaterialInterface* BaseGlowMat = LoadObject<UMaterialInterface>(
        nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    if (!BaseGlowMat) return;

    TArray<UStaticMeshComponent*> MeshComps;
    Vehicle->GetComponents<UStaticMeshComponent>(MeshComps);
    for (UStaticMeshComponent* Comp : MeshComps)
    {
        if (!Comp || !Comp->IsVisible()) continue;
        const int32 NumMats = Comp->GetNumMaterials();
        for (int32 Slot = 0; Slot < NumMats; ++Slot)
        {
            UMaterialInterface* Orig = Comp->GetMaterial(Slot);
            const FString MatName = Orig ? Orig->GetName().ToLower() : FString();
            const FName SlotName = Comp->GetMaterialSlotNames().IsValidIndex(Slot)
                ? Comp->GetMaterialSlotNames()[Slot] : NAME_None;
            const FString SlotStr = SlotName.ToString().ToLower();
            if (Armor.NativeBars.IsValid() && Comp->GetMaterial(Slot) == Armor.NativeBars.Get()) continue;
            if (MatName.Contains(TEXT("led")) || SlotStr.Contains(TEXT("led")) ||
                MatName.Contains(TEXT("light")) || SlotStr.Contains(TEXT("light")) ||
                MatName.Contains(TEXT("lamp")) || SlotStr.Contains(TEXT("lamp")))
            {
                UMaterialInstanceDynamic* Dyn = RMUCArmorPresentation::MakeEmitter(*Vehicle, true);
                if (!Dyn) Dyn = UMaterialInstanceDynamic::Create(BaseGlowMat, this);
                if (Dyn)
                {
                    Comp->SetMaterial(Slot, Dyn);
                    OutMats.Add(Dyn);
                }
            }
        }
    }
    if (OutMats.IsEmpty())
    {
        FString MatDump;
        for (UStaticMeshComponent* Comp : MeshComps)
        {
            if (!Comp) continue;
            for (int32 Slot = 0; Slot < Comp->GetNumMaterials(); ++Slot)
            {
                UMaterialInterface* Orig = Comp->GetMaterial(Slot);
                const FName SlotName = Comp->GetMaterialSlotNames().IsValidIndex(Slot)
                    ? Comp->GetMaterialSlotNames()[Slot] : NAME_None;
                MatDump += FString::Printf(TEXT("[%s:%s=%s] "), *Comp->GetName(), *SlotName.ToString(),
                    Orig ? *Orig->GetName() : TEXT("null"));
            }
        }
        UE_LOG(LogRMSim, Warning, TEXT("RMUC_VEHICLE_LED_NONE actor=%s mats=%s"), *Vehicle->GetName(), *MatDump);
    }
    UE_LOG(LogRMSim, Display, TEXT("RMUC_VEHICLE_LED_BOUND actor=%s slots=%d"), *Vehicle->GetName(), OutMats.Num());
}

void ARMUCGameReferee::BindRealArenaMeshes(const TMap<FString, AStaticMeshActor*>& ArenaMapBoxes)
{
    auto FindActor = [&](const TCHAR* Key) -> AStaticMeshActor*
    {
        if (AStaticMeshActor* const* Found = ArenaMapBoxes.Find(Key)) return *Found;
        return nullptr;
    };

    VisualBaseActor = FindActor(TEXT("visual_base"));
    CollisionBaseActor = FindActor(TEXT("collision_base"));
    FieldVisuals.BindBaseArmor(*this, VisualBaseActor, CollisionBaseActor);
    CollisionCentrePlatform = FindActor(TEXT("collision_centre_platform"));
    FieldVisuals.Bind(*this, FindActor(TEXT("visual_centre_platform")), CollisionCentrePlatform);
    FieldVisuals.BindDartGates(*this, FindActor(TEXT("visual_red_ramp_dart_station")),
        FindActor(TEXT("visual_blue_ramp_dart_station")), FindActor(TEXT("collision_red_ramp_dart_station")),
        FindActor(TEXT("collision_blue_ramp_dart_station")));
    VisualRedFortress = FindActor(TEXT("visual_red_fortress"));
    VisualBlueFortress = FindActor(TEXT("visual_blue_fortress"));
    VisualRedResource = FindActor(TEXT("visual_red_resource_zone"));
    VisualBlueResource = FindActor(TEXT("visual_blue_resource_zone"));
    const AStaticMeshActor* Fortresses[] = {VisualRedFortress, VisualBlueFortress};
    for (int32 Site = 0; Site < 2; ++Site)
    {
        const double Sign = Site == 0 ? 1. : -1.;
        const FLinearColor Color = RMUCArmorPresentation::TeamColor(Site == 0);
        if (VisualBaseActor)
        {
            auto* Base = VisualBaseActor->GetStaticMeshComponent();
            BaseIndicatorLights[Site] = RMUCArmorPresentation::AddPointLight(*this, *Base,
                FVector(Sign * 1130., 0., 84.), Color, 165.f);
            if (auto* Head = FieldVisuals.GetBaseDartHead(Site == 0))
            {
                // CAD RM27_M22 lens face in mesh coordinates, with the emitter just ahead of its curved surface.
                const FVector LensAxis(-Sign * .96815, 0., .25038);
                auto* Guide = NewObject<USpotLightComponent>(this);
                AddInstanceComponent(Guide);
                Guide->SetupAttachment(Head);
                Guide->SetMobility(EComponentMobility::Movable);
                Guide->SetRelativeLocation(FVector(Site == 0 ? 1148.65 : -1148.81, 0., 124.44));
                Guide->SetRelativeRotation(LensAxis.Rotation());
                Guide->SetInnerConeAngle(15.f);
                Guide->SetOuterConeAngle(26.f);
                Guide->SetAttenuationRadius(69.f);
                Guide->SetIntensityUnits(ELightUnits::Candelas);
                Guide->SetUseInverseSquaredFalloff(true);
                Guide->SetCastShadows(false);
                Guide->SetVolumetricScatteringIntensity(0.f);
                RMUCArmorPresentation::SetPointLight(Guide, FLinearColor(.035f, 1.f, .12f), 0.f);
                Guide->RegisterComponent();
                BaseGuideLights[Site] = Guide;
            }
        }
        if (Fortresses[Site])
        {
            auto* Comp = Fortresses[Site]->GetStaticMeshComponent();
            const FVector Position = Comp->GetComponentTransform().InverseTransformPosition(
                Comp->Bounds.Origin + FVector(0., 0., Comp->Bounds.BoxExtent.Z + 8.));
            FortressLights[Site] = RMUCArmorPresentation::AddPointLight(*this, *Comp, Position, Color, 165.f);
        }
    }

    auto WrapDynamicMats = [&](AStaticMeshActor* Actor, TArray<FArenaMatBinding>& OutMats)
    {
        if (!Actor || !Actor->GetStaticMeshComponent()) return;
        UStaticMeshComponent* Comp = Actor->GetStaticMeshComponent();
        const int32 N = Comp->GetNumMaterials();
        for (int32 I = 0; I < N; ++I)
        {
            if (UMaterialInterface* M = Comp->GetMaterial(I))
            {
                if (UMaterialInstanceDynamic* Dyn = Comp->CreateDynamicMaterialInstance(I, M))
                {
                    FArenaMatBinding Binding;
                    Binding.Mat = Dyn;
                    TArray<FMaterialParameterInfo> VecParams;
                    TArray<FGuid> VecGuids;
                    Dyn->GetAllVectorParameterInfo(VecParams, VecGuids);
                    for (const FMaterialParameterInfo& Info : VecParams)
                    {
                        const FString Lower = Info.Name.ToString().ToLower();
                        if (Binding.EmissiveParam.IsNone() && Lower.Contains(TEXT("emissive")))
                        {
                            Binding.EmissiveParam = Info.Name;
                            Dyn->GetVectorParameterValue(Info, Binding.BaseEmissive);
                        }
                        else if (Binding.ColorParam.IsNone() && (Lower.Contains(TEXT("color")) || Lower.Contains(TEXT("tint"))))
                        {
                            Binding.ColorParam = Info.Name;
                            Dyn->GetVectorParameterValue(Info, Binding.BaseColor);
                        }
                    }
                    OutMats.Add(Binding);
                }
            }
        }
    };
    WrapDynamicMats(VisualBaseActor, ArenaBaseMats);
    WrapDynamicMats(VisualRedFortress, ArenaFortressMats[0]);
    WrapDynamicMats(VisualBlueFortress, ArenaFortressMats[1]);

    UE_LOG(LogRMSim, Display, TEXT("RMUC_FIELD_BOUND rune=%d dart_gate_visuals=%d outpost_visuals=%d base=%d centre_collision=%d"),
        FieldVisuals.IsRuneReady(), FieldVisuals.AreDartGatesReady(), FieldVisuals.OutpostsAreReady(),
        CollisionBaseActor != nullptr, CollisionCentrePlatform != nullptr);
}

void ARMUCGameReferee::SpawnRealEnemySentry(const FString& VehicleRigFilename)
{
    if (!GetWorld() || VehicleRigFilename.IsEmpty()) return;
    // Place real enemy aim_sentry on the OPPOSITE half of the RMUC2027 arena from our own vehicle
    const double OwnX = Rig ? (Rig->GetActorLocation().X * 0.01) : -8.0;
    const double EnemyX = (OwnX >= 0.0) ? -8.0 : 8.0;
    const double EnemyY = (OwnX >= 0.0) ? -2.0 : 2.0;
    const FVector SpawnPosCm = RosMetersToUeCm(EnemyX, EnemyY, 0.35);
    const FRotator SpawnRot(0.0, (EnemyX < 0.0) ? 0.0 : 180.0, 0.0);

    EnemyRig = GetWorld()->SpawnActor<AVehicleRig>(SpawnPosCm, SpawnRot);
    if (!EnemyRig) return;
    FString Err;
    const bool bLoaded = (Rig && Rig->IsCanonicalRig())
        ? EnemyRig->LoadCanonical(VehicleRigFilename, Err)
        : EnemyRig->LoadMeshRig(VehicleRigFilename, Err);
    if (!bLoaded)
    {
        UE_LOG(LogRMSim, Warning, TEXT("RMUC_ENEMY_RIG_FAILED %s"), *Err);
        EnemyRig->Destroy();
        EnemyRig = nullptr;
        return;
    }
    // Disable enemy sensor cameras so the enemy sentry incurs zero extra render-target overhead.
    for (const auto& Cam : EnemyRig->GetCameras())
    {
        if (Cam.Value) Cam.Value->bCaptureEveryFrame = false;
    }
    const FBox Bounds = EnemyRig->GetVisualBounds();
    if (Bounds.IsValid) EnemyRig->AddActorWorldOffset(FVector(0.0, 0.0, 2.0 - Bounds.Min.Z));
    // Enable query-only hit collision on the enemy sentry's real visual meshes so 17mm projectiles hit it directly
    TArray<UStaticMeshComponent*> Comps;
    EnemyRig->GetComponents<UStaticMeshComponent>(Comps);
    for (UStaticMeshComponent* C : Comps)
    {
        if (!C || !C->IsVisible()) continue;
        C->SetCollisionProfileName(TEXT("BlockAll"));
        C->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
    }
    FString SnapErr;
    EnemyRig->SnapToTerrain(SnapErr);
    BindVehicleLedMaterials(EnemyRig, EnemyLedMats, EnemyArmor);
    UE_LOG(LogRMSim, Display, TEXT("RMUC_ENEMY_SENTRY_READY pos=(%.2f,%.2f) leds=%d"),
        SpawnPosCm.X * 0.01, -SpawnPosCm.Y * 0.01, EnemyLedMats.Num());
}

void ARMUCGameReferee::LoadUiTextures()
{
    const FString UiDir = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(), TEXT("../../assets/ui/rmuc2026")));
    auto LoadPng = [&](const TCHAR* Name) -> UTexture2D*
    {
        const FString FullPath = FPaths::Combine(UiDir, Name);
        if (!FPaths::FileExists(FullPath))
        {
            UE_LOG(LogRMSim, Warning, TEXT("RMUC_UI_TEX_MISSING %s"), *FullPath);
            return nullptr;
        }
        UTexture2D* Tex = FImageUtils::ImportFileAsTexture2D(FullPath);
        if (Tex)
        {
            Tex->SRGB = true;
            Tex->NeverStream = true;
            Tex->LODGroup = TEXTUREGROUP_UI;
            Tex->Filter = TF_Bilinear;
            Tex->UpdateResource();
        }
        return Tex;
    };
    TexTopChrome = LoadPng(TEXT("top_hud_chrome.png"));
    TexCenterReticle = LoadPng(TEXT("center_reticle.png"));
    TexBottomLeftRed = LoadPng(TEXT("bottom_left_red.png"));
    TexBottomLeftBlue = LoadPng(TEXT("bottom_left_blue.png"));
    TexBottomCenter = LoadPng(TEXT("bottom_center_plate.png"));
    TexMinimap = LoadPng(TEXT("rmuc2027_minimap.png"));
    TexDigitsAtlas = LoadPng(TEXT("digits_atlas.png"));
    UE_LOG(LogRMSim, Display, TEXT("RMUC_UI_TEXTURES_LOADED dir=%s top=%d reticle=%d bl=%d bc=%d mini=%d digits=%d"),
        *UiDir, TexTopChrome != nullptr, TexCenterReticle != nullptr,
        (TexBottomLeftRed != nullptr && TexBottomLeftBlue != nullptr),
        TexBottomCenter != nullptr, TexMinimap != nullptr, TexDigitsAtlas != nullptr);
}

bool ARMUCGameReferee::Initialize(
    AVehicleRig* Vehicle,
    const FString& VehicleRigFilename,
    const FString& MapFilename,
    const FString& SpawnTeam,
    const TMap<FString, AStaticMeshActor*>& ArenaMapBoxes,
    FString& Error, AVehicleRig* Opponent)
{
    if (!Vehicle || !GetWorld())
    {
        Error = TEXT("invalid RMUC referee initialization");
        return false;
    }
    Rig = Vehicle;
    bFriendlyRed = Opponent ? SpawnTeam == TEXT("red") :
        (!FParse::Param(FCommandLine::Get(), TEXT("RefereeBlueTeam")) &&
            (FParse::Param(FCommandLine::Get(), TEXT("RefereeRedTeam")) || SpawnTeam != TEXT("blue")));
    FString ZoneFile = FPaths::Combine(FPaths::GetPath(MapFilename), TEXT("field-zones.json"));
    if (!FPaths::FileExists(ZoneFile))
        ZoneFile = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(),
            TEXT("../../assets/maps/rmuc2027/field-zones.json")));
    FString ZoneError;
    if (!FieldZones.Load(ZoneFile, ZoneError))
        UE_LOG(LogRMSim, Warning, TEXT("RMUC_FIELD_ZONES_UNAVAILABLE %s"), *ZoneError);
    LoadUiTextures();
    BindVehicleLedMaterials(Rig, OwnLedMats, OwnArmor);
    BindRealArenaMeshes(ArenaMapBoxes);
    bPhysicalMatch = Opponent != nullptr;
    if (bPhysicalMatch)
    {
        EnemyRig = Opponent;
        BindVehicleLedMaterials(EnemyRig, EnemyLedMats, EnemyArmor);
    }
    else SpawnRealEnemySentry(VehicleRigFilename);
    BindProjectileVehicleSurfaces(Rig);
    BindProjectileVehicleSurfaces(EnemyRig);
    int32 RunePort = 0;
    if (FParse::Value(FCommandLine::Get(), TEXT("RuneClientBridgePort="), RunePort))
    {
        RuneClientBridge = MakeUnique<FRMUCRuneClientBridge>();
        if (!RuneClientBridge->Open(RunePort))
        {
            RuneClientBridge.Reset();
            Error = TEXT("Could not start the RMUC rune client gateway. Check the loopback port and shared token.");
            return false;
        }
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RUNE_CLIENT_READY port=%d"), RunePort);
    }
    ResetMatch(FParse::Param(FCommandLine::Get(), TEXT("RefereeCountdown")));
    double PreparationSeconds = 0.;
    if (FParse::Value(FCommandLine::Get(), TEXT("RefereePreparationSeconds="), PreparationSeconds) &&
        FMath::IsFinite(PreparationSeconds) && PreparationSeconds > 0.)
    {
        GameProgress = ERMUCGameProgress::Preparation;
        StageRemainSeconds = FMath::Clamp(PreparationSeconds, 10., 180.);
    }
#if WITH_DEV_AUTOMATION_TESTS
    if (FParse::Param(FCommandLine::Get(), TEXT("RefereeHudAudioSelfTest"))) FRMUCHudAudio::RunSelfTest();
#endif
    UE_LOG(LogRMSim, Display, TEXT("RMUC_REFEREE_READY team=%s progress=%s remain=%.0f own_leds=%d enemy_rig=%d"),
        bFriendlyRed ? TEXT("RED") : TEXT("BLUE"), *ProgressName(GameProgress), StageRemainSeconds,
        OwnLedMats.Num(), EnemyRig != nullptr);
    return true;
}

void ARMUCGameReferee::ResetMatch(bool bWithCountdown)
{
    for (FProjectile& P : Projectiles)
    {
        if (auto* Light = P.Light.Get()) Light->DestroyComponent();
        if (P.Visual) P.Visual->DestroyComponent();
    }
    Projectiles.Reset();

    GameProgress = bWithCountdown ? ERMUCGameProgress::CountDown : ERMUCGameProgress::Running;
    StageRemainSeconds = bWithCountdown ? 5.0 : 420.0;
    MatchElapsedSeconds = 0.0;
    WinnerText.Reset();
    RedControlSeconds = BlueControlSeconds = 0.;
    RedAttackDamage = BlueAttackDamage = 0;

    Red1Hp = Blue1Hp = 400;
    Red2Hp = Blue2Hp = 0;
    Red3Hp = Red4Hp = Blue3Hp = Blue4Hp = 200;
    Competitors = {};
    FieldRules.Reset(FMath::RandBool());
    RuneRules.Reset(static_cast<uint32>(FMath::Rand()));
    if (RuneClientBridge) RuneClientBridge->BeginRound();
    ClearDartFlights();
    DartTrayFraction[0] = DartTrayFraction[1] = 0.;
    bDartTrayOut[0] = bDartTrayOut[1] = false;
    RedDart.Reset();
    BlueDart.Reset();
    FieldPointLatches = {};
    FieldPointEffects = {};
    FieldContactHolds = {};
    FieldPhysicalContacts = {};
    OutpostControlSignals = {};
#if WITH_DEV_AUTOMATION_TESTS
    bTacticalHpFixtureTriggered = false;
    TacticalFixtureStage = 0;
#endif
    BaseArmorTravel = {};
    GoldTimer = 0.0;
    DisplayedOwnHp = SentryMaxHp;
    HudAudioHits = 0;
    HudAudioCountdownSecond = 0;
    NextHudHeatSoundSeconds = NextHudHitSoundSeconds = 0.0;
    BaseHitFlash = 0.0;
    PrevShotsForReticle = 0;
    ReticleRecoil = 0.0;
    StepFieldDevices(0.);

    RefreshDynamicMaterials();
    PushBannerEvent(bWithCountdown ? TEXT("RMUC 2027 COUNTDOWN") : TEXT("RMUC 2027 MATCH START — 07:00"), 3.0);
}

void ARMUCGameReferee::StartMatchNow()
{
    if (DartTrayFraction[0] > 0. || DartTrayFraction[1] > 0. || bDartTrayOut[0] || bDartTrayOut[1])
    {
        PushBannerEvent(TEXT("先推回飞镖滑台"), 2.);
        return;
    }
    if (GameProgress == ERMUCGameProgress::GameOver)
    {
        ResetMatch();
        FRMUCHudAudio::Play(GetWorld(), ERMUCHudAudioCue::Start);
        return;
    }
    if (GameProgress != ERMUCGameProgress::Running)
    {
        GameProgress = ERMUCGameProgress::Running;
        StageRemainSeconds = 420.0;
        PushBannerEvent(TEXT("MATCH RUNNING (07:00)"), 3.0);
        FRMUCHudAudio::Play(GetWorld(), ERMUCHudAudioCue::Start);
    }
}

void ARMUCGameReferee::ToggleFriendlyTeam()
{
    if (bPhysicalMatch) return; // The selected operator and ROS seats retain their team identities.
    bFriendlyRed = !bFriendlyRed;
    DisplayedOwnHp = GetOwnHp();
    RefreshDynamicMaterials();
    PushBannerEvent(bFriendlyRed ? TEXT("TEAM SWITCHED: RED SENTRY #7") : TEXT("TEAM SWITCHED: BLUE SENTRY #107"), 2.5);
}

void ARMUCGameReferee::InjectSelfArmorHit(int32 ArmorId, int32 Damage)
{
    ApplySentryDamage(FriendlyTeam(), std::nullopt, FMath::Clamp(ArmorId, 0, 3), Damage, ERMUCHpDeductionReason::ArmorHit);
}

void ARMUCGameReferee::PushCombatBroadcast(
    int32 ActorTeam, const FString& ActorVehicle, const FString& Action,
    int32 TargetTeam, const FString& TargetName, double BannerDurationSec)
{
    auto Localize = [](FString S) -> FString
    {
        S.ReplaceInline(TEXT("RED #7 SENTRY"), TEXT("红方7号哨兵"));
        S.ReplaceInline(TEXT("BLUE #7 SENTRY"), TEXT("蓝方7号哨兵"));
        S.ReplaceInline(TEXT("REFEREE"), TEXT("裁判"));
        S.ReplaceInline(TEXT("CAPTURED & REBUILT"), TEXT("占领并重建"));
        S.ReplaceInline(TEXT("ELIMINATED"), TEXT("击毁"));
        S.ReplaceInline(TEXT("DESTROYED"), TEXT("摧毁"));
        S.ReplaceInline(TEXT("REBUILT"), TEXT("重建"));
        S.ReplaceInline(TEXT("CAPTURED"), TEXT("占领"));
        S.ReplaceInline(TEXT("CONTESTING"), TEXT("争夺中"));
        S.ReplaceInline(TEXT("OVERPOWER"), TEXT("超功率"));
        S.ReplaceInline(TEXT("CHASSIS CUT 5.0s"), TEXT("底盘断电5s"));
        S.ReplaceInline(TEXT("SEVERE OVERHEAT"), TEXT("严重过热"));
        S.ReplaceInline(TEXT("OVERHEAT LOCK"), TEXT("过热锁定"));
        S.ReplaceInline(TEXT("VIDEO CUT -> COOL TO 0"), TEXT("断图传·冷却归零恢复"));
        S.ReplaceInline(TEXT("MATCH LOCKED"), TEXT("本局锁定"));
        S.ReplaceInline(TEXT("BARREL COOLED TO 0"), TEXT("热量归零"));
        S.ReplaceInline(TEXT("SHOOTER & VIDEO RESTORED"), TEXT("发射与图传恢复"));
        S.ReplaceInline(TEXT("RESPAWNED"), TEXT("已复活"));
        S.ReplaceInline(TEXT("100% HP RESPAWN"), TEXT("满血复活"));
        S.ReplaceInline(TEXT("10% HP (WEAK/INVULN 10s)"), TEXT("10%血量(虚弱/无敌)"));
        S.ReplaceInline(TEXT("BUYBACK"), TEXT("金币买活"));
        S.ReplaceInline(TEXT("RED OUTPOST"), TEXT("红方前哨"));
        S.ReplaceInline(TEXT("BLUE OUTPOST"), TEXT("蓝方前哨"));
        S.ReplaceInline(TEXT("RED BASE"), TEXT("红方基地"));
        S.ReplaceInline(TEXT("BLUE BASE"), TEXT("蓝方基地"));
        S.ReplaceInline(TEXT("SUPPLY ZONE"), TEXT("补给区"));
        S.ReplaceInline(TEXT("DART GATE OPENING"), TEXT("飞镖闸门开启中"));
        S.ReplaceInline(TEXT("RMUC 2027 MATCH START!"), TEXT("RMUC 2027 比赛开始！"));
        S.ReplaceInline(TEXT("AMMO"), TEXT("弹丸"));
        return S;
    };

    const FString LocActor  = Localize(ActorVehicle);
    const FString LocAction = Localize(Action);
    const FString LocTarget = Localize(TargetName);

    const int32 RemSec = FMath::Max(0, FMath::FloorToInt(StageRemainSeconds));
    FRhine3DBroadcastItem Item;
    Item.ActorTeam = ActorTeam;
    Item.ActorVehicle = LocActor;
    Item.ActionText = LocAction;
    Item.TargetTeam = TargetTeam;
    Item.TargetName = LocTarget;
    Item.TimeStr = FString::Printf(TEXT("%02d:%02d"), RemSec / 60, RemSec % 60);
    StructuredBroadcasts.Insert(Item, 0);
    if (StructuredBroadcasts.Num() > 6) StructuredBroadcasts.SetNum(6);

    const FString Flat = LocTarget.IsEmpty()
        ? FString::Printf(TEXT("[%s] %s"), *LocActor, *LocAction)
        : FString::Printf(TEXT("[%s] %s %s"), *LocActor, *LocAction, *LocTarget);
    RecentEvents.Insert(Flat, 0);
    if (RecentEvents.Num() > 4) RecentEvents.SetNum(4);
    if (BannerDurationSec > 0.0)
    {
        BannerEventText = Flat;
        BannerEventTimer = BannerDurationSec;
    }
    UE_LOG(LogRMSim, Display, TEXT("RMUC_REFEREE_EVENT %s"), *Flat);
}

void ARMUCGameReferee::PushBannerEvent(const FString& Text, double DurationSec)
{
    PushCombatBroadcast(0, TEXT("REFEREE"), Text, 0, TEXT(""), DurationSec);
}

void ARMUCGameReferee::RefreshDynamicMaterials()
{
    const auto SetArmor = [](const FVehicleArmorLight& Armor, bool bRed, bool bAlive, double Flash)
    {
        const FLinearColor Color = RMUCArmorPresentation::TeamColor(bRed);
        RMUCArmorPresentation::SetLamp(Armor.NativeBars.Get(), bRed, bAlive, Flash);
        const float Level = RMUCArmorPresentation::LampLevel(bAlive, Flash);
        for (const auto& Light : Armor.SceneLights)
            RMUCArmorPresentation::SetPointLight(Light.Get(), Color, 1.30f * Level);
    };
    SetArmor(OwnArmor, bFriendlyRed, IsOwnAlive(), Own().OwnLedFlash);
    const FLinearColor OwnCol = TeamLedColor(bFriendlyRed, IsOwnAlive(), Own().OwnLedFlash);
    for (UMaterialInstanceDynamic* M : OwnLedMats)
    {
        RMUCArmorPresentation::SetLamp(M, bFriendlyRed, IsOwnAlive(), Own().OwnLedFlash);
        if (M) M->SetVectorParameterValue(TEXT("Color"), OwnCol);
    }
    const bool bEnemyAlive = (bFriendlyRed ? Competitor(FieldTeam::Blue).Hp : Competitor(FieldTeam::Red).Hp) > 0;
    const double EnemyFlash = Competitor(FRMUCFieldRules::Other(FriendlyTeam())).OwnLedFlash;
    SetArmor(EnemyArmor, !bFriendlyRed, bEnemyAlive, EnemyFlash);
    const FLinearColor EnemyCol = TeamLedColor(!bFriendlyRed, bEnemyAlive, EnemyFlash);
    for (UMaterialInstanceDynamic* M : EnemyLedMats)
    {
        RMUCArmorPresentation::SetLamp(M, !bFriendlyRed, bEnemyAlive, EnemyFlash);
        if (M) M->SetVectorParameterValue(TEXT("Color"), EnemyCol);
    }

    const float Pulse = 0.5f + 0.5f * FMath::Sin(float(MatchElapsedSeconds * 4.5));
    auto ApplyArenaOverlay = [](const TArray<FArenaMatBinding>& Bindings, const FLinearColor& Boost, float Blend)
    {
        for (const FArenaMatBinding& B : Bindings)
        {
            if (!B.Mat) continue;
            if (!B.EmissiveParam.IsNone())
            {
                B.Mat->SetVectorParameterValue(B.EmissiveParam, B.BaseEmissive + Boost * Blend);
            }
            else if (!B.ColorParam.IsNone())
            {
                B.Mat->SetVectorParameterValue(B.ColorParam, FMath::Lerp(B.BaseColor, Boost, FMath::Clamp(Blend, 0.0f, 1.0f)));
            }
        }
    };

    // Base panel indication follows the referee hardware state.
    if (BaseHitFlash > 0.0)
    {
        ApplyArenaOverlay(ArenaBaseMats, FLinearColor(6.0f, 2.0f, 0.4f, 1.0f), 0.70f);
    }
    else if (FieldRules.Get(FieldTeam::Red).bBaseArmorOpen || FieldRules.Get(FieldTeam::Blue).bBaseArmorOpen)
    {
        ApplyArenaOverlay(ArenaBaseMats, FLinearColor(3.5f, 0.35f, 0.15f, 1.0f), 0.25f + 0.35f * Pulse);
    }
    else
    {
        ApplyArenaOverlay(ArenaBaseMats, FLinearColor::Black, 0.0f);
    }

    const auto& RedPoints = FieldPointEffects[FRMUCFieldRules::Index(FieldTeam::Red)];
    const auto& BluePoints = FieldPointEffects[FRMUCFieldRules::Index(FieldTeam::Blue)];
    for (const FieldTeam Site : FRMUCFieldRules::Teams)
    {
        const int32 Index = FRMUCFieldRules::Index(Site);
        const auto& Base = FieldRules.Get(Site);
        const FLinearColor Color = RMUCArmorPresentation::TeamColor(Site == FieldTeam::Red);
        const float BaseLevel = Base.BaseHp > 0
            ? (BaseHitFlash > 0. ? 20.f : Base.bBaseArmorOpen ? 5.f + 8.f * Pulse : 0.f) : 0.f;
        RMUCArmorPresentation::SetPointLight(BaseIndicatorLights[Index].Get(), Color, BaseLevel);
        RMUCArmorPresentation::SetPointLight(BaseGuideLights[Index].Get(), FLinearColor(.035f, 1.f, .12f),
            Base.BaseHp > 0 ? 4.5f : 0.f);
        const bool bDefending = Site == FieldTeam::Red ? RedPoints.bFortress : BluePoints.bFortress;
        const bool bInvading = Site == FieldTeam::Red ? BluePoints.bEnemyFortress : RedPoints.bEnemyFortress;
        ApplyArenaOverlay(ArenaFortressMats[Index], Index == 0 ? FLinearColor(3.2f, 0.4f, 0.3f, 1.0f)
            : FLinearColor(0.2f, 1.5f, 4.0f, 1.0f), bDefending || bInvading ? 0.35f + 0.25f * Pulse : 0.0f);
        RMUCArmorPresentation::SetPointLight(FortressLights[Index].Get(), Color,
            bDefending || bInvading ? 10.f + 8.f * Pulse : 0.f);
    }
}

void ARMUCGameReferee::StepMatchClock(double DeltaSeconds)
{
    if (GameProgress == ERMUCGameProgress::GameOver || GameProgress == ERMUCGameProgress::NotStart) return;
    StageRemainSeconds = FMath::Max(0.0, StageRemainSeconds - DeltaSeconds);
    if (GameProgress == ERMUCGameProgress::Running)
    {
        const double RunningTime = FMath::Min(DeltaSeconds, 420. - MatchElapsedSeconds);
        MatchElapsedSeconds += RunningTime;
        const auto& Red = FieldRules.Get(FieldTeam::Red);
        const auto& Blue = FieldRules.Get(FieldTeam::Blue);
        if (Red.OutpostControl.Controller == Blue.OutpostControl.Controller)
            (Red.OutpostControl.Controller == FieldTeam::Red ? RedControlSeconds : BlueControlSeconds) += RunningTime;
        if (Red.BaseControl.Controller == Blue.BaseControl.Controller)
            (Red.BaseControl.Controller == FieldTeam::Red ? RedControlSeconds : BlueControlSeconds) += 3. * RunningTime;
        GoldTimer += DeltaSeconds;
        if (GoldTimer >= 60.0)
        {
            GoldTimer -= 60.0;
            const int32 Income = FMath::RoundToInt(MatchElapsedSeconds / 60.) >= 6 ? 300 : 100;
            for (auto& State : Competitors)
                State.RemainingGoldCoin = FMath::Min(9999, State.RemainingGoldCoin + Income);
        }
        if (StageRemainSeconds <= 0.0)
        {
            GameProgress = ERMUCGameProgress::GameOver;
            // RMUC 2027 V1.0.0: Exclude legacy #2 Engineer from end-of-match team HP total
            const int32 RedTotalHp = Red.BaseHp + Red.OutpostHp + Red1Hp + Red3Hp + Red4Hp + Competitor(FieldTeam::Red).Hp;
            const int32 BlueTotalHp = Blue.BaseHp + Blue.OutpostHp + Blue1Hp + Blue3Hp + Blue4Hp + Competitor(FieldTeam::Blue).Hp;
            if (!FMath::IsNearlyEqual(RedControlSeconds, BlueControlSeconds, 1.e-6))
                WinnerText = RedControlSeconds > BlueControlSeconds ? TEXT("RED VICTORY (CONTROL)") : TEXT("BLUE VICTORY (CONTROL)");
            else if (RedAttackDamage != BlueAttackDamage)
                WinnerText = RedAttackDamage > BlueAttackDamage ? TEXT("RED VICTORY (DAMAGE)") : TEXT("BLUE VICTORY (DAMAGE)");
            else if (RedTotalHp != BlueTotalHp)
                WinnerText = RedTotalHp > BlueTotalHp ? TEXT("RED VICTORY (TEAM HP)") : TEXT("BLUE VICTORY (TEAM HP)");
            else WinnerText = TEXT("MATCH DRAW");
            PushBannerEvent(TEXT("TIME EXPIRED — ") + WinnerText, 10.0);
        }
    }
    else if (StageRemainSeconds <= 0.0)
    {
        if (GameProgress == ERMUCGameProgress::Preparation) { GameProgress = ERMUCGameProgress::SelfChecking; StageRemainSeconds = 15.0; }
        else if (GameProgress == ERMUCGameProgress::SelfChecking) { GameProgress = ERMUCGameProgress::CountDown; StageRemainSeconds = 5.0; }
        else if (GameProgress == ERMUCGameProgress::CountDown)
        {
            GameProgress = ERMUCGameProgress::Running;
            StageRemainSeconds = 420.0;
            PushBannerEvent(TEXT("RMUC 2027 MATCH START!"), 3.0);
        }
    }
}

bool ARMUCGameReferee::IsHudFinalCountdown() const
{
    return GameProgress == ERMUCGameProgress::Running && StageRemainSeconds <= 5.0;
}

int32 ARMUCGameReferee::HudClockSeconds() const
{
    return GameProgress == ERMUCGameProgress::CountDown || IsHudFinalCountdown()
        ? FMath::CeilToInt(StageRemainSeconds)
        : FMath::FloorToInt(StageRemainSeconds);
}

void ARMUCGameReferee::AdvanceHudAudio(ERMUCGameProgress PreviousProgress)
{
    static const bool bTrace = FParse::Param(FCommandLine::Get(), TEXT("RefereeHudAudioTrace"));
    const bool bRunning = GameProgress == ERMUCGameProgress::Running;
    const bool bPrestart = GameProgress == ERMUCGameProgress::CountDown;
    const int32 Second = bPrestart || IsHudFinalCountdown() ? HudClockSeconds() : 0;
    if (Second != HudAudioCountdownSecond)
    {
        HudAudioCountdownSecond = Second;
        if (Second > 0 && Second <= 5)
        {
            FRMUCHudAudio::Play(GetWorld(), ERMUCHudAudioCue::Countdown);
            if (bTrace)
                UE_LOG(LogRMSim, Display, TEXT("RMUC_HUD_AUDIO_TIMER second=%d phase=%s"),
                    Second, bPrestart ? TEXT("prestart") : TEXT("round_end"));
        }
    }
    if (bRunning && PreviousProgress == ERMUCGameProgress::CountDown)
        FRMUCHudAudio::Play(GetWorld(), ERMUCHudAudioCue::Start);
    if (GameProgress == ERMUCGameProgress::GameOver && PreviousProgress == ERMUCGameProgress::Running)
        FRMUCHudAudio::Play(GetWorld(), ERMUCHudAudioCue::Finish);

    const bool bConfirmedHit = Own().TotalHitsLanded > HudAudioHits;
    HudAudioHits = Own().TotalHitsLanded;
    if (bRunning && bConfirmedHit && MatchElapsedSeconds >= NextHudHitSoundSeconds)
    {
        FRMUCHudAudio::Play(GetWorld(), ERMUCHudAudioCue::Hit);
        NextHudHitSoundSeconds = MatchElapsedSeconds + .075;
    }

    const double HeatRatio = Own().BarrelHeat / FMath::Max(1.0, BarrelHeatLimit);
    if (!bRunning || !IsOwnAlive() || HeatRatio <= .80)
    {
        NextHudHeatSoundSeconds = 0.0;
    }
    else if (HeatRatio > .85 && MatchElapsedSeconds >= NextHudHeatSoundSeconds)
    {
        FRMUCHudAudio::Play(GetWorld(), ERMUCHudAudioCue::Overheat);
        if (bTrace)
            UE_LOG(LogRMSim, Display, TEXT("RMUC_HUD_AUDIO_HEAT ratio=%.4f match_s=%.3f"), HeatRatio, MatchElapsedSeconds);
        NextHudHeatSoundSeconds = MatchElapsedSeconds + 1.65;
    }
}

void ARMUCGameReferee::StepDynamicArenaAndEnemy(double DeltaSeconds)
{
    if (!bPhysicalMatch && EnemyRig && GameProgress == ERMUCGameProgress::Running &&
        Competitor(FRMUCFieldRules::Other(FriendlyTeam())).Hp > 0)
    {
        EnemyPatrolPhase += DeltaSeconds;
        FString Error;
        EnemyRig->SetGimbal(0.75 * FMath::Sin(EnemyPatrolPhase * 1.1),
            -0.05 * FMath::Sin(EnemyPatrolPhase * 0.7), Error);
    }

    const double Hp = GetOwnHp();
    DisplayedOwnHp = DisplayedOwnHp > Hp ? FMath::Max(Hp, DisplayedOwnHp - 160.0 * DeltaSeconds) : Hp;
    for (auto& Record : Competitors)
    {
        Record.OwnHitHudFlash = FMath::Max(0.0, Record.OwnHitHudFlash - DeltaSeconds);
        Record.OwnLedFlash = FMath::Max(0.0, Record.OwnLedFlash - DeltaSeconds);
        Record.HitConfirmHudFlash = FMath::Max(0.0, Record.HitConfirmHudFlash - DeltaSeconds);
    }
    BaseHitFlash = FMath::Max(0.0, BaseHitFlash - DeltaSeconds);
    BannerEventTimer = FMath::Max(0.0, BannerEventTimer - DeltaSeconds);
    RefreshDynamicMaterials();
}

std::optional<ARMUCGameReferee::FieldTeam> ARMUCGameReferee::TeamFor(const AVehicleRig* Vehicle) const
{
    if (Vehicle && Vehicle == Rig) return FriendlyTeam();
    if (Vehicle && Vehicle == EnemyRig) return FRMUCFieldRules::Other(FriendlyTeam());
    return std::nullopt;
}

bool ARMUCGameReferee::IsChassisPoweredFor(const AVehicleRig* Robot) const
{
    const auto Team = TeamFor(Robot);
    return Team && Competitor(*Team).Hp > 0 && Competitor(*Team).ChassisPowerCutTimer <= 0.0 &&
        GameProgress != ERMUCGameProgress::GameOver;
}

bool ARMUCGameReferee::IsSimMatchAutonomyActive(const AVehicleRig* Robot) const
{
    return bPhysicalMatch && Robot == RosCompetitor &&
        GameProgress == ERMUCGameProgress::Running && IsChassisPoweredFor(Robot);
}

bool ARMUCGameReferee::SetSimMatchStance(const AVehicleRig* Robot, const FString& Stance)
{
    if (!bPhysicalMatch || Robot != RosCompetitor || GameProgress != ERMUCGameProgress::Running) return false;
    const auto Team = TeamFor(Robot);
    if (!Team || Competitor(*Team).Hp <= 0) return false;
    ERMUCSentryStance Target = ERMUCSentryStance::Move;
    if (Stance == TEXT("MOVE")) Target = ERMUCSentryStance::Move;
    else if (Stance == TEXT("ATTACK")) Target = ERMUCSentryStance::Attack;
    else if (Stance == TEXT("DEFENSE")) Target = ERMUCSentryStance::Defense;
    else return false;
    return Competitor(*Team).SimStance.TrySwitch(Target);
}

bool ARMUCGameReferee::IsFpvVideoCutFor(const AVehicleRig* Robot) const
{
    const auto Team = TeamFor(Robot);
    return Team && (Competitor(*Team).bOverheatLocked || Competitor(*Team).bOverheatMatchLocked);
}

void ARMUCGameReferee::RespawnCompetitor(FieldTeam Team, bool bBuyback)
{
    auto& Record = Competitor(Team);
    Record.Hp = bBuyback ? SentryMaxHp : FMath::Max(1, FMath::RoundToInt(SentryMaxHp * 0.10));
    Record.RespawnTimer = 0.0;
    Record.BarrelHeat = 0.0;
    Record.ChassisBufferEnergyJ = 60.0;
    Record.bOverheatLocked = false;
    Record.SimStance.Reset();
    Record.WeakTimer = bBuyback ? 0.0 : 10.0;
    Record.InvulnerableTimer = bBuyback ? 3.0 : 10.0;
}

bool ARMUCGameReferee::RequestInstantRespawn()
{
    if (GameProgress != ERMUCGameProgress::Running || IsOwnAlive()) return false;
    const int32 Cost = GetInstantRespawnCost();
    auto& Record = Own();
    if (Record.RemainingGoldCoin < Cost)
    {
        PushBannerEvent(FString::Printf(TEXT("BUYBACK REQUIRES %d GOLD"), Cost), 2.5);
        return false;
    }
    Record.RemainingGoldCoin -= Cost;
    ++Record.OwnBuybackCount;
    RespawnCompetitor(FriendlyTeam(), true);
    PushCombatBroadcast(BroadcastTeam(FriendlyTeam()), SentryName(FriendlyTeam()),
        FString::Printf(TEXT("BUYBACK (-%dG)"), Cost), 0, TEXT("100% HP RESPAWN"), 3.0);
    return true;
}

void ARMUCGameReferee::StepRfidAndBuffs(FieldTeam Team, double DeltaSeconds)
{
    auto& Record = Competitor(Team);
    const auto& Rune = RuneRules.Get(Team == FieldTeam::Red);
    const auto& Points = FieldPointEffects[FRMUCFieldRules::Index(Team)];
    Record.EnergyBuffTimer = FMath::Max(0., Rune.BuffUntil - MatchElapsedSeconds);
    Record.EvSmallEnergy = Record.EnergyBuffTimer > 0. && !Rune.bBig ? 1 : 0;
    Record.EvBigEnergy = Record.EnergyBuffTimer > 0. && Rune.bBig ? 1 : 0;
    Record.BuffRecovery = Record.OwnLogistics.RecoveryPercent(MatchElapsedSeconds, FieldRules.Get(Team).BaseHp, Points);
    Record.BuffVulnerability = Points.VulnerabilityPercent;
    Record.BuffCooling = Record.EnergyBuffTimer > 0. ? Rune.CoolingMultiplier : 1;
    Record.BuffDefence = FMath::Max(Points.DefencePercent, Record.EnergyBuffTimer > 0. ? Rune.DefencePercent : 0);
    Record.BuffAttack = Record.EnergyBuffTimer > 0. ? Rune.AttackPercent : 0;
    {
        const auto Modifiers = Record.SimStance.Modifiers();
        Record.BuffDefence = FMath::Max<uint8>(Record.BuffDefence, Modifiers.DefenceBonusPercent);
        Record.BuffVulnerability = FMath::Max<uint8>(Record.BuffVulnerability, Modifiers.VulnerabilityBonusPercent);
    }
    Record.bRfidBase = Points.bBase;
    Record.bRfidCentralHighland = Points.bCentralHighland;
    Record.bRfidFortress = Points.bFortress;
    Record.bRfidOutpost = Points.bFullOutpost;
    Record.bRfidSupplyNonExchange = false;
    Record.bRfidSupplyExchange = Points.bSupply;
    Record.EvNonOverlapSupply = Record.bRfidSupplyNonExchange;
    Record.EvOverlapSupply = Record.bRfidSupplyExchange;
    Record.EvSupplyZone = Points.bSupply;
    Record.EvCentralHighland = Record.bRfidCentralHighland;

    if (Record.Hp <= 0)
    {
        // The pitch reader is still qualified during a respawn wait.
        const auto* Vehicle = VehicleFor(Team);
        const bool bAtSupply = Vehicle && ObserveFieldPoints(*Vehicle)[FRMUCFieldRules::Index(Team)].Supply.value_or(false);
        const bool bAccel = bAtSupply || FieldRules.Get(Team).BaseHp < BaseMaxHp / 2;
        Record.RespawnTimer += DeltaSeconds * (bAccel ? 2.0 : 1.0);
        if (Record.RespawnTimer >= Record.OwnRespawnDuration)
        {
            RespawnCompetitor(Team, false);
            PushCombatBroadcast(BroadcastTeam(Team), SentryName(Team), TEXT("RESPAWNED"), 0,
                TEXT("10% HP / WEAK AND INVULNERABLE 10s"), 3.5);
        }
    }
    else if (Record.WeakTimer > 0.0 && Record.Hp >= SentryMaxHp)
    {
        Record.WeakTimer = 0.0;
    }
}

void ARMUCGameReferee::StepPowerAndHeat(FieldTeam Team, double DeltaSeconds)
{
    const auto* Vehicle = VehicleFor(Team);
    if (!Vehicle) return;
    auto& Record = Competitor(Team);
    Record.ChassisPowerCutTimer = FMath::Max(0.0, Record.ChassisPowerCutTimer - DeltaSeconds);
    Record.OverspeedLockTimer = FMath::Max(0.0, Record.OverspeedLockTimer - DeltaSeconds);
    Record.WeakTimer = FMath::Max(0.0, Record.WeakTimer - DeltaSeconds);
    Record.InvulnerableTimer = FMath::Max(0.0, Record.InvulnerableTimer - DeltaSeconds);
    Record.SimStance.Step(DeltaSeconds);

    const FVector Velocity = Vehicle->GetCommandOriginVelocity();
    const double SpeedMps = FVector2D(Velocity.X, Velocity.Y).Size() * 0.01;
    const double YawRate = FMath::Abs(Vehicle->GetMeasuredYawRate());
    Record.ChassisPowerW = IsChassisPoweredFor(Vehicle) ?
        (12.0 + 11.5 * SpeedMps * SpeedMps * 0.35 + 1.8 * YawRate * YawRate) : 0.0;

    Record.Referee10HzAccum += DeltaSeconds;
    const auto& Points = FieldPointEffects[FRMUCFieldRules::Index(Team)];
    const auto StanceMod = Record.SimStance.Modifiers();
    const double Cooling = FMath::Max(StanceMod.BarrelCoolingPerSec * FMath::Max<int32>(1, Record.BuffCooling),
        StanceMod.BarrelCoolingPerSec + Points.FortressCoolingBonus);
    while (Record.Referee10HzAccum >= 0.1)
    {
        Record.Referee10HzAccum -= 0.1;
        Record.BarrelHeat = FMath::Max(0.0, Record.BarrelHeat - Cooling * 0.1);
        if (Record.bOverheatLocked && !Record.bOverheatMatchLocked && Record.BarrelHeat <= 1e-6)
        {
            Record.bOverheatLocked = false;
            PushCombatBroadcast(BroadcastTeam(Team), SentryName(Team), TEXT("BARREL COOLED TO 0"),
                0, TEXT("SHOOTER & VIDEO RESTORED"), 2.5);
        }
        if (Record.Hp <= 0) continue;
        if (Record.ChassisPowerCutTimer > 0.0)
        {
            Record.ChassisBufferEnergyJ = FMath::Min(60.0, Record.ChassisBufferEnergyJ + 6.0 * 0.1);
            continue;
        }
        const double Limit = StanceMod.ChassisPowerLimitW;
        const double NetPower = Record.ChassisPowerW - Limit;
        if (NetPower > 0.0)
        {
            Record.ChassisBufferEnergyJ = FMath::Max(0.0, Record.ChassisBufferEnergyJ - NetPower * 0.1);
            if (Record.ChassisBufferEnergyJ <= 1e-6)
            {
                Record.ChassisPowerCutTimer = 5.0;
                PushCombatBroadcast(BroadcastTeam(Team), SentryName(Team), TEXT("OVERPOWER"), 0, TEXT("CHASSIS CUT 5.0s"), 3.0);
            }
        }
        else
        {
            Record.ChassisBufferEnergyJ = FMath::Min(60.0, Record.ChassisBufferEnergyJ + FMath::Min(25.0, -NetPower * 0.5) * 0.1);
        }
    }
    const double Ratio = Record.ChassisBufferEnergyJ / 60.0;
    Record.BuffRemainingEnergy = Ratio < 0.5 ? static_cast<uint8>(FMath::Clamp(FMath::RoundToInt(Ratio * 100.0), 0, 50)) : 0x32;
}

void ARMUCGameReferee::RecordHit(FieldTeam Attacker, int32 Damage)
{
    auto& Record = Competitor(Attacker);
    ++Record.TotalHitsLanded;
    Record.HitConfirmHudFlash = 0.30;
    Record.LastHitDamageShown = Damage;
    (Attacker == FieldTeam::Red ? RedAttackDamage : BlueAttackDamage) += Damage;
}

bool ARMUCGameReferee::ApplySentryDamage(FieldTeam Target, std::optional<FieldTeam> Attacker, int32 ArmorId,
    int32 RawDamage, ERMUCHpDeductionReason Reason)
{
    auto& Record = Competitor(Target);
    if (GameProgress != ERMUCGameProgress::Running || Record.Hp <= 0 || Record.InvulnerableTimer > 0.0 || RawDamage <= 0)
        return false;
    const int32 Attack = Attacker ? Competitor(*Attacker).BuffAttack : 0;
    const double Scale = (Attack > 0 ? Attack / 100. : 1.) * (1. + (Record.BuffVulnerability - Record.BuffDefence) / 100.);
    const int32 Damage = FMath::Min(Record.Hp, FMath::Max(1, FMath::RoundToInt(RawDamage * Scale)));
    Record.Hp -= Damage;
    Record.OwnLogistics.MarkCombat(MatchElapsedSeconds);
    Record.LastArmorId = static_cast<uint8>(ArmorId);
    Record.LastDeductionReason = Reason;
    Record.bHpDeducedThisStep = true;
    Record.OwnHitHudFlash = 0.55;
    Record.OwnLedFlash = 0.30;
    if (Attacker) RecordHit(*Attacker, Damage);
    if (Record.Hp == 0)
    {
        Record.BarrelHeat = 0.0;
        Record.ChassisBufferEnergyJ = 60.0;
        Record.bOverheatLocked = false;
        Record.RespawnTimer = 0.0;
        Record.OwnRespawnDuration = FMath::Clamp(10.0 + (MatchElapsedSeconds / 60.0) * 3.0 + Record.OwnBuybackCount * 5.0, 10.0, 35.0);
        const FieldTeam Source = Attacker.value_or(FRMUCFieldRules::Other(Target));
        PushCombatBroadcast(BroadcastTeam(Source), SentryName(Source), TEXT("ELIMINATED"),
            BroadcastTeam(Target), SentryName(Target), 4.0);
        if (auto* Vehicle = VehicleFor(Target)) Vehicle->SetContactDrive(0.0, 0.0, true);
    }
    return true;
}

void ARMUCGameReferee::Advance(
    double DeltaSeconds,
    bool bFirePressed,
    const FTransform& MuzzleTransform,
    const FVector& AimTargetWorldCm,
    bool bHasAimTarget,
    bool bHasMuzzle)
{
    const double Dt = FMath::Clamp(DeltaSeconds, 0.0, 0.1);
    const ERMUCGameProgress PreviousProgress = GameProgress;
    for (auto& Record : Competitors) Record.bHpDeducedThisStep = false;
    StepMatchClock(Dt);
    StepFieldDevices(Dt);
    if (RuneClientBridge) RuneClientBridge->Tick(*this);
#if WITH_DEV_AUTOMATION_TESTS
    static const FString ArmorProof = []
    {
        FString Directory;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeArmorPresentationProof="), Directory);
        return Directory;
    }();
    if (!ArmorProof.IsEmpty() && MatchElapsedSeconds >= 3.)
    {
        const bool bPassed = VerifyArmorPresentation(ArmorProof);
        FPlatformMisc::RequestExitWithStatus(false, bPassed ? 0 : 2);
        return;
    }
    static const bool bRespawnProof = FParse::Param(FCommandLine::Get(), TEXT("RefereeRespawnProof"));
    if (bRespawnProof && bPhysicalMatch && GameProgress == ERMUCGameProgress::Running)
    {
        static int32 RespawnProofStage = 0;
        if ((RespawnProofStage == 0 && MatchElapsedSeconds >= 12.0) ||
            (RespawnProofStage == 1 && IsOwnAlive() && Own().InvulnerableTimer <= 0.0))
        {
            InjectSelfArmorHit(0, SentryMaxHp * 2);
            if (!IsOwnAlive()) ++RespawnProofStage;
        }
        else if (RespawnProofStage == 2 && Own().RespawnTimer >= 1.3 && RequestInstantRespawn())
        {
            ++RespawnProofStage;
        }
    }
    if (FParse::Param(FCommandLine::Get(), TEXT("RefereeProjectileSelfTest")))
    {
        const bool bPassed = VerifyProjectileQueries();
        AdvanceHudAudio(PreviousProgress);
        FPlatformMisc::RequestExitWithStatus(true, bPassed ? 0 : 2);
        return;
    }
    static const bool bThermalProof = FParse::Param(FCommandLine::Get(), TEXT("RefereeHudAudioThermalProof"));
    if (bThermalProof && GameProgress == ERMUCGameProgress::Running)
        bFirePressed = MatchElapsedSeconds < 11.0 || (MatchElapsedSeconds >= 14.0 && MatchElapsedSeconds < 23.0);
#endif
    if (RosCompetitor != Rig) SetRosFlywheels(Rig, bFirePressed && bHasMuzzle);
    if (GameProgress == ERMUCGameProgress::Running)
    {
        for (FieldTeam Team : FRMUCFieldRules::Teams)
        {
            StepRfidAndBuffs(Team, Dt);
            StepPowerAndHeat(Team, Dt);
        }
        if (bFirePressed && bHasMuzzle) FireProjectile(MuzzleTransform);
    }
    StepProjectiles(Dt);
#if WITH_DEV_AUTOMATION_TESTS
    StepRuneLifecycleProbe();
#endif
    AdvanceHudAudio(PreviousProgress);
    StepDynamicArenaAndEnemy(Dt);
}

FString ARMUCGameReferee::CompactStatusText() const
{
    const int32 Mins = HudClockSeconds() / 60;
    const int32 Secs = HudClockSeconds() % 60;
    return FString::Printf(
        TEXT("RMUC2027 %s %02d:%02d | %s7 HP %d/%d | Ammo %d | Heat %.0f/%.0f | Pwr %.0fW Buf %.0fJ"),
        *ProgressName(GameProgress), Mins, Secs,
        bFriendlyRed ? TEXT("RED") : TEXT("BLUE"),
        GetOwnHp(), SentryMaxHp, Own().ProjectileAllowance17mm, Own().BarrelHeat, BarrelHeatLimit, Own().ChassisPowerW, Own().ChassisBufferEnergyJ);
}

namespace RmUi
{
FORCEINLINE FLinearColor Srgb(uint8 R, uint8 G, uint8 B, uint8 A = 255)
{
    return FLinearColor::FromSRGBColor(FColor(R, G, B, A));
}

void DrawConvexPoly(UCanvas* Canvas, const TArray<FVector2D>& Pts, const FLinearColor& Color)
{
    if (!Canvas || Pts.Num() < 3) return;
    TArray<FCanvasUVTri> Tris;
    Tris.Reserve(Pts.Num() - 2);
    for (int32 I = 1; I + 1 < Pts.Num(); ++I)
    {
        FCanvasUVTri Tri;
        Tri.V0_Pos = Pts[0];     Tri.V0_UV = FVector2D(0, 0); Tri.V0_Color = Color;
        Tri.V1_Pos = Pts[I];     Tri.V1_UV = FVector2D(1, 0); Tri.V1_Color = Color;
        Tri.V2_Pos = Pts[I + 1]; Tri.V2_UV = FVector2D(1, 1); Tri.V2_Color = Color;
        Tris.Add(Tri);
    }
    FCanvasTriangleItem Item(Tris, GWhiteTexture);
    Item.SetColor(Color);
    Item.BlendMode = SE_BLEND_Translucent;
    Canvas->DrawItem(Item);
}

void DrawQuad(UCanvas* Canvas, const FVector2D& TL, const FVector2D& TR, const FVector2D& BR, const FVector2D& BL, const FLinearColor& Color)
{
    TArray<FVector2D> Pts = { TL, TR, BR, BL };
    DrawConvexPoly(Canvas, Pts, Color);
}

// Smooth horizontal gradient across any slanted quadrilateral (16 strips, rock-solid on UE5 Canvas)
void DrawGradientQuad(UCanvas* Canvas, const FVector2D& TL, const FVector2D& TR, const FVector2D& BR, const FVector2D& BL,
    const FLinearColor& LeftCol, const FLinearColor& RightCol, int32 Strips = 16)
{
    if (!Canvas || Strips < 1) return;
    for (int32 I = 0; I < Strips; ++I)
    {
        const float T0 = float(I) / float(Strips);
        const float T1 = float(I + 1) / float(Strips);
        const FLinearColor C = FMath::Lerp(LeftCol, RightCol, 0.5f * (T0 + T1));
        DrawQuad(Canvas,
            FMath::Lerp(TL, TR, T0), FMath::Lerp(TL, TR, T1),
            FMath::Lerp(BL, BR, T1), FMath::Lerp(BL, BR, T0), C);
    }
}

void DrawPolyOutline(AHUD* Hud, const TArray<FVector2D>& Pts, const FLinearColor& Color, float Thickness, bool bClose = true)
{
    if (!Hud || Pts.Num() < 2) return;
    const int32 Count = bClose ? Pts.Num() : Pts.Num() - 1;
    for (int32 I = 0; I < Count; ++I)
    {
        const FVector2D& A = Pts[I];
        const FVector2D& B = Pts[(I + 1) % Pts.Num()];
        Hud->DrawLine(A.X, A.Y, B.X, B.Y, Color, Thickness);
    }
}

void DrawCircleFilled(UCanvas* Canvas, const FVector2D& Center, float Radius, const FLinearColor& Color, int32 Segs = 32)
{
    if (!Canvas || Radius <= 0.5f) return;
    TArray<FVector2D> Pts;
    Pts.Reserve(Segs);
    for (int32 I = 0; I < Segs; ++I)
    {
        const float Ang = 2.0f * PI * float(I) / float(Segs);
        Pts.Add(Center + FVector2D(FMath::Cos(Ang) * Radius, FMath::Sin(Ang) * Radius));
    }
    DrawConvexPoly(Canvas, Pts, Color);
}

void DrawArcLine(AHUD* Hud, const FVector2D& Center, float Radius, float DegStart, float DegEnd,
    const FLinearColor& Color, float Thickness, int32 Segs = 36)
{
    if (!Hud || Radius <= 0.5f) return;
    FVector2D Prev = FVector2D::ZeroVector;
    for (int32 I = 0; I <= Segs; ++I)
    {
        const float Ang = FMath::DegreesToRadians(FMath::Lerp(DegStart, DegEnd, float(I) / float(Segs)));
        const FVector2D P = Center + FVector2D(FMath::Cos(Ang) * Radius, FMath::Sin(Ang) * Radius);
        if (I > 0) Hud->DrawLine(Prev.X, Prev.Y, P.X, P.Y, Color, Thickness);
        Prev = P;
    }
}

void DrawVerticalGradientRect(UCanvas* Canvas, float X, float Y, float W, float H,
    const FLinearColor& TopCol, const FLinearColor& BotCol, int32 Strips = 14)
{
    for (int32 I = 0; I < Strips; ++I)
    {
        const float T0 = float(I) / float(Strips);
        const float T1 = float(I + 1) / float(Strips);
        const FLinearColor C = FMath::Lerp(TopCol, BotCol, 0.5f * (T0 + T1));
        const float Y0 = Y + H * T0;
        const float Y1 = Y + H * T1;
        DrawQuad(Canvas, FVector2D(X, Y0), FVector2D(X + W, Y0), FVector2D(X + W, Y1), FVector2D(X, Y1), C);
    }
}

void DrawShadowText(AHUD* Hud, const FString& Text, const FLinearColor& Color, float X, float Y, UFont* Font, float Scale)
{
    if (!Hud || !Font) return;
    Hud->DrawText(Text, Srgb(8, 10, 14, 210), X + 1.2f, Y + 1.2f, Font, Scale);
    Hud->DrawText(Text, Color, X, Y, Font, Scale);
}

void DrawArcBand(UCanvas* Canvas, const FVector2D& Center, float InnerR, float OuterR, float DegStart, float DegEnd,
    const FLinearColor& ColStart, const FLinearColor& ColEnd, int32 Segs = 32)
{
    if (!Canvas || OuterR <= InnerR || FMath::IsNearlyEqual(DegStart, DegEnd)) return;
    for (int32 I = 0; I < Segs; ++I)
    {
        const float T0 = float(I) / float(Segs);
        const float T1 = float(I + 1) / float(Segs);
        const float A0 = FMath::DegreesToRadians(FMath::Lerp(DegStart, DegEnd, T0));
        const float A1 = FMath::DegreesToRadians(FMath::Lerp(DegStart, DegEnd, T1));
        const FVector2D D0(FMath::Cos(A0), FMath::Sin(A0));
        const FVector2D D1(FMath::Cos(A1), FMath::Sin(A1));
        DrawQuad(Canvas, Center + D0 * OuterR, Center + D1 * OuterR, Center + D1 * InnerR, Center + D0 * InnerR,
            FMath::Lerp(ColStart, ColEnd, 0.5f * (T0 + T1)));
    }
}

void DrawCenteredText(AHUD* Hud, const FString& Text, const FLinearColor& Color, float CX, float CY, UFont* Font, float Scale)
{
    if (!Hud || !Font) return;
    float TW = 0.0f, TH = 0.0f;
    Hud->GetTextSize(Text, TW, TH, Font, Scale);
    DrawShadowText(Hud, Text, Color, CX - TW * 0.5f, CY - TH * 0.5f, Font, Scale);
}

void DrawRightText(AHUD* Hud, const FString& Text, const FLinearColor& Color, float RightX, float TopY, UFont* Font, float Scale)
{
    if (!Hud || !Font) return;
    float TW = 0.0f, TH = 0.0f;
    Hud->GetTextSize(Text, TW, TH, Font, Scale);
    DrawShadowText(Hud, Text, Color, RightX - TW, TopY, Font, Scale);
}

// Iconic RoboMaster "R" emblem inside team circles (clean geometric strokes)
void DrawRmEmblemR(UCanvas* Canvas, AHUD* Hud, const FVector2D& C, float R, const FLinearColor& CircleCol, const FLinearColor& RingCol)
{
    DrawCircleFilled(Canvas, C, R + 2.5f, Srgb(12, 16, 22, 180), 32);
    DrawCircleFilled(Canvas, C, R, CircleCol, 32);
    DrawArcLine(Hud, C, R, 0.0f, 360.0f, RingCol, 1.8f, 36);
    const float S = R * 0.52f;
    const FLinearColor WCol = Srgb(255, 255, 255, 248);
    // Slanted RoboMaster "R" bars
    DrawQuad(Canvas, C + FVector2D(-0.55f * S, -0.75f * S), C + FVector2D(0.55f * S, -0.75f * S),
        C + FVector2D(0.35f * S, -0.35f * S), C + FVector2D(-0.65f * S, -0.35f * S), WCol);
    DrawQuad(Canvas, C + FVector2D(-0.62f * S, -0.75f * S), C + FVector2D(-0.24f * S, -0.75f * S),
        C + FVector2D(-0.52f * S, 0.78f * S), C + FVector2D(-0.88f * S, 0.78f * S), WCol);
    DrawQuad(Canvas, C + FVector2D(0.18f * S, -0.75f * S), C + FVector2D(0.62f * S, -0.75f * S),
        C + FVector2D(0.50f * S, 0.08f * S), C + FVector2D(0.08f * S, 0.08f * S), WCol);
    DrawQuad(Canvas, C + FVector2D(-0.35f * S, -0.15f * S), C + FVector2D(0.45f * S, -0.15f * S),
        C + FVector2D(0.32f * S, 0.16f * S), C + FVector2D(-0.42f * S, 0.16f * S), WCol);
    DrawQuad(Canvas, C + FVector2D(-0.08f * S, 0.05f * S), C + FVector2D(0.30f * S, 0.05f * S),
        C + FVector2D(0.72f * S, 0.78f * S), C + FVector2D(0.30f * S, 0.78f * S), WCol);
}

// Official RoboMaster slanted robot slot card (\ for Red leaning inward, / for Blue leaning inward)
void DrawSlantedRobotCard(UCanvas* Canvas, AHUD* Hud, UFont* Font, float X, float Y, float W, float H,
    const TCHAR* NumStr, const TCHAR* RoleStr, int32 Hp, int32 MaxHp, bool bIsRed, bool bHighlight, float S)
{
    const float Skew = 13.0f * S;
    const bool bAlive = Hp > 0;
    // Red (\): top-left is further left, bottom shifts RIGHT toward center
    // Blue (/): top-left shifts RIGHT, bottom-left is further left toward center
    const FVector2D TL = bIsRed ? FVector2D(X, Y)               : FVector2D(X + Skew, Y);
    const FVector2D TR = bIsRed ? FVector2D(X + W, Y)           : FVector2D(X + W + Skew, Y);
    const FVector2D BR = bIsRed ? FVector2D(X + W + Skew, Y + H): FVector2D(X + W, Y + H);
    const FVector2D BL = bIsRed ? FVector2D(X + Skew, Y + H)    : FVector2D(X, Y + H);

    const FLinearColor CardBg = bHighlight
        ? Srgb(38, 46, 58, 224)
        : (bAlive ? Srgb(24, 28, 36, 198) : Srgb(14, 16, 20, 145));
    DrawQuad(Canvas, TL, TR, BR, BL, CardBg);
    DrawPolyOutline(Hud, { TL, TR, BR, BL },
        bHighlight ? Srgb(250, 212, 92, 240) : Srgb(120, 134, 152, 135), bHighlight ? 1.8f : 1.0f);

    // Stylized miniature mech silhouette inside card (turret + chassis blocks)
    const FVector2D Mid = (TL + TR + BR + BL) * 0.25f;
    const FLinearColor MechCol = bAlive ? Srgb(198, 210, 225, 115) : Srgb(80, 84, 90, 85);
    DrawQuad(Canvas, Mid + FVector2D(-14 * S, -2 * S), Mid + FVector2D(14 * S, -2 * S),
        Mid + FVector2D(16 * S, 6 * S), Mid + FVector2D(-16 * S, 6 * S), MechCol);
    DrawQuad(Canvas, Mid + FVector2D(-8 * S, -10 * S), Mid + FVector2D(8 * S, -10 * S),
        Mid + FVector2D(10 * S, -2 * S), Mid + FVector2D(-10 * S, -2 * S), MechCol * FLinearColor(1.1f, 1.1f, 1.1f, 1.2f));
    DrawCenteredText(Hud, RoleStr, bAlive ? Srgb(232, 240, 248, 240) : Srgb(112, 118, 125, 175),
        Mid.X, Mid.Y - 3.5f * S, Font, 0.78f * S);

    // Glowing slanted bottom HP underline bar (signature official RMUC style)
    const float BarH = 5.5f * S;
    const float Pct = FMath::Clamp(float(Hp) / float(FMath::Max(1, MaxHp)), 0.0f, 1.0f);
    const FVector2D BTL = FMath::Lerp(TL, BL, 1.0f - BarH / H);
    const FVector2D BTR = FMath::Lerp(TR, BR, 1.0f - BarH / H);
    DrawQuad(Canvas, BTL, BTR, BR, BL, Srgb(16, 18, 24, 230));
    if (Pct > 0.005f)
    {
        const FLinearColor ColA = bIsRed ? Srgb(236, 36, 36, 250) : Srgb(22, 128, 248, 250);
        const FLinearColor ColB = bIsRed ? Srgb(255, 114, 48, 250) : Srgb(62, 226, 255, 250);
        if (bIsRed)
        {
            DrawGradientQuad(Canvas, FMath::Lerp(BTR, BTL, Pct), BTR, BR, FMath::Lerp(BR, BL, Pct), ColA, ColB, 8);
        }
        else
        {
            DrawGradientQuad(Canvas, BTL, FMath::Lerp(BTL, BTR, Pct), FMath::Lerp(BL, BR, Pct), BL, ColA, ColB, 8);
        }
    }

    // Crisp corner robot number cleanly OUTSIDE the slanted card (no overlap!)
    const float NumX = bIsRed ? (BL.X - 11.0f * S) : (BR.X + 11.0f * S);
    DrawCenteredText(Hud, NumStr, bAlive ? Srgb(255, 255, 255, 255) : Srgb(120, 125, 130, 190),
        NumX, BL.Y - 7.5f * S, Font, 1.22f * S);
}
// Crisp DIN/Bahnschrift Bold dynamic numeral renderer backed by digits_atlas.png (16 cells of 64x128)
// Align: -1 = Left, 0 = Center, +1 = Right
void DrawDinText(AHUD* Hud, UTexture2D* Atlas, const FString& Text, float AnchorX, float CenterY,
    float HeightPx, int32 Align = 0, const FLinearColor& Tint = FLinearColor::White)
{
    if (!Hud || !Atlas || Text.IsEmpty()) return;
    static const TCHAR* GlyphOrder = TEXT("0123456789:/.WJ-");
    auto GlyphAdvance = [HeightPx](TCHAR Ch) -> float
    {
        if (Ch == TEXT(' ')) return HeightPx * 0.30f;
        if (Ch == TEXT(':') || Ch == TEXT('.')) return HeightPx * 0.28f;
        if (Ch == TEXT('/') || Ch == TEXT('-')) return HeightPx * 0.42f;
        if (Ch == TEXT('W')) return HeightPx * 0.68f;
        if (Ch == TEXT('1')) return HeightPx * 0.44f;
        return HeightPx * 0.54f;
    };
    float TotalW = 0.0f;
    for (int32 I = 0; I < Text.Len(); ++I) TotalW += GlyphAdvance(Text[I]);

    float CurX = AnchorX;
    if (Align == 0) CurX -= TotalW * 0.5f;
    else if (Align > 0) CurX -= TotalW;

    const float CellDrawW = HeightPx * 0.56f;
    const float CellDrawH = HeightPx * 1.12f;
    const float DrawY = CenterY - CellDrawH * 0.5f;
    constexpr float Inv16 = 1.0f / 16.0f;

    for (int32 I = 0; I < Text.Len(); ++I)
    {
        const TCHAR Ch = Text[I];
        const float Adv = GlyphAdvance(Ch);
        int32 Idx = -1;
        for (int32 K = 0; K < 16; ++K)
        {
            if (GlyphOrder[K] == Ch) { Idx = K; break; }
        }
        if (Idx >= 0)
        {
            const float DrawX = CurX + (Adv - CellDrawW) * 0.5f;
            Hud->DrawTexture(Atlas, DrawX, DrawY, CellDrawW, CellDrawH,
                float(Idx) * Inv16, 0.0f, Inv16, 1.0f, Tint, BLEND_Translucent);
        }
        CurX += Adv;
    }
}
} // namespace RmUi

void ARMUCGameReferee::DrawTacticalMinimap(AHUD* Hud, UCanvas* Canvas, float X, float Y, float W, float H) const
{
    using namespace RmUi;
    UFont* Font = GEngine ? GEngine->GetSmallFont() : nullptr;
    const float S = W / 380.0f;

    // Exact coordinate projection matching generate_rmuc2026_ui.py (pad_x=24/1120, pad_y=20/640)
    const float PadX = W * (24.0f / 1120.0f);
    const float PadY = H * (20.0f / 640.0f);
    const float InnerW = W - 2.0f * PadX;
    const float InnerH = H - 2.0f * PadY;
    auto ToMini = [&](double RosX, double RosY) -> FVector2D
    {
        const float U = FMath::Clamp(float((14.0 - RosX) / 28.0), 0.0f, 1.0f);
        const float V = FMath::Clamp(float((8.0 - RosY) / 16.0), 0.0f, 1.0f);
        return FVector2D(X + PadX + U * InnerW, Y + PadY + V * InnerH);
    };

    if (TexMinimap)
    {
        Hud->DrawTexture(TexMinimap, X, Y, W, H, 0.0f, 0.0f, 1.0f, 1.0f, FLinearColor::White, BLEND_Translucent);
    }
    else
    {
        DrawQuad(Canvas, FVector2D(X, Y), FVector2D(X + W, Y), FVector2D(X + W, Y + H), FVector2D(X, Y + H), Srgb(24, 30, 38, 224));
        DrawPolyOutline(Hud, { FVector2D(X, Y), FVector2D(X + W, Y), FVector2D(X + W, Y + H), FVector2D(X, Y + H) },
            Srgb(122, 138, 156, 210), 1.6f);
    }

    const auto TeamColor = [](FieldTeam Team, uint8 Alpha = 255)
    {
        return Team == FieldTeam::Red ? RmUi::Srgb(255, 91, 82, Alpha) : RmUi::Srgb(82, 177, 255, Alpha);
    };
    const auto TeamName = [](FieldTeam Team) { return Team == FieldTeam::Red ? TEXT("RED") : TEXT("BLUE"); };
    for (const FieldTeam Site : FRMUCFieldRules::Teams)
    {
        const unsigned Index = FRMUCFieldRules::Index(Site);
        const auto& Post = FieldRules.Get(Site);
        const auto& Control = Post.OutpostControl;
        const auto ZoneSide = Site == FieldTeam::Red ? ERMUCFieldZoneSide::Red : ERMUCFieldZoneSide::Blue;
        const FLinearColor OwnerColor = Post.OutpostHp > 0 ? TeamColor(Post.OutpostOwner) : Srgb(172, 181, 192, 255);
        const FLinearColor ControllerColor = TeamColor(Control.Controller);
        for (const auto Kind : {ERMUCFieldZoneKind::Outpost, ERMUCFieldZoneKind::CentralHighland})
        {
            const auto* Zone = FieldZones.Find(Kind, ZoneSide);
            if (!Zone) continue;
            TArray<FVector2D> Outline;
            Outline.Reserve(Zone->OuterM.Num());
            for (const FVector2D& Point : Zone->OuterM) Outline.Add(ToMini(Point.X, Point.Y));
            DrawPolyOutline(Hud, Outline, TeamColor(Site, Kind == ERMUCFieldZoneKind::Outpost ? 230 : 148), 1.2f * S);
            if (Kind == ERMUCFieldZoneKind::Outpost)
            {
                const FVector2D Marker = ToMini(Zone->ReferenceM.X, Zone->ReferenceM.Y);
                DrawCircleFilled(Canvas, Marker, 6.4f * S, OwnerColor, 16);
                DrawCircleFilled(Canvas, Marker, 4.2f * S, ControllerColor, 16);
            }
        }

        const float RowY = Y - (46.0f - 22.0f * Index) * S;
        const FVector2D A(X, RowY), B(X + W, RowY + 20.0f * S);
        DrawQuad(Canvas, A, FVector2D(B.X, A.Y), B, FVector2D(A.X, B.Y), Srgb(15, 23, 36, 225));
        const FString Phase = FString(OutpostControlPhase(Site)).ToUpper();
        const FString Tower = Post.OutpostHp > 0
            ? FString::Printf(TEXT("%c POST  %s %d HP"), TeamName(Site)[0], TeamName(Post.OutpostOwner), Post.OutpostHp)
            : FString::Printf(TEXT("%c POST  DESTROYED"), TeamName(Site)[0]);
        const bool bShowProgress = GameProgress == ERMUCGameProgress::Running &&
            (Phase == TEXT("CAPTURING") || Phase == TEXT("RETURNING") || Phase == TEXT("DECAYING") ||
                (Phase == TEXT("CONTESTED") && Control.TransitionSeconds > 0.0));
        const FieldTeam Challenger = FRMUCFieldRules::Other(Control.Controller);
        const bool bShowChallenger = Phase == TEXT("CAPTURING") || Phase == TEXT("RETURNING") || Phase == TEXT("DECAYING");
        const FieldTeam DisplayTeam = bShowChallenger ? Challenger : Control.Controller;
        FString Label = Phase == TEXT("CONTESTED") || Phase == TEXT("UNAVAILABLE")
            ? Phase : FString::Printf(TEXT("%s %s"), TeamName(DisplayTeam), *Phase);
        const double Required = FRMUCFieldRules::ControlTransitionSeconds;
        if (bShowProgress) Label += FString::Printf(TEXT("  %.1f/%.0fs"), Control.TransitionSeconds, Required);
        DrawShadowText(Hud, Tower, OwnerColor, X + 7.0f * S, RowY + 2.0f * S, Font, .80f * S);
        DrawShadowText(Hud, Label, TeamColor(DisplayTeam), X + 167.0f * S, RowY + 2.0f * S, Font, .80f * S);
        if (bShowProgress && Control.TransitionSeconds > 0.0)
        {
            Hud->DrawLine(X, B.Y - S, X + W * FMath::Clamp(Control.TransitionSeconds / Required, 0., 1.),
                B.Y - S, TeamColor(Challenger), 2.0f * S);
        }
    }

    // Live Central Highland occupied glow pulse on minimap
    if (Own().bRfidCentralHighland)
    {
        const FVector2D C = ToMini(0.0, 0.0);
        DrawArcLine(Hud, C, 24.0f * S, 0, 360, Srgb(82, 255, 165, 220), 2.0f * S, 24);
    }

    // Enemy Sentry live circular numbered marker
    if (EnemyRig)
    {
        const FVector EPos = UeCmToRosMeters(EnemyRig->GetActorLocation());
        const FVector2D EP = ToMini(EPos.X, EPos.Y);
        const bool bEnemyRed = !bFriendlyRed;
        const FLinearColor ECol = bEnemyRed ? Srgb(238, 48, 42, 248) : Srgb(28, 148, 255, 248);
        DrawCircleFilled(Canvas, EP, 9.5f * S, Srgb(12, 16, 22, 210), 20);
        DrawCircleFilled(Canvas, EP, 8.2f * S, ECol, 20);
        DrawArcLine(Hud, EP, 8.2f * S, 0, 360, Srgb(255, 255, 255, 235), 1.4f * S, 20);
        if (TexDigitsAtlas) DrawDinText(Hud, TexDigitsAtlas, TEXT("7"), EP.X, EP.Y, 11.5f * S, 0);
        else DrawCenteredText(Hud, TEXT("7"), Srgb(255, 255, 255, 255), EP.X, EP.Y, Font, 0.85f * S);
    }

    // Own Sentry live marker + translucent viewing cone + gold halo
    if (Rig)
    {
        const FVector OPos = UeCmToRosMeters(Rig->GetActorLocation());
        const FVector2D OP = ToMini(OPos.X, OPos.Y);
        FVector GimbalFwd = Rig->GetActorForwardVector();
        if (USceneCaptureComponent2D* const* AimCam = Rig->GetCameras().Find(TEXT("aim_camera")); AimCam && *AimCam)
        {
            GimbalFwd = (*AimCam)->GetForwardVector();
        }
        else if (!Rig->GetCameras().IsEmpty())
        {
            if (USceneCaptureComponent2D* FirstCam = Rig->GetCameras().CreateConstIterator()->Value)
            {
                GimbalFwd = FirstCam->GetForwardVector();
            }
        }
        const FVector2D Dir = FVector2D(float(-GimbalFwd.X), float(GimbalFwd.Y)).GetSafeNormal();
        const FVector2D Perp(-Dir.Y, Dir.X);
        DrawConvexPoly(Canvas, { OP, OP + (Dir * 30.0f + Perp * 14.0f) * S, OP + (Dir * 30.0f - Perp * 14.0f) * S },
            Srgb(120, 238, 255, 78));
        const FLinearColor OCol = bFriendlyRed ? Srgb(242, 46, 40, 252) : Srgb(24, 148, 255, 252);
        DrawCircleFilled(Canvas, OP, 11.0f * S, Srgb(12, 16, 22, 215), 24);
        DrawCircleFilled(Canvas, OP, 9.2f * S, OCol, 24);
        DrawArcLine(Hud, OP, 11.2f * S, 0, 360, Srgb(255, 232, 112, 245), 1.8f * S, 24);
        if (TexDigitsAtlas) DrawDinText(Hud, TexDigitsAtlas, TEXT("7"), OP.X, OP.Y, 12.5f * S, 0);
        else DrawCenteredText(Hud, TEXT("7"), Srgb(255, 255, 255, 255), OP.X, OP.Y, Font, 0.90f * S);
    }
}

void ARMUCGameReferee::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    ClearDartFlights();
    RuneClientBridge.Reset();
    if (Rhine3DHud.IsValid())
    {
        Rhine3DHud->Destroy(GetWorld());
        Rhine3DHud.Reset();
    }
    Super::EndPlay(EndPlayReason);
}

namespace RmUi
{
struct FClientRuneStatus
{
    FString Text;
    bool bAvailable = false;
};

FClientRuneStatus ClientRuneStatus(const FRMUCRuneRules& Rules, ERMUCGameProgress MatchPhase,
    double Elapsed, bool bRed, bool bAlive, bool bAssetsReady)
{
    using Phase = FRMUCRuneRules::Phase;
    const bool bRunning = MatchPhase == ERMUCGameProgress::Running;
    const int32 Chances = bRunning ? Rules.Available(bRed) : 0;
    const auto& Rune = Rules.Get(bRed);
    const TCHAR* Attempt = Rune.bBig ? TEXT("LARGE") : TEXT("SMALL");
    FString Status;
    bool bAvailable = false;
    if (!bRunning)
    {
        Status = MatchPhase == ERMUCGameProgress::GameOver ? TEXT("MATCH COMPLETE") : TEXT("WAITING FOR MATCH");
    }
    else if (!bAssetsReady)
    {
        Status = TEXT("FIELD DEVICE UNAVAILABLE");
    }
    else if (Rune.State == Phase::Activating)
    {
        Status = FString::Printf(TEXT("%s WINDOW  %d/5  %ds"), Attempt, Rune.Groups,
            FMath::CeilToInt(FMath::Max(0., Rune.AttemptUntil - Elapsed)));
    }
    else if (Rune.State == Phase::Failed)
    {
        Status = TEXT("FAILED GROUP  /  RESETTING");
    }
    else if (Rune.State == Phase::Activated)
    {
        Status = FString::Printf(TEXT("%s BUFF  %ds"), Attempt,
            FMath::CeilToInt(FMath::Max(0., Rune.BuffUntil - Elapsed)));
    }
    else if (!bAlive)
    {
        Status = TEXT("S7 INACTIVE");
    }
    else
    {
        bAvailable = Chances > 0;
        Status = bAvailable ? TEXT("READY") : TEXT("NO CHANCES LEFT IN PHASE");
    }
    return { FString::Printf(TEXT("%s PHASE  |  CHANCES %d  |  %s"),
        Elapsed >= 180. ? TEXT("LARGE") : TEXT("SMALL"), Chances, *Status), bAvailable };
}
} // namespace RmUi

void ARMUCGameReferee::DrawMatchHud(AHUD* Hud, UCanvas* Canvas, double GyroRadS) const
{
    using namespace RmUi;
    if (!Hud || !Canvas) return;

    // F11 runtime toggle (or test console button) between Rhine Lab 3D Transparent HUD & Official RMUC 2026 UI
    if (APlayerController* PC = GetWorld() ? GetWorld()->GetFirstPlayerController() : nullptr)
    {
        const bool bF11 = PC->IsInputKeyDown(EKeys::F11);
        if (bF11 && !bPrevF11Down)
        {
            bUseRhineLab3DHud = !bUseRhineLab3DHud;
        }
        bPrevF11Down = bF11;
    }
    if (Rhine3DHud.IsValid() && Rhine3DHud->ConsumeUiSwitchRequest())
    {
        bUseRhineLab3DHud = !bUseRhineLab3DHud;
    }

    if (bUseRhineLab3DHud)
    {
        DrawRhineLab3DHud(Hud, Canvas, GyroRadS);
        return;
    }
    else if (Rhine3DHud.IsValid())
    {
        Rhine3DHud->SetEnabled(GetWorld(), false);
    }

    UFont* LargeFont = GEngine ? GEngine->GetLargeFont() : nullptr;
    UFont* MedFont = GEngine ? GEngine->GetMediumFont() : LargeFont;
    if (!LargeFont) LargeFont = MedFont;
    UFont* SmallFont = GEngine ? GEngine->GetSmallFont() : MedFont;
    const float W = Canvas->SizeX;
    const float H = Canvas->SizeY;
    const float S = FMath::Clamp(FMath::Min(W / 1920.0f, H / 1080.0f), 0.58f, 1.5f);
    const float CX = W * 0.5f;
    const float CY = H * 0.5f;
    const float TopOriginX = CX - 960.0f * S;

    // =========================================================================
    // 1. TOP OFFICIAL 1:1 ROBOMASTER SCOREBOARD (Texture Chrome + Live Dynamic Fills + DIN Numerals)
    // =========================================================================
    if (TexTopChrome)
    {
        Hud->DrawTexture(TexTopChrome, TopOriginX, 0.0f, 1920.0f * S, 168.0f * S,
            0.0f, 0.0f, 1.0f, 1.0f, FLinearColor::White, BLEND_Translucent);
    }

    const int32 RedBaseHpNow = FieldRules.Get(FieldTeam::Red).BaseHp;
    const int32 BlueBaseHpNow = FieldRules.Get(FieldTeam::Blue).BaseHp;
    const int32 RedOutpostHpNow = FieldRules.Get(FieldTeam::Red).OutpostHp;
    const int32 BlueOutpostHpNow = FieldRules.Get(FieldTeam::Blue).OutpostHp;

    // Live Red Base HP gradient fill (stops cleanly at R emblem edge X=736 so circular emblem stays crisp!)
    const float BaseY = 36.0f * S;
    const float BaseH = 28.0f * S;
    const float BaseSk = 14.0f * S;
    const float RedBasePct = FMath::Clamp(float(RedBaseHpNow) / float(BaseMaxHp), 0.0f, 1.0f);
    const float RBaseL = TopOriginX + 359.0f * S;
    const float RBaseR = TopOriginX + 736.0f * S;
    if (RedBasePct > 0.004f)
    {
        const float FillTop = FMath::Lerp(RBaseR, RBaseL - BaseSk + 2.0f * S, RedBasePct);
        const float FillBot = FMath::Lerp(RBaseR, RBaseL + 2.0f * S, RedBasePct);
        DrawGradientQuad(Canvas,
            FVector2D(FillTop, BaseY + 2.0f * S), FVector2D(RBaseR, BaseY + 2.0f * S),
            FVector2D(RBaseR, BaseY + BaseH - 2.0f * S), FVector2D(FillBot, BaseY + BaseH - 2.0f * S),
            Srgb(254, 166, 42, 248), Srgb(232, 34, 34, 248), 20);
        Hud->DrawLine(FillTop + 2.0f * S, BaseY + 3.0f * S, RBaseR, BaseY + 3.0f * S, Srgb(255, 245, 220, 95), 1.2f);
    }

    // Live Blue Base HP gradient fill (starts cleanly at R emblem right edge X=1184)
    const float BlueBasePct = FMath::Clamp(float(BlueBaseHpNow) / float(BaseMaxHp), 0.0f, 1.0f);
    const float BBaseL = TopOriginX + 1184.0f * S;
    const float BBaseR = TopOriginX + 1561.0f * S;
    if (BlueBasePct > 0.004f)
    {
        const float FillTop = FMath::Lerp(BBaseL, BBaseR + BaseSk - 2.0f * S, BlueBasePct);
        const float FillBot = FMath::Lerp(BBaseL, BBaseR - 2.0f * S, BlueBasePct);
        DrawGradientQuad(Canvas,
            FVector2D(BBaseL, BaseY + 2.0f * S), FVector2D(FillTop, BaseY + 2.0f * S),
            FVector2D(FillBot, BaseY + BaseH - 2.0f * S), FVector2D(BBaseL, BaseY + BaseH - 2.0f * S),
            Srgb(20, 124, 248, 248), Srgb(62, 236, 246, 248), 20);
        Hud->DrawLine(BBaseL, BaseY + 3.0f * S, FillTop - 2.0f * S, BaseY + 3.0f * S, Srgb(220, 250, 255, 95), 1.2f);
    }

    // Live Outer Flank HP Fills (前哨站 / 哨兵)
    auto DrawFlankFill = [&](float BaseLeftPx, int32 Hp, int32 MaxHp, bool bIsRed, bool bIsSentry)
    {
        const float FX = TopOriginX + BaseLeftPx * S;
        const float FY = 43.0f * S;
        const float FW = 104.0f * S;
        const float FH = 21.0f * S;
        const float Sk = (bIsRed ? -1.0f : 1.0f) * 10.0f * S;
        const FVector2D TL(FX + FMath::Min(0.0f, Sk) + 1.5f * S, FY + 1.5f * S);
        const FVector2D TR(FX + FW + FMath::Min(0.0f, Sk) - 1.5f * S, FY + 1.5f * S);
        const FVector2D BR(FX + FW + FMath::Max(0.0f, Sk) - 1.5f * S, FY + FH - 1.5f * S);
        const FVector2D BL(FX + FMath::Max(0.0f, Sk) + 1.5f * S, FY + FH - 1.5f * S);
        const float P = FMath::Clamp(float(Hp) / float(FMath::Max(1, MaxHp)), 0.0f, 1.0f);
        if (P > 0.005f)
        {
            const FLinearColor C1 = bIsRed ? (bIsSentry ? Srgb(250, 138, 38, 248) : Srgb(236, 44, 40, 248))
                                           : (bIsSentry ? Srgb(40, 216, 252, 248) : Srgb(26, 138, 250, 248));
            const FLinearColor C2 = bIsRed ? Srgb(220, 32, 30, 248) : Srgb(18, 108, 236, 248);
            if (bIsRed) DrawGradientQuad(Canvas, FMath::Lerp(TR, TL, P), TR, BR, FMath::Lerp(BR, BL, P), C1, C2, 10);
            else DrawGradientQuad(Canvas, TL, FMath::Lerp(TL, TR, P), FMath::Lerp(BL, BR, P), BL, C2, C1, 10);
        }
        const FString HpStr = FString::Printf(TEXT("%d"), Hp);
        if (TexDigitsAtlas) DrawDinText(Hud, TexDigitsAtlas, HpStr, FX + FW * 0.5f, FY + FH * 0.5f, 16.5f * S, 0);
        else DrawCenteredText(Hud, HpStr, FLinearColor::White, FX + FW * 0.5f, FY + FH * 0.5f, SmallFont, 0.90f * S);
    };
    DrawFlankFill(223.0f, Competitor(FieldTeam::Red).Hp, SentryMaxHp, true, true);
    DrawFlankFill(101.0f, RedOutpostHpNow, OutpostMaxHp, true, false);
    DrawFlankFill(1593.0f, Competitor(FieldTeam::Blue).Hp, SentryMaxHp, false, true);
    DrawFlankFill(1715.0f, BlueOutpostHpNow, OutpostMaxHp, false, false);

    // Live 5-Robot Slanted Bottom HP Underline Fills (aligned pixel-exact to top_hud_chrome.png!)
    auto DrawRobotUnderline = [&](int32 Idx, int32 Hp, int32 MaxHp, bool bIsRed, bool bOwnCard)
    {
        const float CardW = 64.0f * S;
        const float CardH = 42.0f * S;
        const float CardGap = 34.0f * S;
        const float CardY = 94.0f * S;
        const float Sk = 14.0f * S;
        const float X = bIsRed
            ? (TopOriginX + 714.0f * S - float(Idx + 1) * CardW - float(Idx) * CardGap)
            : (TopOriginX + 1206.0f * S + float(Idx) * (CardW + CardGap));
        const FVector2D TL = bIsRed ? FVector2D(X, CardY) : FVector2D(X + Sk, CardY);
        const FVector2D TR = bIsRed ? FVector2D(X + CardW, CardY) : FVector2D(X + CardW + Sk, CardY);
        const FVector2D BR = bIsRed ? FVector2D(X + CardW + Sk, CardY + CardH) : FVector2D(X + CardW, CardY + CardH);
        const FVector2D BL = bIsRed ? FVector2D(X + Sk, CardY + CardH) : FVector2D(X, CardY + CardH);
        if (bOwnCard)
        {
            DrawPolyOutline(Hud, { TL, TR, BR, BL }, Srgb(252, 214, 92, 235), 2.0f * S);
        }
        const float Pct = FMath::Clamp(float(Hp) / float(FMath::Max(1, MaxHp)), 0.0f, 1.0f);
        if (Pct > 0.005f)
        {
            const float BarRatio = 6.0f / 42.0f;
            const FVector2D BTL = FMath::Lerp(TL, BL, 1.0f - BarRatio);
            const FVector2D BTR = FMath::Lerp(TR, BR, 1.0f - BarRatio);
            const FLinearColor C1 = bIsRed ? Srgb(238, 36, 36, 252) : Srgb(22, 130, 250, 252);
            const FLinearColor C2 = bIsRed ? Srgb(255, 118, 48, 252) : Srgb(66, 228, 255, 252);
            if (bIsRed) DrawGradientQuad(Canvas, FMath::Lerp(BTR, BTL, Pct), BTR, BR, FMath::Lerp(BR, BL, Pct), C1, C2, 8);
            else DrawGradientQuad(Canvas, BTL, FMath::Lerp(BTL, BTR, Pct), FMath::Lerp(BL, BR, Pct), BL, C1, C2, 8);
        }
    };
    DrawRobotUnderline(0, Red1Hp, 200, true, false);
    DrawRobotUnderline(1, Red2Hp, 250, true, false);
    DrawRobotUnderline(2, Red3Hp, 200, true, false);
    DrawRobotUnderline(3, Red4Hp, 200, true, false);
    DrawRobotUnderline(4, Competitor(FieldTeam::Red).Hp, SentryMaxHp, true, bFriendlyRed);
    DrawRobotUnderline(0, Blue1Hp, 200, false, false);
    DrawRobotUnderline(1, Blue2Hp, 250, false, false);
    DrawRobotUnderline(2, Blue3Hp, 200, false, false);
    DrawRobotUnderline(3, Blue4Hp, 200, false, false);
    DrawRobotUnderline(4, Competitor(FieldTeam::Blue).Hp, SentryMaxHp, false, !bFriendlyRed);

    // Active Energy Rune Glow Ring when Rune is activated
    if (RuneRules.Get(true).State == FRMUCRuneRules::Phase::Activated)
    {
        DrawArcLine(Hud, FVector2D(TopOriginX + 36.0f * S, 114.0f * S), 22.0f * S, 0, 360,
            bBigEnergyMode ? Srgb(255, 212, 68, 252) : Srgb(78, 246, 255, 252), 3.0f * S, 24);
    }
    if (RuneRules.Get(false).State == FRMUCRuneRules::Phase::Activated)
    {
        DrawArcLine(Hud, FVector2D(TopOriginX + 1884.0f * S, 114.0f * S), 22.0f * S, 0, 360,
            bBigEnergyMode ? Srgb(255, 212, 68, 252) : Srgb(78, 246, 255, 252), 3.0f * S, 24);
    }

    // Crisp DIN Bold Top Numbers (Clock, Scores, Base HP, Gold)
    const int32 Mins = HudClockSeconds() / 60;
    const int32 Secs = HudClockSeconds() % 60;
    const FString ClockStr = FString::Printf(TEXT("%d:%02d"), Mins, Secs);
    const int32 RedGold = bFriendlyRed ? Own().RemainingGoldCoin : Competitor(FRMUCFieldRules::Other(FriendlyTeam())).RemainingGoldCoin;
    const int32 BlueGold = bFriendlyRed ? Competitor(FRMUCFieldRules::Other(FriendlyTeam())).RemainingGoldCoin : Own().RemainingGoldCoin;
    if (TexDigitsAtlas)
    {
        DrawDinText(Hud, TexDigitsAtlas, ClockStr, CX, 43.0f * S, 38.0f * S, 0);
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%d"), RedScore), CX - 108.0f * S, 42.0f * S, 29.0f * S, 0);
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%d"), BlueScore), CX + 108.0f * S, 42.0f * S, 29.0f * S, 0);
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%d"), RedBaseHpNow), RBaseL + 10.0f * S, BaseY + BaseH * 0.5f, 21.0f * S, -1);
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%d"), BlueBaseHpNow), BBaseR - 10.0f * S, BaseY + BaseH * 0.5f, 21.0f * S, 1);
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%d"), RedGold), CX - 56.0f * S, 105.0f * S, 19.0f * S, 0);
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%d"), BlueGold), CX + 56.0f * S, 105.0f * S, 19.0f * S, 0);
    }
    else
    {
        DrawCenteredText(Hud, ClockStr, FLinearColor::White, CX, 42.0f * S, LargeFont, 1.75f * S);
    }
    DrawShadowText(Hud, FieldRules.BaseProtected(FieldTeam::Red) ? TEXT("PROTECTED") :
            FieldVisuals.IsBaseLargeArmorExposed(true) ? TEXT("LARGE ARMOR") : TEXT("SMALL ARMOR"),
        Srgb(220, 236, 248, 225), RBaseL + 6.0f * S, BaseY + BaseH + 4.0f * S, SmallFont, 0.76f * S);
    DrawRightText(Hud, FieldRules.BaseProtected(FieldTeam::Blue) ? TEXT("PROTECTED") :
            FieldVisuals.IsBaseLargeArmorExposed(false) ? TEXT("LARGE ARMOR") : TEXT("SMALL ARMOR"),
        Srgb(220, 236, 248, 225), BBaseR - 6.0f * S, BaseY + BaseH + 4.0f * S, SmallFont, 0.76f * S);

    // =========================================================================
    // 2. CENTER LONE TRAIL 3D ORBITAL RINGS, SAND-DOT HEAT GLASS & MILKY STACK
    //    (All other 2D HUD modules remain 100% untouched per user instruction)
    // =========================================================================
    const float DtHud = FMath::Clamp(GetWorld() ? GetWorld()->GetDeltaSeconds() : 0.016f, 0.001f, 0.05f);
    ReticleTimeSec += DtHud;
    const float T = static_cast<float>(ReticleTimeSec);

    // Recoil pulse when a new shot is fired
    if (Own().TotalShotsFired > PrevShotsForReticle)
    {
        ReticleRecoil = FMath::Min(1.0, ReticleRecoil + 0.55);
        PrevShotsForReticle = Own().TotalShotsFired;
    }
    ReticleRecoil = FMath::Max(0.0, ReticleRecoil - DtHud * 4.5);

    // Auto-Aim Lock detection: right-mouse button OR Q key OR enemy sentry near aim center
    bool bAutoAimActive = false;
    if (APlayerController* PC = GetWorld() ? GetWorld()->GetFirstPlayerController() : nullptr)
    {
        if (PC->IsInputKeyDown(EKeys::RightMouseButton) || PC->IsInputKeyDown(EKeys::Q))
        {
            bAutoAimActive = true;
        }
    }
    if (!bAutoAimActive && EnemyRig && Rig && IsValid(EnemyRig) && IsValid(Rig))
    {
        const FVector ToEnemy = (EnemyRig->GetActorLocation() - Rig->GetActorLocation()).GetSafeNormal();
        const FVector AimDir = Rig->GetActorForwardVector();
        if (FVector::DotProduct(ToEnemy, AimDir) > 0.988f)
        {
            bAutoAimActive = true;
        }
    }
    ReticleAutoAimBlend += ((bAutoAimActive ? 1.0 : 0.0) - ReticleAutoAimBlend) * FMath::Min(1.0, DtHud * 9.0);
    const float Lock = static_cast<float>(ReticleAutoAimBlend);
    const float Rec = static_cast<float>(ReticleRecoil);

    // Subtle gimbal-coupled parallax for the 3D orbital rings
    double GimYawR = 0.0, GimPitchR = 0.0;
    if (Rig) Rig->GetGimbalAngles(GimYawR, GimPitchR);
    const float ParX = FMath::Clamp(float(FMath::Sin(GimYawR)) * 0.22f, -0.22f, 0.22f);
    const float ParY = FMath::Clamp(float(GimPitchR) * 0.25f, -0.18f, 0.18f);

    const float BigArcR = 268.0f * S;
    const float SightR = 68.0f * S;

    // Helper: 3D rotation & perspective projection for Lone Trail tilted orbital rings
    auto ProjOrbital = [&](float Radius, float AngRad, float Rx, float Ry, float Rz) -> FVector2D
    {
        const float X0 = FMath::Cos(AngRad) * Radius;
        const float Y0 = FMath::Sin(AngRad) * Radius;
        const float Cz = FMath::Cos(Rz), Sz = FMath::Sin(Rz);
        const float X1 = X0 * Cz - Y0 * Sz;
        const float Y1 = X0 * Sz + Y0 * Cz;
        const float Cy = FMath::Cos(Ry), Sy = FMath::Sin(Ry);
        const float X2 = X1 * Cy;
        const float Z2 = -X1 * Sy;
        const float Cx = FMath::Cos(Rx), Sx = FMath::Sin(Rx);
        const float Y3 = Y1 * Cx - Z2 * Sx;
        const float Z3 = Y1 * Sx + Z2 * Cx;
        const float Fov = 620.0f * S;
        const float Sc = Fov / FMath::Max(120.0f * S, Fov + Z3);
        return FVector2D(CX + X2 * Sc, CY + Y3 * Sc);
    };

    auto DrawOrbitalRing3D = [&](float Radius, float Rx, float Ry, float Rz,
        const FLinearColor& Col, float Thick, int32 Segs, bool bDashed, int32 Ticks)
    {
        for (int32 I = 0; I < Segs; ++I)
        {
            if (bDashed && (I % 2 == 1)) continue;
            const float A0 = (float(I) / float(Segs)) * 2.0f * PI;
            const float A1 = (float(I + 1) / float(Segs)) * 2.0f * PI;
            const FVector2D P0 = ProjOrbital(Radius, A0, Rx, Ry, Rz);
            const FVector2D P1 = ProjOrbital(Radius, A1, Rx, Ry, Rz);
            Hud->DrawLine(P0.X, P0.Y, P1.X, P1.Y, Col, Thick);
        }
        for (int32 K = 0; K < Ticks; ++K)
        {
            const float A = (float(K) / float(Ticks)) * 2.0f * PI;
            const float Len = (K % 4 == 0) ? 7.0f * S : 3.5f * S;
            const FVector2D P0 = ProjOrbital(Radius - 3.0f * S, A, Rx, Ry, Rz);
            const FVector2D P1 = ProjOrbital(Radius + Len, A, Rx, Ry, Rz);
            Hud->DrawLine(P0.X, P0.Y, P1.X, P1.Y, Col, Thick);
        }
    };

    // 2A. 3-Layer Tilted Counter-Rotating Lone Trail Orbital Rings (exact match to uploaded reference!)
    const float TiltFade = 1.0f - Lock * 0.80f;
    const float OuterOrbR = (232.0f * (1.0f - Lock * 0.14f) + Rec * 16.0f) * S;
    const float MidOrbR   = (166.0f * (1.0f - Lock * 0.12f) + Rec * 10.0f) * S;
    const float InnerOrbR = (96.0f  * (1.0f - Lock * 0.10f) + Rec * 5.0f)  * S;

    const FLinearColor RingOuterCol = Lock > 0.45f ? Srgb(236, 206, 164, 150) : Srgb(242, 240, 234, 92);
    const FLinearColor RingMidCol   = Lock > 0.45f ? Srgb(218, 184, 138, 195) : Srgb(176, 190, 188, 128);
    const FLinearColor RingInCol    = Srgb(242, 240, 234, 165);

    DrawOrbitalRing3D(OuterOrbR, 0.34f * TiltFade + ParY * 0.18f, -0.26f * TiltFade + ParX * 0.22f,  T * 0.22f, RingOuterCol, 1.15f, 72, true,  24);
    DrawOrbitalRing3D(MidOrbR,  -0.42f * TiltFade - ParY * 0.12f,  0.36f * TiltFade - ParX * 0.16f, -T * 0.38f, RingMidCol,   1.30f, 64, true,  12);
    DrawOrbitalRing3D(InnerOrbR, 0.16f * TiltFade,                -0.18f * TiltFade,                 T * 0.65f, RingInCol,    1.10f, 48, true,  0);

    // Auto-Aim Lock Champagne-Gold Diamond snapping smoothly around crosshair
    if (Lock > 0.04f)
    {
        const float D = (34.0f - Lock * 9.0f) * S;
        const float RotD = (1.0f - Lock) * 0.55f;
        const float Cd = FMath::Cos(RotD), Sd = FMath::Sin(RotD);
        const FVector2D Pts[4] = {
            FVector2D(CX + (0 * Cd - (-D) * Sd), CY + (0 * Sd + (-D) * Cd)),
            FVector2D(CX + (D * Cd -   0  * Sd), CY + (D * Sd +   0  * Cd)),
            FVector2D(CX + (0 * Cd -   D  * Sd), CY + (0 * Sd +   D  * Cd)),
            FVector2D(CX + ((-D) * Cd - 0 * Sd), CY + ((-D) * Sd + 0 * Cd))
        };
        const FLinearColor LockCol = Srgb(236, 206, 164, static_cast<uint8>(Lock * 235.0f));
        for (int32 I = 0; I < 4; ++I)
        {
            Hud->DrawLine(Pts[I].X, Pts[I].Y, Pts[(I + 1) % 4].X, Pts[(I + 1) % 4].Y, LockCol, 1.6f * S);
        }
    }

    // Clean Precision Crosshair + Ballistic Elevation Drop Ticks
    const float Gap = (9.0f + Rec * 8.0f - Lock * 3.0f) * S;
    const float Arm = 18.0f * S;
    const FLinearColor CrossCol = Lock > 0.45f ? Srgb(236, 206, 164, 245) : Srgb(242, 240, 234, 242);
    Hud->DrawLine(CX - Gap - Arm, CY, CX - Gap, CY, CrossCol, 1.6f * S);
    Hud->DrawLine(CX + Gap, CY, CX + Gap + Arm, CY, CrossCol, 1.6f * S);
    Hud->DrawLine(CX, CY - Gap - Arm * 0.8f, CX, CY - Gap, CrossCol, 1.6f * S);
    Hud->DrawLine(CX, CY + Gap, CX, CY + Gap + Arm * 1.25f, CrossCol, 1.6f * S);
    for (int32 D = 1; D <= 3; ++D)
    {
        const float Ty = CY + Gap + 8.5f * D * S;
        const float Tw = (9.5f - D * 2.1f) * S;
        Hud->DrawLine(CX - Tw, Ty, CX + Tw, Ty, CrossCol, 1.3f * S);
    }
    DrawCircleFilled(Canvas, FVector2D(CX, CY), 2.4f * S, Rec > 0.1f ? Srgb(218, 92, 62, 250) : CrossCol, 10);

    // 2B. LEFT HEAT ARC: Tight 10%-Peek Receding Translucent Glass Layers + Fine Sand-Grain Top-Down Dots!
    const float HeatPct = FMath::Clamp(float(Own().BarrelHeat / BarrelHeatLimit), 0.0f, 1.0f);
    const float HeatW = 20.0f * S;            // Translucent glass arc band thickness
    const float HeatPeekStep = HeatW * 0.10f; // ONLY 10% exposed per layer -> ~90% overlap, almost flush!
    const int32 ActiveLayers = 1 + FMath::Clamp(FMath::FloorToInt(HeatPct * 3.2f), 0, 3);

    // Draw receding glass layers BACK-TO-FRONT (k = ActiveLayers-1 down to 0, receding AWAY from camera!)
    for (int32 K = ActiveLayers - 1; K >= 0; --K)
    {
        // Receding depth away from camera: slightly smaller radius (10% peek inward) + softer translucent alpha
        const float DepthShrink = 1.0f - float(K) * 0.008f;
        const float LayerROut = (BigArcR - float(K) * HeatPeekStep) * DepthShrink;
        const float LayerRIn  = LayerROut - HeatW * DepthShrink;
        const float AngInset  = float(K) * 1.2f;
        const uint8 GlassA = static_cast<uint8>(FMath::Max(20, 62 - K * 14));
        const uint8 RimA   = static_cast<uint8>(FMath::Max(32, 118 - K * 24));
        DrawArcBand(Canvas, FVector2D(CX, CY), LayerRIn, LayerROut, 144.0f + AngInset, 216.0f - AngInset,
            Srgb(195, 206, 204, GlassA), Srgb(142, 158, 156, GlassA), 28);
        // Thin translucent outer specular rim
        DrawArcBand(Canvas, FVector2D(CX, CY), LayerROut - 1.2f * S, LayerROut, 144.0f + AngInset, 216.0f - AngInset,
            Srgb(242, 240, 234, RimA), Srgb(218, 184, 138, RimA), 28);
    }

    // Fine Sand-Grain Micro-Dots accumulating TOP-TO-BOTTOM (144° -> 216°) inside the translucent glass arc!
    if (HeatPct > 0.005f)
    {
        const int32 TotalGrains = 168;
        const int32 FilledGrains = FMath::Clamp(FMath::RoundToInt(TotalGrains * HeatPct), 1, TotalGrains);
        for (int32 G = 0; G < FilledGrains; ++G)
        {
            // Deterministic micro-sand jitter (multi-column organic grain packing)
            const uint32 Hash = static_cast<uint32>(G * 2654435761u);
            const float JitterR = ((Hash & 0xFFu) / 255.0f - 0.5f) * (HeatW - 5.0f * S);
            const float JitterA = (((Hash >> 8) & 0xFFu) / 255.0f - 0.5f) * 0.9f;
            const float U = float(G) / float(TotalGrains - 1); // 0 = top (144°), 1 = bottom (216°)
            // Subtle settling sparkle on the newest accumulating grains near the front
            const float FrontDist = float(FilledGrains - 1 - G) / 10.0f;
            const float FallOffset = (FrontDist < 1.0f && Rec > 0.02f) ? (1.0f - FrontDist) * 2.2f * Rec : 0.0f;
            const float AngDeg = 145.5f + U * 69.0f + JitterA - FallOffset;
            const float AngRad = FMath::DegreesToRadians(AngDeg);
            const float GrainR = (BigArcR - HeatW * 0.5f) + JitterR;
            const FVector2D Pos(CX + FMath::Cos(AngRad) * GrainR, CY + FMath::Sin(AngRad) * GrainR);

            // Preserve top-to-bottom Lone Trail color gradient: Vermilion -> Warm Amber -> Champagne Gold
            FLinearColor GCol;
            if (U < 0.5f)
            {
                GCol = FLinearColor::LerpUsingHSV(Srgb(214, 78, 56, 235), Srgb(226, 134, 74, 228), U * 2.0f);
            }
            else
            {
                GCol = FLinearColor::LerpUsingHSV(Srgb(226, 134, 74, 228), Srgb(224, 192, 138, 220), (U - 0.5f) * 2.0f);
            }
            const float GrainRad = (1.25f + ((Hash >> 16) & 0x7u) * 0.09f) * S;
            DrawCircleFilled(Canvas, Pos, GrainRad, GCol, 6);
        }
    }

    // 2C. RIGHT SUPERCAP / BUFFER ENERGY ARC (clean mint-sage curved arc matching reference!)
    const float BufPct = FMath::Clamp(float(Own().ChassisBufferEnergyJ / 60.0), 0.0f, 1.0f);
    DrawArcBand(Canvas, FVector2D(CX, CY), BigArcR - 6.0f * S, BigArcR, -36.0f, 36.0f,
        Srgb(242, 240, 234, 34), Srgb(242, 240, 234, 34), 24);
    if (BufPct > 0.01f)
    {
        const FLinearColor BColA = BufPct < 0.30f ? Srgb(214, 88, 62, 240) : Srgb(98, 196, 158, 235);
        const FLinearColor BColB = BufPct < 0.30f ? Srgb(226, 154, 82, 240) : Srgb(112, 214, 184, 242);
        DrawArcBand(Canvas, FVector2D(CX, CY), BigArcR - 6.0f * S, BigArcR,
            36.0f - 72.0f * BufPct, 36.0f, BColB, BColA, 22);
    }

    // 2D. RIGHT-SIDE MILKY-WHITE FROSTED GLASS STACK (Lower transparency than heat bar,
    //     almost flush ~90% overlap with ONLY 10% peek per layer toward upper-right, receding AWAY from camera!)
    const float CardW = 196.0f * S;
    const float CardH = 114.0f * S;
    const float CardX = CX + BigArcR + 28.0f * S;
    const float CardY = CY - CardH * 0.5f;
    const float PeekX = CardW * 0.10f; // Exactly 10% horizontal exposure per receding layer
    const float PeekY = CardH * 0.10f; // Exactly 10% vertical exposure toward upper-right

    // Subtle leader line connecting Right Arc to Front Milky-White Glass Plate
    Hud->DrawLine(CX + BigArcR + 2.0f * S, CY - 14.0f * S, CardX, CY - 14.0f * S, Srgb(218, 184, 138, 190), 1.4f * S);

    // Draw 3 receding Milky-White Frosted Glass layers BACK-TO-FRONT (k = 2, 1, 0 -> receding away from camera!)
    for (int32 K = 2; K >= 0; --K)
    {
        const float ScaleD = 1.0f - float(K) * 0.018f; // Perspective shrink away from camera
        const float LW = CardW * ScaleD;
        const float LH = CardH * ScaleD;
        const float LX = CardX + float(K) * PeekX;
        const float LY = CardY - float(K) * PeekY;
        // Lower transparency (higher alpha) than the translucent heat arc!
        const uint8 MilkA = (K == 0) ? 216 : ((K == 1) ? 168 : 122);
        const uint8 EdgeA = (K == 0) ? 235 : ((K == 1) ? 165 : 110);
        const FLinearColor MilkFill = (K == 0) ? Srgb(242, 240, 235, MilkA)
            : ((K == 1) ? Srgb(228, 228, 224, MilkA) : Srgb(212, 214, 212, MilkA));
        const float Cr = 4.0f * S; // Small crisp corner bevel
        // Main frosted milky-white plate
        DrawQuad(Canvas, FVector2D(LX + Cr, LY), FVector2D(LX + LW - Cr, LY),
            FVector2D(LX + LW, LY + LH), FVector2D(LX, LY + LH), MilkFill);
        DrawQuad(Canvas, FVector2D(LX, LY + Cr), FVector2D(LX + LW, LY + Cr),
            FVector2D(LX + LW, LY + LH), FVector2D(LX, LY + LH), MilkFill);
        // Subtle specular top/left rim
        Hud->DrawLine(LX + Cr, LY, LX + LW - Cr, LY, Srgb(255, 255, 252, EdgeA), 1.2f * S);
        Hud->DrawLine(LX, LY + Cr, LX, LY + LH, Srgb(255, 255, 252, static_cast<uint8>(EdgeA * 0.7f)), 1.0f * S);
        Hud->DrawLine(LX, LY + LH, LX + LW, LY + LH, Srgb(140, 150, 148, static_cast<uint8>(EdgeA * 0.6f)), 1.0f * S);
        Hud->DrawLine(LX + LW, LY + Cr, LX + LW, LY + LH, Srgb(140, 150, 148, static_cast<uint8>(EdgeA * 0.6f)), 1.0f * S);
    }

    // Strict Typography on Front Milky-White Frosted Plate:
    // - Tiny Monospace for telemetry headers/codes
    // - Wide-tracked tabular DIN digits for big numbers (Ammo / Velocity)
    const FLinearColor InkDark = Srgb(24, 28, 30, 245);
    const FLinearColor InkSub  = Srgb(86, 98, 96, 235);
    const FLinearColor InkGold = Srgb(148, 108, 56, 245);
    const FLinearColor InkMint = Srgb(38, 128, 98, 245);
    Hud->DrawText(TEXT("BALLISTICS // 17MM"), InkGold, CardX + 14.0f * S, CardY + 10.0f * S, SmallFont, 0.72f * S);
    if (TexDigitsAtlas)
    {
        DrawDinText(Hud, TexDigitsAtlas, FString::FromInt(Own().ProjectileAllowance17mm), CardX + 14.0f * S, CardY + 46.0f * S, 28.0f * S, -1, InkDark);
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%.1f"), LaunchSpeedCmS * 0.01), CardX + 62.0f * S, CardY + 80.0f * S, 17.0f * S, -1, InkDark);
    }
    Hud->DrawText(TEXT("RDS"), InkSub, CardX + 96.0f * S, CardY + 42.0f * S, SmallFont, 0.72f * S);
    Hud->DrawLine(CardX + 14.0f * S, CardY + 64.0f * S, CardX + CardW - 14.0f * S, CardY + 64.0f * S, Srgb(92, 104, 102, 110), 1.0f);
    Hud->DrawText(TEXT("VEL"), InkSub, CardX + 14.0f * S, CardY + 74.0f * S, SmallFont, 0.72f * S);
    Hud->DrawText(TEXT("m/s"), InkSub, CardX + 114.0f * S, CardY + 74.0f * S, SmallFont, 0.72f * S);
    Hud->DrawText(Lock > 0.45f ? TEXT("LOCK // TARGET ENGAGED") : TEXT("MODE // FREE AIM"),
        Lock > 0.45f ? InkMint : InkSub, CardX + 14.0f * S, CardY + 94.0f * S, SmallFont, 0.68f * S);

    // 4-Direction Curved Armor-Hit Warning Arcs hugging the sight ring
    if (Own().OwnHitHudFlash > 0.0)
    {
        const uint8 Alpha = static_cast<uint8>(FMath::Clamp(Own().OwnHitHudFlash * 420.0, 50.0, 245.0));
        const FLinearColor HitWarn = Srgb(218, 62, 52, Alpha);
        const float StartDeg = (Own().LastArmorId == 0) ? -125.0f : (Own().LastArmorId == 1) ? 145.0f : (Own().LastArmorId == 2) ? 55.0f : -35.0f;
        DrawArcBand(Canvas, FVector2D(CX, CY), SightR + 6.0f * S, SightR + 16.0f * S, StartDeg, StartDeg + 70.0f, HitWarn, HitWarn, 18);
    }

    // Crisp Hit-Confirm X + Damage Popup
    if (Own().HitConfirmHudFlash > 0.0)
    {
        const FLinearColor HitCol = Srgb(218, 72, 54, 250);
        Hud->DrawLine(CX - 14 * S, CY - 14 * S, CX - 5 * S, CY - 5 * S, HitCol, 2.4f);
        Hud->DrawLine(CX + 14 * S, CY - 14 * S, CX + 5 * S, CY - 5 * S, HitCol, 2.4f);
        Hud->DrawLine(CX - 14 * S, CY + 14 * S, CX - 5 * S, CY + 5 * S, HitCol, 2.4f);
        Hud->DrawLine(CX + 14 * S, CY + 14 * S, CX + 5 * S, CY + 5 * S, HitCol, 2.4f);
        if (TexDigitsAtlas)
            DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("-%d"), Own().LastHitDamageShown), CX + 22.0f * S, CY - 16.0f * S, 24.0f * S, -1, Srgb(236, 206, 164, 255));
    }

    // =========================================================================
    // 3. BOTTOM-LEFT 1:1 OFFICIAL AVATAR, SLANTED HP/POWER & CHINESE MODULE STRIP
    // =========================================================================
    UTexture2D* BottomLeftTex = bFriendlyRed ? TexBottomLeftRed : TexBottomLeftBlue;
    if (BottomLeftTex)
    {
        Hud->DrawTexture(BottomLeftTex, 0.0f, H - 240.0f * S, 760.0f * S, 240.0f * S,
            0.0f, 0.0f, 1.0f, 1.0f, FLinearColor::White, BLEND_Translucent);
    }

    const float AvX = 86.0f * S;
    const float AvY = H - 108.0f * S;
    const float HpStart = 146.0f * S;
    const float HpEnd = 462.0f * S;
    const float HpW = HpEnd - HpStart;
    const float HpY = H - 120.0f * S;
    const float HpH = 33.0f * S;
    const float HpSkew = 19.0f * S;
    const float HpPct = FMath::Clamp(float(GetOwnHp()) / float(SentryMaxHp), 0.0f, 1.0f);
    const float TrailPct = FMath::Clamp(float(DisplayedOwnHp) / float(SentryMaxHp), 0.0f, 1.0f);

    if (TrailPct > HpPct)
    {
        DrawQuad(Canvas,
            FVector2D(HpStart, HpY + 2.0f * S), FVector2D(HpStart + (HpW + HpSkew) * TrailPct, HpY + 2.0f * S),
            FVector2D(HpStart + HpW * TrailPct, HpY + HpH - 2.0f * S), FVector2D(HpStart, HpY + HpH - 2.0f * S),
            Srgb(255, 226, 165, 215));
    }
    if (HpPct > 0.005f)
    {
        const FLinearColor HLeft = bFriendlyRed ? Srgb(220, 32, 32, 248) : Srgb(20, 114, 244, 248);
        const FLinearColor HRight = bFriendlyRed ? Srgb(255, 86, 62, 248) : Srgb(62, 224, 255, 248);
        DrawGradientQuad(Canvas,
            FVector2D(HpStart, HpY + 2.0f * S), FVector2D(HpStart + (HpW + HpSkew - 2.0f * S) * HpPct, HpY + 2.0f * S),
            FVector2D(HpStart + (HpW - 2.0f * S) * HpPct, HpY + HpH - 2.0f * S), FVector2D(HpStart, HpY + HpH - 2.0f * S),
            HLeft, HRight, 18);
        Hud->DrawLine(HpStart + 4.0f * S, HpY + 3.0f * S, HpStart + (HpW + HpSkew - 4.0f * S) * HpPct, HpY + 3.0f * S, Srgb(255, 255, 255, 95), 1.2f);
    }
    if (TexDigitsAtlas)
    {
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%d / %d"), GetOwnHp(), SentryMaxHp),
            HpStart + 22.0f * S, HpY + HpH * 0.5f, 23.0f * S, -1);
    }

    // Slim Glowing Cyan Chassis Power / Buffer Strip
    const float PwrStart = 146.0f * S;
    const float PwrW = 232.0f * S;
    const float PwrY = H - 78.0f * S;
    const float PwrH = 9.0f * S;
    const float PwrSkew = 7.0f * S;
    if (BufPct > 0.01f)
    {
        DrawGradientQuad(Canvas,
            FVector2D(PwrStart, PwrY + 1.0f * S), FVector2D(PwrStart + (PwrW + PwrSkew) * BufPct, PwrY + 1.0f * S),
            FVector2D(PwrStart + PwrW * BufPct, PwrY + PwrH - 1.0f * S), FVector2D(PwrStart, PwrY + PwrH - 1.0f * S),
            Srgb(122, 248, 234, 248), Srgb(218, 255, 252, 252), 12);
    }
    if (TexDigitsAtlas)
    {
        DrawDinText(Hud, TexDigitsAtlas, FString::Printf(TEXT("%.1f W"), Own().ChassisPowerW),
            PwrStart + PwrW + 14.0f * S, PwrY + PwrH * 0.5f, 16.0f * S, -1);
    }

    // Live Rotating Top-Down 4-Wheel Sentry Diagram inside Circular Avatar Aperture!
    const FLinearColor TeamAccent = bFriendlyRed ? Srgb(242, 46, 40, 248) : Srgb(28, 150, 255, 248);
    double GimbalYawRad = 0.0, GimbalPitchRad = 0.0;
    if (Rig) Rig->GetGimbalAngles(GimbalYawRad, GimbalPitchRad);
    const float ChassisRelAng = float(-GimbalYawRad);
    const float Ca = FMath::Cos(ChassisRelAng), Sa = FMath::Sin(ChassisRelAng);
    auto RotChassis = [&](float LX, float LY) -> FVector2D
    {
        return FVector2D(AvX + (LX * Ca - LY * Sa) * S, AvY + (LX * Sa + LY * Ca) * S);
    };
    const float Wx[4] = { -19, 13, -19, 13 };
    const float Wy[4] = { -22, -22, 10, 10 };
    for (int32 K = 0; K < 4; ++K)
    {
        DrawQuad(Canvas, RotChassis(Wx[K], Wy[K]), RotChassis(Wx[K] + 6, Wy[K]),
            RotChassis(Wx[K] + 6, Wy[K] + 12), RotChassis(Wx[K], Wy[K] + 12), Srgb(14, 16, 22, 245));
    }
    DrawQuad(Canvas, RotChassis(-15, -20), RotChassis(15, -20), RotChassis(15, 20), RotChassis(-15, 20), Srgb(78, 90, 108, 235));
    Hud->DrawLine(RotChassis(-8, -21).X, RotChassis(-8, -21).Y, RotChassis(8, -21).X, RotChassis(8, -21).Y, TeamAccent, 3.0f * S);
    Hud->DrawLine(RotChassis(-8, 21).X, RotChassis(-8, 21).Y, RotChassis(8, 21).X, RotChassis(8, 21).Y, TeamAccent, 3.0f * S);
    Hud->DrawLine(RotChassis(-16, -8).X, RotChassis(-16, -8).Y, RotChassis(-16, 8).X, RotChassis(-16, 8).Y, TeamAccent, 3.0f * S);
    Hud->DrawLine(RotChassis(16, -8).X, RotChassis(16, -8).Y, RotChassis(16, 8).X, RotChassis(16, 8).Y, TeamAccent, 3.0f * S);
    Hud->DrawLine(RotChassis(-8, -23).X, RotChassis(-8, -23).Y, RotChassis(0, -30).X, RotChassis(0, -30).Y, Srgb(255, 232, 120, 245), 2.2f * S);
    Hud->DrawLine(RotChassis(8, -23).X, RotChassis(8, -23).Y, RotChassis(0, -30).X, RotChassis(0, -30).Y, Srgb(255, 232, 120, 245), 2.2f * S);
    DrawQuad(Canvas, FVector2D(AvX - 3.0f * S, AvY - 37.0f * S), FVector2D(AvX + 3.0f * S, AvY - 37.0f * S),
        FVector2D(AvX + 3.0f * S, AvY + 4.0f * S), FVector2D(AvX - 3.0f * S, AvY + 4.0f * S), Srgb(235, 244, 252, 245));
    DrawCircleFilled(Canvas, FVector2D(AvX, AvY), 9.5f * S, Srgb(202, 216, 232, 242), 16);
    DrawCircleFilled(Canvas, FVector2D(AvX, AvY), 5.0f * S, Srgb(32, 40, 50, 248), 14);

    // Live Diagonal "/" LED Ticks aligned pixel-exact before the Chinese labels in bottom_left_*.png!
    const float ModY = H - 30.0f * S;
    const bool bAlive = IsOwnAlive();
    const bool bAnyRfid = Own().bRfidSupplyExchange || Own().bRfidCentralHighland || Own().bRfidFortress || Own().bRfidBase || Own().bRfidOutpost;
    const float ModTickX[7] = { 42.0f, 120.0f, 198.0f, 262.0f, 326.0f, 390.0f, 454.0f };
    const int32 ModState[7] = {
        bAlive ? 2 : 0,
        2,
        2,
        bAnyRfid ? 2 : 1,
        (bAlive && Own().ChassisBufferEnergyJ > 5.0) ? 2 : 0,
        bAlive ? 2 : 0,
        (bAlive && Own().BarrelHeat < BarrelHeatLimit) ? 2 : 0
    };
    for (int32 I = 0; I < 7; ++I)
    {
        const float MX = ModTickX[I] * S;
        const FLinearColor TickCol = (ModState[I] == 2) ? Srgb(112, 246, 148, 250)
            : ((ModState[I] == 1) ? Srgb(245, 198, 78, 238) : Srgb(240, 56, 50, 248));
        DrawQuad(Canvas,
            FVector2D(MX + 5 * S, ModY - 7 * S), FVector2D(MX + 9 * S, ModY - 7 * S),
            FVector2D(MX + 4 * S, ModY + 7 * S), FVector2D(MX, ModY + 7 * S), TickCol);
    }

    // =========================================================================
    // 4. BOTTOM-RIGHT OFFICIAL ARCHITECTURAL RMUC2027 TACTICAL MINIMAP
    // =========================================================================
    const float MiniW = 380.0f * S;
    const float MiniH = 220.0f * S;
    const float MiniX = W - MiniW - 24.0f * S;
    const float MiniY = H - MiniH - 22.0f * S;
    DrawTacticalMinimap(Hud, Canvas, MiniX, MiniY, MiniW, MiniH);

    const FClientRuneStatus RuneClient = ClientRuneStatus(RuneRules, GameProgress, MatchElapsedSeconds,
        bFriendlyRed, IsOwnAlive(), FieldVisuals.IsRuneReady());
    const float RuneY = MiniY - 95.0f * S;
    const FLinearColor RuneAccent = RuneClient.bAvailable ? Srgb(112, 255, 178, 252) : Srgb(255, 236, 160, 255);
    Hud->DrawRect(Srgb(15, 23, 36, 235), MiniX, RuneY, MiniW, 44.0f * S);
    Hud->DrawRect(RuneAccent, MiniX, RuneY, 2.0f * S, 44.0f * S);
    DrawShadowText(Hud, TEXT("[T] REQUEST ENERGY RUNE  /  S7"), RuneAccent,
        MiniX + 10.0f * S, RuneY + 4.0f * S, SmallFont, 1.0f * S);
    DrawShadowText(Hud, RuneClient.Text, Srgb(240, 246, 252, 255),
        MiniX + 10.0f * S, RuneY + 23.0f * S, SmallFont, .88f * S);

    // =========================================================================
    // 5. BOTTOM-CENTER GLOWING TRAPEZOID PLATE, BUFF PILLS & LEFT EVENT FEED
    // =========================================================================
    if (TexBottomCenter)
    {
        Hud->DrawTexture(TexBottomCenter, CX - 240.0f * S, H - 68.0f * S, 480.0f * S, 68.0f * S,
            0.0f, 0.0f, 1.0f, 1.0f, FLinearColor::White, BLEND_Translucent);
    }

    FString Badges;
    if (Own().bRfidSupplyExchange) Badges += TEXT("[SUPPLY: F3 AMMO] ");
    if (Own().BuffRecovery > 0) Badges += FString::Printf(TEXT("[RECOVERY %d%%/s] "), Own().BuffRecovery);
    if (Own().bRfidCentralHighland) Badges += TEXT("[CENTRAL HIGHLAND] ");
    const unsigned OwnIndex = FRMUCFieldRules::Index(FriendlyTeam());
    const auto EnemyTeam = FRMUCFieldRules::Other(FriendlyTeam());
    const unsigned EnemyIndex = FRMUCFieldRules::Index(EnemyTeam);
    const auto& OwnPoints = FieldPointEffects[OwnIndex];
    const auto& OwnContacts = FieldPhysicalContacts[OwnIndex];
    if (OwnPoints.bFortress) Badges += FString::Printf(TEXT("[FORTRESS  %d RESERVE] "),
        Own().OwnLogistics.FortressReserve(OwnPoints));
    else if (OwnPoints.bEnemyFortress)
    {
        const auto& Enemy = FieldRules.Get(EnemyTeam);
        Badges += Enemy.bBaseArmorOpen ? TEXT("[ENEMY ARMOR OPEN] ")
            : FString::Printf(TEXT("[ENEMY FORTRESS  %.1f/%.0fs  +100%% VULNERABILITY] "),
                Enemy.ArmorActivationSeconds, FRMUCFieldRules::EnemyFortressActivationSeconds);
    }
    else if (OwnContacts[OwnIndex].Fortress.value_or(false) || OwnContacts[EnemyIndex].Fortress.value_or(false))
    {
        Badges += TEXT("[FORTRESS LOCKED] ");
    }
    if (Own().EnergyBuffTimer > 0.0) Badges += FString::Printf(TEXT("[RUNE DEF+%d%% ATK+%d%% %.0fs] "), Own().BuffDefence, Own().BuffAttack, Own().EnergyBuffTimer);
    if (!Badges.IsEmpty())
    {
        const float PillW = 420.0f * S;
        const float PillY = H - 102.0f * S;
        DrawQuad(Canvas,
            FVector2D(CX - PillW * 0.5f + 10 * S, PillY), FVector2D(CX + PillW * 0.5f - 10 * S, PillY),
            FVector2D(CX + PillW * 0.5f, PillY + 24 * S), FVector2D(CX - PillW * 0.5f, PillY + 24 * S),
            Srgb(16, 48, 38, 212));
        DrawCenteredText(Hud, Badges, Srgb(112, 255, 178, 252), CX, PillY + 12.0f * S, SmallFont, 0.88f * S);
    }

    // Left-mid translucent event log lines
    for (int32 I = 0; I < RecentEvents.Num(); ++I)
    {
        const uint8 A = static_cast<uint8>(FMath::Max(110, 240 - I * 45));
        DrawShadowText(Hud, RecentEvents[I], Srgb(240, 246, 252, A), 30.0f * S, H - (265.0f + I * 24.0f) * S, SmallFont, 0.88f * S);
    }

    // Center-top transient event alert banner
    if (BannerEventTimer > 0.0 && !BannerEventText.IsEmpty())
    {
        const float BanW = 460.0f * S;
        const float BanY = 148.0f * S;
        DrawQuad(Canvas,
            FVector2D(CX - BanW * 0.5f + 14 * S, BanY), FVector2D(CX + BanW * 0.5f - 14 * S, BanY),
            FVector2D(CX + BanW * 0.5f, BanY + 30 * S), FVector2D(CX - BanW * 0.5f, BanY + 30 * S),
            Srgb(20, 26, 36, 218));
        Hud->DrawLine(CX - BanW * 0.5f + 14 * S, BanY, CX + BanW * 0.5f - 14 * S, BanY, Srgb(112, 236, 255, 240), 2.0f);
        DrawCenteredText(Hud, BannerEventText, Srgb(255, 236, 160, 255), CX, BanY + 15.0f * S, MedFont, 0.96f * S);
    }
}

TSharedRef<FJsonObject> ARMUCGameReferee::State() const
{
    return StateFor(Rig);
}

TSharedRef<FJsonObject> ARMUCGameReferee::StateFor(const AVehicleRig* Robot) const
{
    const FieldTeam Team = TeamFor(Robot).value_or(FriendlyTeam());
    const auto& View = Competitor(Team);
    const bool bViewRed = Team == FieldTeam::Red;
    auto Root = MakeShared<FJsonObject>();
    Root->SetBoolField(TEXT("available"), true);
    Root->SetBoolField(TEXT("friendly_red"), bViewRed);

    auto GS = MakeShared<FJsonObject>();
    GS->SetNumberField(TEXT("game_progress"), static_cast<int32>(GameProgress));
    GS->SetNumberField(TEXT("stage_remain_time"), FMath::RoundToInt(StageRemainSeconds));
    Root->SetObjectField(TEXT("game_status"), GS);

    auto HP = MakeShared<FJsonObject>();
    HP->SetNumberField(TEXT("red_1_robot_hp"), Red1Hp);
    HP->SetNumberField(TEXT("red_2_robot_hp"), Red2Hp);
    HP->SetNumberField(TEXT("red_3_robot_hp"), Red3Hp);
    HP->SetNumberField(TEXT("red_4_robot_hp"), Red4Hp);
    HP->SetNumberField(TEXT("red_7_robot_hp"), Competitor(FieldTeam::Red).Hp);
    HP->SetNumberField(TEXT("red_outpost_hp"), FieldRules.Get(FieldTeam::Red).OutpostHp);
    HP->SetNumberField(TEXT("red_base_hp"), FieldRules.Get(FieldTeam::Red).BaseHp);
    HP->SetNumberField(TEXT("blue_1_robot_hp"), Blue1Hp);
    HP->SetNumberField(TEXT("blue_2_robot_hp"), Blue2Hp);
    HP->SetNumberField(TEXT("blue_3_robot_hp"), Blue3Hp);
    HP->SetNumberField(TEXT("blue_4_robot_hp"), Blue4Hp);
    HP->SetNumberField(TEXT("blue_7_robot_hp"), Competitor(FieldTeam::Blue).Hp);
    HP->SetNumberField(TEXT("blue_outpost_hp"), FieldRules.Get(FieldTeam::Blue).OutpostHp);
    HP->SetNumberField(TEXT("blue_base_hp"), FieldRules.Get(FieldTeam::Blue).BaseHp);
    Root->SetObjectField(TEXT("all_robot_hp"), HP);

    auto RS = MakeShared<FJsonObject>();
    RS->SetNumberField(TEXT("robot_id"), bViewRed ? 7 : 107);
    RS->SetNumberField(TEXT("robot_level"), RobotLevel);
    RS->SetNumberField(TEXT("current_hp"), View.Hp);
    RS->SetNumberField(TEXT("maximum_hp"), SentryMaxHp);
    RS->SetNumberField(TEXT("shooter_barrel_cooling_value"), FMath::RoundToInt(View.SimStance.Modifiers().BarrelCoolingPerSec));
    RS->SetNumberField(TEXT("shooter_barrel_heat_limit"), FMath::RoundToInt(BarrelHeatLimit));
    RS->SetNumberField(TEXT("shooter_17mm_1_barrel_heat"), FMath::RoundToInt(View.BarrelHeat));
    RS->SetNumberField(TEXT("armor_id"), View.LastArmorId);
    RS->SetNumberField(TEXT("hp_deduction_reason"), static_cast<int32>(View.LastDeductionReason));
    RS->SetNumberField(TEXT("projectile_allowance_17mm"), View.ProjectileAllowance17mm);
    RS->SetNumberField(TEXT("remaining_gold_coin"), View.RemainingGoldCoin);
    RS->SetBoolField(TEXT("is_hp_deduced"), View.bHpDeducedThisStep);
    if (Robot)
    {
        const FVector Pos = UeCmToRosMeters(Robot->GetActorLocation());
        RS->SetNumberField(TEXT("pos_x"), Pos.X);
        RS->SetNumberField(TEXT("pos_y"), Pos.Y);
        RS->SetNumberField(TEXT("pos_z"), Pos.Z);
        RS->SetNumberField(TEXT("yaw_rad"), -FMath::DegreesToRadians(Robot->GetActorRotation().Yaw));
    }
    Root->SetObjectField(TEXT("robot_status"), RS);

    auto EV = MakeShared<FJsonObject>();
    EV->SetNumberField(TEXT("non_overlapping_supply_zone"), View.EvNonOverlapSupply);
    EV->SetNumberField(TEXT("overlapping_supply_zone"), View.EvOverlapSupply);
    EV->SetNumberField(TEXT("supply_zone"), View.EvSupplyZone);
    EV->SetNumberField(TEXT("small_energy"), View.EvSmallEnergy);
    EV->SetNumberField(TEXT("big_energy"), View.EvBigEnergy);
    EV->SetNumberField(TEXT("central_highland"), View.EvCentralHighland);
    EV->SetNumberField(TEXT("trapezoidal_highland"), View.EvTrapezoidalHighland);
    EV->SetNumberField(TEXT("center_gain_zone"), View.EvCenterGainZone);
    Root->SetObjectField(TEXT("event_data"), EV);

    auto BF = MakeShared<FJsonObject>();
    BF->SetNumberField(TEXT("recovery_buff"), View.BuffRecovery);
    BF->SetNumberField(TEXT("cooling_buff"), View.BuffCooling);
    BF->SetNumberField(TEXT("defence_buff"), View.BuffDefence);
    BF->SetNumberField(TEXT("vulnerability_buff"), View.BuffVulnerability);
    BF->SetNumberField(TEXT("attack_buff"), View.BuffAttack);
    BF->SetNumberField(TEXT("remaining_energy"), View.BuffRemainingEnergy);
    Root->SetObjectField(TEXT("buff"), BF);

    auto RF = MakeShared<FJsonObject>();
    RF->SetBoolField(TEXT("base_gain_point"), View.bRfidBase);
    RF->SetBoolField(TEXT("friendly_central_highland_upper_gain_point"), View.bRfidCentralHighland);
    RF->SetBoolField(TEXT("friendly_fortress_gain_point"), View.bRfidFortress);
    RF->SetBoolField(TEXT("friendly_outpost_gain_point"), View.bRfidOutpost);
    RF->SetBoolField(TEXT("friendly_supply_zone_non_exchange"), View.bRfidSupplyNonExchange);
    RF->SetBoolField(TEXT("friendly_supply_zone_exchange"), View.bRfidSupplyExchange);
    RF->SetBoolField(TEXT("center_gain_point"), View.bRfidCenterGain);
    Root->SetObjectField(TEXT("rfid_status"), RF);

    Root->SetNumberField(TEXT("chassis_power_w"), View.ChassisPowerW);
    Root->SetNumberField(TEXT("chassis_power_limit_w"), View.SimStance.Modifiers().ChassisPowerLimitW);
    Root->SetNumberField(TEXT("chassis_buffer_energy_j"), View.ChassisBufferEnergyJ);
    Root->SetObjectField(TEXT("field_devices"), FieldDeviceState());
    Root->SetBoolField(TEXT("chassis_powered"), IsChassisPoweredFor(Robot));
    {
        static const TCHAR* Stances[] = {TEXT("MOVE"), TEXT("ATTACK"), TEXT("DEFENSE")};
        Root->SetStringField(TEXT("sentry_stance"), Stances[static_cast<int32>(View.SimStance.Current)]);
        Root->SetNumberField(TEXT("stance_cooldown_seconds"), View.SimStance.CooldownRemainingSec);
    }
    Root->SetBoolField(TEXT("fpv_video_cut"), IsFpvVideoCutFor(Robot));
    Root->SetBoolField(TEXT("shooter_locked"), View.ShooterLocked());
    Root->SetNumberField(TEXT("weak_seconds"), View.WeakTimer);
    Root->SetNumberField(TEXT("invulnerable_seconds"), View.InvulnerableTimer);
    Root->SetNumberField(TEXT("respawn_elapsed_seconds"), View.RespawnTimer);
    Root->SetNumberField(TEXT("respawn_required_seconds"), View.OwnRespawnDuration);
    Root->SetNumberField(TEXT("shots_fired"), View.TotalShotsFired);
    Root->SetNumberField(TEXT("hits_landed"), View.TotalHitsLanded);
    return Root;
}

// =============================================================================
// RHINE LAB TRANSPARENT SCI-FI 3D HUD BRIDGE -> GPU SBackgroundBlur + 3D SLATE RIG
// Toggle live at runtime with F11 (Default ON) | Test Console: F10
// =============================================================================
void ARMUCGameReferee::DrawRhineLab3DHud(AHUD* Hud, UCanvas* Canvas, double GyroRadS) const
{
    UWorld* World = GetWorld();
    if (!World) return;
    if (!Rhine3DHud.IsValid())
    {
        Rhine3DHud = MakeUnique<FRhine3DHudController>();
    }
    Rhine3DHud->SetEnabled(World, true);

    FRhine3DHudState St;
    St.bFriendlyRed = bFriendlyRed;
    St.StageRemainSeconds = static_cast<float>(HudClockSeconds());
    St.MatchPhaseText = (GameProgress == ERMUCGameProgress::Running) ? TEXT("比赛中")
        : ((GameProgress == ERMUCGameProgress::CountDown) ? TEXT("倒计时")
        : ((GameProgress == ERMUCGameProgress::GameOver) ? TEXT("比赛结束") : TEXT("待机准备")));
    const RmUi::FClientRuneStatus RuneClient = RmUi::ClientRuneStatus(RuneRules, GameProgress, MatchElapsedSeconds,
        bFriendlyRed, IsOwnAlive(), FieldVisuals.IsRuneReady());
    St.RuneClientText = RuneClient.Text;
    St.bRuneClientAvailable = RuneClient.bAvailable;

    const FRMUCDartStation& OwnDart = bFriendlyRed ? RedDart : BlueDart;
    const FRMUCDartStation& OpponentDart = bFriendlyRed ? BlueDart : RedDart;
    const auto DartGateText = [](ERMUCDartState Gate)
    {
        switch (Gate)
        {
        case ERMUCDartState::Closed: return TEXT("关闭");
        case ERMUCDartState::SelfCheck: return TEXT("自检");
        case ERMUCDartState::Opening: return TEXT("开启中");
        case ERMUCDartState::Open: return TEXT("已打开");
        case ERMUCDartState::Closing: return TEXT("关闭中");
        case ERMUCDartState::Cooldown: return TEXT("冷却中");
        case ERMUCDartState::Offline: return TEXT("离线");
        }
        return TEXT("未知");
    };
    const auto DartTargetText = [](ERMUCDartTarget Target)
    {
        switch (Target)
        {
        case ERMUCDartTarget::Outpost: return TEXT("前哨站");
        case ERMUCDartTarget::FixedBase: return TEXT("基地固定目标");
        case ERMUCDartTarget::RandomFixedBase: return TEXT("基地随机固定目标");
        case ERMUCDartTarget::RandomMovingBase: return TEXT("基地随机移动目标");
        case ERMUCDartTarget::LateMovingBase: return TEXT("基地末端移动目标");
        }
        return TEXT("未知");
    };
    St.DartClientText = FString::Printf(TEXT("闸门 %s   目标 %s   余弹 %d   开门 %d 次"),
        DartGateText(OwnDart.State()), DartTargetText(OwnDart.Target()), OwnDart.DartsRemaining(), OwnDart.AvailableChances());
    const auto SuppressionSeconds = [](const FRMUCDartStation& Source, ERMUCDartTarget Site)
    {
        return FMath::CeilToInt(Source.GainPointSuppressionRemaining(Site));
    };
    St.DartSuppressionText = FString::Printf(TEXT("增益点压制  己方 基地%d秒·前哨%d秒  敌方 基地%d秒·前哨%d秒"),
        SuppressionSeconds(OpponentDart, ERMUCDartTarget::FixedBase),
        SuppressionSeconds(OpponentDart, ERMUCDartTarget::Outpost),
        SuppressionSeconds(OwnDart, ERMUCDartTarget::FixedBase),
        SuppressionSeconds(OwnDart, ERMUCDartTarget::Outpost));

    St.RedBaseHp          = FieldRules.Get(FieldTeam::Red).BaseHp;
    St.BlueBaseHp         = FieldRules.Get(FieldTeam::Blue).BaseHp;
    St.BaseMaxHp          = BaseMaxHp;
    St.bRedBaseProtected  = FieldRules.BaseProtected(FieldTeam::Red);
    St.bBlueBaseProtected = FieldRules.BaseProtected(FieldTeam::Blue);
    St.bRedBaseLargeArmorExposed = FieldVisuals.IsBaseLargeArmorExposed(true);
    St.bBlueBaseLargeArmorExposed = FieldVisuals.IsBaseLargeArmorExposed(false);

    auto FillOutpost = [this](FieldTeam Site, ERMUCFieldZoneSide ZoneSide, FRhine3DOutpostState& Out)
    {
        const auto& SideState = FieldRules.Get(Site);
        Out.Hp             = SideState.OutpostHp;
        Out.MaxHp          = OutpostMaxHp;
        Out.bOwnerRed      = (SideState.OutpostOwner == FieldTeam::Red);
        Out.bControllerRed = (SideState.OutpostControl.Controller == FieldTeam::Red);
        Out.bFullControl   = SideState.OutpostControl.bFull;
        Out.TransitionPct  = FMath::Clamp(
            static_cast<float>(SideState.OutpostControl.TransitionSeconds / FRMUCFieldRules::ControlTransitionSeconds),
            0.0f, 1.0f);
        Out.PhaseLabel     = FString(OutpostControlPhase(Site)).ToUpper();
        const auto FillContour = [&](ERMUCFieldZoneKind Kind, TArray<FVector2f>& Points)
        {
            if (const FRMUCFieldZone* Zone = FieldZones.Find(Kind, ZoneSide))
            {
                Points.Reserve(Zone->OuterM.Num());
                for (const FVector2D& Pt : Zone->OuterM)
                    Points.Add(FVector2f(static_cast<float>(-Pt.X), static_cast<float>(-Pt.Y)));
            }
        };
        FillContour(ERMUCFieldZoneKind::Outpost, Out.ZonePolyArena);
        FillContour(ERMUCFieldZoneKind::CentralHighland, Out.HighlandPolyArena);
        if (!Out.ZonePolyArena.IsEmpty())
        {
            // Center the tower ring right on the real CAD outpost tower inside the C-notch (+/-3.06, +/-3.89 ROS)
            const float Sign = (ZoneSide == ERMUCFieldZoneSide::Red) ? 1.0f : -1.0f;
            Out.ArenaX = -3.06f * Sign;
            Out.ArenaY = -3.89f * Sign;
            Out.bHasMapPos = true;
        }
    };
    FillOutpost(FieldTeam::Red, ERMUCFieldZoneSide::Red, St.RedOutpost);
    FillOutpost(FieldTeam::Blue, ERMUCFieldZoneSide::Blue, St.BlueOutpost);
    St.MinimapTexture = TexMinimap;

    // RMUC 2027 V1.0.0 Ground Roster: 4 active units (重装H1=400, 步兵I3/I4=200, 哨兵S7=400)
    const TCHAR* Codes[4] = { TEXT("重装H1"), TEXT("步兵I3"), TEXT("步兵I4"), TEXT("哨兵S7") };
    const int32 RHps[4]  = { Red1Hp, Red3Hp, Red4Hp, Competitor(FieldTeam::Red).Hp };
    const int32 BHps[4]  = { Blue1Hp, Blue3Hp, Blue4Hp, Competitor(FieldTeam::Blue).Hp };
    const int32 MHps[4]  = { 400, 200, 200, SentryMaxHp };
    St.RedUnits.Reserve(4);
    St.BlueUnits.Reserve(4);
    for (int32 I = 0; I < 4; ++I)
    {
        FRhine3DRobotUnitState RU; RU.Code = Codes[I]; RU.Hp = RHps[I]; RU.MaxHp = MHps[I]; RU.bIsOwnUnit = (I == 3 && bFriendlyRed);
        St.RedUnits.Add(RU);
        FRhine3DRobotUnitState BU; BU.Code = Codes[I]; BU.Hp = BHps[I]; BU.MaxHp = MHps[I]; BU.bIsOwnUnit = (I == 3 && !bFriendlyRed);
        St.BlueUnits.Add(BU);
    }

    St.OwnHp = GetOwnHp();
    St.OwnMaxHp = SentryMaxHp;
    St.bOwnAlive = IsOwnAlive();
    St.Ammo17mm = Own().ProjectileAllowance17mm;
    St.MuzzleSpeedMs = static_cast<float>(LaunchSpeedCmS * 0.01);
    St.BarrelHeatPct = FMath::Clamp(static_cast<float>(Own().BarrelHeat / FMath::Max(1.0, BarrelHeatLimit)), 0.0f, 1.0f);
    St.SupercapPct   = FMath::Clamp(static_cast<float>(Own().ChassisBufferEnergyJ / 60.0), 0.0f, 1.0f);

    double GimYaw = 0.0, GimPitch = 0.0;
    if (Rig) Rig->GetGimbalAngles(GimYaw, GimPitch);
    St.GimbalYawRad   = static_cast<float>(GimYaw);
    St.GimbalPitchRad = static_cast<float>(GimPitch);
    St.GyroRadS       = static_cast<float>(GyroRadS);

    bool bLocked = false;
    if (APlayerController* PC = World->GetFirstPlayerController())
    {
        if (PC->IsInputKeyDown(EKeys::RightMouseButton) || PC->IsInputKeyDown(EKeys::Q)) bLocked = true;
    }
    if (EnemyRig && Rig && IsValid(EnemyRig) && IsValid(Rig))
    {
        const FVector Diff = EnemyRig->GetActorLocation() - Rig->GetActorLocation();
        St.LockTargetDistM = static_cast<float>(Diff.Size() * 0.01);
        if (FVector::DotProduct(Diff.GetSafeNormal(), Rig->GetActorForwardVector()) > 0.988f) bLocked = true;
    }
    St.bAutoAimLocked = bLocked;
    St.LockTargetLabel = bFriendlyRed ? TEXT("BLUE-07") : TEXT("RED-07");

    St.TotalShotsFired = Own().TotalShotsFired;
    St.OwnHitFlash     = static_cast<float>(Own().OwnHitHudFlash);
    St.LastArmorId     = Own().LastArmorId;
    St.HitConfirmFlash = static_cast<float>(Own().HitConfirmHudFlash);
    St.LastHitDamage   = Own().LastHitDamageShown;

    uint8 Mask = 0;
    if (IsChassisPowered()) Mask |= (1 << 0);
    if (!IsFpvVideoCut()) Mask |= (1 << 1);
    Mask |= (1 << 2) | (1 << 4);
    if (!IsShooterLocked()) Mask |= (1 << 3);
    if (St.bOwnAlive) Mask |= (1 << 5);
    if (IsChassisPowered() && Own().ChassisBufferEnergyJ > 5.0) Mask |= (1 << 6);
    St.ModuleStatusMask = Mask;

    St.bFpvVideoCut           = IsFpvVideoCut();
    St.bOverheatMatchLocked   = Own().bOverheatMatchLocked;
    St.OverspeedLockSeconds   = static_cast<float>(Own().OverspeedLockTimer);
    St.bOverspeedMatchLocked  = Own().bOverspeedMatchLocked;
    St.ChassisPowerCutSeconds = static_cast<float>(Own().ChassisPowerCutTimer);
    St.RespawnProgress        = (!St.bOwnAlive && Own().OwnRespawnDuration > 0.1)
        ? FMath::Clamp(static_cast<float>(Own().RespawnTimer / Own().OwnRespawnDuration), 0.0f, 1.0f) : 0.0f;
    St.RespawnRemainSeconds   = !St.bOwnAlive ? FMath::Max(0.0f, static_cast<float>(Own().OwnRespawnDuration - Own().RespawnTimer)) : 0.0f;
    St.BuybackGoldCost        = GetInstantRespawnCost();
    St.RemainingGold          = Own().RemainingGoldCoin;
    St.WeakRemainSeconds      = static_cast<float>(Own().WeakTimer);
    St.InvulnerableRemainSeconds = static_cast<float>(Own().InvulnerableTimer);

    const auto OwnTeam = FriendlyTeam();
    const auto EnemyTeam = FRMUCFieldRules::Other(OwnTeam);
    const unsigned OwnIndex = FRMUCFieldRules::Index(OwnTeam);
    const unsigned EnemyIndex = FRMUCFieldRules::Index(EnemyTeam);
    const auto& OwnPoints = FieldPointEffects[OwnIndex];
    const auto& OwnContacts = FieldPhysicalContacts[OwnIndex];
    St.bBuffHeal    = (Own().BuffRecovery > 0);
    St.bBuffArmor   = (Own().BuffDefence > 0);
    St.bBuffCooling = (Own().BuffCooling > 1) || (OwnPoints.FortressCoolingBonus > 0);
    St.bBuffPower   = (Own().EnergyBuffTimer > 0.0);

    if (OwnPoints.bFortress)
    {
        St.ZoneStatusText = FString::Printf(TEXT("堡垒增益激活  //  防御 -50%%  冷却 +%d  储备弹量 %d"),
            OwnPoints.FortressCoolingBonus, Own().OwnLogistics.FortressReserve(OwnPoints));
        St.bZoneStatusLocked = false;
    }
    else if (OwnPoints.bEnemyFortress)
    {
        const auto& Enemy = FieldRules.Get(EnemyTeam);
        St.ZoneStatusText = Enemy.bBaseArmorOpen
            ? TEXT("敌方堡垒  //  对方基地护甲已展开")
            : FString::Printf(TEXT("敌方堡垒  //  易伤 +100%%  开甲 %.1f / %.0f 秒"),
                Enemy.ArmorActivationSeconds, FRMUCFieldRules::EnemyFortressActivationSeconds);
    }
    else if (OwnContacts[OwnIndex].Fortress.value_or(false))
    {
        St.ZoneStatusText = TEXT("己方堡垒待机  //  己方前哨阵地失去控制后解锁");
        St.bZoneStatusLocked = true;
    }
    else if (OwnContacts[EnemyIndex].Fortress.value_or(false))
    {
        const double TimeToUnlock = FRMUCFieldRules::EnemyFortressUnlockSeconds - MatchElapsedSeconds;
        St.ZoneStatusText = TimeToUnlock > 0.0
            ? FString::Printf(TEXT("敌方堡垒待机  //  %d 秒后可争夺，需控制敌方前哨阵地"), FMath::CeilToInt(TimeToUnlock))
            : TEXT("敌方堡垒待机  //  需控制敌方前哨阵地");
        St.bZoneStatusLocked = true;
    }
    else if (OwnPoints.bBase)
    {
        St.ZoneStatusText = TEXT("基地增益区  //  防御 -50%  可就地买弹");
        St.bZoneStatusLocked = false;
    }
    else if (OwnPoints.bVantage)
    {
        St.ZoneStatusText = TEXT("制高点增益区  //  防御 -50%  可就地买弹");
        St.bZoneStatusLocked = false;
    }
    else if (OwnPoints.bCentralHighland)
    {
        St.ZoneStatusText = TEXT("中央高地增益区  //  防御 -25%  前哨控制联动");
        St.bZoneStatusLocked = false;
    }
    else if (OwnPoints.bSupply)
    {
        St.ZoneStatusText = TEXT("补给区  //  持续回血 + 可买弹兑换");
        St.bZoneStatusLocked = false;
    }
    else if (OwnPoints.bOutpost)
    {
        St.ZoneStatusText = OwnPoints.bFullOutpost
            ? TEXT("前哨站阵地  //  已完全控制  可就地买弹")
            : TEXT("前哨站增益点  //  参与阵地争夺");
        St.bZoneStatusLocked = false;
    }

    St.BannerEventText  = BannerEventText;
    St.BannerEventTimer = static_cast<float>(BannerEventTimer);
    St.BroadcastFeed    = StructuredBroadcasts;

    auto WorldToArena = [](const FVector& LocCm, float& OutX, float& OutY)
    {
        const float RosX = static_cast<float>(LocCm.X * 0.01);
        const float RosY = static_cast<float>(-LocCm.Y * 0.01);
        OutX = FMath::Clamp(-RosX, -13.6f, 13.6f);
        OutY = FMath::Clamp(-RosY, -7.6f, 7.6f);
    };
    St.MapPins.Reserve(4);
    if (Rig && IsValid(Rig))
    {
        FRhine3DMapPin P;
        WorldToArena(Rig->GetActorLocation(), P.ArenaX, P.ArenaY);
        P.bIsRed = bFriendlyRed;
        P.bIsSelf = true;
        P.Label = TEXT("7");
        // Align viewing cone with the real turret-mounted gimbal camera, not the chassis body!
        FVector GimbalFwd = Rig->GetActorForwardVector();
        if (USceneCaptureComponent2D* const* AimCam = Rig->GetCameras().Find(TEXT("aim_camera")); AimCam && *AimCam)
        {
            GimbalFwd = (*AimCam)->GetForwardVector();
        }
        else if (!Rig->GetCameras().IsEmpty())
        {
            if (USceneCaptureComponent2D* FirstCam = Rig->GetCameras().CreateConstIterator()->Value)
            {
                GimbalFwd = FirstCam->GetForwardVector();
            }
        }
        P.HeadingRad = FMath::Atan2(static_cast<float>(GimbalFwd.Y), static_cast<float>(-GimbalFwd.X));
        St.MapPins.Add(P);
    }
    if (EnemyRig && IsValid(EnemyRig))
    {
        FRhine3DMapPin P;
        WorldToArena(EnemyRig->GetActorLocation(), P.ArenaX, P.ArenaY);
        P.bIsRed = !bFriendlyRed;
        P.bIsSelf = false;
        P.Label = TEXT("7");
        St.MapPins.Add(P);
    }

    Rhine3DHud->UpdateFrame(World, St, World->GetDeltaSeconds());
}
