#include "RMUCGameReferee.h"
#include "VehicleRig.h"

void ARMUCGameReferee::RequestOwnAmmoPurchase()
{
    FMatchRobotSlot* Slot = OwnSlot();
    if (!Slot || GameProgress != ERMUCGameProgress::Running || Slot->State.Hp <= 0) return;

    const auto& Points = Slot->PointEffects;
    if (Points.bSupply)
    {
        // Inside Supply Zone: Infantry (#3/#4) first claim 10 free rounds per press from the team's
        // unlocked free infantry pool (50/min up to 300 total), then seamlessly buy 10 rounds / 10 gold.
        if (!Slot->IsSentry())
        {
            const unsigned TeamIdx = FRMUCFieldRules::Index(Slot->Team);
            const int32 AvailableMinutes = FMath::Clamp(FMath::FloorToInt((MatchElapsedSeconds + 1e-9) / 60.0), 0, 6);
            const int32 UnlockedFree = FMath::Min(FRMUCSupplyRules::SentryFreeAmmoCap, AvailableMinutes * 50);
            const int32 RemainingFree = UnlockedFree - TeamInfantryFreeAmmoClaimed[TeamIdx];
            if (RemainingFree >= FRMUCSupplyRules::LocalAmmoAmount)
            {
                constexpr int32 StepRounds = FRMUCSupplyRules::LocalAmmoAmount; // 10 rounds per press
                Slot->State.ProjectileAllowance17mm += StepRounds;
                TeamInfantryFreeAmmoClaimed[TeamIdx] += StepRounds;
                PushCombatBroadcast(BroadcastTeam(Slot->Team), Slot->DisplayName(),
                    FString::Printf(TEXT("免费领弹 +%d (剩余可领 %d)"), StepRounds, RemainingFree - StepRounds),
                    0, TEXT(""), 2.0);
                return;
            }
        }
        RequestSentryAmmoPurchase(Rig, false);
        return;
    }

    // Outside Supply Zone: order the minimum official remote purchase (100 rounds / 150 gold, 6s out of combat).
    RequestSentryAmmoPurchase(Rig, true);
}

void ARMUCGameReferee::RequestSentryAmmoPurchase(const AVehicleRig* Vehicle, bool bRemote)
{
    using Mode = FRMUCSupplyRules::EPurchaseMode;
    FMatchRobotSlot* Slot = FindSlot(Vehicle);
    if (Slot && GameProgress == ERMUCGameProgress::Running && Slot->State.Hp > 0)
    {
        auto& Request = Slot->State.AmmoPurchaseRequest;
        if (!Request) Request = bRemote ? Mode::Remote : Mode::Local;
    }
}

void ARMUCGameReferee::StepFieldObserver(double DeltaSeconds)
{
    if (GameProgress != ERMUCGameProgress::Running)
    {
        FieldPointEffects = {};
        FieldContactHolds = {};
        FieldPhysicalContacts = {};
        OutpostControlSignals = {};
        for (auto& Seat : Competitors) Seat.AmmoPurchaseRequest.reset();
        for (auto& Slot : MatchRobots)
        {
            Slot.ContactHold = {};
            Slot.PhysicalContacts = {};
            Slot.PointEffects = {};
            Slot.State.AmmoPurchaseRequest.reset();
        }
        return;
    }

    const double RunningSeconds = FMath::Clamp(DeltaSeconds, 0.0, MatchElapsedSeconds);
    auto MergeContact = [](std::optional<bool>& Dst, const std::optional<bool>& Src)
    {
        if (Src.value_or(false)) Dst = true;
        else if (!Dst && Src) Dst = false;
    };
    auto MergePoints = [&](FRMUCFieldPoints& Dst, const FRMUCFieldPoints& Src)
    {
        for (unsigned Site = 0; Site < 2; ++Site)
        {
            MergeContact(Dst[Site].Supply, Src[Site].Supply);
            MergeContact(Dst[Site].Base, Src[Site].Base);
            MergeContact(Dst[Site].Fortress, Src[Site].Fortress);
            MergeContact(Dst[Site].Outpost, Src[Site].Outpost);
            MergeContact(Dst[Site].CentralHighland, Src[Site].CentralHighland);
            MergeContact(Dst[Site].Vantage, Src[Site].Vantage);
        }
    };

    FRMUCFieldPoints TeamHeld[2];
    FRMUCFieldPoints TeamPhysical[2];
    for (int32 T = 0; T < 2; ++T)
    {
        TeamHeld[T].fill({false, false, false, false, false, false});
        TeamPhysical[T].fill({false, false, false, false, false, false});
    }
    TArray<FRMUCFieldPoints> SlotHeld;
    SlotHeld.SetNum(MatchRobots.Num());
    for (int32 I = 0; I < MatchRobots.Num(); ++I)
    {
        FMatchRobotSlot& Slot = MatchRobots[I];
        const AVehicleRig* Vehicle = Slot.Vehicle.Get();
        const bool bAlive = IsValid(Vehicle) && Slot.State.Hp > 0;
        if (bAlive)
        {
            Slot.PhysicalContacts = ObserveFieldPoints(*Vehicle);
        }
        else
        {
            Slot.PhysicalContacts.fill({false, false, false, false, false, false});
        }
        SlotHeld[I] = Slot.ContactHold.Observe(Slot.PhysicalContacts, MatchElapsedSeconds, bAlive);
        const unsigned TeamIdx = FRMUCFieldRules::Index(Slot.Team);
        MergePoints(TeamPhysical[TeamIdx], Slot.PhysicalContacts);
        MergePoints(TeamHeld[TeamIdx], SlotHeld[I]);
    }
    FieldPhysicalContacts[0] = TeamPhysical[0];
    FieldPhysicalContacts[1] = TeamPhysical[1];

    FRMUCFieldPoints Red = TeamHeld[0];
    FRMUCFieldPoints Blue = TeamHeld[1];
    std::array<FRMUCFieldRules::FSiteActivation, 2> Activation;
    for (const FieldTeam Site : FRMUCFieldRules::Teams)
    {
        const unsigned Index = FRMUCFieldRules::Index(Site);
        Activation[Index] = FieldPointLatches[Index].Observe(Site, FieldRules, MatchElapsedSeconds, Red[Index], Blue[Index]);
        OutpostControlSignals[Index] = Activation[Index].OutpostPosition;
    }
    StepFieldControls(RunningSeconds, Activation);

    // Official ruling #208. Dart hits suppress the card's buffs, while the
    // continuous control observation still comes from the detected card.
    auto ApplyDartSuppression = [&](FRMUCFieldPoints& Points)
    {
        for (const FieldTeam Site : FRMUCFieldRules::Teams)
        {
            const unsigned Index = FRMUCFieldRules::Index(Site);
            const auto& Incoming = Site == FieldTeam::Red ? BlueDart : RedDart;
            if (Incoming.GainPointSuppressionRemaining(ERMUCDartTarget::Outpost) > 0.)
                Points[Index].Outpost = false;
            if (Incoming.GainPointSuppressionRemaining(ERMUCDartTarget::FixedBase) > 0.)
                Points[Index].Base = false;
        }
    };
    ApplyDartSuppression(Red);
    ApplyDartSuppression(Blue);
    FieldPointEffects[FRMUCFieldRules::Index(FieldTeam::Red)] =
        FRMUCFieldPointEffects::Evaluate(FieldRules, FieldTeam::Red, MatchElapsedSeconds, Red, FieldPointLatches);
    FieldPointEffects[FRMUCFieldRules::Index(FieldTeam::Blue)] =
        FRMUCFieldPointEffects::Evaluate(FieldRules, FieldTeam::Blue, MatchElapsedSeconds, Blue, FieldPointLatches);

    for (int32 I = 0; I < MatchRobots.Num(); ++I)
    {
        FMatchRobotSlot& Slot = MatchRobots[I];
        ApplyDartSuppression(SlotHeld[I]);
        Slot.PointEffects = FRMUCFieldPointEffects::Evaluate(
            FieldRules, Slot.Team, MatchElapsedSeconds, SlotHeld[I], FieldPointLatches);

        auto& Record = Slot.State;
        const auto& Points = Slot.PointEffects;
        const unsigned TeamIdx = FRMUCFieldRules::Index(Slot.Team);
        const int32 Before = Record.ProjectileAllowance17mm;
        Record.OwnLogistics.Step(RunningSeconds, MatchElapsedSeconds, FieldRules.Get(Slot.Team).BaseHp,
            Points, Slot.MaxHp(), Record.Hp, Record.ProjectileAllowance17mm,
            Slot.IsSentry(), &TeamInfantryFreeAmmoClaimed[TeamIdx], &TeamInfantryClaimedMinutes[TeamIdx]);
        if (Record.ProjectileAllowance17mm > Before)
        {
            PushCombatBroadcast(BroadcastTeam(Slot.Team), Slot.DisplayName(),
                FString::Printf(TEXT("SUPPLY +%d AMMO"), Record.ProjectileAllowance17mm - Before), 0, TEXT(""), 2.5);
        }
        const int32 Delivered = Record.OwnLogistics.DeliverRemoteAmmo(MatchElapsedSeconds, Record.ProjectileAllowance17mm);
        if (Delivered > 0)
        {
            PushCombatBroadcast(BroadcastTeam(Slot.Team), Slot.DisplayName(),
                FString::Printf(TEXT("REMOTE PURCHASE +%d AMMO"), Delivered), 0, TEXT(""), 2.5);
        }
        if (!Record.AmmoPurchaseRequest) continue;
        const auto Mode = *Record.AmmoPurchaseRequest;
        Record.AmmoPurchaseRequest.reset();
        const bool bRemote = Mode == FRMUCSupplyRules::EPurchaseMode::Remote;
        const bool bLocalSeat = (I == 0);
        using EPurchase = FRMUCSupplyRules::EPurchase;
        switch (Record.OwnLogistics.PurchaseAmmo(Points, Record.Hp > 0, TeamGold[TeamIdx],
            Record.ProjectileAllowance17mm, Mode, MatchElapsedSeconds))
        {
        case EPurchase::Accepted:
            PushCombatBroadcast(BroadcastTeam(Slot.Team), Slot.DisplayName(), bRemote
                ? TEXT("REMOTE AMMO ORDER -150 GOLD / 6s") : TEXT("AMMO PURCHASE +10 / -10 GOLD"), 0, TEXT(""), 2.5);
            break;
        case EPurchase::Unavailable:
            if (bLocalSeat) PushBannerEvent(bRemote ? TEXT("REMOTE AMMO REQUIRES 6s OUT OF COMBAT")
                : TEXT("AMMO PURCHASE REQUIRES AN ACTIVE EXCHANGE POINT"), 2.5);
            break;
        case EPurchase::LimitReached:
            if (bLocalSeat) PushBannerEvent(TEXT("TEAM AMMO PURCHASE LIMIT REACHED"), 2.5);
            break;
        case EPurchase::InsufficientGold:
            if (bLocalSeat) PushBannerEvent(bRemote ? TEXT("REMOTE AMMO REQUIRES 150 GOLD") : TEXT("AMMO PURCHASE REQUIRES 10 GOLD"), 2.5);
            break;
        }
    }
    SyncSharedTeamState();
}

void ARMUCGameReferee::StepFieldControls(
    double DeltaSeconds,
    const std::array<FRMUCFieldRules::FSiteActivation, 2>& Activation)
{
    if (GameProgress != ERMUCGameProgress::Running)
    {
        return;
    }

    using Team = FRMUCFieldRules::Team;
    const bool bRedWasOpen = FieldRules.Get(Team::Red).bBaseArmorOpen;
    const bool bBlueWasOpen = FieldRules.Get(Team::Blue).bBaseArmorOpen;
    const std::array Previous{FieldRules.Get(Team::Red), FieldRules.Get(Team::Blue)};
    FieldRules.Step(DeltaSeconds, MatchElapsedSeconds, Activation);
    for (const Team Site : FRMUCFieldRules::Teams)
    {
        const auto& Before = Previous[FRMUCFieldRules::Index(Site)];
        const auto& After = FieldRules.Get(Site);
        const TCHAR* SiteName = Site == Team::Red ? TEXT("RED") : TEXT("BLUE");
        const int32 CtrlSign = (After.OutpostControl.Controller == Team::Red) ? 1 : -1;
        const int32 SiteSign = (Site == Team::Red) ? 1 : -1;
        const FString CtrlVeh = (After.OutpostControl.Controller == Team::Red) ? TEXT("RED #7 SENTRY") : TEXT("BLUE #7 SENTRY");
        const FString SitePost = FString::Printf(TEXT("%s OUTPOST"), SiteName);
        if (Before.OutpostHp == 0 && After.OutpostHp > 0)
        {
            PushCombatBroadcast(CtrlSign, CtrlVeh, TEXT("REBUILT"), SiteSign, SitePost, 4.5);
        }
        else if (Before.OutpostControl.Controller != After.OutpostControl.Controller ||
                 Before.OutpostControl.bFull != After.OutpostControl.bFull)
        {
            PushCombatBroadcast(CtrlSign, CtrlVeh,
                After.OutpostControl.bFull ? TEXT("CAPTURED") : TEXT("CONTESTING"),
                SiteSign, SitePost, 3.5);
        }
    }
    if (!bRedWasOpen && FieldRules.Get(Team::Red).bBaseArmorOpen)
    {
        PushBannerEvent(TEXT("RED BASE ARMOR UNFOLDED"), 3.5);
    }
    if (!bBlueWasOpen && FieldRules.Get(Team::Blue).bBaseArmorOpen)
    {
        PushBannerEvent(TEXT("BLUE BASE ARMOR UNFOLDED"), 3.5);
    }
}

int32 ARMUCGameReferee::ApplyFieldStructureHit(bool bTargetRed, bool bOutpost, bool bAttackerRed, int32 Damage, bool bDart)
{
    if (GameProgress != ERMUCGameProgress::Running || Damage <= 0)
    {
        return 0;
    }

    using Team = FRMUCFieldRules::Team;
    const Team Site = bTargetRed ? Team::Red : Team::Blue;
    const Team Attacker = bAttackerRed ? Team::Red : Team::Blue;
    const TCHAR* SiteName = bTargetRed ? TEXT("RED") : TEXT("BLUE");
    const int32 AtkSign = bAttackerRed ? 1 : -1;
    const int32 TgtSign = bTargetRed ? 1 : -1;
    const FString AtkVeh = bDart ? (bAttackerRed ? TEXT("红方飞镖") : TEXT("蓝方飞镖"))
        : (bAttackerRed ? TEXT("RED #7 SENTRY") : TEXT("BLUE #7 SENTRY"));
    if (bOutpost)
    {
        const FRMUCFieldRules::FOutpostHit Hit = FieldRules.HitOutpost(Site, Attacker, Damage);
        const FString PostLbl = FString::Printf(TEXT("%s OUTPOST"), SiteName);
        if (Hit.bRebuilt)
        {
            PushCombatBroadcast(AtkSign, AtkVeh, TEXT("CAPTURED & REBUILT"), TgtSign, PostLbl, 4.5);
        }
        else if (Hit.bDestroyed)
        {
            PushCombatBroadcast(AtkSign, AtkVeh, TEXT("DESTROYED"), TgtSign, PostLbl, 4.5);
        }
        return Hit.HpLost;
    }

    if (Site == Attacker || FieldRules.Get(Site).BaseHp == 0)
    {
        return 0;
    }
    if (FieldRules.BaseProtected(Site))
    {
        if (BaseHitFlash <= 0.0)
        {
            PushBannerEvent(TEXT("BASE PROTECTED BY OWN FULL OUTPOST CONTROL"), 1.5);
        }
        BaseHitFlash = 0.25;
        return 0;
    }

    BaseHitFlash = 0.25;
    const int32 HpLost = FieldRules.HitBase(Site, Attacker, Damage);
    if (FieldRules.Get(Site).BaseHp == 0)
    {
        GameProgress = ERMUCGameProgress::GameOver;
        WinnerText = bTargetRed ? TEXT("BLUE VICTORY — RED BASE DESTROYED") : TEXT("RED VICTORY — BLUE BASE DESTROYED");
        PushCombatBroadcast(AtkSign, AtkVeh, TEXT("DESTROYED"), TgtSign, FString::Printf(TEXT("%s BASE"), SiteName), 10.0);
    }
    return HpLost;
}
