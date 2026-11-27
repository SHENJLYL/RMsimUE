#pragma once

#include <algorithm>
#include <array>
#include <cmath>

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

// RoboMaster 2027 Auto Sentry Three-Stance Rules (5.0s switch cooldown)
inline FRMUCSentryStanceModifiers GetRMUCSentryStanceModifiers(ERMUCSentryStance Stance)
{
    switch (Stance)
    {
    case ERMUCSentryStance::Attack:
        // Attack: 100W chassis power, 30/s cooling, 0% defence, +25% vulnerability
        return {30.0, 100.0, 0, 25};
    case ERMUCSentryStance::Defense:
        // Defense: 100W chassis power, 10/s cooling, +25% defence, 0% vulnerability
        return {10.0, 100.0, 25, 0};
    case ERMUCSentryStance::Move:
    default:
        // Move (opening default): 150W chassis power, 10/s cooling, 0% defence, +25% vulnerability
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

// Official RMUC 10Hz chassis overpower HP deduction (after 60J buffer energy is exhausted):
//   K = (P_r - P_l) / P_l
//   K <= 10%: 10% MaxHP/s | 10% < K <= 20%: 20% MaxHP/s | K > 20%: 40% MaxHP/s
inline double ComputeRMUCOverpowerDamagePerTick(
    double PowerW, double PowerLimitW, double BufferEnergyJ, int MaxHp)
{
    if (MaxHp <= 0 || PowerLimitW <= 0.0 || BufferEnergyJ > 1e-6 || PowerW <= PowerLimitW)
    {
        return 0.0;
    }
    const double Ratio = (PowerW - PowerLimitW) / PowerLimitW;
    const double TierRate = (Ratio <= 0.10) ? 0.10 : ((Ratio <= 0.20) ? 0.20 : 0.40);
    return static_cast<double>(MaxHp) * TierRate * 0.1;
}

// Official RMUC 10Hz barrel overheat HP deduction while Q1 > Q0:
//   DeltaHP = MaxHP * 0.1 * (Q1 - Q0) / 250
inline double ComputeRMUCOverheatDamagePerTick(double BarrelHeat, double HeatLimit, int MaxHp)
{
    if (MaxHp <= 0 || HeatLimit <= 0.0 || BarrelHeat <= HeatLimit)
    {
        return 0.0;
    }
    return static_cast<double>(MaxHp) * 0.1 * (BarrelHeat - HeatLimit) / 250.0;
}

// Official RMUC immediate severe overheat HP deduction when Q1 >= 2 * Q0:
//   DeltaHP = MaxHP * (Q1 - 2*Q0) / 250 (with Q1 clamped back to 2*Q0)
inline double ComputeRMUCSevereOverheatDamage(double BarrelHeat, double HeatLimit, int MaxHp)
{
    if (MaxHp <= 0 || HeatLimit <= 0.0 || BarrelHeat < 2.0 * HeatLimit)
    {
        return 0.0;
    }
    return static_cast<double>(MaxHp) * (BarrelHeat - 2.0 * HeatLimit) / 250.0;
}

// Official RMUC respawn progress bar duration:
//   round(10 + (420 - remain_sec) / 10 + 20 * buyback_count)
inline double ComputeRMUCRespawnDurationSeconds(double MatchElapsedSeconds, int BuybackCount)
{
    const double Elapsed = std::clamp(MatchElapsedSeconds, 0.0, 420.0);
    const int Buybacks = std::max(0, BuybackCount);
    return std::round(10.0 + (Elapsed + 1e-9) / 10.0 + 20.0 * static_cast<double>(Buybacks));
}

// RMUC 2027 §§5.3.2, 5.4.1 and 5.6.1.
struct FRMUCRespawnRules
{
    static constexpr double NaturalShieldSeconds = 30.0;
    static constexpr double NaturalMinimumShieldSeconds = 10.0;
    static constexpr double PaidProtectionSeconds = 3.0;
    static constexpr double PaidPowerSeconds = 4.0;

    static int SpawnHp(int MaxHp, bool bPaid)
    {
        return bPaid ? MaxHp : std::max(1, static_cast<int>(std::round(MaxHp * 0.10)));
    }

    static int ProgressPerSecond(bool bAtOwnSupply, int OwnBaseHp)
    {
        return (bAtOwnSupply || OwnBaseHp < 4000) ? 4 : 1;
    }

    static double ShieldAfterGainCard(double Remaining, bool bPaid)
    {
        if (bPaid) return Remaining;
        return std::max(0.0, Remaining - (NaturalShieldSeconds - NaturalMinimumShieldSeconds));
    }

    static double PowerLimit(double BaseLimit, double PaidPowerRemaining)
    {
        return PaidPowerRemaining > 0.0 ? std::min(2.0 * BaseLimit, 200.0) : BaseLimit;
    }

    static int PaidCost(double MatchElapsedSeconds, int Level)
    {
        const double Elapsed = std::max(0.0, MatchElapsedSeconds - 1e-9);
        return static_cast<int>(std::ceil(Elapsed / 60.0)) * 80 + Level * 20;
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
