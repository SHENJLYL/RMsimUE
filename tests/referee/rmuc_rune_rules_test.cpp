#include "RMUCRuneRules.h"

#include <cassert>
#include <cmath>

int FirstArm(int Mask)
{
    for (int Arm = 0; Arm < 5; ++Arm)
        if (Mask & (1 << Arm)) return Arm;
    return -1;
}

int main()
{
    using Phase = FRMUCRuneRules::Phase;
    FRMUCRuneRules Rune;
    Rune.Reset(2027);
    Rune.AdvanceTo(1.);
    assert(std::abs(std::abs(Rune.AngleRad()) - 1.0471975512) < 1.e-9);
    assert(Rune.Available(true) == 1 && Rune.Available(false) == 1);
    assert(Rune.Request(true) && !Rune.Request(true));
    assert(Rune.Get(false).State == Phase::Idle);
    const int Wrong = (FirstArm(Rune.Get(true).Targets) + 1) % 5;
    assert(!Rune.Hit(true, Wrong, 10) && Rune.Get(true).Groups == 0);
    assert(Rune.Get(true).State == Phase::Failed && Rune.Get(true).Targets == 0);
    assert(!Rune.Request(true));
    Rune.AdvanceTo(1.05);
    assert(Rune.Get(true).State == Phase::Failed);
    Rune.AdvanceTo(1.11);
    assert(Rune.Get(true).State == Phase::Activating);
    for (int Group = 0; Group < 5; ++Group)
    {
        const auto& Face = Rune.Get(true);
        assert((Face.Targets & Face.Lit) == 0);
        assert(Rune.Hit(true, FirstArm(Face.Targets), 10));
        if (Group < 4) Rune.AdvanceTo(1.11 + .11 * (Group + 1));
    }
    assert(Rune.Get(true).State == Phase::Activated);
    assert(Rune.Get(true).DefencePercent == 25 && Rune.Get(true).AttackPercent == 0);
    Rune.AdvanceTo(90.);
    assert(Rune.Get(true).State == Phase::Idle && Rune.Available(true) == 1);
    assert(Rune.Available(false) == 2 && Rune.Request(false));
    Rune.AdvanceTo(111.);
    assert(Rune.Get(false).State == Phase::Idle);

    Rune.AdvanceTo(180.);
    assert(Rune.Request(false));
    for (int Group = 0; Group < 5; ++Group)
    {
        auto Mask = Rune.Get(false).Targets;
        const int Arm = FirstArm(Mask);
        assert(Arm >= 0 && Rune.Hit(false, Arm, 10));
        assert(Rune.Get(false).Confirmed == (1 << Arm));
        const int Second = FirstArm(Rune.Get(false).Targets);
        assert(Second >= 0 && Second != Arm && Rune.Hit(false, Second, 10));
        if (Group < 4) Rune.AdvanceTo(180. + .11 * (Group + 1));
    }
    assert(Rune.Get(false).AttackPercent == 300 && Rune.Get(false).CoolingMultiplier == 5);
    assert(std::abs(Rune.Get(false).BuffUntil - 240.44) < 1.e-9);
    Rune.AdvanceTo(255.);
    assert(Rune.Get(false).State == Phase::Idle && Rune.Get(false).Groups == 0 && Rune.Get(false).Lit == 0);
    assert(Rune.Get(false).AttackPercent == 0 && Rune.Get(false).CoolingMultiplier == 1);
    assert(Rune.Request(false));
    assert(!Rune.Hit(false, FirstArm(Rune.Get(false).Targets), 3));
    assert(Rune.Hit(false, FirstArm(Rune.Get(false).Targets), 4));
    Rune.AdvanceTo(256.01);
    assert(Rune.Get(false).Groups == 1);
    Rune.AdvanceTo(256.12);
    assert(Rune.Get(false).Confirmed == 0 && Rune.Get(false).Targets != 0);
    Rune.AdvanceTo(258.64);
    assert(Rune.Get(false).State == Phase::Failed && Rune.Get(false).Groups == 0);
}
