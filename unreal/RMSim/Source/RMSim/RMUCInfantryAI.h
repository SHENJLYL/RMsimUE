#pragma once

#include "CoreMinimal.h"

// Match-only state for one UE infantry seat. The referee owns the roster,
// resources and shot settlement.
struct FRMUCInfantryAI
{
    enum class EMode : uint8 { Resupply, Defend, Engage, AssaultOutpost, Patrol };

    static constexpr int32 FullHp = 200;
    static constexpr double HeatLimit = 200.;
    static constexpr double CoolingPerSecond = 20.;
    static constexpr double ChassisPowerW = 60.;

    EMode Mode = EMode::Resupply;
    FVector2D ProgressOrigin = FVector2D::ZeroVector;
    FVector2D ProgressDirection = FVector2D::ZeroVector;
    double NextThink = 0.;
    double LastThink = 0.;
    double NextPurchase = 0.;
    double NextShot = 0.;
    double EngagedAt = 0.;
    double DefendUntil = 0.;
    double ProgressAt = 0.;
    double RecoverUntil = 0.;
    double ReactionDelay = .3;
    int32 LastBaseHp = -1;
    int32 TargetId = INDEX_NONE;
    int32 RouteGoal = INDEX_NONE;
    int32 RouteNode = INDEX_NONE;
    int32 PatrolLeg = 0;
    int32 CrewIndex = 0;
    FRandomStream Random;
    bool bWasActive = false;
    bool bResupplying = true;
    bool bHasDeparted = false;
    bool bCooling = false;
    bool bDriving = false;
    bool bBraking = false;
    bool bFlywheels = false;
    double DodgeSide = 0.;
    bool bVisibleEnemy = false;
    bool bVisibleOutpost = false;
    int32 LocalPaid = 0;
    int32 FreeRounds = 0;
    int32 LogicTicks = 0;
    int32 StuckRecoveries = 0;

    void Reset(int32 InCrewIndex, int32 RobotId)
    {
        *this = {};
        CrewIndex = InCrewIndex;
        Random.Initialize(202700 + RobotId);
    }
};
