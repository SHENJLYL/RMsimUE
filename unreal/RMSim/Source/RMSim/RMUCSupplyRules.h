#pragma once

#include "RMUCFieldRules.h"
#include <deque>

struct FRMUCPointContacts
{
    std::optional<bool> Supply, Base, Fortress, Outpost, CentralHighland, Vantage;
};

using FRMUCFieldPoints = std::array<FRMUCPointContacts, 2>;

// Rule manual §5.6.4.1. Losing the physical reader signal expires the card after
// two seconds. An inactive robot loses all cards immediately.
struct FRMUCFieldContactHold
{
    FRMUCFieldPoints Observe(FRMUCFieldPoints Contacts, double Elapsed, bool bAlive)
    {
        if (!bAlive)
        {
            ValidUntil = {};
            return Contacts;
        }
        constexpr std::array Cards{
            &FRMUCPointContacts::Supply, &FRMUCPointContacts::Base, &FRMUCPointContacts::Fortress,
            &FRMUCPointContacts::Outpost, &FRMUCPointContacts::CentralHighland, &FRMUCPointContacts::Vantage
        };
        for (unsigned Site = 0; Site < Contacts.size(); ++Site)
        {
            for (unsigned Card = 0; Card < Cards.size(); ++Card)
            {
                auto& Contact = Contacts[Site].*Cards[Card];
                auto& Expiry = ValidUntil[Site][Card];
                if (Contact.value_or(false))
                {
                    Expiry = Elapsed + 2.0;
                }
                else if (Contact)
                {
                    Contact = Elapsed + 1e-9 < Expiry;
                }
                else
                {
                    Expiry = 0.0;
                }
            }
        }
        return Contacts;
    }

private:
    std::array<std::array<double, 6>, 2> ValidUntil{};
};

// One independent Central Highland card group on each side. A simultaneous
// first sample is resolved in favor of the side whose card group it is.
struct FRMUCFieldPointLatch
{
    using Team = FRMUCFieldRules::Team;
    std::optional<Team> CentralHighlandOwner;

    FRMUCFieldRules::FSiteActivation Observe(Team Site, const FRMUCFieldRules& Rules, double Elapsed,
        const FRMUCPointContacts& Red, const FRMUCPointContacts& Blue)
    {
        const auto RedFortress = Fortress(Rules, Site, Team::Red, Elapsed, Red.Fortress);
        const auto BlueFortress = Fortress(Rules, Site, Team::Blue, Elapsed, Blue.Fortress);
        const auto Highland = ObserveHighland(Site, Red.CentralHighland, Blue.CentralHighland);
        return {
            Pair(Either(Red.Outpost, ActiveBy(Highland, Team::Red)),
                Either(Blue.Outpost, ActiveBy(Highland, Team::Blue))),
            Pair(Either(Red.Base, RedFortress), Either(Blue.Base, BlueFortress)),
            Site == Team::Red ? BlueFortress : RedFortress
        };
    }

private:
    using FActivation = FRMUCFieldRules::FActivation;

    std::optional<FActivation> ObserveHighland(Team Site, std::optional<bool> Red, std::optional<bool> Blue)
    {
        const auto Contacts = Pair(Red, Blue);
        if (!Contacts)
        {
            return std::nullopt;
        }
        if (!CentralHighlandOwner || !Contacts->By(*CentralHighlandOwner))
        {
            CentralHighlandOwner.reset();
            if (Contacts->By(Site))
            {
                CentralHighlandOwner = Site;
            }
            else if (Contacts->By(FRMUCFieldRules::Other(Site)))
            {
                CentralHighlandOwner = FRMUCFieldRules::Other(Site);
            }
        }
        return FActivation{CentralHighlandOwner == Team::Red, CentralHighlandOwner == Team::Blue};
    }

    static std::optional<bool> Fortress(const FRMUCFieldRules& Rules, Team Site, Team Occupant,
        double Elapsed, std::optional<bool> Contact)
    {
        return Rules.CanActivateFortress(Site, Occupant, Elapsed) ? Contact : std::optional<bool>{false};
    }

    static std::optional<bool> ActiveBy(std::optional<FActivation> State, Team Occupant)
    {
        return State ? std::optional<bool>{State->By(Occupant)} : std::nullopt;
    }

    static std::optional<bool> Either(std::optional<bool> First, std::optional<bool> Second)
    {
        if (First.value_or(false) || Second.value_or(false))
        {
            return true;
        }
        return First && Second ? std::optional<bool>{false} : std::nullopt;
    }

    static std::optional<FActivation> Pair(std::optional<bool> Red, std::optional<bool> Blue)
    {
        return Red && Blue ? std::optional<FActivation>{{*Red, *Blue}} : std::nullopt;
    }
};

struct FRMUCFieldPointEffects
{
    bool bSupply = false;
    bool bBase = false;
    bool bVantage = false;
    bool bFortress = false;
    bool bEnemyFortress = false;
    bool bOutpost = false;
    bool bFullOutpost = false;
    bool bCentralHighland = false;
    int DefencePercent = 0;
    int VulnerabilityPercent = 0;
    int FortressCoolingBonus = 0;
    int FortressReserveCapacity = 0;

    // Standard 1:1 ammo purchase (10 gold / 10 rounds) is only available inside the Supply/Repair Zone.
    bool CanPurchaseAmmo() const { return bSupply; }

    static FRMUCFieldPointEffects Evaluate(const FRMUCFieldRules& Rules, FRMUCFieldRules::Team Team,
        double Elapsed, const FRMUCFieldPoints& Contacts, const std::array<FRMUCFieldPointLatch, 2>& Latches)
    {
        const auto Enemy = FRMUCFieldRules::Other(Team);
        const unsigned OwnIndex = FRMUCFieldRules::Index(Team);
        const unsigned EnemyIndex = FRMUCFieldRules::Index(Enemy);
        const auto& Own = Contacts[OwnIndex];
        const auto& Opposing = Contacts[EnemyIndex];
        FRMUCFieldPointEffects Effects;
        Effects.bSupply = Own.Supply.value_or(false);
        Effects.bBase = Own.Base.value_or(false);
        Effects.bVantage = Own.Vantage.value_or(false);
        Effects.bOutpost = Own.Outpost.value_or(false) || Opposing.Outpost.value_or(false);
        Effects.bFortress = Own.Fortress.value_or(false) && Rules.CanActivateFortress(Team, Team, Elapsed);
        Effects.bEnemyFortress = Opposing.Fortress.value_or(false) && Rules.CanActivateFortress(Enemy, Team, Elapsed);
        const auto& OwnPostControl = Rules.Get(Team).OutpostControl;
        const auto& OpposingPostControl = Rules.Get(Enemy).OutpostControl;
        Effects.bFullOutpost =
            (Own.Outpost.value_or(false) && OwnPostControl.Controller == Team && OwnPostControl.bFull) ||
            (Opposing.Outpost.value_or(false) && OpposingPostControl.Controller == Team && OpposingPostControl.bFull);
        Effects.bCentralHighland =
            (Own.CentralHighland.value_or(false) && Latches[OwnIndex].CentralHighlandOwner == Team) ||
            (Opposing.CentralHighland.value_or(false) && Latches[EnemyIndex].CentralHighlandOwner == Team);
        Effects.DefencePercent = (Effects.bBase || Effects.bFortress || Effects.bVantage) ? 50 :
            (Effects.bCentralHighland ? 25 : 0);
        if (Effects.bEnemyFortress && !Rules.Get(Enemy).bBaseArmorOpen)
        {
            Effects.VulnerabilityPercent = 100;
        }
        if (Effects.bFortress)
        {
            const int LostBaseHp = FRMUCFieldRules::BaseMaxHp - Rules.Get(Team).BaseHp;
            Effects.FortressCoolingBonus = std::min(75, LostBaseHp / 80);
            Effects.FortressReserveCapacity = std::min(500, 100 + LostBaseHp / 15);
        }
        return Effects;
    }
};

// RMUC 2027 V1.0.0 sections 5.3.1, 5.4.1, 5.4.2 and 5.6.1.
class FRMUCSupplyRules
{
public:
    static constexpr int InitialAllowance = 300;
    static constexpr int SentryFreeAmmoCap = 300;
    static constexpr int InitialGold = 800;
    static constexpr int LocalAmmoCost = 10;
    static constexpr int LocalAmmoAmount = 10;
    static constexpr int RemoteAmmoCost = 150;
    static constexpr int RemoteAmmoAmount = 100;
    static constexpr double RemoteAmmoDelaySeconds = 6.0;
    enum class EPurchaseMode { Local, Remote };
    enum class EPurchase { Accepted, Unavailable, LimitReached, InsufficientGold };

    void MarkCombat(double Elapsed) { LastCombat = Elapsed; }
    bool IsOutOfCombat(double Elapsed) const { return Elapsed + TimeTolerance >= LastCombat + CombatTimeoutSeconds; }
    bool HasPendingRemoteAmmo() const { return !RemoteAmmoOrders.empty(); }

    double HealPercent(double Elapsed, int OwnBaseHp, const FRMUCFieldPointEffects& Effects) const
    {
        return ZoneHealingFactor(OwnBaseHp, Effects) * (Elapsed + TimeTolerance >= EnhancedHealingAfter() ? 25.0 : 10.0);
    }

    int RecoveryPercent(double Elapsed, int OwnBaseHp, const FRMUCFieldPointEffects& Effects) const
    {
        return static_cast<int>(std::round(HealPercent(Elapsed, OwnBaseHp, Effects)));
    }

    void Step(double Seconds, double Elapsed, int OwnBaseHp, const FRMUCFieldPointEffects& Effects,
        int MaxHp, int& Hp, int& Allowance, bool bIsSentry = true,
        int* SharedInfantryFreeClaimed = nullptr, int* SharedInfantryClaimedMinutes = nullptr)
    {
        if (Hp <= 0)
        {
            FractionalRecovery = 0.0;
            return;
        }
        if (Effects.bSupply)
        {
            const int AvailableMinutes = std::clamp(static_cast<int>((Elapsed + TimeTolerance) / 60.0), 0, 6);
            if (bIsSentry)
            {
                if (SentryFreeAmmoClaimed < SentryFreeAmmoCap && Allowance < 200 && AvailableMinutes > ClaimedMinutes)
                {
                    // Reserve half of each minute's 100 free 17mm rounds for infantry teammates;
                    // sentry claims at most 50/min and at most 300 total across the match.
                    const int Grant = std::min(50 * (AvailableMinutes - ClaimedMinutes),
                        SentryFreeAmmoCap - SentryFreeAmmoClaimed);
                    if (Grant > 0)
                    {
                        Allowance += Grant;
                        SentryFreeAmmoClaimed += Grant;
                    }
                    ClaimedMinutes = AvailableMinutes;
                }
            }
            else if (SharedInfantryFreeClaimed && SharedInfantryClaimedMinutes &&
                *SharedInfantryFreeClaimed < SentryFreeAmmoCap && AvailableMinutes > *SharedInfantryClaimedMinutes)
            {
                // Infantry (#3/#4) share the remaining 50/min (up to 300 total) from the team's free 17mm pool.
                const int Grant = std::min(50 * (AvailableMinutes - *SharedInfantryClaimedMinutes),
                    SentryFreeAmmoCap - *SharedInfantryFreeClaimed);
                if (Grant > 0)
                {
                    Allowance += Grant;
                    *SharedInfantryFreeClaimed += Grant;
                }
                *SharedInfantryClaimedMinutes = AvailableMinutes;
            }
        }
        if (Hp >= MaxHp)
        {
            FractionalRecovery = 0.0;
            return;
        }
        const double Factor = ZoneHealingFactor(OwnBaseHp, Effects);
        if (Factor <= 0.0 || Seconds <= 0.0)
        {
            return;
        }
        const double NormalTime = std::clamp(EnhancedHealingAfter() - (Elapsed - Seconds), 0.0, Seconds);
        FractionalRecovery += MaxHp * Factor * (0.10 * NormalTime + 0.25 * (Seconds - NormalTime));
        const int Recovered = static_cast<int>(std::floor(FractionalRecovery + 0.5 + TimeTolerance));
        Hp = std::min(MaxHp, Hp + Recovered);
        FractionalRecovery = Hp == MaxHp ? 0.0 : FractionalRecovery - Recovered;
    }

    EPurchase CheckAmmoPurchase(const FRMUCFieldPointEffects& Effects, bool bAlive, int Gold,
        EPurchaseMode Mode, double Elapsed) const
    {
        const bool bRemote = Mode == EPurchaseMode::Remote;
        if (!bAlive || (bRemote ? !IsOutOfCombat(Elapsed) : !Effects.CanPurchaseAmmo()))
        {
            return EPurchase::Unavailable;
        }
        if (PurchasedAmmo + (bRemote ? RemoteAmmoAmount : LocalAmmoAmount) > PurchasedAmmoLimit)
        {
            return EPurchase::LimitReached;
        }
        if (Gold < (bRemote ? RemoteAmmoCost : LocalAmmoCost))
        {
            return EPurchase::InsufficientGold;
        }
        return EPurchase::Accepted;
    }

    EPurchase PurchaseAmmo(const FRMUCFieldPointEffects& Effects, bool bAlive, int& Gold, int& Allowance,
        EPurchaseMode Mode = EPurchaseMode::Local, double Elapsed = 0.0)
    {
        const auto Result = CheckAmmoPurchase(Effects, bAlive, Gold, Mode, Elapsed);
        if (Result != EPurchase::Accepted) return Result;
        if (Mode == EPurchaseMode::Remote)
        {
            Gold -= RemoteAmmoCost;
            PurchasedAmmo += RemoteAmmoAmount;
            RemoteAmmoOrders.push_back(Elapsed + RemoteAmmoDelaySeconds);
        }
        else
        {
            Gold -= LocalAmmoCost;
            PurchasedAmmo += LocalAmmoAmount;
            Allowance += LocalAmmoAmount;
        }
        return Result;
    }

    int DeliverRemoteAmmo(double Elapsed, int& Allowance)
    {
        int Delivered = 0;
        while (HasPendingRemoteAmmo() && Elapsed + TimeTolerance >= RemoteAmmoOrders.front())
        {
            RemoteAmmoOrders.pop_front();
            Delivered += RemoteAmmoAmount;
        }
        Allowance += Delivered;
        return Delivered;
    }

    int FortressReserve(const FRMUCFieldPointEffects& Effects) const
    {
        return std::max(0, Effects.FortressReserveCapacity - FortressAmmoSpent);
    }

    bool ConsumeFortressRound(const FRMUCFieldPointEffects& Effects)
    {
        if (FortressReserve(Effects) == 0)
        {
            return false;
        }
        ++FortressAmmoSpent;
        return true;
    }

private:
    static constexpr int PurchasedAmmoLimit = 1000;
    static constexpr double TimeTolerance = 1e-9;
    static constexpr double CombatTimeoutSeconds = 6.0;
    double LastCombat = -CombatTimeoutSeconds;
    double FractionalRecovery = 0.0;
    int ClaimedMinutes = 0;
    int SentryFreeAmmoClaimed = 0;
    int PurchasedAmmo = 0;
    int FortressAmmoSpent = 0;
    std::deque<double> RemoteAmmoOrders;

    double EnhancedHealingAfter() const { return std::max(240.0, LastCombat + CombatTimeoutSeconds); }

    static double ZoneHealingFactor(int OwnBaseHp, const FRMUCFieldPointEffects& Effects)
    {
        const double Logistics = 0.5 + std::clamp(OwnBaseHp, 0, FRMUCFieldRules::BaseMaxHp) / 20000.0;
        return Logistics * (Effects.bSupply ? 1.0 : (Effects.bFullOutpost ? 0.5 : 0.0));
    }
};
