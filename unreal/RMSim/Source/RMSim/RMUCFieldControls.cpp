#include "RMUCGameReferee.h"
#include "VehicleRig.h"

void ARMUCGameReferee::RequestOwnAmmoPurchase()
{
    FMatchRobotSlot* Slot = OwnSlot();
    if (!Slot || Slot->bAiControlled || GameProgress != ERMUCGameProgress::Running || Slot->State.Hp <= 0) return;

    Slot->State.AmmoPurchaseRequest = EAmmoPurchaseRequest::Manual;
}

void ARMUCGameReferee::RequestSentryAmmoPurchase(const AVehicleRig* Vehicle, bool bRemote)
{
    FMatchRobotSlot* Slot = FindSlot(Vehicle);
    if (Slot && Slot->IsSentry() && GameProgress == ERMUCGameProgress::Running && Slot->State.Hp > 0)
    {
        auto& Request = Slot->State.AmmoPurchaseRequest;
        if (!Request) Request = bRemote ? EAmmoPurchaseRequest::Remote : EAmmoPurchaseRequest::Local;
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
            Slot.HighlandTraversal = {};
            Slot.PhysicalContacts = {};
            Slot.bSupplyVisualTagVisible = false;
            Slot.SupplyVisualTagExpiry = 0.0;
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
        const unsigned TeamIdx = FRMUCFieldRules::Index(Slot.Team);
        ERMUCHighlandCard TraversalCard = ERMUCHighlandCard::None;
        FVector ReaderSurfaceM = FVector::ZeroVector;
        if (bAlive)
        {
            Slot.PhysicalContacts = ObserveFieldPoints(*Vehicle, &TraversalCard, &ReaderSurfaceM);
#if WITH_DEV_AUTOMATION_TESTS
            static const bool bVisualOnlyProbe = FParse::Param(FCommandLine::Get(), TEXT("RefereeSupplyVisionProbe"));
            if (bVisualOnlyProbe && Vehicle == Rig) Slot.PhysicalContacts[TeamIdx].Supply = false;
#endif
        }
        else
        {
            Slot.PhysicalContacts.fill({false, false, false, false, false, false});
        }
        Slot.HighlandTraversal.Observe(TraversalCard, MatchElapsedSeconds, bAlive && !Slot.State.IsWeak());
        SlotHeld[I] = Slot.ContactHold.Observe(Slot.PhysicalContacts, MatchElapsedSeconds, bAlive);
        Slot.bSupplyVisualTagVisible = bAlive && IsSupplyVisualTagVisible(*Vehicle, Slot.Team, &ReaderSurfaceM);
        if (!bAlive) Slot.SupplyVisualTagExpiry = 0.0;
        if (Slot.bSupplyVisualTagVisible) Slot.SupplyVisualTagExpiry = MatchElapsedSeconds + 2.0;
        if (MatchElapsedSeconds + 1e-9 < Slot.SupplyVisualTagExpiry) SlotHeld[I][TeamIdx].Supply = true;
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
        if (Slot.HighlandTraversal.HasEffect(MatchElapsedSeconds))
            Slot.PointEffects.DefencePercent = FMath::Max(Slot.PointEffects.DefencePercent, FRMUCHighlandTraversal::DefencePercent);
        ClearWeakAtGainCard(Slot);

        auto& Record = Slot.State;
        const auto Points = Record.IsWeak() ? FRMUCFieldPointEffects{} : Slot.PointEffects;
        const unsigned TeamIdx = FRMUCFieldRules::Index(Slot.Team);
        const int32 Before = Record.ProjectileAllowance17mm;
        Record.OwnLogistics.Step(RunningSeconds, MatchElapsedSeconds, FieldRules.Get(Slot.Team).BaseHp,
            Points, Slot.MaxHp(), Record.Hp, Record.ProjectileAllowance17mm, Slot.IsSentry());
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
        const EAmmoPurchaseRequest Request = *Record.AmmoPurchaseRequest;
        Record.AmmoPurchaseRequest.reset();
        using Mode = FRMUCSupplyRules::EPurchaseMode;
        const bool bRemote = Request == EAmmoPurchaseRequest::Remote ||
            (Request == EAmmoPurchaseRequest::Manual && !Slot.PointEffects.CanPurchaseAmmo());
        const bool bLocalSeat = (I == 0);
        using EPurchase = FRMUCSupplyRules::EPurchase;
        switch (Record.OwnLogistics.PurchaseAmmo(Points, Record.Hp > 0, TeamGold[TeamIdx],
            TeamPurchasedAmmo17mm[TeamIdx], Record.ProjectileAllowance17mm,
            bRemote ? Mode::Remote : Mode::Local, MatchElapsedSeconds))
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
    if (!bDart && FieldRules.BaseProtected(Site))
    {
        if (BaseHitFlash <= 0.0)
        {
            PushBannerEvent(TEXT("BASE PROTECTED BY OWN FULL OUTPOST CONTROL"), 1.5);
        }
        BaseHitFlash = 0.25;
        return 0;
    }

    BaseHitFlash = 0.25;
    const int32 HpLost = FieldRules.HitBase(Site, Attacker, Damage, bDart);
    if (FieldRules.Get(Site).BaseHp == 0)
    {
        SetGameProgress(ERMUCGameProgress::GameOver);
        WinnerText = bTargetRed ? TEXT("BLUE VICTORY — RED BASE DESTROYED") : TEXT("RED VICTORY — BLUE BASE DESTROYED");
        PushCombatBroadcast(AtkSign, AtkVeh, TEXT("DESTROYED"), TgtSign, FString::Printf(TEXT("%s BASE"), SiteName), 10.0);
    }
    return HpLost;
}
