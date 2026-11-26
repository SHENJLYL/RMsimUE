#include "RMUCGameReferee.h"
#include "VehicleRig.h"

void ARMUCGameReferee::RequestOwnAmmoPurchase()
{
    RequestSentryAmmoPurchase(Rig, false);
}

void ARMUCGameReferee::RequestSentryAmmoPurchase(const AVehicleRig* Vehicle, bool bRemote)
{
    using Mode = FRMUCSupplyRules::EPurchaseMode;
    const auto Seat = TeamFor(Vehicle);
    if (Seat && GameProgress == ERMUCGameProgress::Running && Competitor(*Seat).Hp > 0)
    {
        auto& Request = Competitor(*Seat).AmmoPurchaseRequest;
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
        return;
    }

    const double RunningSeconds = FMath::Clamp(DeltaSeconds, 0.0, MatchElapsedSeconds);
    const auto Sample = [this](FieldTeam Team)
    {
        const unsigned Index = FRMUCFieldRules::Index(Team);
        const auto* Vehicle = VehicleFor(Team);
        const bool bAlive = IsValid(Vehicle) && Competitor(Team).Hp > 0;
        auto& Physical = FieldPhysicalContacts[Index];
        if (bAlive)
        {
            Physical = ObserveFieldPoints(*Vehicle);
        }
        else
        {
            Physical.fill({false, false, false, false, false, false});
        }
        return FieldContactHolds[Index].Observe(Physical, MatchElapsedSeconds, bAlive);
    };
    FRMUCFieldPoints Red = Sample(FieldTeam::Red);
    FRMUCFieldPoints Blue = Sample(FieldTeam::Blue);
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
    for (const FieldTeam Site : FRMUCFieldRules::Teams)
    {
        const unsigned Index = FRMUCFieldRules::Index(Site);
        const auto& Incoming = Site == FieldTeam::Red ? BlueDart : RedDart;
        if (Incoming.GainPointSuppressionRemaining(ERMUCDartTarget::Outpost) > 0.)
        {
            Red[Index].Outpost = false;
            Blue[Index].Outpost = false;
        }
        if (Incoming.GainPointSuppressionRemaining(ERMUCDartTarget::FixedBase) > 0.)
        {
            Red[Index].Base = false;
            Blue[Index].Base = false;
        }
    }
    FieldPointEffects[FRMUCFieldRules::Index(FieldTeam::Red)] =
        FRMUCFieldPointEffects::Evaluate(FieldRules, FieldTeam::Red, MatchElapsedSeconds, Red, FieldPointLatches);
    FieldPointEffects[FRMUCFieldRules::Index(FieldTeam::Blue)] =
        FRMUCFieldPointEffects::Evaluate(FieldRules, FieldTeam::Blue, MatchElapsedSeconds, Blue, FieldPointLatches);

    for (FieldTeam Team : FRMUCFieldRules::Teams)
    {
        auto& Record = Competitor(Team);
        const auto& Points = FieldPointEffects[FRMUCFieldRules::Index(Team)];
        const int32 Before = Record.ProjectileAllowance17mm;
        Record.OwnLogistics.Step(RunningSeconds, MatchElapsedSeconds, FieldRules.Get(Team).BaseHp,
            Points, SentryMaxHp, Record.Hp, Record.ProjectileAllowance17mm);
        if (Record.ProjectileAllowance17mm > Before)
        {
            PushCombatBroadcast(BroadcastTeam(Team), SentryName(Team),
                FString::Printf(TEXT("SUPPLY +%d AMMO"), Record.ProjectileAllowance17mm - Before), 0, TEXT(""), 2.5);
        }
        const int32 Delivered = Record.OwnLogistics.DeliverRemoteAmmo(MatchElapsedSeconds, Record.ProjectileAllowance17mm);
        if (Delivered > 0)
        {
            PushCombatBroadcast(BroadcastTeam(Team), SentryName(Team),
                FString::Printf(TEXT("REMOTE PURCHASE +%d AMMO"), Delivered), 0, TEXT(""), 2.5);
        }
        if (!Record.AmmoPurchaseRequest) continue;
        const auto Mode = *Record.AmmoPurchaseRequest;
        Record.AmmoPurchaseRequest.reset();
        const bool bRemote = Mode == FRMUCSupplyRules::EPurchaseMode::Remote;
        const bool bLocalSeat = Team == FriendlyTeam();
        using EPurchase = FRMUCSupplyRules::EPurchase;
        switch (Record.OwnLogistics.PurchaseAmmo(Points, Record.Hp > 0, Record.RemainingGoldCoin,
            Record.ProjectileAllowance17mm, Mode, MatchElapsedSeconds))
        {
        case EPurchase::Accepted:
            PushCombatBroadcast(BroadcastTeam(Team), SentryName(Team), bRemote
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
