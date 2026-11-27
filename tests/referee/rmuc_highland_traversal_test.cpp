#include "../../unreal/RMSim/Source/RMSim/RMUCHighlandTraversal.h"

#include <array>
#include <cassert>
#include <utility>

int main()
{
    using Card = ERMUCHighlandCard;
    constexpr std::array Pairings{std::pair{Card::RedLower, Card::RedUpper}, std::pair{Card::BlueLower, Card::BlueUpper}};
    for (const auto& [Lower, Upper] : Pairings)
    {
        const Card Wrong = Upper == Card::RedUpper ? Card::BlueUpper : Card::RedUpper;
        FRMUCHighlandTraversal Traversal;
        Traversal.Observe(Upper, 1.0, true);
        Traversal.Observe(Lower, 2.0, true);
        assert(Traversal.Remaining(2.0) == 0.0); // Direct upper and reverse order.
        Traversal.Observe(Wrong, 3.0, true);
        Traversal.Observe(Upper, 4.0, true);
        assert(Traversal.Remaining(4.0) == 0.0);
        Traversal.Observe(Lower, 5.0, true);
        Traversal.Observe(Card::Other, 6.0, true);
        Traversal.Observe(Upper, 7.0, true);
        assert(Traversal.Remaining(7.0) == 0.0);
        Traversal.Observe(Lower, 8.0, true);
        Traversal.Observe(Lower, 12.0, true);
        Traversal.Observe(Upper, 13.01, true);
        assert(Traversal.Remaining(13.01) == 0.0); // Continuous reading leaves the deadline at first contact.
        Traversal.Observe(Lower, 14.0, true);
        Traversal.Observe(Upper, 15.0, false);
        Traversal.Observe(Upper, 16.0, true);
        assert(Traversal.Remaining(16.0) == 0.0);
        Traversal.Observe(Lower, 17.0, true);
        Traversal.Observe(Card::None, 17.1, true);
        Traversal.Observe(Upper, 22.0, true);
        assert(Traversal.Remaining(22.0) == 30.0);
        Traversal.Observe(Upper, 30.0, true);
        Traversal.Observe(Card::Other, 31.0, true);
        assert(Traversal.Remaining(51.5) == 0.5);
        assert(Traversal.Remaining(52.0) == 0.0);
        Traversal.Observe(Lower, 53.0, true);
        Traversal.Observe(Upper, 54.0, true);
        assert(Traversal.Remaining(54.0) == 30.0);
        Traversal.Observe(Card::None, 55.0, false);
        assert(Traversal.Remaining(55.0) == 0.0);
        Traversal.Observe(Lower, 56.0, true);
        Traversal = {}; // Round reset discards the pre-reset card.
        Traversal.Observe(Upper, 1.0, true);
        assert(Traversal.Remaining(1.0) == 0.0);
    }
    static_assert(FRMUCHighlandTraversal::DefencePercent == 25);
}
