#pragma once

#include <algorithm>

enum class ERMUCHighlandCard
{
    None,
    RedLower,
    RedUpper,
    BlueLower,
    BlueUpper,
    Other
};

// RMUC 2027 §5.6.4.5. This sequence consumes raw read events, before the
// two-second hold used for continuous field buffs.
class FRMUCHighlandTraversal
{
public:
    static constexpr int DefencePercent = 25;

    void Observe(ERMUCHighlandCard Card, double Elapsed, bool bAlive)
    {
        using CardType = ERMUCHighlandCard;
        if (!bAlive)
        {
            *this = {};
            return;
        }
        if (Card == PreviousCard) return;
        PreviousCard = Card;
        if (Card == CardType::None) return;

        if (Card == CardType::RedLower || Card == CardType::BlueLower)
        {
            ExpectedCard = Card == CardType::RedLower ? CardType::RedUpper : CardType::BlueUpper;
            PairDeadline = Elapsed + 5.0;
            return;
        }
        if (Card == ExpectedCard && Elapsed <= PairDeadline + 1e-9)
        {
            BuffUntil = Elapsed + 30.0;
            SuppressedUntil = 0.0;
        }
        ExpectedCard = CardType::None;
    }

    double Remaining(double Elapsed) const { return std::max(0.0, BuffUntil - Elapsed); }
    bool HasEffect(double Elapsed) const { return Elapsed >= SuppressedUntil && Remaining(Elapsed) > 0.0; }
    void SuppressActiveBuff(double Elapsed, double Seconds)
    {
        if (Seconds > 0.0 && Remaining(Elapsed) > 0.0)
            SuppressedUntil = std::max(Elapsed, SuppressedUntil) + Seconds;
    }
    void ClearActiveBuff() { BuffUntil = SuppressedUntil = 0.0; }
    ERMUCHighlandCard ObservedCard() const { return PreviousCard; }

private:
    ERMUCHighlandCard PreviousCard = ERMUCHighlandCard::None;
    ERMUCHighlandCard ExpectedCard = ERMUCHighlandCard::None;
    double PairDeadline = 0.0;
    double BuffUntil = 0.0;
    double SuppressedUntil = 0.0;
};
