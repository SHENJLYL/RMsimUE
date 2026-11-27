#include "RMUCGameReferee.h"
#include "VehicleRig.h"
#include "CollisionQueryParams.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

namespace
{
const TCHAR* DartTargetName(ERMUCDartTarget Target)
{
    switch (Target)
    {
    case ERMUCDartTarget::Outpost: return TEXT("outpost");
    case ERMUCDartTarget::FixedBase: return TEXT("fixed_base");
    case ERMUCDartTarget::RandomFixedBase: return TEXT("random_fixed_base");
    case ERMUCDartTarget::RandomMovingBase: return TEXT("random_moving_base");
    case ERMUCDartTarget::LateMovingBase: return TEXT("late_moving_base");
    }
    return TEXT("unknown");
}

const TCHAR* DartStateName(ERMUCDartState State)
{
    switch (State)
    {
    case ERMUCDartState::Closed: return TEXT("closed");
    case ERMUCDartState::SelfCheck: return TEXT("self_check");
    case ERMUCDartState::Opening: return TEXT("opening");
    case ERMUCDartState::Open: return TEXT("open");
    case ERMUCDartState::Closing: return TEXT("closing");
    case ERMUCDartState::Cooldown: return TEXT("cooldown");
    case ERMUCDartState::Offline: return TEXT("offline");
    }
    return TEXT("unknown");
}

const TCHAR* RuneStateName(FRMUCRuneRules::Phase State)
{
    if (State == FRMUCRuneRules::Phase::Activating) return TEXT("activating");
    if (State == FRMUCRuneRules::Phase::Activated) return TEXT("activated");
    if (State == FRMUCRuneRules::Phase::Failed) return TEXT("failed");
    return TEXT("idle");
}
}

FHitResult ARMUCGameReferee::TraceFieldReaderSurface(const AVehicleRig& Vehicle, const FVector& Reader) const
{
    FCollisionQueryParams Params(SCENE_QUERY_STAT(RMSimRFIDSurface), true, &Vehicle);
    Params.AddIgnoredActor(this);
    if (IsValid(Rig)) Params.AddIgnoredActor(Rig);
    if (IsValid(EnemyRig)) Params.AddIgnoredActor(EnemyRig);
    for (const FMatchRobotSlot& Slot : MatchRobots)
        if (AVehicleRig* V = Slot.Vehicle.Get(); IsValid(V)) Params.AddIgnoredActor(V);
    FHitResult Ground;
    GetWorld()->LineTraceSingleByObjectType(Ground, Reader, Reader - FVector(0., 0., 18.),
        FCollisionObjectQueryParams(ECC_WorldStatic), Params);
    return Ground;
}

FRMUCFieldPoints ARMUCGameReferee::ObserveFieldPoints(const AVehicleRig& Vehicle, ERMUCHighlandCard* TraversalCard,
    FVector* ReaderSurfaceM) const
{
    FRMUCFieldPoints Result;
    using Card = ERMUCHighlandCard;
    if (TraversalCard) *TraversalCard = Card::None;
    if (!FieldZones.IsLoaded() || !GetWorld()) return Result;

    // The diagram-derived reader is under the chassis, 8 cm above the visual wheel bottoms.
    // Require nearby ground beneath that reader; vertical projections from upper levels must not read a floor card.
    const FBox Body = Vehicle.GetLocalCollisionBounds();
    FHitResult Ground;
    if (Body.IsValid)
    {
        const FVector Reader = Vehicle.GetActorTransform().TransformPosition(FVector(0., 0., Body.Min.Z + 8.));
        Ground = TraceFieldReaderSurface(Vehicle, Reader);
    }
    const bool bHasSurface = Ground.bBlockingHit && Ground.ImpactNormal.Z >= .65;
    const FVector PositionM(Ground.ImpactPoint.X * .01, -Ground.ImpactPoint.Y * .01, Ground.ImpactPoint.Z * .01);
    if (ReaderSurfaceM && bHasSurface) *ReaderSurfaceM = PositionM;

    Card Observed = Card::None;
    const auto RecordCard = [&Observed](Card Detected)
    {
        Observed = Observed == Card::None ? Detected : Card::Other;
    };
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const auto Side = Team == 0 ? ERMUCFieldZoneSide::Red : ERMUCFieldZoneSide::Blue;
        const auto Contact = [&](ERMUCFieldZoneKind Kind) -> std::optional<bool>
        {
            const FRMUCFieldZone* Zone = FieldZones.Find(Kind, Side);
            return Zone ? std::optional<bool>{bHasSurface && Zone->Contains(PositionM)} : std::nullopt;
        };
        FRMUCPointContacts& Site = Result[Team];
        Site.Supply = Contact(ERMUCFieldZoneKind::Supply);
        Site.Base = Contact(ERMUCFieldZoneKind::Base);
        Site.Outpost = Contact(ERMUCFieldZoneKind::Outpost);
        Site.Fortress = Contact(ERMUCFieldZoneKind::Fortress);
        Site.CentralHighland = Contact(ERMUCFieldZoneKind::CentralHighland);
        Site.Vantage = Contact(ERMUCFieldZoneKind::Vantage);
        if (!TraversalCard || !bHasSurface) continue;
        if (Contact(ERMUCFieldZoneKind::HighlandLower).value_or(false))
            RecordCard(Team == 0 ? Card::RedLower : Card::BlueLower);
        if (Site.CentralHighland.value_or(false))
            RecordCard(Team == 0 ? Card::RedUpper : Card::BlueUpper);
        if (Site.Supply.value_or(false) || Site.Base.value_or(false) || Site.Outpost.value_or(false)
            || Site.Fortress.value_or(false) || Site.Vantage.value_or(false))
            RecordCard(Card::Other);
    }
    if (TraversalCard)
        *TraversalCard = bHasSurface && FieldZones.HasTraversalInterruption(PositionM) ? Card::Other : Observed;
    return Result;
}

FString ARMUCGameReferee::RequestClientRune(AVehicleRig* Vehicle)
{
    if (const FMatchRobotSlot* Slot = FindSlot(Vehicle))
    {
        if (Slot->State.Hp <= 0) return TEXT("robot_inactive");
        return StartRuneAttempt(Slot->Team == FieldTeam::Red, Slot->RobotNumber);
    }
    const auto Team = TeamFor(Vehicle);
    if (!Team) return TEXT("invalid_seat");
    if (Competitor(*Team).Hp <= 0) return TEXT("robot_inactive");
    return StartRuneAttempt(*Team == FieldTeam::Red, 7);
}

FString ARMUCGameReferee::RequestInfantryClientRune(bool bRed, int32 RobotNumber)
{
    if (RobotNumber != 3 && RobotNumber != 4) return TEXT("invalid_seat");
    const int32 Hp = bRed ? (RobotNumber == 3 ? Red3Hp : Red4Hp) : (RobotNumber == 3 ? Blue3Hp : Blue4Hp);
    if (Hp <= 0) return TEXT("robot_inactive");
    return StartRuneAttempt(bRed, RobotNumber);
}

FString ARMUCGameReferee::StartRuneAttempt(bool bRed, int32 RobotNumber)
{
    if (GameProgress != ERMUCGameProgress::Running) return TEXT("match_not_running");
    if (!FieldVisuals.IsRuneReady()) return TEXT("assets_unavailable");
    if (RuneRules.Get(bRed).State != FRMUCRuneRules::Phase::Idle) return TEXT("already_active");
    if (!RuneRules.Request(bRed)) return TEXT("no_attempts");
    PushCombatBroadcast(bRed ? 1 : -1,
        FString::Printf(TEXT("%s #%d"), bRed ? TEXT("RED") : TEXT("BLUE"), RobotNumber),
        RuneRules.Get(bRed).bBig ? TEXT("LARGE RUNE REQUESTED") : TEXT("SMALL RUNE REQUESTED"),
        0, TEXT(""), 2.0);
    return TEXT("accepted");
}

TSharedRef<FJsonObject> ARMUCGameReferee::RuneClientState() const
{
    using Phase = FRMUCRuneRules::Phase;
    auto State = MakeShared<FJsonObject>();
    for (const bool bRed : {true, false})
    {
        const auto& Face = RuneRules.Get(bRed);
        const bool bRunning = GameProgress == ERMUCGameProgress::Running;
        const bool bAttempt = bRunning && (Face.State == Phase::Activating || Face.State == Phase::Failed);
        const bool bActive = bRunning && Face.State == Phase::Activated;
        auto Side = MakeShared<FJsonObject>();
        Side->SetNumberField(TEXT("rune_status"), bActive ? 3 : bAttempt ? 2 : 1);
        Side->SetNumberField(TEXT("activated_arms"), bAttempt || bActive ? Face.Hits : 0);
        Side->SetNumberField(TEXT("average_rings"), bAttempt && Face.bBig && Face.Hits ? double(Face.RingTotal) / Face.Hits : 0.);
        State->SetObjectField(bRed ? TEXT("red") : TEXT("blue"), Side);
    }
    return State;
}

void ARMUCGameReferee::RequestOwnDartGate()
{
    if (GameProgress != ERMUCGameProgress::Running) return;
    FRMUCDartStation& Dart = bFriendlyRed ? RedDart : BlueDart;
    if (!FieldVisuals.AreDartGatesReady()) return;
    if (Dart.RequestOpen()) PushBannerEvent(TEXT("DART GATE OPENING"), 2.);
}

void ARMUCGameReferee::ToggleOwnDartTray()
{
    if (GameProgress != ERMUCGameProgress::Preparation || StageRemainSeconds <= 3.)
    {
        PushBannerEvent(TEXT("飞镖滑台仅在准备阶段可操作"), 2.);
        return;
    }
    const int32 Team = bFriendlyRed ? 0 : 1;
    if (!FieldVisuals.GetDartTray(bFriendlyRed)) return;
    bDartTrayOut[Team] = !bDartTrayOut[Team];
    PushBannerEvent(bDartTrayOut[Team] ? TEXT("抽出飞镖滑台") : TEXT("推回飞镖滑台"), 2.);
}

void ARMUCGameReferee::CycleOwnDartTarget()
{
    auto& Station = bFriendlyRed ? RedDart : BlueDart;
    constexpr int32 Count = int32(ERMUCDartTarget::LateMovingBase) + 1;
    const auto Next = ERMUCDartTarget((int32(Station.Target()) + 1) % Count);
    if (Station.SelectTarget(Next))
        UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_TARGET team=%s target=%s"),
            bFriendlyRed ? TEXT("red") : TEXT("blue"), DartTargetName(Next));
}

bool ARMUCGameReferee::ConfirmDartDetector(bool bRed, ERMUCDartTarget Target)
{
    if (GameProgress != ERMUCGameProgress::Running) return false;
    const FieldTeam Sender = bRed ? FieldTeam::Red : FieldTeam::Blue;
    const FieldTeam Site = FRMUCFieldRules::Other(Sender);
    const bool bOutpost = Target == ERMUCDartTarget::Outpost;
    const auto& Defending = FieldRules.Get(Site);
    if (bOutpost ? Defending.OutpostHp <= 0 || Defending.OutpostOwner == Sender : Defending.BaseHp <= 0)
        return false;
    FRMUCDartStation& Station = bRed ? RedDart : BlueDart;
    const FRMUCDartHitResult Hit = Station.SubmitConfirmedHit(Target);
    if (!Hit.Accepted) return false;
    const int32 Sign = bRed ? 1 : -1;
    const TCHAR* Shooter = bRed ? TEXT("红方飞镖") : TEXT("蓝方飞镖");
    const FString Victim = FString::Printf(TEXT("%s%s"), bRed ? TEXT("蓝方") : TEXT("红方"),
        bOutpost ? TEXT("前哨站") : TEXT("基地"));
    if (Hit.OpensBaseArmor && FieldRules.OpenBaseArmor(Site))
        PushCombatBroadcast(Sign, Shooter, TEXT("展开护甲"), -Sign, Victim, 3.5);

    if (Hit.ClearsExistingBuffs) RuneRules.ClearActiveBuff(!bRed);
    else if (Hit.ExistingBuffSuppressionSeconds > 0.)
        RuneRules.SuppressActiveBuff(!bRed, Hit.ExistingBuffSuppressionSeconds);

    TArray<FMatchRobotSlot*, TInlineAllocator<4>> Infantry;
    int32 GroundHpLost = 0;
    for (FMatchRobotSlot& Slot : MatchRobots)
    {
        if (!IsValid(Slot.Vehicle.Get()) || Slot.State.Hp <= 0) continue;
        if (Slot.Team == Sender)
        {
            if (Hit.InfantryExperience > 0 && Slot.IsInfantry()) Infantry.Add(&Slot);
            continue;
        }
        if (Hit.ClearsExistingBuffs) Slot.HighlandTraversal.ClearActiveBuff();
        else if (Hit.ExistingBuffSuppressionSeconds > 0.)
            Slot.HighlandTraversal.SuppressActiveBuff(MatchElapsedSeconds, Hit.ExistingBuffSuppressionSeconds);
        if (Hit.GroundRobotHpPercent > 0)
        {
            const int32 Before = Slot.State.Hp;
            ApplyRobotDamage(Slot, nullptr, 0, FMath::RoundToInt(Slot.MaxHp() * Hit.GroundRobotHpPercent / 100.),
                ERMUCHpDeductionReason::Dart);
            GroundHpLost += Before - Slot.State.Hp;
        }
    }
    if (!Infantry.IsEmpty())
    {
        const int32 BaseExperience = Hit.InfantryExperience * 10;
        const int32 TotalExperience = BaseExperience + RuneRules.AwardExperienceBonus(bRed, BaseExperience);
        const int32 Share = TotalExperience / Infantry.Num();
        for (FMatchRobotSlot* Slot : Infantry)
        {
            const int32 BeforeMax = Slot->MaxHp();
            Slot->State.ExperienceTenths += Share;
            Slot->State.Hp += Slot->MaxHp() - BeforeMax;
        }
    }

    const int32 Lost = ApplyFieldStructureHit(!bRed, bOutpost, bRed, Hit.Damage, true);
    RecordHit(Sender, Lost + GroundHpLost);
    SyncSharedTeamState();
    PushCombatBroadcast(Sign, Shooter, FString::Printf(TEXT("命中 -%d HP"), Lost), -Sign, Victim, 4.);
    if (Hit.SuppressesGainPoint)
        PushCombatBroadcast(Sign, Shooter, FString::Printf(TEXT("压制增益点 %.0f 秒"), Hit.SuppressionSeconds), -Sign, Victim, 4.);
    UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_SETTLED team=%s target=%s lost=%d nominal=%d shield=%d armor=%d suppressed_s=%.1f time=%.3f ground_lost=%d infantry=%d xp=%d blackout_s=%.1f"),
        bRed ? TEXT("red") : TEXT("blue"), DartTargetName(Target), Lost, Hit.Damage,
        FieldRules.BaseProtected(Site), FieldRules.Get(Site).bBaseArmorOpen, Hit.SuppressionSeconds,
        MatchElapsedSeconds, GroundHpLost, Infantry.Num(), Hit.InfantryExperience, Station.OpponentBlackoutRemaining());
    return true;
}

void ARMUCGameReferee::StepFieldDevices(double DeltaSeconds)
{
    switch (GameProgress)
    {
    case ERMUCGameProgress::Running:
        RuneRules.AdvanceTo(MatchElapsedSeconds);
        RedDart.Update(MatchElapsedSeconds, ERMUCDartPhase::Running);
        BlueDart.Update(MatchElapsedSeconds, ERMUCDartPhase::Running);
        break;
    case ERMUCGameProgress::SelfChecking:
        RedDart.Update(15. - StageRemainSeconds, ERMUCDartPhase::SelfCheck);
        BlueDart.Update(15. - StageRemainSeconds, ERMUCDartPhase::SelfCheck);
        break;
    case ERMUCGameProgress::GameOver:
        RedDart.Update(MatchElapsedSeconds, ERMUCDartPhase::Finished);
        BlueDart.Update(MatchElapsedSeconds, ERMUCDartPhase::Finished);
        break;
    default:
        RedDart.Update(0., ERMUCDartPhase::Inactive);
        BlueDart.Update(0., ERMUCDartPhase::Inactive);
        break;
    }
    const bool bPreparing = GameProgress == ERMUCGameProgress::Preparation;
    for (int32 Team = 0; Team < 2; ++Team)
    {
        if (!bPreparing || StageRemainSeconds <= 3.) bDartTrayOut[Team] = false;
        DartTrayFraction[Team] = FMath::FInterpConstantTo(DartTrayFraction[Team], bDartTrayOut[Team] ? 1. : 0.,
            DeltaSeconds, 1. / 1.6);
    }
    FieldVisuals.UpdateDartTrays(DartTrayFraction[0], DartTrayFraction[1]);
    FieldVisuals.UpdateDartGates(bPreparing ? 1. : RedDart.GateOpenFraction(), bPreparing ? 1. : BlueDart.GateOpenFraction());
    StepDartFlights();
    StepFieldObserver(DeltaSeconds);
#if WITH_DEV_AUTOMATION_TESTS
    StepDartRewardProbe();
    StepAmmoLedgerProbe();
    StepFieldBoundaryProbe();
    static const double TacticalHpAt = []()
    {
        double Seconds = -1.0;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeTacticalHpAfter="), Seconds);
        return FMath::IsFinite(Seconds) ? Seconds : -1.0;
    }();
    if (TacticalHpAt >= 0.0 && !bTacticalHpFixtureTriggered &&
        GameProgress == ERMUCGameProgress::Running && MatchElapsedSeconds >= TacticalHpAt)
    {
        bTacticalHpFixtureTriggered = true;
        const FieldTeam Seat = (!RosCompetitors.IsEmpty() ? TeamFor(RosCompetitors[0]) : std::nullopt).value_or(FriendlyTeam());
        auto& Record = Competitor(Seat);
        const int32 Before = Record.Hp;
        if (Before > 140)
        {
            // Choose the fixture's exact HP independently of the stance or card
            // at the crossing, and use the usual damage event and combat timer.
            const uint8 Defence = Record.BuffDefence;
            const uint8 Vulnerability = Record.BuffVulnerability;
            Record.BuffDefence = 0;
            Record.BuffVulnerability = 0;
            ApplySentryDamage(Seat, std::nullopt, 0, Before - 140, ERMUCHpDeductionReason::ArmorHit);
            Record.BuffDefence = Defence;
            Record.BuffVulnerability = Vulnerability;
        }
        UE_LOG(LogTemp, Display, TEXT("RMUC_TACTICAL_FIXTURE timed_hp seat=%s t=%.3f before=%d after=%d"),
            Seat == FieldTeam::Red ? TEXT("red") : TEXT("blue"), MatchElapsedSeconds, Before, Record.Hp);
    }
    static const FString TacticalFixture = []()
    {
        FString Fixture;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeTacticalFixture="), Fixture);
        return Fixture;
    }();
    if (!TacticalFixture.IsEmpty() && GameProgress == ERMUCGameProgress::Running && MatchElapsedSeconds >= .5)
    {
        if (TacticalFixture == TEXT("remote_ammo") && TacticalFixtureStage == 0)
        {
            TacticalFixtureStage = 1;
            Own().ProjectileAllowance17mm = 20;
            UE_LOG(LogTemp, Display, TEXT("RMUC_TACTICAL_FIXTURE remote_ammo seat=%s ammo=20"), bFriendlyRed ? TEXT("red") : TEXT("blue"));
        }
        else if (TacticalFixture == TEXT("own_fortress"))
        {
            if (TacticalFixtureStage == 0)
            {
                TacticalFixtureStage = 1;
                ApplyFieldStructureHit(bFriendlyRed, true, !bFriendlyRed, OutpostMaxHp);
            }
            else if (TacticalFixtureStage == 1 && FieldRules.CanActivateFortress(FriendlyTeam(), FriendlyTeam(), MatchElapsedSeconds))
            {
                TacticalFixtureStage = 2;
                ApplyFieldStructureHit(bFriendlyRed, false, !bFriendlyRed, FieldRules.Get(FriendlyTeam()).BaseHp - 3500);
                UE_LOG(LogTemp, Display, TEXT("RMUC_TACTICAL_FIXTURE own_fortress seat=%s base_hp=%d"),
                    bFriendlyRed ? TEXT("red") : TEXT("blue"), FieldRules.Get(FriendlyTeam()).BaseHp);
            }
        }
    }
    static const bool bOutpostRebuildProbe = FParse::Param(FCommandLine::Get(), TEXT("RefereeOutpostRebuildSelfTest"));
    if (bOutpostRebuildProbe && !bOutpostRebuildProbeTriggered && MatchElapsedSeconds >= .5)
    {
        bOutpostRebuildProbeTriggered = true;
        ApplyFieldStructureHit(!bFriendlyRed, true, bFriendlyRed, OutpostMaxHp);
    }
    static const bool bLogisticsProbe = FParse::Param(FCommandLine::Get(), TEXT("RefereeLogisticsSelfTest"));
    const FMatchRobotSlot* LocalSlot = OwnSlot();
    if (bLogisticsProbe && !bLogisticsProbeTriggered && MatchElapsedSeconds >= .5 &&
        LocalSlot && LocalSlot->PointEffects.bSupply)
    {
        bLogisticsProbeTriggered = true;
        InjectSelfArmorHit(0, 120);
        RequestOwnAmmoPurchase();
        FieldRules.OpenBaseArmor(bFriendlyRed ? FieldTeam::Red : FieldTeam::Blue);
    }
    static const bool bAmmoExchangeProbe = FParse::Param(FCommandLine::Get(), TEXT("RefereeAmmoExchangeProbe"));
    static const bool bVisionExchangeProbe = FParse::Param(FCommandLine::Get(), TEXT("RefereeSupplyVisionProbe"));
    if (bAmmoExchangeProbe && !bLogisticsProbeTriggered && GameProgress == ERMUCGameProgress::Running &&
        MatchElapsedSeconds >= .5 && LocalSlot && (!bVisionExchangeProbe || LocalSlot->bSupplyVisualTagVisible))
    {
        bLogisticsProbeTriggered = true;
        if (!LocalSlot->IsSentry() || RosCompetitors.IsEmpty())
        {
            RequestOwnAmmoPurchase();
        }
        for (const AVehicleRig* Vehicle : RosCompetitors)
        {
            if (FMatchRobotSlot* Slot = FindSlot(Vehicle); Slot && Slot->IsSentry())
            {
                Slot->State.ProjectileAllowance17mm = 20;
            }
        }
    }
#endif

    const auto& Red = RuneRules.Get(true);
    const auto& Blue = RuneRules.Get(false);
    const auto VisualState = [this](const FRMUCRuneRules::Face& Face)
    {
        using Phase = FRMUCRuneRules::Phase;
        if (GameProgress != ERMUCGameProgress::Running)
        {
            FRMUCRuneFaceVisualState Off;
            Off.bBlanked = true;
            return Off;
        }
        const bool Blank = Face.State == Phase::Failed ||
            (Face.State == Phase::Activating && Face.Targets == 0);
        return FRMUCRuneFaceVisualState{Face.Lit, Face.Targets, Face.Confirmed, Face.Groups,
            Face.bBig, Blank, Face.State == Phase::Activated};
    };
    FieldVisuals.UpdateRune(RuneRules.AngleRad(), MatchElapsedSeconds, VisualState(Red), VisualState(Blue));
    FieldVisuals.UpdateOutposts(FieldRules.Get(FieldTeam::Red).OutpostRotor.AngleRadians,
        FieldRules.Get(FieldTeam::Blue).OutpostRotor.AngleRadians);
    const auto& RedPost = FieldRules.Get(FieldTeam::Red);
    const auto& BluePost = FieldRules.Get(FieldTeam::Blue);
    FieldVisuals.UpdateOutpostAffiliation(RedPost.OutpostOwner == FieldTeam::Red,
        BluePost.OutpostOwner == FieldTeam::Red, RedPost.OutpostHp > 0, BluePost.OutpostHp > 0);
    for (const FieldTeam Team : {FieldTeam::Red, FieldTeam::Blue})
    {
        const double Goal = FieldRules.Get(Team).bBaseArmorOpen ? 1. : 0.;
        double& Travel = BaseArmorTravel[FRMUCFieldRules::Index(Team)];
        Travel = FMath::FInterpConstantTo(Travel, Goal, DeltaSeconds, 1. / 1.5);
    }
    FieldVisuals.UpdateBaseArmor(FMath::SmoothStep(0., 1., BaseArmorTravel[0]), FMath::SmoothStep(0., 1., BaseArmorTravel[1]));
    bBigEnergyMode = MatchElapsedSeconds >= 180.;
}

#if WITH_DEV_AUTOMATION_TESTS
void ARMUCGameReferee::StepAmmoLedgerProbe()
{
    static const bool bEnabled = FParse::Param(FCommandLine::Get(), TEXT("RefereeAmmoLedgerProbe"));
    if (!bEnabled || AmmoLedgerProbeStage == 5 || GameProgress != ERMUCGameProgress::Running || MatchElapsedSeconds < .5)
        return;

    FMatchRobotSlot* Infantry = OwnSlot();
    FMatchRobotSlot* Sentry = FindSentrySlot(FriendlyTeam());
    if (!Infantry || Infantry->IsSentry() || !Sentry || !Infantry->PointEffects.bSupply || !Sentry->PointEffects.bSupply)
        return;

    const unsigned TeamIdx = FRMUCFieldRules::Index(FriendlyTeam());
    const TCHAR* Side = bFriendlyRed ? TEXT("red") : TEXT("blue");
    const TCHAR* OpposingSide = bFriendlyRed ? TEXT("blue") : TEXT("red");
    const auto Report = [&](const TCHAR* Stage)
    {
        const auto InfantryView = FieldDeviceState(Infantry);
        const auto SentryView = FieldDeviceState(Sentry);
        const auto& Local = InfantryView->GetObjectField(Side);
        const auto& Remote = SentryView->GetObjectField(Side);
        UE_LOG(LogTemp, Display, TEXT("RMUC_AMMO_LEDGER_PROBE %s %s t=%.3f cap_used=%d gold=%d infantry=%d sentry=%d local=%d remote=%d pending=%d other_remote=%d"),
            Side, Stage, MatchElapsedSeconds, TeamPurchasedAmmo17mm[TeamIdx], TeamGold[TeamIdx],
            Infantry->State.ProjectileAllowance17mm, Sentry->State.ProjectileAllowance17mm,
            Local->GetBoolField(TEXT("local_ammo_purchase_available")), Remote->GetBoolField(TEXT("remote_ammo_purchase_available")),
            Remote->GetBoolField(TEXT("remote_ammo_purchase_pending")),
            InfantryView->GetObjectField(OpposingSide)->GetBoolField(TEXT("remote_ammo_purchase_available")));
    };
    switch (AmmoLedgerProbeStage)
    {
    case 0:
        TeamPurchasedAmmo17mm[TeamIdx] = 890;
        Report(TEXT("initial"));
        RequestSentryAmmoPurchase(Sentry->Vehicle.Get(), true);
        RequestSentryAmmoPurchase(Sentry->Vehicle.Get(), true);
        break;
    case 1:
        Report(TEXT("ordered"));
        RequestOwnAmmoPurchase();
        RequestOwnAmmoPurchase();
        RequestSentryAmmoPurchase(Sentry->Vehicle.Get(), true);
        break;
    case 2:
        Report(TEXT("capped"));
        RequestOwnAmmoPurchase();
        RequestSentryAmmoPurchase(Sentry->Vehicle.Get(), true);
        break;
    case 3:
        if (Sentry->State.OwnLogistics.HasPendingRemoteAmmo()) return;
        Report(TEXT("delivered"));
        // Jump directly to the rule manual's 00:30 example after paid delivery.
        MatchElapsedSeconds = 390.0;
        StageRemainSeconds = 30.0;
        RequestOwnAmmoPurchase();
        break;
    case 4:
        if (MatchElapsedSeconds < 390.1) return;
        Report(TEXT("free"));
        break;
    default:
        return;
    }
    ++AmmoLedgerProbeStage;
}
#endif

const TCHAR* ARMUCGameReferee::OutpostControlPhase(FieldTeam Site) const
{
    const auto& Signal = OutpostControlSignals[FRMUCFieldRules::Index(Site)];
    if (!Signal) return TEXT("unavailable");
    if (Signal->bRed && Signal->bBlue) return TEXT("contested");
    const auto& Control = FieldRules.Get(Site).OutpostControl;
    if (!Control.bFull) return Signal->By(Control.Controller) ? TEXT("temporary") : TEXT("returning");
    if (Signal->By(FRMUCFieldRules::Other(Control.Controller))) return TEXT("capturing");
    return Control.TransitionSeconds > 0 ? TEXT("decaying") : TEXT("full");
}

TSharedRef<FJsonObject> ARMUCGameReferee::FieldDeviceState(const FMatchRobotSlot* ViewSlot) const
{
    auto Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("rules"), TEXT("RMUC2027 V1.0.0"));
    Root->SetBoolField(TEXT("rune_mesh_bound"), FieldVisuals.IsRuneReady());
    Root->SetBoolField(TEXT("dart_gate_meshes_bound"), FieldVisuals.AreDartGatesReady());
    Root->SetBoolField(TEXT("outpost_meshes_bound"), FieldVisuals.OutpostsAreReady());
    Root->SetBoolField(TEXT("centre_visibility_split_bound"), FieldVisuals.IsCentreVisibilitySplitReady());
    Root->SetBoolField(TEXT("rfid_geometry_calibrated"), false);
    Root->SetStringField(TEXT("rfid_mode"), FieldZones.IsLoaded() ? TEXT("diagram_cad_ground_reader") : TEXT("unavailable"));
    Root->SetBoolField(TEXT("base_armor_meshes_bound"), FieldVisuals.IsBaseArmorReady());
    Root->SetNumberField(TEXT("rune_angle_rad"), RuneRules.AngleRad());
    if (FieldVisuals.IsBaseArmorReady())
    {
        TArray<TSharedPtr<FJsonValue>> JointPositions;
        for (const bool bRed : {true, false})
        {
            TArray<double> Values;
            FieldVisuals.GetBaseJointPositions(bRed, Values);
            for (const double Value : Values) JointPositions.Add(MakeShared<FJsonValueNumber>(Value));
        }
        Root->SetArrayField(TEXT("base_armor_joint_positions"), JointPositions);
    }

    auto Side = [&](bool bRed, const FRMUCDartStation& Dart)
    {
        using Mode = FRMUCSupplyRules::EPurchaseMode;
        using Purchase = FRMUCSupplyRules::EPurchase;
        const FieldTeam Team = bRed ? FieldTeam::Red : FieldTeam::Blue;
        const FieldTeam Enemy = FRMUCFieldRules::Other(Team);
        const unsigned Index = FRMUCFieldRules::Index(Team);
        const unsigned EnemyIndex = FRMUCFieldRules::Index(Enemy);
        const auto& Control = FieldRules.Get(Team);
        const auto& EnemyControl = FieldRules.Get(Enemy);
        const auto& Rune = RuneRules.Get(bRed);
        auto State = MakeShared<FJsonObject>();
        const auto& Points = FieldPointEffects[Index];
        const auto& Contacts = FieldPhysicalContacts[Index];
        const FMatchRobotSlot* Seat = ViewSlot && ViewSlot->Team == Team ? ViewSlot : FindSentrySlot(Team);
        const bool bRunning = GameProgress == ERMUCGameProgress::Running;
        const bool bAlive = bRunning && Seat && IsValid(Seat->Vehicle.Get()) && Seat->State.Hp > 0;
        const bool bCanUseGainPoint = bAlive && !Seat->State.IsWeak();
        const auto& Incoming = bRed ? BlueDart : RedDart;
        State->SetBoolField(TEXT("supply_active"), bCanUseGainPoint && Seat->PointEffects.bSupply);
        State->SetBoolField(TEXT("supply_visual_tag_visible"), bAlive && Seat->bSupplyVisualTagVisible);
        State->SetBoolField(TEXT("supply_physical_card_read"),
            bAlive && Seat->PhysicalContacts[Index].Supply.value_or(false));
        State->SetBoolField(TEXT("local_ammo_exchange_active"), bCanUseGainPoint && Seat->PointEffects.CanPurchaseAmmo());
        State->SetBoolField(TEXT("vantage_active"), Points.bVantage);
        State->SetBoolField(TEXT("fortress_active"), Points.bFortress);
        State->SetBoolField(TEXT("enemy_fortress_active"), Points.bEnemyFortress);
        State->SetBoolField(TEXT("own_fortress_occupied"), Contacts[Index].Fortress.value_or(false));
        State->SetBoolField(TEXT("enemy_fortress_occupied"), Contacts[EnemyIndex].Fortress.value_or(false));
        State->SetBoolField(TEXT("own_fortress_available"), bAlive && FieldRules.CanActivateFortress(Team, Team, MatchElapsedSeconds));
        State->SetBoolField(TEXT("enemy_fortress_available"), bAlive && FieldRules.CanActivateFortress(Enemy, Team, MatchElapsedSeconds));
        State->SetNumberField(TEXT("enemy_fortress_armor_progress_s"), EnemyControl.ArmorActivationSeconds);
        State->SetNumberField(TEXT("enemy_fortress_armor_required_s"), FRMUCFieldRules::EnemyFortressActivationSeconds);
        State->SetBoolField(TEXT("central_highland_active"), Points.bCentralHighland);
        State->SetNumberField(TEXT("highland_traversal_remaining_s"),
            bAlive ? Seat->HighlandTraversal.Remaining(MatchElapsedSeconds) : 0.0);
        State->SetBoolField(TEXT("highland_traversal_effective"),
            bAlive && Seat->HighlandTraversal.HasEffect(MatchElapsedSeconds));
        static constexpr const TCHAR* TraversalCardNames[] = {
            TEXT("none"), TEXT("red_lower"), TEXT("red_upper"), TEXT("blue_lower"), TEXT("blue_upper"), TEXT("other")
        };
        const auto Card = bAlive ? Seat->HighlandTraversal.ObservedCard() : ERMUCHighlandCard::None;
        State->SetStringField(TEXT("highland_traversal_card"), TraversalCardNames[static_cast<int32>(Card)]);
        State->SetBoolField(TEXT("can_heal_at_own_outpost"), bAlive && FieldZones.IsLoaded() &&
            Control.OutpostControl.Controller == Team && Control.OutpostControl.bFull &&
            Incoming.GainPointSuppressionRemaining(ERMUCDartTarget::Outpost) <= 0.);
        const auto CanPurchase = [&](Mode Method)
        {
            return bAlive && (Method == Mode::Remote || bCanUseGainPoint) &&
                Seat->State.OwnLogistics.CheckAmmoPurchase(Seat->PointEffects, true, TeamGold[Index],
                    TeamPurchasedAmmo17mm[Index], Method, MatchElapsedSeconds) == Purchase::Accepted;
        };
        State->SetBoolField(TEXT("local_ammo_purchase_available"), CanPurchase(Mode::Local));
        State->SetBoolField(TEXT("remote_ammo_purchase_available"), CanPurchase(Mode::Remote));
        State->SetBoolField(TEXT("remote_ammo_purchase_pending"), bRunning && Seat && Seat->State.OwnLogistics.HasPendingRemoteAmmo());
        State->SetNumberField(TEXT("base_hp"), Control.BaseHp);
        State->SetBoolField(TEXT("base_protected"), FieldRules.BaseProtected(Team));
        State->SetBoolField(TEXT("base_armor_open"), Control.bBaseArmorOpen);
        State->SetNumberField(TEXT("base_armor_fraction"), FMath::SmoothStep(0., 1., BaseArmorTravel[bRed ? 0 : 1]));
        State->SetBoolField(TEXT("base_controller_red"), Control.BaseControl.Controller == FieldTeam::Red);
        State->SetBoolField(TEXT("base_control_full"), Control.BaseControl.bFull);
        State->SetNumberField(TEXT("base_capture_seconds"), Control.BaseControl.TransitionSeconds);
        State->SetNumberField(TEXT("outpost_capture_seconds"), Control.OutpostControl.TransitionSeconds);
        State->SetNumberField(TEXT("outpost_transition_required_seconds"), FRMUCFieldRules::ControlTransitionSeconds);
        const auto& Signal = OutpostControlSignals[FRMUCFieldRules::Index(Team)];
        State->SetStringField(TEXT("outpost_control_phase"), OutpostControlPhase(Team));
        State->SetBoolField(TEXT("outpost_control_red_active"), Signal && Signal->bRed);
        State->SetBoolField(TEXT("outpost_control_blue_active"), Signal && Signal->bBlue);
        const auto& RedContact = FieldPhysicalContacts[0][Index];
        const auto& BlueContact = FieldPhysicalContacts[1][Index];
        State->SetBoolField(TEXT("outpost_card_red_occupied"), RedContact.Outpost.value_or(false));
        State->SetBoolField(TEXT("outpost_card_blue_occupied"), BlueContact.Outpost.value_or(false));
        State->SetBoolField(TEXT("central_highland_card_red_occupied"), RedContact.CentralHighland.value_or(false));
        State->SetBoolField(TEXT("central_highland_card_blue_occupied"), BlueContact.CentralHighland.value_or(false));
        State->SetNumberField(TEXT("enemy_fortress_activation_seconds"), Control.ArmorActivationSeconds);
        State->SetNumberField(TEXT("outpost_hp"), Control.OutpostHp);
        State->SetBoolField(TEXT("outpost_owner_red"), Control.OutpostOwner == FieldTeam::Red);
        State->SetBoolField(TEXT("outpost_controller_red"), Control.OutpostControl.Controller == FieldTeam::Red);
        State->SetBoolField(TEXT("outpost_control_full"), Control.OutpostControl.bFull);
        State->SetBoolField(TEXT("outpost_rebuild_pending"), Control.OutpostHp == 0);
        State->SetNumberField(TEXT("control_score"), bRed ? RedControlSeconds : BlueControlSeconds);
        State->SetNumberField(TEXT("attack_damage"), bRed ? RedAttackDamage : BlueAttackDamage);
        State->SetNumberField(TEXT("outpost_angle_rad"), Control.OutpostRotor.AngleRadians);
        State->SetNumberField(TEXT("outpost_angular_rate_rad_s"), Control.OutpostRotor.AngularRateRadS);
        State->SetBoolField(TEXT("outpost_rotor_parked"), Control.OutpostRotor.bParked);
        State->SetStringField(TEXT("rune_phase"), RuneStateName(Rune.State));
        State->SetNumberField(TEXT("rune_chances"), RuneRules.Available(bRed));
        State->SetNumberField(TEXT("rune_targets"), Rune.Targets);
        State->SetNumberField(TEXT("rune_groups"), Rune.Groups);
        const double EffectiveRuneSeconds = RuneRules.EffectiveBuffRemaining(bRed);
        State->SetNumberField(TEXT("rune_defence_pct"), EffectiveRuneSeconds > 0. ? Rune.DefencePercent : 0);
        State->SetBoolField(TEXT("rune_large"), Rune.bBig);
        State->SetNumberField(TEXT("rune_hits"), Rune.Hits);
        State->SetNumberField(TEXT("rune_average_rings"), Rune.Hits ? double(Rune.RingTotal) / Rune.Hits : 0.);
        State->SetNumberField(TEXT("rune_attack_pct"), EffectiveRuneSeconds > 0. ? Rune.AttackPercent : 0);
        State->SetNumberField(TEXT("rune_cooling_multiplier"), EffectiveRuneSeconds > 0. ? Rune.CoolingMultiplier : 1);
        State->SetNumberField(TEXT("rune_buff_seconds"), FMath::Max(0., Rune.BuffUntil - MatchElapsedSeconds));
        State->SetNumberField(TEXT("rune_effective_buff_seconds"), EffectiveRuneSeconds);
        State->SetStringField(TEXT("dart_gate"), DartStateName(Dart.State()));
        State->SetNumberField(TEXT("dart_gate_open_fraction"), Dart.GateOpenFraction());
        State->SetNumberField(TEXT("dart_gate_chances"), Dart.AvailableChances());
        State->SetStringField(TEXT("dart_target"), DartTargetName(Dart.Target()));
        State->SetNumberField(TEXT("dart_remaining"), Dart.DartsRemaining());
        State->SetNumberField(TEXT("dart_incoming_base_suppression_s"),
            Incoming.GainPointSuppressionRemaining(ERMUCDartTarget::FixedBase));
        State->SetNumberField(TEXT("dart_incoming_outpost_suppression_s"),
            Incoming.GainPointSuppressionRemaining(ERMUCDartTarget::Outpost));
        State->SetNumberField(TEXT("dart_incoming_blackout_s"), Incoming.OpponentBlackoutRemaining());
        State->SetBoolField(TEXT("base_point_active"), Points.bBase);
        State->SetBoolField(TEXT("dart_detector_open"), Dart.DetectorWindowOpen());
        State->SetNumberField(TEXT("dart_detector_seconds"), Dart.DetectorWindowRemaining());
        return State;
    };
    Root->SetObjectField(TEXT("red"), Side(true, RedDart));
    Root->SetObjectField(TEXT("blue"), Side(false, BlueDart));
    return Root;
}
