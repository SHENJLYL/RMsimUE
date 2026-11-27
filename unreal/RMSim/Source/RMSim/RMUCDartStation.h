#pragma once

#include <algorithm>
#include <array>
#include <cmath>

enum class ERMUCDartPhase { Inactive, SelfCheck, Running, Finished };
enum class ERMUCDartState { Closed, SelfCheck, Opening, Open, Closing, Cooldown, Offline };
enum class ERMUCDartTarget { Outpost, FixedBase, RandomFixedBase, RandomMovingBase, LateMovingBase };

struct FRMUCDartHitResult
{
    bool Accepted = false;
    bool OpensBaseArmor = false;
    bool SuppressesGainPoint = false;
    int Damage = 0;
    double SuppressionSeconds = 0.0;
    int GroundRobotHpPercent = 0;
    int InfantryExperience = 0;
    double BlackoutSeconds = 0.0;
    double ExistingBuffSuppressionSeconds = 0.0;
    bool ClearsExistingBuffs = false;
};

// RMUC 2027 V1.0.0 sections 4.2.3, 5.1.5.1 and 5.7.3.
// One instance per team. Hit inputs are confirmed virtual referee events.
class FRMUCDartStation
{
public:
    static constexpr double OpeningSeconds = 7.0;
    static constexpr double OpenSeconds = 30.0;
    static constexpr double WindowSeconds = 40.0;
    static constexpr double CooldownSeconds = 15.0;
    static constexpr double SelfCheckSeconds = 5.0;
    static constexpr int MatchDartBudget = 4;
    // ponytail: bounded linear visual closing until an official closing duration and profile are available.
    static constexpr double AssumedClosingSeconds = 1.0;

    void Reset() { *this = {}; }

    // Each phase has its own clock. During Running, pass match elapsed time.
    void Update(double SimSeconds, ERMUCDartPhase Phase)
    {
        if (!std::isfinite(SimSeconds) || SimSeconds < 0.0)
        {
            return;
        }
        if (Phase != CurrentPhase)
        {
            if (Phase == ERMUCDartPhase::Running)
            {
                const bool WasOnline = bOnline;
                Reset();
                bOnline = WasOnline;
            }
            CurrentPhase = Phase;
            Now = 0.0;
            Gate = bOnline ? ERMUCDartState::Closed : ERMUCDartState::Offline;
            if (bOnline && Phase == ERMUCDartPhase::SelfCheck)
            {
                Gate = ERMUCDartState::SelfCheck;
                TransitionAt = SelfCheckSeconds;
            }
        }
        Now = std::max(Now, SimSeconds);
        Advance();
    }

    bool SelectTarget(ERMUCDartTarget NewTarget)
    {
        if (!IsValidTarget(NewTarget) || CurrentPhase != ERMUCDartPhase::Running || !bOnline)
        {
            return false;
        }
        if (Gate == ERMUCDartState::Open && DetectorTarget == ERMUCDartTarget::Outpost
            && NewTarget != ERMUCDartTarget::Outpost && bCanSwitchFromOutpost)
        {
            SelectedTarget = NewTarget;
            DetectorTarget = NewTarget;
            bCanSwitchFromOutpost = false;
            return true;
        }
        if (Gate != ERMUCDartState::Closed && Gate != ERMUCDartState::Cooldown)
        {
            return false;
        }
        SelectedTarget = NewTarget;
        return true;
    }

    bool RequestOpen()
    {
        if (Gate != ERMUCDartState::Closed || AvailableChances() == 0)
        {
            return false;
        }
        ++UsedChances;
        DetectorTarget = SelectedTarget;
        ClearVirtualFlights();
        bCanSwitchFromOutpost = false;
        Gate = ERMUCDartState::Opening;
        TransitionAt = Now + OpeningSeconds;
        return true;
    }

    void SetOnline(bool Online)
    {
        if (bOnline == Online)
        {
            return;
        }
        bOnline = Online;
        if (Online)
        {
            Gate = RestingState();
            return;
        }
        if (CurrentPhase == ERMUCDartPhase::Running && UsedChances == 1
            && (Gate == ERMUCDartState::Opening || Gate == ERMUCDartState::Open
                || Gate == ERMUCDartState::Closing))
        {
            CooldownUntil = Now + CooldownSeconds;
        }
        WindowUntil = 0.0;
        bCanSwitchFromOutpost = false;
        ClearVirtualFlights();
        Gate = ERMUCDartState::Offline;
    }

    static constexpr double VirtualDetectorSettleSeconds(ERMUCDartTarget Target)
    {
        // Use the outer bound of the documented motion interval for virtual arrivals.
        return Target == ERMUCDartTarget::RandomMovingBase ? 0.6
            : Target == ERMUCDartTarget::LateMovingBase ? 1.8 : 0.0;
    }

    bool TryLaunch(ERMUCDartTarget& Target, int* FlightId = nullptr)
    {
        if (CurrentPhase != ERMUCDartPhase::Running || !bOnline || Gate != ERMUCDartState::Open || DartsRemaining() == 0)
        {
            return false;
        }
        Target = DetectorTarget;
        if (FlightId) *FlightId = UsedDarts;
        VirtualFlights[UsedDarts++] = {DetectorTarget, true};
        VirtualDetectorReadyAt = Now + VirtualDetectorSettleSeconds(Target);
        return true;
    }

    FRMUCDartHitResult ResolveVirtualFlight(int FlightId, bool bDetectorTriggered)
    {
        if (FlightId < 0 || FlightId >= MatchDartBudget || !VirtualFlights[FlightId].Pending)
        {
            return {};
        }
        FVirtualFlight& Flight = VirtualFlights[FlightId];
        Flight.Pending = false;
        if (!bDetectorTriggered || Now + 1.e-9 < VirtualDetectorReadyAt || !CanConfirmHit(Flight.Target))
        {
            return {};
        }
        return ApplyConfirmedHit(Flight.Target);
    }

    FRMUCDartHitResult ResolveVirtualFlight(ERMUCDartTarget Target, bool bDetectorTriggered)
    {
        for (int Index = 0; Index < MatchDartBudget; ++Index)
        {
            if (VirtualFlights[Index].Pending && VirtualFlights[Index].Target == Target)
                return ResolveVirtualFlight(Index, bDetectorTriggered);
        }
        return {};
    }

    FRMUCDartHitResult SubmitConfirmedHit(ERMUCDartTarget HitTarget)
    {
        for (int Index = 0; Index < MatchDartBudget; ++Index)
        {
            if (VirtualFlights[Index].Pending && VirtualFlights[Index].Target == HitTarget)
                return ResolveVirtualFlight(Index, true);
        }
        // Also accept an already confirmed external detector event without a local launch record.
        if (!CanConfirmHit(HitTarget) || DartsRemaining() == 0) return {};
        ++UsedDarts;
        return ApplyConfirmedHit(HitTarget);
    }

    ERMUCDartState State() const { return Gate; }
    ERMUCDartTarget Target() const { return SelectedTarget; }
    ERMUCDartTarget CurrentDetectorTarget() const { return DetectorTarget; }
    int DartsRemaining() const { return MatchDartBudget - UsedDarts; }

    int AvailableChances() const
    {
        if (CurrentPhase != ERMUCDartPhase::Running)
        {
            return 0;
        }
        const int Granted = (Now >= 30.0) + (Now >= 240.0);
        return Granted - UsedChances;
    }

    double GateOpenFraction() const
    {
        switch (Gate)
        {
            case ERMUCDartState::SelfCheck:
            case ERMUCDartState::Open:
                return 1.0;
            case ERMUCDartState::Opening:
                return std::clamp(1.0 - (TransitionAt - Now) / OpeningSeconds, 0.0, 1.0);
            case ERMUCDartState::Closing:
                return std::clamp((TransitionAt - Now) / AssumedClosingSeconds, 0.0, 1.0);
            default:
                return 0.0;
        }
    }

    bool DetectorWindowOpen() const
    {
        const int Detector = DetectorTarget == ERMUCDartTarget::Outpost ? 0 : 1;
        return DetectorWindowRemaining() > 0.0 && Now >= DetectorBlockedUntil[Detector];
    }

    double DetectorWindowRemaining() const
    {
        return CurrentPhase == ERMUCDartPhase::Running && bOnline ? std::max(0.0, WindowUntil - Now) : 0.0;
    }

    double OpponentBlackoutRemaining() const
    {
        return CurrentPhase == ERMUCDartPhase::Running ? std::max(0.0, OpponentBlackoutUntil - Now) : 0.0;
    }

    double GainPointSuppressionRemaining(ERMUCDartTarget Site) const
    {
        if (CurrentPhase != ERMUCDartPhase::Running || !IsValidTarget(Site))
        {
            return 0.0;
        }
        const double Until = Site == ERMUCDartTarget::Outpost ? OutpostSuppressedUntil : BaseSuppressedUntil;
        return std::max(0.0, Until - Now);
    }

private:
    struct FVirtualFlight
    {
        ERMUCDartTarget Target = ERMUCDartTarget::Outpost;
        bool Pending = false;
    };

    void ClearVirtualFlights()
    {
        VirtualFlights = {};
        VirtualDetectorReadyAt = 0.0;
    }

    bool CanConfirmHit(ERMUCDartTarget HitTarget) const
    {
        return HitTarget == DetectorTarget && DetectorWindowOpen();
    }

    FRMUCDartHitResult ApplyConfirmedHit(ERMUCDartTarget Target)
    {
        static constexpr std::array<int, 5> DamageByTarget{1000, 200, 300, 800, 1250};
        static constexpr std::array<int, 5> ExperienceByTarget{0, 200, 600, 2500, 2500};
        static constexpr std::array<double, 4> StaticBlackouts{10.0, 5.0, 3.0, 2.0};
        constexpr double SuppressSeconds = 30.0;
        const bool IsOutpost = Target == ERMUCDartTarget::Outpost;
        const bool MovingBase = Target == ERMUCDartTarget::RandomMovingBase || Target == ERMUCDartTarget::LateMovingBase;
        DetectorBlockedUntil[IsOutpost ? 0 : 1] = Now + 2.0;
        (IsOutpost ? OutpostSuppressedUntil : BaseSuppressedUntil) = Now + SuppressSeconds;

        if (IsOutpost) bCanSwitchFromOutpost = true;
        if (!IsOutpost && !MovingBase) ++FixedBaseHits;
        const bool FirstArmorOpening = !bArmorOpeningReported && (MovingBase || FixedBaseHits == MatchDartBudget);
        bArmorOpeningReported = bArmorOpeningReported || FirstArmorOpening;

        FRMUCDartHitResult Result;
        Result.Accepted = true;
        Result.OpensBaseArmor = FirstArmorOpening;
        Result.SuppressesGainPoint = true;
        Result.Damage = DamageByTarget[static_cast<int>(Target)];
        Result.SuppressionSeconds = SuppressSeconds;
        Result.GroundRobotHpPercent = MovingBase ? (Target == ERMUCDartTarget::LateMovingBase ? 50 : 20) : 0;
        Result.InfantryExperience = ExperienceByTarget[static_cast<int>(Target)];
        Result.BlackoutSeconds = MovingBase ? 10.0 : StaticBlackouts[StaticDetectorHits++];
        OpponentBlackoutUntil = std::max(Now, OpponentBlackoutUntil) + Result.BlackoutSeconds;
        Result.ExistingBuffSuppressionSeconds = Target == ERMUCDartTarget::RandomFixedBase ? Result.BlackoutSeconds : 0.0;
        Result.ClearsExistingBuffs = MovingBase;
        return Result;
    }

    static bool IsValidTarget(ERMUCDartTarget Target)
    {
        return Target >= ERMUCDartTarget::Outpost && Target <= ERMUCDartTarget::LateMovingBase;
    }

    ERMUCDartState RestingState() const
    {
        return CurrentPhase == ERMUCDartPhase::Running && Now < CooldownUntil
            ? ERMUCDartState::Cooldown : ERMUCDartState::Closed;
    }

    void Advance()
    {
        for (;;)
        {
            switch (Gate)
            {
                case ERMUCDartState::SelfCheck:
                case ERMUCDartState::Open:
                    if (Now < TransitionAt)
                    {
                        return;
                    }
                    Gate = ERMUCDartState::Closing;
                    TransitionAt += AssumedClosingSeconds;
                    if (CurrentPhase == ERMUCDartPhase::Running && UsedChances == 1)
                    {
                        CooldownUntil = TransitionAt + CooldownSeconds;
                    }
                    break;
                case ERMUCDartState::Opening:
                    if (Now < TransitionAt)
                    {
                        return;
                    }
                    Gate = ERMUCDartState::Open;
                    WindowUntil = TransitionAt + WindowSeconds;
                    TransitionAt += OpenSeconds;
                    break;
                case ERMUCDartState::Closing:
                    if (Now < TransitionAt)
                    {
                        return;
                    }
                    Gate = RestingState();
                    return;
                case ERMUCDartState::Cooldown:
                    Gate = RestingState();
                    return;
                default:
                    return;
            }
        }
    }

    ERMUCDartPhase CurrentPhase = ERMUCDartPhase::Inactive;
    ERMUCDartState Gate = ERMUCDartState::Closed;
    ERMUCDartTarget SelectedTarget = ERMUCDartTarget::Outpost;
    ERMUCDartTarget DetectorTarget = ERMUCDartTarget::Outpost;
    bool bOnline = true;
    bool bCanSwitchFromOutpost = false;
    bool bArmorOpeningReported = false;
    int UsedChances = 0;
    int UsedDarts = 0;
    int FixedBaseHits = 0;
    int StaticDetectorHits = 0;
    std::array<FVirtualFlight, MatchDartBudget> VirtualFlights{};
    double VirtualDetectorReadyAt = 0.0;
    double Now = 0.0;
    double OpponentBlackoutUntil = 0.0;
    double TransitionAt = 0.0;
    double CooldownUntil = 0.0;
    double WindowUntil = 0.0;
    std::array<double, 2> DetectorBlockedUntil{};
    double OutpostSuppressedUntil = 0.0;
    double BaseSuppressedUntil = 0.0;
};
