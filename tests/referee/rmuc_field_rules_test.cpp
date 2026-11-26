#include "RMUCFieldRules.h"

#include <cassert>
#include <iostream>

using Rules = FRMUCFieldRules;
using Team = Rules::Team;

int main()
{
    Rules Match;
    std::array<Rules::FSiteActivation, 2> Activation;
    auto& Red = Activation[Rules::Index(Team::Red)];
    auto Step = [&](double Seconds, double Elapsed = 200.0) {
        Match.Step(Seconds, Elapsed, Activation);
    };

    assert(Match.Get(Team::Red).BaseHp == 10000);
    assert(Match.Get(Team::Red).OutpostHp == 1000);
    assert(Match.HitBase(Team::Red, Team::Blue, 20) == 0);
    assert(Match.HitOutpost(Team::Red, Team::Red, 20).HpLost == 0);
    const auto UncontestedHit = Match.HitOutpost(Team::Red, Team::Blue, 1000);
    assert(UncontestedHit.bDestroyed && !UncontestedHit.bRebuilt);
    assert(!Match.BaseProtected(Team::Red));
    Step(6.0);
    assert(!Match.Get(Team::Red).OutpostControl.bFull);
    assert(Match.Get(Team::Red).OutpostControl.Controller == Team::Red);
    assert(Match.HitBase(Team::Red, Team::Blue, 20) == 20);
    assert(Match.HitBase(Team::Red, Team::Red, 20) == 0);
    assert(Match.Get(Team::Red).BaseHp == 9980);

    // Kill first, then allow the original team's temporary control to expire.
    Red.OutpostPosition = Rules::FActivation{false, false};
    Step(2.0);
    Red.OutpostPosition = Rules::FActivation{true, true};
    Step(6.0);
    assert(Match.Get(Team::Red).OutpostControl.TransitionSeconds == 2.0);
    Red.OutpostPosition = Rules::FActivation{true, false};
    Step(1.0);
    assert(Match.Get(Team::Red).OutpostControl.TransitionSeconds == 0.0);
    Red.OutpostPosition = Rules::FActivation{false, false};
    Step(4.995);
    assert(Match.Get(Team::Red).OutpostHp == 0);
    Step(.005);
    const auto& DelayedRebuild = Match.Get(Team::Red);
    assert(DelayedRebuild.OutpostControl.bFull && DelayedRebuild.OutpostControl.Controller == Team::Blue);
    assert(DelayedRebuild.OutpostOwner == Team::Blue && DelayedRebuild.OutpostHp == 500);
    assert(!Match.BaseProtected(Team::Red));
    Step(1.0);
    assert(DelayedRebuild.OutpostHp == 500);

    Match.Reset();
    Red.OutpostPosition = Rules::FActivation{false, true};
    Step(2.0);
    Red.OutpostPosition = Rules::FActivation{true, true};
    Step(6.0);
    assert(Match.Get(Team::Red).OutpostControl.TransitionSeconds == 2.0);
    Red.OutpostPosition = Rules::FActivation{true, false};
    Step(1.0);
    assert(Match.Get(Team::Red).OutpostControl.TransitionSeconds == 1.0);
    Red.OutpostPosition = Rules::FActivation{false, true};
    for (int Tick = 0; Tick < 800; ++Tick)
    {
        Step(0.005);
    }
    assert(!Match.Get(Team::Red).OutpostControl.bFull);
    assert(Match.Get(Team::Red).OutpostControl.Controller == Team::Blue);
    assert(!Match.BaseProtected(Team::Red));
    const auto RebuiltHit = Match.HitOutpost(Team::Red, Team::Blue, 1200);
    assert(RebuiltHit.HpLost == 1000 && RebuiltHit.bDestroyed && RebuiltHit.bRebuilt);
    assert(Match.Get(Team::Red).OutpostOwner == Team::Blue);
    assert(Match.Get(Team::Red).OutpostHp == 500);
    assert(Match.Get(Team::Red).OutpostControl.bFull);
    assert(Match.HitOutpost(Team::Red, Team::Blue, 20).HpLost == 0);
    Red.OutpostPosition = Rules::FActivation{true, false};
    Step(5.0);
    assert(Match.Get(Team::Red).OutpostControl.Controller == Team::Red);
    assert(!Match.Get(Team::Red).OutpostControl.bFull);
    assert(Match.HitOutpost(Team::Red, Team::Red, 500).bRebuilt);
    assert(Match.Get(Team::Red).OutpostOwner == Team::Red);
    assert(Match.Get(Team::Red).OutpostHp == 500 && Match.BaseProtected(Team::Red));

    // Lose control again while keeping the red tower alive. Both defending
    // and invading fortress conditions depend on control, not tower health.
    Red.OutpostPosition = Rules::FActivation{false, true};
    Step(5.0);
    assert(Match.CanActivateFortress(Team::Red, Team::Red, 179.0));
    assert(!Match.CanActivateFortress(Team::Red, Team::Blue, 179.0));
    assert(Match.CanActivateFortress(Team::Red, Team::Blue, 180.0));
    assert(!Match.CanActivateFortress(Team::Blue, Team::Blue, 200.0));
    Red.BasePosition = Rules::FActivation{false, true};
    Step(5.0);
    assert(!Match.Get(Team::Red).BaseControl.bFull);
    assert(Match.Get(Team::Red).BaseControl.Controller == Team::Blue);
    Red.BasePosition = Rules::FActivation{};
    Step(5.0);
    assert(Match.Get(Team::Red).BaseControl.bFull);
    assert(Match.Get(Team::Red).BaseControl.Controller == Team::Red);

    Red.OpponentFortress = true;
    Step(10.0, 179.0);
    assert(!Match.Get(Team::Red).bBaseArmorOpen);
    Step(6.0);
    Red.OpponentFortress = false;
    Step(3.0);
    assert(Match.Get(Team::Red).ArmorActivationSeconds == 0.0);
    Red.OpponentFortress = true;
    Step(6.0);
    Red.OpponentFortress = false;
    Step(2.5);
    Red.OpponentFortress = true;
    Step(4.0);
    assert(Match.Get(Team::Red).bBaseArmorOpen);
    assert(!Match.CanActivateEnemyFortress(Team::Red, 200.0));
    assert(!Match.Get(Team::Blue).bBaseArmorOpen);

    Match.Reset();
    Activation = {};
    auto& Blue = Activation[Rules::Index(Team::Blue)];
    Blue.OutpostPosition = Rules::FActivation{true, false};
    Step(5.0);
    assert(Match.HitOutpost(Team::Blue, Team::Red, 1000).bRebuilt);
    Blue.OutpostPosition = Rules::FActivation{false, true};
    Step(5.0);
    assert(Match.HitOutpost(Team::Blue, Team::Blue, 500).bRebuilt);
    assert(Match.Get(Team::Blue).OutpostOwner == Team::Blue);
    assert(Match.Get(Team::Blue).OutpostHp == 500 && Match.BaseProtected(Team::Blue));

    Match.Reset();
    assert(Match.Get(Team::Red).OutpostOwner == Team::Red);
    assert(Match.Get(Team::Blue).OutpostOwner == Team::Blue);
    assert(Match.Get(Team::Red).OutpostHp == 1000);
    assert(Match.BaseProtected(Team::Red) && Match.BaseProtected(Team::Blue));
    assert(!Match.Get(Team::Red).bBaseArmorOpen);
    assert(Match.OpenBaseArmor(Team::Blue));
    assert(!Match.OpenBaseArmor(Team::Blue));
    assert(Match.Get(Team::Blue).bBaseArmorOpen);
    assert(Match.Get(Team::Blue).ArmorActivationSeconds == 0.0);
    assert(Match.BaseProtected(Team::Blue));

    constexpr double FullSpeed = 0.8 * 3.14159265358979323846;
    Activation = {};
    Match.Reset(false);
    Step(2.0, 2.0);
    const auto& RedRotor = Match.Get(Team::Red).OutpostRotor;
    const auto& BlueRotor = Match.Get(Team::Blue).OutpostRotor;
    assert(std::abs(RedRotor.AngularRateRadS + 0.4 * FullSpeed) < 1e-12);
    assert(std::abs(RedRotor.AngleRadians + 0.4 * FullSpeed) < 1e-12);
    assert(RedRotor.AngleRadians == BlueRotor.AngleRadians);
    Step(3.0, 5.0);
    assert(std::abs(RedRotor.AngularRateRadS + FullSpeed) < 1e-12);
    Step(1.0, 6.0);
    assert(std::abs(RedRotor.AngleRadians + FullSpeed) < 1e-12);
    assert(Match.OpenBaseArmor(Team::Blue));
    assert(RedRotor.bStopRequested && !RedRotor.bParked);
    assert(!BlueRotor.bStopRequested);
    Step(1.0, 7.0);
    assert(RedRotor.AngularRateRadS < 0.0 && std::abs(RedRotor.AngularRateRadS) < FullSpeed);
    assert(RedRotor.AngleRadians < -FullSpeed);
    Step(2.1, 9.1);
    assert(RedRotor.bParked && RedRotor.AngularRateRadS == 0.0 && RedRotor.AngleRadians == 0.0);
    assert(!BlueRotor.bStopRequested);
    assert(Match.HitOutpost(Team::Blue, Team::Red, 1000).bDestroyed);
    assert(BlueRotor.bStopRequested);
    Step(10.0, 19.1);
    assert(RedRotor.bParked && BlueRotor.bParked);

    Match.Reset(true);
    assert(!RedRotor.bStopRequested && !BlueRotor.bStopRequested);
    Step(0.01, 179.99);
    assert(RedRotor.AngularRateRadS > 0.0 && BlueRotor.AngularRateRadS > 0.0);
    Step(0.02, 180.01);
    assert(RedRotor.bStopRequested && BlueRotor.bStopRequested);
    assert(RedRotor.bParked && BlueRotor.bParked);
    Step(1.0, 181.01);
    assert(RedRotor.AngleRadians == 0.0 && BlueRotor.AngleRadians == 0.0);
    std::cout << "RMUC field rules checks passed\n";
}
