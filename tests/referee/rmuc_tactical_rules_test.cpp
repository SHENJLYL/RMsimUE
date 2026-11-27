#include <cassert>
#include <cmath>

#include "../../unreal/RMSim/Source/RMSim/RMUCFieldRules.h"
#include "../../unreal/RMSim/Source/RMSim/RMUCTacticalRules.h"

int main()
{
    // 1. Three-stance modifiers & 5.0s cooldown
    FRMUCSentryStanceState Stance;
    assert(Stance.Current == ERMUCSentryStance::Move);
    assert(Stance.CooldownRemainingSec == 0.0);
    {
        const auto Mod = Stance.Modifiers();
        assert(std::abs(Mod.ChassisPowerLimitW - 150.0) < 1e-6);
        assert(std::abs(Mod.BarrelCoolingPerSec - 10.0) < 1e-6);
        assert(Mod.DefenceBonusPercent == 0);
        assert(Mod.VulnerabilityBonusPercent == 25);
    }
    assert(Stance.TrySwitch(ERMUCSentryStance::Move));
    assert(Stance.CooldownRemainingSec == 0.0);
    assert(Stance.TrySwitch(ERMUCSentryStance::Attack));
    assert(Stance.Current == ERMUCSentryStance::Attack);
    assert(std::abs(Stance.CooldownRemainingSec - 5.0) < 1e-6);
    {
        const auto Mod = Stance.Modifiers();
        assert(std::abs(Mod.ChassisPowerLimitW - 100.0) < 1e-6);
        assert(std::abs(Mod.BarrelCoolingPerSec - 30.0) < 1e-6);
        assert(Mod.DefenceBonusPercent == 0);
        assert(Mod.VulnerabilityBonusPercent == 25);
    }
    Stance.Step(4.9);
    assert(!Stance.TrySwitch(ERMUCSentryStance::Defense));
    assert(Stance.Current == ERMUCSentryStance::Attack);
    Stance.Step(0.11);
    assert(Stance.TrySwitch(ERMUCSentryStance::Defense));
    {
        const auto Mod = Stance.Modifiers();
        assert(std::abs(Mod.ChassisPowerLimitW - 100.0) < 1e-6);
        assert(std::abs(Mod.BarrelCoolingPerSec - 10.0) < 1e-6);
        assert(Mod.DefenceBonusPercent == 25);
        assert(Mod.VulnerabilityBonusPercent == 0);
    }
    Stance.Reset();
    assert(Stance.Current == ERMUCSentryStance::Move && Stance.CooldownRemainingSec == 0.0);

    // 2. Official 10Hz chassis overpower HP deduction (MaxHP = 400)
    // Buffer > 0 -> 0 damage
    assert(ComputeRMUCOverpowerDamagePerTick(150.0, 100.0, 10.0, 400) == 0.0);
    // K <= 10% -> 10% * 400 * 0.1 = 4.0 HP/tick
    assert(std::abs(ComputeRMUCOverpowerDamagePerTick(108.0, 100.0, 0.0, 400) - 4.0) < 1e-6);
    // 10% < K <= 20% -> 20% * 400 * 0.1 = 8.0 HP/tick
    assert(std::abs(ComputeRMUCOverpowerDamagePerTick(115.0, 100.0, 0.0, 400) - 8.0) < 1e-6);
    // K > 20% -> 40% * 400 * 0.1 = 16.0 HP/tick
    assert(std::abs(ComputeRMUCOverpowerDamagePerTick(130.0, 100.0, 0.0, 400) - 16.0) < 1e-6);

    // 3. Official 10Hz barrel overheat HP deduction (MaxHP = 400, Q0 = 260)
    assert(ComputeRMUCOverheatDamagePerTick(260.0, 260.0, 400) == 0.0);
    // Q1 = 310 -> 400 * 0.1 * (50 / 250) = 8.0 HP/tick
    assert(std::abs(ComputeRMUCOverheatDamagePerTick(310.0, 260.0, 400) - 8.0) < 1e-6);
    // Severe overheat Q1 = 570 (2*Q0 = 520) -> 400 * (50 / 250) = 80.0 HP immediate
    assert(std::abs(ComputeRMUCSevereOverheatDamage(570.0, 260.0, 400) - 80.0) < 1e-6);

    // 4. Official respawn progress bar formula: round(10 + elapsed/10 + 20*buybacks)
    assert(std::abs(ComputeRMUCRespawnDurationSeconds(0.0, 0) - 10.0) < 1e-6);
    assert(std::abs(ComputeRMUCRespawnDurationSeconds(180.0, 0) - 28.0) < 1e-6);
    assert(std::abs(ComputeRMUCRespawnDurationSeconds(420.0, 0) - 52.0) < 1e-6);
    assert(std::abs(ComputeRMUCRespawnDurationSeconds(180.0, 1) - 48.0) < 1e-6);
    assert(std::abs(ComputeRMUCRespawnDurationSeconds(180.0, 2) - 68.0) < 1e-6);
    assert(ComputeRMUCRespawnDurationSeconds(300.0, 2) == 80.0);
    // The round-half-up occurs before division. The scaled requirement retains its fraction.
    assert(ComputeRMUCRespawnDurationSeconds(4.99, 0) == 10.0);
    assert(ComputeRMUCRespawnDurationSeconds(5.0, 0) == 11.0);
    assert(ComputeRMUCRespawnDurationSeconds(4.99999999999, 0) == 11.0);
    assert(std::abs(ComputeRMUCRespawnDurationSeconds(5.0, 0) / FRMUCFieldRules::LogisticsFactor(4000) - 110.0 / 7.0) < 1e-9);
    assert(FRMUCRespawnRules::ProgressPerSecond(false, 4000) == 1);
    assert(FRMUCRespawnRules::ProgressPerSecond(false, 3999) == 4);
    assert(FRMUCRespawnRules::ProgressPerSecond(true, 10000) == 4);
    assert(FRMUCRespawnRules::ProgressPerSecond(true, 3999) == 4);

    assert(FRMUCRespawnRules::SpawnHp(400, false) == 40);
    assert(FRMUCRespawnRules::SpawnHp(200, false) == 20);
    assert(FRMUCRespawnRules::SpawnHp(400, true) == 400);
    assert(FRMUCRespawnRules::ShieldAfterGainCard(30.0, false) == 10.0);
    assert(FRMUCRespawnRules::ShieldAfterGainCard(25.0, false) == 5.0);
    assert(FRMUCRespawnRules::ShieldAfterGainCard(20.0, false) == 0.0);
    assert(FRMUCRespawnRules::ShieldAfterGainCard(19.9, false) == 0.0);
    assert(FRMUCRespawnRules::ShieldAfterGainCard(2.0, true) == 2.0);
    assert(FRMUCRespawnRules::PowerLimit(150.0, 1.0) == 200.0);
    assert(FRMUCRespawnRules::PowerLimit(60.0, 1.0) == 120.0);
    assert(FRMUCRespawnRules::PowerLimit(150.0, 0.0) == 150.0);
    assert(FRMUCRespawnRules::PaidCost(60.0, 1) == 100);
    assert(FRMUCRespawnRules::PaidCost(60.00000000001, 1) == 100);
    assert(FRMUCRespawnRules::PaidCost(60.005, 1) == 180);
    assert(FRMUCRespawnRules::PaidCost(300.0, 3) == 460);

    return 0;
}
