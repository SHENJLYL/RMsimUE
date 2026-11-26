#include "RMUCDartStation.h"

#include <cassert>
#include <iostream>
#include <limits>

using Phase = ERMUCDartPhase;
using State = ERMUCDartState;
using Target = ERMUCDartTarget;

int main()
{
    FRMUCDartStation Red;
    FRMUCDartStation Blue;
    Red.Update(0.0, Phase::SelfCheck);
    assert(Red.State() == State::SelfCheck && Red.GateOpenFraction() == 1.0);
    assert(!Red.RequestOpen() && !Red.DetectorWindowOpen());
    Red.Update(5.5, Phase::SelfCheck);
    assert(Red.State() == State::Closing && Red.GateOpenFraction() == 0.5);
    Red.Update(15.0, Phase::SelfCheck);
    assert(Red.State() == State::Closed);

    Red.Update(29.999, Phase::Running);
    Target LaunchedTarget = Target::FixedBase;
    int FlightId = -1;
    assert(Red.AvailableChances() == 0 && Red.DartsRemaining() == 4 && !Red.RequestOpen());
    assert(!Red.TryLaunch(LaunchedTarget, &FlightId));
    Red.Update(30.0, Phase::Running);
    Blue.Update(30.0, Phase::Running);
    assert(Red.AvailableChances() == 1 && Red.RequestOpen());
    assert(Blue.AvailableChances() == 1 && Blue.State() == State::Closed);
    Red.Update(33.5, Phase::Running);
    assert(Red.State() == State::Opening && Red.GateOpenFraction() == 0.5);
    assert(!Red.SelectTarget(Target::FixedBase) && !Red.SubmitConfirmedHit(Target::Outpost).Accepted);
    Red.Update(37.0, Phase::Running);
    assert(Red.State() == State::Open && Red.DetectorWindowRemaining() == 40.0);
    assert(!Red.SubmitConfirmedHit(Target::FixedBase).Accepted);
    assert(!Red.SelectTarget(Target::FixedBase));
    const auto OutpostHit = Red.SubmitConfirmedHit(Target::Outpost);
    assert(OutpostHit.Accepted && OutpostHit.SuppressesGainPoint && !OutpostHit.OpensBaseArmor);
    assert(OutpostHit.Damage == 1000 && OutpostHit.SuppressionSeconds == 30.0 && OutpostHit.BlackoutSeconds == 10.0);
    assert(Red.DartsRemaining() == 3 && !Red.DetectorWindowOpen());
    assert(Red.GainPointSuppressionRemaining(Target::Outpost) == 30.0);
    assert(Red.SelectTarget(Target::RandomMovingBase));
    assert(!Red.SelectTarget(Target::Outpost));
    assert(Red.DetectorWindowOpen() && !Red.SubmitConfirmedHit(Target::Outpost).Accepted);
    Red.Update(39.0, Phase::Running);
    const auto MovingHit = Red.SubmitConfirmedHit(Target::RandomMovingBase);
    assert(MovingHit.Accepted && MovingHit.OpensBaseArmor && MovingHit.Damage == 800);
    assert(MovingHit.GroundRobotHpPercent == 20 && MovingHit.ClearsExistingBuffs && MovingHit.BlackoutSeconds == 10.0);
    assert(Red.GainPointSuppressionRemaining(Target::FixedBase) == 30.0);
    assert(!Red.DetectorWindowOpen() && !Red.SubmitConfirmedHit(Target::RandomMovingBase).Accepted);
    Red.Update(67.5, Phase::Running);
    assert(Red.State() == State::Closing && Red.GateOpenFraction() == 0.5);
    assert(Red.DetectorWindowOpen());
    Red.Update(68.0, Phase::Running);
    assert(Red.State() == State::Cooldown && Red.GateOpenFraction() == 0.0);
    assert(Red.SelectTarget(Target::FixedBase));
    assert(Red.CurrentDetectorTarget() == Target::RandomMovingBase);
    assert(!Red.SubmitConfirmedHit(Target::FixedBase).Accepted);
    Red.Update(77.0, Phase::Running);
    assert(!Red.DetectorWindowOpen());
    Red.Update(83.0, Phase::Running);
    assert(Red.State() == State::Closed && !Red.RequestOpen());
    Red.Update(240.0, Phase::Running);
    assert(Red.RequestOpen() && !Red.RequestOpen());
    Red.SetOnline(false);
    assert(Red.State() == State::Offline && Red.GateOpenFraction() == 0.0);
    assert(!Red.DetectorWindowOpen() && !Red.SubmitConfirmedHit(Target::FixedBase).Accepted);
    Red.SetOnline(true);
    assert(Red.State() == State::Closed && Red.AvailableChances() == 0);

    Blue.Update(240.0, Phase::Running);
    assert(Blue.AvailableChances() == 2);
    assert(Blue.SelectTarget(Target::RandomFixedBase) && Blue.RequestOpen());
    Blue.Update(247.0, Phase::Running);
    for (int Index = 0; Index < 4; ++Index)
    {
        Blue.Update(247.0 + 2.0 * Index, Phase::Running);
        const auto Hit = Blue.SubmitConfirmedHit(Target::RandomFixedBase);
        assert(Hit.Accepted && Hit.OpensBaseArmor == (Index == 3));
        constexpr double ExpectedBlackouts[] = {10.0, 5.0, 3.0, 2.0};
        assert(Hit.Damage == 300 && Hit.SuppressionSeconds == 30.0 && Hit.BlackoutSeconds == ExpectedBlackouts[Index]);
        assert(Hit.ExistingBuffSuppressionSeconds == Hit.BlackoutSeconds && !Hit.ClearsExistingBuffs);
        assert(Blue.DartsRemaining() == 3 - Index);
    }
    Blue.Update(255.0, Phase::Running);
    assert(!Blue.SubmitConfirmedHit(Target::RandomFixedBase).Accepted);
    Blue.Update(270.0, Phase::Running);
    Blue.SetOnline(false);
    assert(Blue.State() == State::Offline && !Blue.DetectorWindowOpen() && !Blue.RequestOpen());
    Blue.SetOnline(true);
    assert(Blue.State() == State::Cooldown && !Blue.RequestOpen());
    Blue.Update(285.0, Phase::Running);
    assert(Blue.State() == State::Closed && Blue.RequestOpen());
    Blue.Update(400.0, Phase::Running);
    assert(Blue.State() == State::Closed && !Blue.DetectorWindowOpen());
    Blue.Update(250.0, Phase::Running);
    Blue.Update(std::numeric_limits<double>::quiet_NaN(), Phase::Running);
    assert(Blue.State() == State::Closed && Blue.AvailableChances() == 0);
    Blue.Update(0.0, Phase::Finished);
    assert(Blue.State() == State::Closed && !Blue.RequestOpen());
    Blue.Update(30.0, Phase::Running);
    assert(Blue.AvailableChances() == 1 && Blue.DartsRemaining() == 4 && Blue.RequestOpen());

    FRMUCDartStation Virtual;
    Virtual.Update(30.0, Phase::Running);
    // Late refers to detector motion after launch rather than the stage of the match.
    assert(Virtual.SelectTarget(Target::LateMovingBase) && Virtual.RequestOpen());
    assert(Virtual.DartsRemaining() == 4 && !Virtual.TryLaunch(LaunchedTarget));
    Virtual.Update(37.0, Phase::Running);
    assert(Virtual.TryLaunch(LaunchedTarget, &FlightId) && LaunchedTarget == Target::LateMovingBase);
    Virtual.Update(38.799, Phase::Running);
    assert(!Virtual.SubmitConfirmedHit(LaunchedTarget).Accepted && Virtual.DartsRemaining() == 3);
    assert(!Virtual.ResolveVirtualFlight(FlightId, true).Accepted);
    assert(Virtual.TryLaunch(LaunchedTarget, &FlightId) && Virtual.DartsRemaining() == 2);
    Virtual.Update(40.599, Phase::Running);
    const auto Late = Virtual.ResolveVirtualFlight(FlightId, true);
    assert(Late.Accepted && Late.Damage == 1250 && Late.OpensBaseArmor && Late.GroundRobotHpPercent == 50);
    assert(!Virtual.SelectTarget(Target::Outpost));
    assert(Virtual.TryLaunch(LaunchedTarget, &FlightId) && Virtual.DartsRemaining() == 1);
    assert(!Virtual.ResolveVirtualFlight(FlightId, false).Accepted);
    Virtual.Update(66.5, Phase::Running);
    assert(Virtual.TryLaunch(LaunchedTarget, &FlightId) && Virtual.DartsRemaining() == 0);
    Virtual.Update(67.5, Phase::Running);
    assert(Virtual.State() == State::Closing && !Virtual.TryLaunch(LaunchedTarget));
    Virtual.Update(68.3, Phase::Running);
    const auto InFlight = Virtual.ResolveVirtualFlight(FlightId, true);
    assert(InFlight.Accepted && InFlight.Damage == 1250 && !InFlight.OpensBaseArmor);
    Virtual.Update(240.0, Phase::Running);
    assert(Virtual.AvailableChances() == 1 && Virtual.RequestOpen());
    Virtual.Update(247.0, Phase::Running);
    assert(Virtual.State() == State::Open && !Virtual.TryLaunch(LaunchedTarget));
    assert(!Virtual.SubmitConfirmedHit(Target::LateMovingBase).Accepted);

    FRMUCDartStation External;
    External.Update(30.0, Phase::Running);
    assert(External.SelectTarget(Target::FixedBase) && External.RequestOpen());
    External.Update(37.0, Phase::Running);
    assert(External.TryLaunch(LaunchedTarget, &FlightId) && !External.SelectTarget(Target::Outpost));
    const auto Fixed = External.SubmitConfirmedHit(Target::FixedBase);
    assert(Fixed.Accepted && Fixed.Damage == 200 && !Fixed.OpensBaseArmor);
    assert(External.DartsRemaining() == 3 && !External.ResolveVirtualFlight(FlightId, true).Accepted);
    std::cout << "RMUC dart station checks passed\n";
}
