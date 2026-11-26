#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <optional>

// RoboMaster 2027 University Championship Rule Manual V1.0.0 (20261015),
// sections 5.6.1, 5.6.2 and 5.6.4.9. Inputs are already-qualified field events.
class FRMUCFieldRules
{
public:
    enum class Team : unsigned { Red, Blue };
    static constexpr int BaseMaxHp = 10000;
    static constexpr int OutpostMaxHp = 1000;
    static constexpr int RebuiltOutpostHp = 500;
    static constexpr double ControlTransitionSeconds = 5.0;
    static constexpr double EnemyFortressUnlockSeconds = 180.0;
    static constexpr double EnemyFortressActivationSeconds = 10.0;
    static constexpr double EnemyFortressRetentionSeconds = 3.0;

    struct FControl
    {
        Team Controller = Team::Red;
        bool bFull = true;
        double TransitionSeconds = 0.0;
    };

    struct FOutpostRotor
    {
        double AngleRadians = 0.0;
        double AngularRateRadS = 0.0;
        bool bStopRequested = false;
        bool bParked = false;
    };

    struct FSide
    {
        FControl OutpostControl;
        FControl BaseControl;
        FOutpostRotor OutpostRotor;
        Team OutpostOwner = Team::Red;
        int OutpostHp = OutpostMaxHp;
        int BaseHp = BaseMaxHp;
        bool bBaseArmorOpen = false;
        double ArmorActivationSeconds = 0.0;
        double ArmorInactiveSeconds = 0.0;
    };

    struct FActivation
    {
        bool bRed = false;
        bool bBlue = false;
        bool By(Team Side) const { return Side == Team::Red ? bRed : bBlue; }
    };

    struct FSiteActivation
    {
        std::optional<FActivation> OutpostPosition;
        std::optional<FActivation> BasePosition;
        std::optional<bool> OpponentFortress;
    };

    struct FOutpostHit
    {
        int HpLost = 0;
        bool bDestroyed = false;
        bool bRebuilt = false;
    };

    FRMUCFieldRules() { Reset(); }

    void Reset(bool bRotateCounterclockwise = true)
    {
        RotateSign = bRotateCounterclockwise ? 1.0 : -1.0;
        for (Team Site : Teams)
        {
            FSide& State = Sites[Index(Site)];
            State = {};
            State.OutpostControl.Controller = Site;
            State.BaseControl.Controller = Site;
            State.OutpostOwner = Site;
        }
    }

    const FSide& Get(Team Site) const { return Sites[Index(Site)]; }

    bool BaseProtected(Team Site) const
    {
        const FControl& Control = Get(Site).OutpostControl;
        return Control.bFull && Control.Controller == Site;
    }

    bool CanActivateEnemyFortress(Team Site, double MatchElapsedSeconds) const
    {
        const FSide& State = Get(Site);
        return !State.bBaseArmorOpen && CanActivateFortress(Site, Other(Site), MatchElapsedSeconds);
    }

    bool CanActivateFortress(Team Site, Team Activator, double MatchElapsedSeconds) const
    {
        const FSide& State = Get(Site);
        return State.BaseHp > 0 && State.OutpostControl.Controller == Other(Site)
            && (Activator == Site || Reached(MatchElapsedSeconds, EnemyFortressUnlockSeconds));
    }

    bool OpenBaseArmor(Team Site)
    {
        FSide& State = Sites[Index(Site)];
        if (State.bBaseArmorOpen || State.BaseHp == 0)
        {
            return false;
        }
        State.bBaseArmorOpen = true;
        RequestRotorStop(Sites[Index(Other(Site))].OutpostRotor);
        return true;
    }

    void Step(double Seconds, double MatchElapsedSeconds, const std::array<FSiteActivation, 2>& Activation)
    {
        if (Seconds <= 0.0)
        {
            return;
        }

        for (FSide& State : Sites)
        {
            StepRotor(State.OutpostRotor, Seconds, MatchElapsedSeconds);
        }
        for (Team Site : Teams)
        {
            FSide& State = Sites[Index(Site)];
            const FSiteActivation& Active = Activation[Index(Site)];
            if (Active.OutpostPosition)
            {
                StepControl(State.OutpostControl, Site, true, Seconds, *Active.OutpostPosition);
                RebuildOutpostForNewController(State);
            }
            if (Active.BasePosition)
            {
                StepControl(State.BaseControl, Site, false, Seconds, *Active.BasePosition);
            }

            if (State.bBaseArmorOpen || !Active.OpponentFortress)
            {
                continue;
            }

            if (*Active.OpponentFortress && CanActivateEnemyFortress(Site, MatchElapsedSeconds))
            {
                State.ArmorInactiveSeconds = 0.0;
                State.ArmorActivationSeconds += std::clamp(MatchElapsedSeconds - EnemyFortressUnlockSeconds, 0.0, Seconds);
                if (Reached(State.ArmorActivationSeconds, EnemyFortressActivationSeconds))
                {
                    State.ArmorActivationSeconds = EnemyFortressActivationSeconds;
                    OpenBaseArmor(Site);
                }
            }
            else if (State.ArmorActivationSeconds > 0.0)
            {
                State.ArmorInactiveSeconds += Seconds;
                if (Reached(State.ArmorInactiveSeconds, EnemyFortressRetentionSeconds))
                {
                    State.ArmorActivationSeconds = 0.0;
                    State.ArmorInactiveSeconds = 0.0;
                }
            }
        }
    }

    int HitBase(Team Site, Team Attacker, int Damage)
    {
        if (Site == Attacker || BaseProtected(Site) || Damage <= 0)
        {
            return 0;
        }

        FSide& State = Sites[Index(Site)];
        const int HpLost = std::min(State.BaseHp, Damage);
        State.BaseHp -= HpLost;
        return HpLost;
    }

    FOutpostHit HitOutpost(Team Site, Team Attacker, int Damage)
    {
        FSide& State = Sites[Index(Site)];
        if (State.OutpostOwner == Attacker || State.OutpostHp == 0 || Damage <= 0)
        {
            return {};
        }

        const int HpLost = std::min(State.OutpostHp, Damage);
        State.OutpostHp -= HpLost;
        if (State.OutpostHp > 0)
        {
            return {HpLost, false, false};
        }

        RequestRotorStop(State.OutpostRotor);
        FControl& Control = State.OutpostControl;
        if (!Control.bFull && Control.Controller == Attacker)
        {
            Control = {Attacker, true, 0.0};
            return {HpLost, true, RebuildOutpostForNewController(State)};
        }

        if (Control.bFull && Control.Controller == State.OutpostOwner)
        {
            Control = {State.OutpostOwner, false, 0.0};
        }
        return {HpLost, true, false};
    }

    static constexpr unsigned Index(Team Side) { return static_cast<unsigned>(Side); }
    static constexpr Team Other(Team Side) { return Side == Team::Red ? Team::Blue : Team::Red; }
    static constexpr std::array Teams{Team::Red, Team::Blue};

private:
    std::array<FSide, 2> Sites;
    double RotateSign = 1.0;
    static constexpr double Pi = 3.14159265358979323846;
    static constexpr double Turn = 2.0 * Pi;
    static constexpr double RotorMaxRateRadS = 0.8 * Pi;
    static constexpr double RotorRampSeconds = 5.0;
    static constexpr double RotorStopSeconds = 180.0;

    // Small tolerance for accumulated world steps such as 200 x 0.005 s.
    static bool Reached(double Actual, double Threshold) { return Actual + 1e-9 >= Threshold; }

    static bool RebuildOutpostForNewController(FSide& State)
    {
        if (State.OutpostHp == 0 && State.OutpostControl.bFull &&
            State.OutpostControl.Controller != State.OutpostOwner)
        {
            State.OutpostOwner = State.OutpostControl.Controller;
            State.OutpostHp = RebuiltOutpostHp;
            return true;
        }
        return false;
    }

    static void ParkRotor(FOutpostRotor& Rotor)
    {
        Rotor.AngleRadians = 0.0;
        Rotor.AngularRateRadS = 0.0;
        Rotor.bParked = true;
    }

    static void RequestRotorStop(FOutpostRotor& Rotor)
    {
        Rotor.bStopRequested = true;
        const double Angle = std::abs(Rotor.AngleRadians);
        if (std::min(Angle, Turn - Angle) <= 1e-8 || std::abs(Rotor.AngularRateRadS) <= 1e-8)
        {
            ParkRotor(Rotor);
        }
    }

    void StepRotor(FOutpostRotor& Rotor, double Seconds, double MatchElapsedSeconds) const
    {
        if (!Rotor.bStopRequested)
        {
            const bool bTimeLimit = Reached(MatchElapsedSeconds, RotorStopSeconds);
            const double Elapsed = bTimeLimit ? RotorStopSeconds : std::max(0.0, MatchElapsedSeconds);
            const double Ramp = std::min(RotorRampSeconds, Elapsed);
            const double Travel = RotorMaxRateRadS * (Elapsed - Ramp + Ramp * Ramp / (2.0 * RotorRampSeconds));
            Rotor.AngleRadians = RotateSign * std::fmod(Travel, Turn);
            Rotor.AngularRateRadS = RotateSign * RotorMaxRateRadS * Ramp / RotorRampSeconds;
            if (!bTimeLimit)
            {
                return;
            }

            RequestRotorStop(Rotor);
            Seconds = std::clamp(MatchElapsedSeconds - RotorStopSeconds, 0.0, Seconds);
        }

        if (Rotor.bParked || Seconds <= 0.0)
        {
            return;
        }

        // RMUC 2027 describes the rest angle but leaves the parking profile unspecified.
        // Use uniform simulated deceleration in the selected direction to the next rest angle.
        const double Remaining = Turn - std::abs(Rotor.AngleRadians);
        const double Speed = std::abs(Rotor.AngularRateRadS);
        const double TimeToPark = 2.0 * Remaining / Speed;
        if (Reached(Seconds, TimeToPark))
        {
            ParkRotor(Rotor);
            return;
        }
        const double NextSpeed = Speed * (1.0 - Seconds / TimeToPark);
        Rotor.AngleRadians += RotateSign * 0.5 * (Speed + NextSpeed) * Seconds;
        Rotor.AngularRateRadS = RotateSign * NextSpeed;
    }

    static void StepControl(FControl& Control, Team Original, bool bOutpost,
        double Seconds, const FActivation& Active)
    {
        if (Active.bRed && Active.bBlue)
        {
            return;
        }

        const Team Challenger = Other(Control.Controller);
        if (Control.bFull)
        {
            if (!Active.By(Challenger))
            {
                Control.TransitionSeconds = std::max(0.0, Control.TransitionSeconds - Seconds);
                return;
            }
            Control.TransitionSeconds += Seconds;
            if (Reached(Control.TransitionSeconds, ControlTransitionSeconds))
            {
                Control = {Challenger, false, 0.0};
            }
        }
        else if (bOutpost || Challenger == Original)
        {
            if (Active.By(Control.Controller))
            {
                Control.TransitionSeconds = 0.0;
                return;
            }
            Control.TransitionSeconds += Seconds;
            if (Reached(Control.TransitionSeconds, ControlTransitionSeconds))
            {
                Control = {Challenger, true, 0.0};
            }
        }
    }
};
