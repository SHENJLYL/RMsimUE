#include "RMUCGameReferee.h"
#include "VehicleRig.h"

#if WITH_DEV_AUTOMATION_TESTS
void ARMUCGameReferee::StepRespawnParityProbe()
{
    static int Stage = 0;
    static double DeathAt = 0.0;
    static double SupplySpawnAt = 0.0;
    static double FieldSpawnAt = 0.0;
    static double SecondDeathAt = 0.0;
    static double ThresholdAt = 0.0;
    static double ThresholdProgress = 0.0;
    static double PaidAt = 0.0;
    constexpr double TimeTolerance = .11;
    if (Stage < 0 || MatchElapsedSeconds < 1.0) return;

    const auto Check = [&](bool bPassed, const TCHAR* Name)
    {
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_CHECK name=%s passed=%d match_s=%.6f"), Name, bPassed, MatchElapsedSeconds);
        if (!bPassed)
        {
            Stage = -1;
            FPlatformMisc::RequestExitWithStatus(false, 2);
        }
        return bPassed;
    };
    FMatchRobotSlot* Field = OwnSlot();
    FMatchRobotSlot* Supply = FindSentrySlot(FRMUCFieldRules::Other(FriendlyTeam()));
    if (!Field || !Supply || !Field->Vehicle.IsValid() || !Supply->Vehicle.IsValid())
    {
        Check(false, TEXT("paired_seats"));
        return;
    }
    auto& F = Field->State;
    auto& S = Supply->State;
    const auto Hit = [&](FMatchRobotSlot& Slot)
    {
        return ApplyRobotDamage(Slot, nullptr, 0, 100000, ERMUCHpDeductionReason::ArmorHit);
    };
    const auto Near = [](double Actual, double Expected) { return FMath::Abs(Actual - Expected) < TimeTolerance; };
    const auto RawSupply = [](const FMatchRobotSlot& Slot)
    {
        return Slot.PhysicalContacts[FRMUCFieldRules::Index(Slot.Team)].Supply.value_or(false);
    };

    if (Stage == 0)
    {
        if (!Check(!Field->PointEffects.bSupply && RawSupply(*Supply), TEXT("physical_supply_fixture"))) return;
        DeathAt = MatchElapsedSeconds;
        F.BarrelHeat = S.BarrelHeat = 31.0;
        F.ChassisBufferEnergyJ = S.ChassisBufferEnergyJ = 1.0;
        if (!Check(Hit(*Field) && Hit(*Supply) && F.Hp == 0 && S.Hp == 0 &&
            F.BarrelHeat == 0.0 && S.BarrelHeat == 0.0 && F.ChassisBufferEnergyJ == 60.0 && S.ChassisBufferEnergyJ == 60.0 &&
            F.OwnRespawnDuration == 10.0 && S.OwnRespawnDuration == 10.0, TEXT("death_resets_and_requirement"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_DEATH field_id=%d supply_id=%d requirement=%.6f match_s=%.6f"),
            Field->ProtocolRobotId(), Supply->ProtocolRobotId(), F.OwnRespawnDuration, DeathAt);
        Stage = 1;
    }
    else if (Stage == 1 && S.Hp > 0)
    {
        SupplySpawnAt = MatchElapsedSeconds;
        if (!Check(F.Hp == 0 && Near(SupplySpawnAt - DeathAt, 2.5) && S.Hp == Supply->MaxHp() / 10 &&
            S.bNaturalRespawnWeak && S.InvulnerableTimer == 30.0, TEXT("supply_natural_spawn"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_NATURAL case=supply id=%d wait_s=%.6f hp=%d shield_s=%.6f weak=%d"),
            Supply->ProtocolRobotId(), SupplySpawnAt - DeathAt, S.Hp, S.InvulnerableTimer, S.IsWeak());
        Stage = 2;
    }
    else if (Stage == 2)
    {
        if (!Check(!S.IsWeak() && Near(S.InvulnerableTimer, 10.0) && !Hit(*Supply), TEXT("supply_card_keeps_minimum_shield"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_CARD id=%d since_spawn_s=%.6f shield_s=%.6f weak=%d"),
            Supply->ProtocolRobotId(), MatchElapsedSeconds - SupplySpawnAt, S.InvulnerableTimer, S.IsWeak());
        Stage = 3;
    }
    else if (Stage == 3 && F.Hp > 0)
    {
        FieldSpawnAt = MatchElapsedSeconds;
        if (!Check(Near(FieldSpawnAt - DeathAt, 10.0) && F.Hp == Field->MaxHp() / 10 && F.bNaturalRespawnWeak &&
            F.InvulnerableTimer == 30.0 && !Hit(*Field) &&
            RequestRosShot(Field->Vehicle.Get(), FTransform::Identity) == TEXT("weak"), TEXT("field_natural_spawn_and_shooter_lock"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_NATURAL case=field id=%d wait_s=%.6f hp=%d shield_s=%.6f weak=%d"),
            Field->ProtocolRobotId(), FieldSpawnAt - DeathAt, F.Hp, F.InvulnerableTimer, F.IsWeak());
        Stage = 4;
    }
    else if (Stage == 4 && S.InvulnerableTimer == 0.0)
    {
        if (!Check(Near(MatchElapsedSeconds - SupplySpawnAt, 10.0), TEXT("supply_shield_expired"))) return;
        Stage = 5;
    }
    else if (Stage == 5 && F.InvulnerableTimer == 0.0)
    {
        if (!Check(Near(MatchElapsedSeconds - FieldSpawnAt, 30.0) && F.bNaturalRespawnWeak &&
            RequestRosShot(Field->Vehicle.Get(), FTransform::Identity) == TEXT("weak"), TEXT("field_shield_expired_weak_remains"))) return;
        // Supply geometry is exercised above. Inject an observed base card to check late clearing.
        const auto PriorContacts = Field->PhysicalContacts;
        const auto PriorEffects = Field->PointEffects;
        Field->PhysicalContacts[FRMUCFieldRules::Index(Field->Team)].Base = true;
        Field->PointEffects.bBase = true;
        ClearWeakAtGainCard(*Field);
        Field->PhysicalContacts = PriorContacts;
        Field->PointEffects = PriorEffects;
        if (!Check(!F.IsWeak() && F.InvulnerableTimer == 0.0 && Hit(*Field), TEXT("late_base_card_clears_weak"))) return;
        SecondDeathAt = MatchElapsedSeconds;
        const auto Team = Field->Team;
        const auto Enemy = FRMUCFieldRules::Other(Team);
        FieldRules.HitOutpost(Team, Enemy, FRMUCFieldRules::OutpostMaxHp);
        if (!Check(FieldRules.HitBase(Team, Enemy, 6000) == 6000, TEXT("base_damage_fixture"))) return;
        Stage = 6;
    }
    else if (Stage == 6 && MatchElapsedSeconds >= SecondDeathAt + .50)
    {
        ThresholdAt = MatchElapsedSeconds;
        ThresholdProgress = F.RespawnTimer;
        const double Expected = std::round(10.0 + SecondDeathAt / 10.0) / .70;
        if (!Check(Near(ThresholdProgress, ThresholdAt - SecondDeathAt) && Near(F.OwnRespawnDuration, Expected),
            TEXT("exact_4000_base_hp"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_BASE id=%d base_hp=4000 progress_per_s=%.6f required=%.6f"),
            Field->ProtocolRobotId(), ThresholdProgress / (ThresholdAt - SecondDeathAt), F.OwnRespawnDuration);
        FieldRules.HitBase(Field->Team, FRMUCFieldRules::Other(Field->Team), 1);
        Stage = 7;
    }
    else if (Stage == 7 && MatchElapsedSeconds >= ThresholdAt + .50)
    {
        const double Rate = (F.RespawnTimer - ThresholdProgress) / (MatchElapsedSeconds - ThresholdAt);
        const double Expected = std::round(10.0 + SecondDeathAt / 10.0) / .69995;
        if (!Check(FieldRules.Get(Field->Team).BaseHp == 3999 && Near(Rate, 4.0) && Near(F.OwnRespawnDuration, Expected),
            TEXT("below_4000_base_hp"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_BASE id=%d base_hp=3999 progress_per_s=%.6f required=%.6f"),
            Field->ProtocolRobotId(), Rate, F.OwnRespawnDuration);
        PaidAt = MatchElapsedSeconds;
        const int32 BeforeGold = F.RemainingGoldCoin;
        if (!Check(GetInstantRespawnCost() == 100 && RequestInstantRespawn() && F.Hp == Field->MaxHp() &&
            F.WeakTimer == 3.0 && F.InvulnerableTimer == 3.0 && F.BuybackPowerTimer == 4.0 &&
            Field->PowerLimitW() == 200.0 && F.RemainingGoldCoin == BeforeGold - 100 && !Hit(*Field), TEXT("paid_spawn"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_PAID id=%d hp=%d shield_s=%.6f weak_s=%.6f power_w=%.6f power_s=%.6f gold=%d->%d"),
            Field->ProtocolRobotId(), F.Hp, F.InvulnerableTimer, F.WeakTimer, Field->PowerLimitW(), F.BuybackPowerTimer, BeforeGold, F.RemainingGoldCoin);
        Stage = 8;
    }
    else if (Stage == 8 && F.InvulnerableTimer == 0.0)
    {
        if (!Check(Near(MatchElapsedSeconds - PaidAt, 3.0) && !F.IsWeak() && Field->PowerLimitW() == 200.0,
            TEXT("paid_protection_expired"))) return;
        Stage = 9;
    }
    else if (Stage == 9 && F.BuybackPowerTimer == 0.0)
    {
        const double Expected = std::round(30.0 + MatchElapsedSeconds / 10.0) / .69995;
        if (!Check(Near(MatchElapsedSeconds - PaidAt, 4.0) && Field->PowerLimitW() == 150.0 && Hit(*Field) &&
            F.OwnBuybackCount == 1 && Near(F.OwnRespawnDuration, Expected), TEXT("paid_power_expired_and_next_requirement"))) return;
        UE_LOG(LogRMSim, Display, TEXT("RMUC_RESPAWN_FINISHED passed=1 id=%d next_required=%.6f"), Field->ProtocolRobotId(), F.OwnRespawnDuration);
        Stage = -1;
        FPlatformMisc::RequestExitWithStatus(false, 0);
    }
}
#endif
