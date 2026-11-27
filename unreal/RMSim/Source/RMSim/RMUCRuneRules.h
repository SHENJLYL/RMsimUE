#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <random>

// RMUC 2027 V1.0.0, §5.6.3 and tables 5-22–5-24. The two faces share one axle.
class FRMUCRuneRules
{
public:
    enum class Phase { Idle, Activating, Activated, Failed };
    struct Face
    {
        Phase State = Phase::Idle;
        int Targets = 0;
        int Lit = 0;
        int Confirmed = 0;
        int Groups = 0;
        int BigWins = 0;
        int AttackPercent = 0;
        int DefencePercent = 0;
        int CoolingMultiplier = 1;
        double BuffUntil = 0.;
        double BuffSuppressedUntil = 0.;
        int ExtraExperienceTenths = 0;
        bool bBig = false;

        int SmallUsed = 0;
        int BigUsed = 0;
        int Hits = 0;
        int RingTotal = 0;
        bool bOptionalSecond = false;
        double AttemptUntil = 0.;
        double NextGroupAt = 0.;
        double HitUntil = 0.;
    };

    void Reset(std::uint32_t Seed)
    {
        Random.seed(Seed);
        Faces = {};
        Now = Angle = BigStart = 0.;
        Direction = Pick(2) ? 1. : -1.;
    }

    const Face& Get(bool bRed) const { return Faces[bRed ? 0 : 1]; }
    double EffectiveBuffRemaining(bool bRed) const
    {
        const Face& Side = Get(bRed);
        return Side.State == Phase::Activated && Now >= Side.BuffSuppressedUntil ? std::max(0., Side.BuffUntil - Now) : 0.;
    }
    void SuppressActiveBuff(bool bRed, double Seconds)
    {
        Face& Side = Faces[bRed ? 0 : 1];
        if (Seconds > 0. && Side.State == Phase::Activated && Now < Side.BuffUntil)
            Side.BuffSuppressedUntil = std::max(Now, Side.BuffSuppressedUntil) + Seconds;
    }
    void ClearActiveBuff(bool bRed)
    {
        Face& Side = Faces[bRed ? 0 : 1];
        if (Side.State != Phase::Activated) return;
        Side.BuffUntil = Now;
        ExpireBuff(Side);
    }
    int AwardExperienceBonus(bool bRed, int BaseExperienceTenths)
    {
        Face& Side = Faces[bRed ? 0 : 1];
        if (Side.bBig || EffectiveBuffRemaining(bRed) <= 0. || BaseExperienceTenths <= 0) return 0;
        const int Bonus = std::min(BaseExperienceTenths, 12000 - Side.ExtraExperienceTenths);
        Side.ExtraExperienceTenths += Bonus;
        return Bonus;
    }
    double AngleRad() const { return Angle; }

    int Available(bool bRed) const
    {
        const Face& Side = Get(bRed);
        if (Now < 180.) return 1 + int(Now >= 90.) - Side.SmallUsed;
        return 1 + int(Now >= 255.) + int(Now >= 330.) - Side.BigUsed;
    }

    bool Request(bool bRed)
    {
        Face& Side = Faces[bRed ? 0 : 1];
        if (Side.State != Phase::Idle || Available(bRed) <= 0) return false;
        Side.State = Phase::Activating;
        Side.bBig = Now >= 180.;
        if (Side.bBig)
        {
            ++Side.BigUsed;
            if (!Faces[bRed ? 1 : 0].bBig || !IsAttempt(Faces[bRed ? 1 : 0]))
            {
                BigStart = Now;
                A = std::uniform_real_distribution<double>(0.780, 1.045)(Random);
                Omega = std::uniform_real_distribution<double>(1.884, 2.000)(Random);
            }
        }
        else ++Side.SmallUsed;
        Side.AttemptUntil = Now + 20.;
        Restart(Side);
        ChooseGroup(Side);
        return true;
    }

    void AdvanceTo(double Seconds)
    {
        if (!std::isfinite(Seconds) || Seconds <= Now) return;
        constexpr double Pi = 3.14159265358979323846;
        double VariableUntil = Now;
        for (const Face& Side : Faces)
            if (Side.bBig && IsAttempt(Side))
                VariableUntil = std::max(VariableUntil, std::min(Seconds, Side.AttemptUntil));
        const double VariableDuration = VariableUntil - Now;
        const double VariableAngle = (2.090 - A) * VariableDuration + A / Omega *
            (std::cos(Omega * (Now - BigStart)) - std::cos(Omega * (VariableUntil - BigStart)));
        Angle = std::remainder(Angle + Direction * (VariableAngle + Pi / 3. * (Seconds - VariableUntil)), 2. * Pi);
        Now = Seconds;
        for (Face& Side : Faces)
        {
            if (Side.State == Phase::Activated && Now >= Side.BuffUntil) ExpireBuff(Side);
            if (!IsAttempt(Side)) continue;
            if (Now >= Side.AttemptUntil)
            {
                Side.State = Phase::Idle;
                Side.Targets = Side.Lit = Side.Confirmed = Side.Groups = 0;
            }
            else if (Side.Targets == 0 && Now >= Side.NextGroupAt) ChooseGroup(Side);
            else if (Side.Targets != 0 && Now >= Side.HitUntil)
            {
                if (Side.bOptionalSecond) FinishGroup(Side);
                else FailAttempt(Side);
            }
        }
    }

    bool Hit(bool bRed, int Arm, int Ring)
    {
        Face& Side = Faces[bRed ? 0 : 1];
        if (Side.State != Phase::Activating || Arm < 0 || Arm >= 5 || Side.Targets == 0) return false;
        const int Bit = 1 << Arm;
        if (Side.bBig && (Ring < std::min(10, 1 + 3 * Side.BigWins) || Ring > 10)) return false;
        if ((Side.Targets & Bit) == 0)
        {
            FailAttempt(Side);
            return false;
        }
        Side.RingTotal += Ring;
        ++Side.Hits;
        if (!Side.bBig)
        {
            Side.Lit |= Bit;
            FinishGroup(Side);
        }
        else if (Side.bOptionalSecond) FinishGroup(Side);
        else
        {
            Side.Targets &= ~Bit;
            Side.Confirmed = Bit;
            Side.bOptionalSecond = true;
            Side.HitUntil = Now + 1.;
        }
        return true;
    }

private:
    static void ExpireBuff(Face& Side)
    {
        Side.State = Phase::Idle;
        Side.Groups = Side.Lit = Side.AttackPercent = Side.DefencePercent = 0;
        Side.CoolingMultiplier = 1;
        Side.BuffSuppressedUntil = 0.;
    }

    int Pick(int Count) { return std::uniform_int_distribution<int>(0, Count - 1)(Random); }
    int PickArm(int Candidates)
    {
        std::array<int, 5> Arms{};
        int Count = 0;
        for (int Arm = 0; Arm < 5; ++Arm)
            if (Candidates & (1 << Arm)) Arms[Count++] = 1 << Arm;
        return Arms[Pick(Count)];
    }

    static bool IsAttempt(const Face& Side)
    {
        return Side.State == Phase::Activating || Side.State == Phase::Failed;
    }

    void FailAttempt(Face& Side)
    {
        Restart(Side);
        Side.State = Phase::Failed;
    }

    void Restart(Face& Side)
    {
        Side.Targets = Side.Lit = Side.Confirmed = Side.Groups = Side.Hits = Side.RingTotal = 0;
        Side.bOptionalSecond = false;
        Side.NextGroupAt = Now + .1;
    }

    void ChooseGroup(Face& Side)
    {
        Side.State = Phase::Activating;
        const int Candidates = Side.bBig ? 31 : 31 & ~Side.Lit;
        Side.Targets = PickArm(Candidates);
        if (Side.bBig) Side.Targets |= PickArm(Candidates & ~Side.Targets);
        Side.HitUntil = Now + 2.5;
        Side.bOptionalSecond = false;
    }

    void FinishGroup(Face& Side)
    {
        Side.Targets = Side.Confirmed = 0;
        if (++Side.Groups < 5)
        {
            if (Side.bBig) Side.Lit = (1 << Side.Groups) - 1;
            Side.NextGroupAt = Now + .1;
            return;
        }
        Side.State = Phase::Activated;
        Side.Lit = 31;
        Side.DefencePercent = 25;
        Side.BuffUntil = Now + 45.;
        Side.BuffSuppressedUntil = 0.;
        Side.ExtraExperienceTenths = 0;
        if (Side.bBig)
        {
            ++Side.BigWins;
            const double Mean = double(Side.RingTotal) / Side.Hits;
            const std::array<int, 6> Duration{30, 35, 40, 45, 50, 60};
            Side.BuffUntil = Now + Duration[Side.Hits - 5];
            Side.AttackPercent = Mean > 9. ? 300 : Mean > 7. ? 200 : 150;
            Side.DefencePercent = Mean > 9. ? 50 : 25;
            Side.CoolingMultiplier = Mean > 9. ? 5 : Mean > 8. ? 3 : Mean > 3. ? 2 : 1;
        }
    }

    std::array<Face, 2> Faces{};
    std::mt19937 Random{};
    double Now = 0.;
    double Angle = 0.;
    double Direction = 1.;
    double BigStart = 0.;
    double A = 0.9;
    double Omega = 1.95;
};
