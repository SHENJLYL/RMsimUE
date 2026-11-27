#include "RMUCGameReferee.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Engine/GameViewportClient.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

void ARMUCGameReferee::StepDartRewardProbe()
{
    static const FString Capture = []()
    {
        FString Path;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeDartRewardProof="), Path);
        return Path;
    }();
    if (Capture.IsEmpty()) return;
    static double CapturedAt = -1.;
    if (CapturedAt >= 0.)
    {
        if (MatchElapsedSeconds >= CapturedAt + 4.4)
            FPlatformMisc::RequestExitWithStatus(false, 0);
        return;
    }
    if (GameProgress != ERMUCGameProgress::Running || MatchElapsedSeconds < 1.) return;

    const FieldTeam Defending = FriendlyTeam();
    const FieldTeam Attacking = FRMUCFieldRules::Other(Defending);
    const bool bAttackRed = Attacking == FieldTeam::Red;
    auto& Station = bAttackRed ? RedDart : BlueDart;
    TArray<FMatchRobotSlot*> AttackInfantry, DefenceInfantry;
    for (FMatchRobotSlot& Slot : MatchRobots)
    {
        Slot.State.ExperienceTenths = 0;
        Slot.State.Hp = Slot.MaxHp();
        if (Slot.IsInfantry()) (Slot.Team == Attacking ? AttackInfantry : DefenceInfantry).Add(&Slot);
    }
    FMatchRobotSlot* Sentry = FindSentrySlot(Defending);
    if (AttackInfantry.Num() != 2 || DefenceInfantry.Num() != 2 || !Sentry)
    {
        UE_LOG(LogTemp, Error, TEXT("RMUC_DART_REWARD_PROOF requires two infantry and a sentry per team"));
        FPlatformMisc::RequestExitWithStatus(false, 2);
        return;
    }

    FieldRules = {};
    RedDart.Reset();
    BlueDart.Reset();
    RuneRules.Reset(10);
    const auto At = [&](double Time)
    {
        MatchElapsedSeconds = Time;
        StageRemainSeconds = 420. - Time;
        RuneRules.AdvanceTo(Time);
        RedDart.Update(Time, ERMUCDartPhase::Running);
        BlueDart.Update(Time, ERMUCDartPhase::Running);
    };
    const auto GrantTraversal = [&](FMatchRobotSlot& Slot, double Time)
    {
        Slot.HighlandTraversal.Observe(ERMUCHighlandCard::None, Time, true);
        Slot.HighlandTraversal.Observe(ERMUCHighlandCard::RedLower, Time, true);
        Slot.HighlandTraversal.Observe(ERMUCHighlandCard::RedUpper, Time + .1, true);
    };
    using Target = ERMUCDartTarget;
    bool bPassed = true;
    const auto Check = [&](bool bOk, const TCHAR* Name)
    {
        UE_LOG(LogTemp, Display, TEXT("RMUC_DART_REWARD_CHECK %s=%d"), Name, bOk);
        bPassed &= bOk;
    };
    At(30.);
    Check(FieldRules.BaseProtected(Defending), TEXT("protected_base_fixture"));
    Check(RuneRules.Request(!bAttackRed), TEXT("rune_fixture"));
    for (int32 Index = 0; Index < 5; ++Index)
    {
        At(30. + Index * .2);
        const int32 Targets = RuneRules.Get(!bAttackRed).Targets;
        int32 Arm = 0;
        while (Arm < 5 && !(Targets & (1 << Arm))) ++Arm;
        Check(RuneRules.Hit(!bAttackRed, Arm, 10), TEXT("seed_rune_hit"));
    }
    const double RuneExpiry = RuneRules.Get(!bAttackRed).BuffUntil;
    GrantTraversal(*Sentry, 31.);
    Check(Station.SelectTarget(Target::RandomFixedBase) && Station.RequestOpen(), TEXT("open_random_fixed"));
    At(38.);
    Check(ConfirmDartDetector(bAttackRed, Target::RandomFixedBase), TEXT("first_fixed_hit"));
    Check(FieldRules.Get(Defending).BaseHp == FRMUCFieldRules::BaseMaxHp - 300, TEXT("bypass_base_and_rune_defence"));
    Check(GetOwnDartBlackoutRemaining() == 10. && !Sentry->HighlandTraversal.HasEffect(38.), TEXT("first_blackout_and_traversal"));
    Check(RuneRules.EffectiveBuffRemaining(!bAttackRed) == 0. && RuneRules.Get(!bAttackRed).BuffUntil == RuneExpiry,
        TEXT("suppress_rune_keep_countdown"));
    Check(AttackInfantry[0]->State.ExperienceTenths == 3000 && AttackInfantry[1]->State.ExperienceTenths == 3000,
        TEXT("split_fixed_experience"));
    At(40.);
    Check(ConfirmDartDetector(bAttackRed, Target::RandomFixedBase), TEXT("second_fixed_hit"));
    Check(GetOwnDartBlackoutRemaining() == 13. && AttackInfantry[0]->Level() == 2 &&
        AttackInfantry[0]->State.Hp == AttackInfantry[0]->MaxHp(), TEXT("stack_and_level"));
    At(54.);
    Check(GetOwnDartBlackoutRemaining() == 0. && RuneRules.EffectiveBuffRemaining(!bAttackRed) > 0. &&
        Sentry->HighlandTraversal.HasEffect(54.), TEXT("restore_at_expiry"));

    Station.Reset();
    At(60.);
    Check(Station.SelectTarget(Target::LateMovingBase) && Station.RequestOpen(), TEXT("open_late_moving"));
    At(67.);
    GrantTraversal(*Sentry, 67.);
    FMatchRobotSlot* LowHpInfantry = DefenceInfantry[0] == OwnSlot() ? DefenceInfantry[1] : DefenceInfantry[0];
    FMatchRobotSlot* HealthyInfantry = LowHpInfantry == DefenceInfantry[0] ? DefenceInfantry[1] : DefenceInfantry[0];
    LowHpInfantry->State.Hp = 35;
    Sentry->State.BuffDefence = 50;
    Sentry->State.InvulnerableTimer = 5.;
    At(69.);
    const int32 PreviousDamage = bAttackRed ? RedAttackDamage : BlueAttackDamage;
    Check(ConfirmDartDetector(bAttackRed, Target::LateMovingBase), TEXT("late_moving_hit"));
    Check(Sentry->State.Hp == 200 && LowHpInfantry->State.Hp == 0 &&
        HealthyInfantry->State.Hp == 100 && (bAttackRed ? RedAttackDamage : BlueAttackDamage) - PreviousDamage == 1585,
        TEXT("direct_ground_damage_and_team_total"));
    Check(FieldRules.Get(Defending).bBaseArmorOpen && RuneRules.Get(!bAttackRed).BuffUntil == 69. &&
        Sentry->HighlandTraversal.Remaining(69.) == 0., TEXT("open_armor_and_remove_buffs"));
    Check(AttackInfantry[0]->State.ExperienceTenths == 18500 && AttackInfantry[1]->State.ExperienceTenths == 18500 &&
        AttackInfantry[0]->Level() == 4 && DefenceInfantry[0]->State.ExperienceTenths == 0,
        TEXT("moving_experience_no_kill_bonus"));
    FVector2D Ros;
    Check(DartBlackoutMapScreenToWorld(FVector2D(1280., 720.), FVector2D(640., 414.), Ros) &&
        Ros.IsNearlyZero(.01) && !DartBlackoutMapScreenToWorld(FVector2D(1280., 720.), FVector2D::ZeroVector, Ros),
        TEXT("map_projection_and_clipping"));
    // Keep the 3-second operator-input check isolated from subsequent auto launches.
    Target Pending;
    int32 PendingId = -1;
    while (Station.TryLaunch(Pending, &PendingId)) Station.ResolveVirtualFlight(PendingId, false);
    UE_LOG(LogTemp, Display, TEXT("RMUC_DART_REWARD_PROOF passed=%d"), bPassed);
    if (!bPassed)
    {
        FPlatformMisc::RequestExitWithStatus(false, 2);
        return;
    }
    FScreenshotRequest::RequestScreenshot(Capture, true, false);
    CapturedAt = MatchElapsedSeconds;
}
#endif
