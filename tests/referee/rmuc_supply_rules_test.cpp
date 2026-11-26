#include "RMUCSupplyRules.h"

#include <cassert>
#include <iostream>

using Rules = FRMUCFieldRules;
using Team = Rules::Team;
using Purchase = FRMUCSupplyRules::EPurchase;
using Mode = FRMUCSupplyRules::EPurchaseMode;

int main()
{
    Rules Match;
    FRMUCFieldPoints Red, Blue;
    std::array<FRMUCFieldPointLatch, 2> Latches;
    auto& RedSite = Latches[0];
    auto& RedAtRed = Red[0];
    auto& BlueAtRed = Blue[0];
    const auto Step = [&](double Seconds, double Elapsed) {
        std::array<Rules::FSiteActivation, 2> Active;
        for (const Team Site : Rules::Teams)
        {
            const auto I = Rules::Index(Site);
            Active[I] = Latches[I].Observe(Site, Match, Elapsed, Red[I], Blue[I]);
        }
        Match.Step(Seconds, Elapsed, Active);
    };

    assert(!RedSite.Observe(Team::Red, Match, 0.0, RedAtRed, BlueAtRed).OutpostPosition);
    RedAtRed = {false, false, false, false, false, false};
    BlueAtRed = RedAtRed;
    BlueAtRed.CentralHighland = true;
    Step(2.0, 2.0);
    assert(RedSite.CentralHighlandOwner == Team::Blue);
    RedAtRed.CentralHighland = true;
    RedAtRed.Outpost = true;
    Step(1.0, 3.0);
    assert(Match.Get(Team::Red).OutpostControl.TransitionSeconds == 2.0);
    assert(RedSite.CentralHighlandOwner == Team::Blue);
    RedAtRed.Outpost = false;
    Step(3.0, 6.0);
    assert(Match.Get(Team::Red).OutpostControl.Controller == Team::Blue);
    assert(!Match.Get(Team::Red).OutpostControl.bFull);
    assert(!Match.BaseProtected(Team::Red));

    RedAtRed.Fortress = true;
    BlueAtRed.Fortress = true;
    assert(Match.HitBase(Team::Red, Team::Blue, 4000) == 4000);
    Step(1.0, 179.5);
    auto RedEffects = FRMUCFieldPointEffects::Evaluate(Match, Team::Red, 179.5, Red, Latches);
    assert(RedEffects.bFortress && RedEffects.DefencePercent == 50);
    assert(RedEffects.FortressCoolingBonus == 50 && RedEffects.FortressReserveCapacity == 366);
    assert(!RedEffects.CanPurchaseAmmo());
    auto BlueEffects = FRMUCFieldPointEffects::Evaluate(Match, Team::Blue, 179.5, Blue, Latches);
    assert(BlueEffects.bCentralHighland && BlueEffects.DefencePercent == 25 && !BlueEffects.bEnemyFortress);
    Step(1.0, 180.5);
    assert(Match.Get(Team::Red).ArmorActivationSeconds == 0.5);
    BlueEffects = FRMUCFieldPointEffects::Evaluate(Match, Team::Blue, 180.5, Blue, Latches);
    assert(BlueEffects.bEnemyFortress && BlueEffects.VulnerabilityPercent == 100);
    Step(9.5, 190.0);
    assert(Match.Get(Team::Red).bBaseArmorOpen);
    BlueEffects = FRMUCFieldPointEffects::Evaluate(Match, Team::Blue, 190.0, Blue, Latches);
    assert(BlueEffects.bEnemyFortress && BlueEffects.VulnerabilityPercent == 0);

    BlueAtRed.CentralHighland = false;
    BlueAtRed.Fortress = false;
    BlueAtRed.Base = true;
    RedAtRed.Fortress = false;
    Step(5.0, 195.0);
    assert(RedSite.CentralHighlandOwner == Team::Red);
    assert(Match.Get(Team::Red).BaseControl.Controller == Team::Blue);
    assert(Match.Get(Team::Red).BaseControl.TransitionSeconds == 0.0);
    BlueAtRed.Base = false;
    Step(1.0, 196.0);
    RedAtRed.Base = true;
    BlueAtRed.Base = true;
    Step(2.0, 198.0);
    assert(Match.Get(Team::Red).BaseControl.TransitionSeconds == 1.0);
    BlueAtRed.Base = false;
    Step(4.0, 202.0);
    assert(Match.Get(Team::Red).BaseControl.Controller == Team::Red);
    assert(Match.Get(Team::Red).BaseControl.bFull);

    FRMUCFieldContactHold Reader;
    FRMUCFieldPoints Cards;
    Cards[0].Outpost = true;
    Cards[1].CentralHighland = false;
    assert(Reader.Observe(Cards, 1.0, true)[0].Outpost.value());
    Cards[0].Outpost = false;
    const auto InGrace = Reader.Observe(Cards, 2.999, true);
    assert(InGrace[0].Outpost.value() && !InGrace[1].CentralHighland.value());
    assert(!Reader.Observe(Cards, 3.0, true)[0].Outpost.value());
    assert(!Reader.Observe(Cards, 3.0, true)[0].Base.has_value());
    Cards[0].Outpost = true;
    assert(Reader.Observe(Cards, 5.0, true)[0].Outpost.value());
    Cards[0].Outpost = false;
    assert(!Reader.Observe(Cards, 5.5, false)[0].Outpost.value());
    assert(!Reader.Observe(Cards, 5.6, true)[0].Outpost.value());

    FRMUCSupplyRules Logistics;
    assert(Logistics.FortressReserve(RedEffects) == 366 && Logistics.ConsumeFortressRound(RedEffects));
    assert(Logistics.FortressReserve(RedEffects) == 365);
    assert(!Logistics.ConsumeFortressRound({}));
    int Hp = 100;
    int Allowance = FRMUCSupplyRules::InitialAllowance;
    FRMUCFieldPointEffects Supply;
    Supply.bSupply = true;
    FRMUCSupplyRules RoundedRecovery;
    int RoundedHp = 100;
    RoundedRecovery.Step(0.015, 1.0, 9999, Supply, 400, RoundedHp, Allowance);
    assert(RoundedHp == 101);
    RoundedRecovery.Step(1.0, 2.0, 9999, {}, 400, RoundedHp, Allowance);
    RoundedRecovery.Step(0.015, 2.015, 9999, Supply, 400, RoundedHp, Allowance);
    assert(RoundedHp == 101);
    RoundedRecovery.Step(0.970, 2.985, 9999, Supply, 400, RoundedHp, Allowance);
    assert(RoundedHp == 140);
    Logistics.Step(1.0, 59.999, 10000, Supply, 400, Hp, Allowance);
    assert(Hp == 140 && Allowance == 300);
    Logistics.Step(0.001, 60.0, 10000, Supply, 400, Hp, Allowance);
    assert(Allowance == 400);
    Logistics.Step(1.0, 180.0, 10000, {}, 400, Hp, Allowance);
    assert(Hp == 140 && Allowance == 400);
    Logistics.Step(1.0, 181.0, 10000, Supply, 400, Hp, Allowance);
    assert(Hp == 180 && Allowance == 600);
    Hp = 100;
    Logistics.Step(2.0, 241.0, 10000, Supply, 400, Hp, Allowance);
    assert(Hp == 240);
    Logistics.MarkCombat(241.0);
    Logistics.Step(1.0, 242.0, 6000, Supply, 400, Hp, Allowance);
    assert(Hp == 272 && Logistics.RecoveryPercent(242.0, 6000, Supply) == 8);
    assert(Logistics.RecoveryPercent(247.0, 6000, Supply) == 20);
    FRMUCFieldPointEffects Outpost;
    Outpost.bFullOutpost = true;
    Logistics.Step(1.0, 248.0, 6000, Outpost, 400, Hp, Allowance);
    assert(Hp == 312);
    Hp = 0;
    Logistics.Step(1.0, 360.0, 10000, Supply, 400, Hp, Allowance);
    assert(Hp == 0 && Allowance == 700);
    Hp = 390;
    Logistics.Step(1.0, 361.0, 10000, Supply, 400, Hp, Allowance);
    assert(Hp == 400 && Allowance == 900);

    int Gold = 9;
    assert(Logistics.PurchaseAmmo({}, true, Gold, Allowance) == Purchase::Unavailable);
    assert(Logistics.PurchaseAmmo(Supply, true, Gold, Allowance) == Purchase::InsufficientGold);
    assert(Gold == 9 && Allowance == 900);
    Gold = 1200;
    assert(Logistics.PurchaseAmmo(Supply, false, Gold, Allowance) == Purchase::Unavailable);
    for (int I = 0; I < 100; ++I)
    {
        assert(Logistics.PurchaseAmmo(Outpost, true, Gold, Allowance) == Purchase::Accepted);
    }
    assert(Logistics.PurchaseAmmo(Supply, true, Gold, Allowance) == Purchase::LimitReached);
    assert(Gold == 200 && Allowance == 1900);

    FRMUCSupplyRules Orders;
    int Cash = 2000;
    int Rounds = 0;
    Orders.MarkCombat(5.0);
    assert(Orders.PurchaseAmmo({}, false, Cash, Rounds, Mode::Remote, 11.0) == Purchase::Unavailable);
    assert(Orders.PurchaseAmmo({}, true, Cash, Rounds, Mode::Remote, 10.999) == Purchase::Unavailable);
    assert(Orders.PurchaseAmmo({}, true, Cash, Rounds, Mode::Remote, 11.0) == Purchase::Accepted);
    assert(Orders.HasPendingRemoteAmmo() && Cash == 1850 && Rounds == 0);
    assert(Orders.PurchaseAmmo({}, true, Cash, Rounds, Mode::Remote, 12.0) == Purchase::Accepted);
    assert(Orders.DeliverRemoteAmmo(16.999, Rounds) == 0);
    assert(Orders.DeliverRemoteAmmo(17.0, Rounds) == 100 && Rounds == 100);
    assert(Orders.DeliverRemoteAmmo(18.0, Rounds) == 100 && Rounds == 200);
    assert(!Orders.HasPendingRemoteAmmo() && Orders.DeliverRemoteAmmo(18.0, Rounds) == 0);
    for (int I = 0; I < 71; ++I)
    {
        assert(Orders.PurchaseAmmo(Supply, true, Cash, Rounds) == Purchase::Accepted);
    }
    assert(Orders.PurchaseAmmo({}, true, Cash, Rounds, Mode::Remote, 20.0) == Purchase::LimitReached);
    assert(Orders.PurchaseAmmo(Supply, true, Cash, Rounds) == Purchase::Accepted);
    assert(!Orders.HasPendingRemoteAmmo() && Rounds == 920 && Cash == 980);
    assert(Orders.CheckAmmoPurchase({}, true, 149, Mode::Remote, 20.0) == Purchase::LimitReached);

    std::cout << "RMUC supply and point rules checks passed\n";
}
