#pragma once

#include <algorithm>
#include <array>

enum class ERMUCSentryStance
{
    Move = 0,
    Attack = 1,
    Defense = 2
};

struct FRMUCSentryStanceModifiers
{
    double BarrelCoolingPerSec = 10.0;
    double ChassisPowerLimitW = 150.0;
    int DefenceBonusPercent = 0;
    int VulnerabilityBonusPercent = 25;
};

// RoboMaster 2027 Auto Sentry Three-Stance Rules (全自动哨兵三姿态, 5.0s 切换冷却)
inline FRMUCSentryStanceModifiers GetRMUCSentryStanceModifiers(ERMUCSentryStance Stance)
{
    switch (Stance)
    {
    case ERMUCSentryStance::Attack:
        // 进攻姿态: 100W 底盘功率, 30/s 枪口冷却, 0% 防御, +25% 易伤
        return {30.0, 100.0, 0, 25};
    case ERMUCSentryStance::Defense:
        // 防御姿态: 100W 底盘功率, 10/s 枪口冷却, +25% 防御, 0% 易伤
        return {10.0, 100.0, 25, 0};
    case ERMUCSentryStance::Move:
    default:
        // 移动姿态 (开局默认): 150W 底盘功率, 10/s 枪口冷却, 0% 防御, +25% 易伤
        return {10.0, 150.0, 0, 25};
    }
}

struct FRMUCSentryStanceState
{
    static constexpr double SwitchCooldownSeconds = 5.0;

    ERMUCSentryStance Current = ERMUCSentryStance::Move;
    double CooldownRemainingSec = 0.0;

    void Reset()
    {
        Current = ERMUCSentryStance::Move;
        CooldownRemainingSec = 0.0;
    }

    void Step(double DeltaSeconds)
    {
        if (DeltaSeconds > 0.0)
        {
            CooldownRemainingSec = std::max(0.0, CooldownRemainingSec - DeltaSeconds);
        }
    }

    bool TrySwitch(ERMUCSentryStance Target)
    {
        if (Target == Current)
        {
            return true;
        }
        if (CooldownRemainingSec > 1e-6)
        {
            return false;
        }
        Current = Target;
        CooldownRemainingSec = SwitchCooldownSeconds;
        return true;
    }

    FRMUCSentryStanceModifiers Modifiers() const
    {
        return GetRMUCSentryStanceModifiers(Current);
    }
};

// Shared team economy pool (Red = 0, Blue = 1)
struct FRMUCSharedEconomy
{
    std::array<int, 2> Gold{800, 800};

    void Reset(int InitialGold = 800) { Gold = {InitialGold, InitialGold}; }
    int Get(bool bRed) const { return Gold[bRed ? 0 : 1]; }
    void Add(bool bRed, int Amount)
    {
        int& Wallet = Gold[bRed ? 0 : 1];
        Wallet = std::clamp(Wallet + Amount, 0, 9999);
    }
    bool Spend(bool bRed, int Cost)
    {
        int& Wallet = Gold[bRed ? 0 : 1];
        if (Cost < 0 || Wallet < Cost) return false;
        Wallet -= Cost;
        return true;
    }
};

// Radar station marking & double-vulnerability rules (Red = 0, Blue = 1)
struct FRMUCRadarRules
{
    struct FTeamRadar
    {
        double MarkProgress = 0.0;       // 0..100
        int DoubleVulnCharges = 0;       // 0..2
        double ActiveDoubleVulnSec = 0.0; // >0 => opponent takes +100% vulnerability
    };
    std::array<FTeamRadar, 2> Teams{};

    void Reset() { Teams = {}; }

    void AddMarkProgress(bool bRed, double Delta)
    {
        FTeamRadar& T = Teams[bRed ? 0 : 1];
        T.MarkProgress = std::clamp(T.MarkProgress + Delta, 0.0, 100.0);
        if (T.MarkProgress >= 100.0 && T.DoubleVulnCharges < 2)
        {
            T.MarkProgress = 0.0;
            ++T.DoubleVulnCharges;
        }
    }

    bool TriggerDoubleVulnerability(bool bRed, double DurationSec = 30.0)
    {
        FTeamRadar& T = Teams[bRed ? 0 : 1];
        if (T.DoubleVulnCharges <= 0 || T.ActiveDoubleVulnSec > 0.0) return false;
        --T.DoubleVulnCharges;
        T.ActiveDoubleVulnSec = DurationSec;
        return true;
    }

    void Step(double DeltaSeconds)
    {
        if (DeltaSeconds <= 0.0) return;
        for (FTeamRadar& T : Teams)
        {
            T.ActiveDoubleVulnSec = std::max(0.0, T.ActiveDoubleVulnSec - DeltaSeconds);
        }
    }

    int OpponentVulnerabilityBonus(bool bTargetRed) const
    {
        const FTeamRadar& Attacker = Teams[bTargetRed ? 1 : 0];
        return Attacker.ActiveDoubleVulnSec > 0.0 ? 100 : 0;
    }
};
