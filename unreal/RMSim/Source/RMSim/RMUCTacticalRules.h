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
    double CoolingMultiplier = 1.0;
    double ChassisPowerLimitW = 100.0;
    int DefenceBonusPercent = 0;
    int VulnerabilityBonusPercent = 0;
};

inline FRMUCSentryStanceModifiers GetRMUCSentryStanceModifiers(ERMUCSentryStance Stance)
{
    switch (Stance)
    {
    case ERMUCSentryStance::Attack:
        return {2.0, 100.0, 0, 25};
    case ERMUCSentryStance::Defense:
        return {1.0, 80.0, 25, 0};
    case ERMUCSentryStance::Move:
    default:
        return {1.0, 120.0, 0, 0};
    }
}

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
