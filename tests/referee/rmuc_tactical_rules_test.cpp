#include <cassert>
#include <cmath>

#include "../../unreal/RMSim/Source/RMSim/RMUCTacticalRules.h"

int main()
{
    FRMUCSentryStanceState Stance;
    // Default opening stance is MOVE (150W, 10/s cooling, 0% defence, +25% vulnerability)
    assert(Stance.Current == ERMUCSentryStance::Move);
    assert(Stance.CooldownRemainingSec == 0.0);
    {
        const auto Mod = Stance.Modifiers();
        assert(std::abs(Mod.ChassisPowerLimitW - 150.0) < 1e-6);
        assert(std::abs(Mod.BarrelCoolingPerSec - 10.0) < 1e-6);
        assert(Mod.DefenceBonusPercent == 0);
        assert(Mod.VulnerabilityBonusPercent == 25);
    }

    // Idempotent switch to same stance does not consume cooldown
    assert(Stance.TrySwitch(ERMUCSentryStance::Move));
    assert(Stance.CooldownRemainingSec == 0.0);

    // Switch to ATTACK (100W, 30/s cooling, 0% defence, +25% vulnerability) starts 5.0s cooldown
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

    // Switching during 5s cooldown must be rejected
    Stance.Step(4.9);
    assert(!Stance.TrySwitch(ERMUCSentryStance::Defense));
    assert(Stance.Current == ERMUCSentryStance::Attack);

    // After full 5.0s cooldown elapses, switch to DEFENSE (100W, 10/s cooling, +25% defence, 0% vulnerability) succeeds
    Stance.Step(0.11);
    assert(Stance.CooldownRemainingSec == 0.0);
    assert(Stance.TrySwitch(ERMUCSentryStance::Defense));
    assert(Stance.Current == ERMUCSentryStance::Defense);
    assert(std::abs(Stance.CooldownRemainingSec - 5.0) < 1e-6);
    {
        const auto Mod = Stance.Modifiers();
        assert(std::abs(Mod.ChassisPowerLimitW - 100.0) < 1e-6);
        assert(std::abs(Mod.BarrelCoolingPerSec - 10.0) < 1e-6);
        assert(Mod.DefenceBonusPercent == 25);
        assert(Mod.VulnerabilityBonusPercent == 0);
    }

    // Reset restores default MOVE stance and clears cooldown
    Stance.Reset();
    assert(Stance.Current == ERMUCSentryStance::Move);
    assert(Stance.CooldownRemainingSec == 0.0);
    return 0;
}
