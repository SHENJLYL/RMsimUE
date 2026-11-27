#include "RMUCInfantryAI.h"
#include "RMUCGameReferee.h"
#include "VehicleRig.h"

#include "CollisionQueryParams.h"
#include "Components/BoxComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/World.h"
#include "HAL/PlatformTime.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "Misc/FileHelper.h"
#include "RMUCFieldRules.h"
#include "RMSimViewportClient.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#endif

namespace
{
using Mode = FRMUCInfantryAI::EMode;
constexpr double ThinkInterval = .05;
constexpr double SightRangeCm = 900.;
constexpr int32 PerSide = 8;
constexpr int32 NodeCount = 2 * PerSide;
enum Node : int32 { Supply, SupplyApproach, Defend, InnerFlank, RampInner, RampOuter, RampCrest, Mid };

// ROS metres in the red half of the shipped RMUC2027 field. Both mirrored
// route footprints were checked against its CAD collision and support map.
const FVector2D RedNodes[PerSide] = {
    {12.00, 6.00}, {11.15, 3.25}, {8.00, -2.50}, {5.20, 3.80},
    {9.55, 4.85}, {9.50, 6.45}, {2.40, 6.20}, {1.80, -.20}
};
const TCHAR* const ModeNames[] = {
    TEXT("Resupply"), TEXT("Defend"), TEXT("Engage"), TEXT("AssaultOutpost"), TEXT("Patrol")
};

FVector2D WorldNode(int32 Index)
{
    const FVector2D P = RedNodes[Index % PerSide] * (Index < PerSide ? 1. : -1.);
    return FVector2D(P.X, -P.Y) * 100.;
}

int32 NextNode(int32 Start, int32 Goal)
{
    double Distance[NodeCount];
    int32 Hop[NodeCount];
    bool Visited[NodeCount]{};
    for (int32 Index = 0; Index < NodeCount; ++Index)
    {
        Distance[Index] = TNumericLimits<double>::Max();
        Hop[Index] = INDEX_NONE;
    }
    Distance[Goal] = 0.;
    Hop[Goal] = Goal;
    for (int32 Iteration = 0; Iteration < NodeCount; ++Iteration)
    {
        int32 Current = INDEX_NONE;
        for (int32 Candidate = 0; Candidate < NodeCount; ++Candidate)
            if (!Visited[Candidate] && (Current == INDEX_NONE || Distance[Candidate] < Distance[Current]))
                Current = Candidate;
        if (Current == Start) return Hop[Start];
        if (Current == INDEX_NONE || Hop[Current] == INDEX_NONE) break;
        Visited[Current] = true;
        const int32 SideStart = Current / PerSide * PerSide;
        const int32 Local = Current % PerSide;
        const int32 OtherStart = PerSide - SideStart;
        const int32 Neighbours[] = {
            Local > 0 ? Current - 1 : INDEX_NONE,
            Local + 1 < PerSide ? Current + 1 : INDEX_NONE,
            Local == Mid ? OtherStart + RampCrest : Local == RampCrest ? OtherStart + Mid : INDEX_NONE
        };
        for (int32 Other : Neighbours)
        {
            if (Other == INDEX_NONE || Visited[Other]) continue;
            const double Cost = Distance[Current] + FVector2D::Distance(WorldNode(Current), WorldNode(Other));
            if (Cost >= Distance[Other]) continue;
            Distance[Other] = Cost;
            Hop[Other] = Current;
        }
    }
    return INDEX_NONE;
}

int32 NearestNode(const FVector2D& Position)
{
    double Best = TNumericLimits<double>::Max();
    int32 NodeIndex = 0;
    for (int32 Index = 0; Index < NodeCount; ++Index)
    {
        const double Distance = FVector2D::DistSquared(Position, WorldNode(Index));
        if (Distance >= Best) continue;
        Best = Distance;
        NodeIndex = Index;
    }
    return NodeIndex;
}

FVector2D RouteVelocity(FRMUCInfantryAI& AI, const FVector2D& Position, int32 Goal, int32 RobotNumber)
{
    if (Goal != AI.RouteGoal)
    {
        AI.RouteGoal = Goal;
        AI.RouteNode = NearestNode(Position);
    }
    if (AI.RouteNode == INDEX_NONE) return FVector2D::ZeroVector;
    FVector2D Waypoint = WorldNode(AI.RouteNode);
    if (FVector2D::DistSquared(Position, Waypoint) <= FMath::Square(22.) && AI.RouteNode != Goal)
    {
        AI.RouteNode = NextNode(AI.RouteNode, Goal);
        if (AI.RouteNode == INDEX_NONE) return FVector2D::ZeroVector;
        Waypoint = WorldNode(AI.RouteNode);
    }
    if (AI.RouteNode == Goal && Goal % PerSide == Supply)
        Waypoint.Y += (RobotNumber == 3 ? 46. : -46.) * (Goal < PerSide ? 1. : -1.);
    const FVector2D Offset = Waypoint - Position;
    const int32 Local = AI.RouteNode % PerSide;
    const double SpeedCap = Local >= RampInner ? 1.4 : 1.8;
    const double Speed = FMath::Clamp((Offset.Size() - 12.) * .017, 0., SpeedCap);
    return Offset.GetSafeNormal() * Speed;
}

struct FVisibleTarget
{
    FVector Point = FVector::ZeroVector;
    FVector Velocity = FVector::ZeroVector;
    int32 Id = INDEX_NONE;
    double DistanceSquared = SightRangeCm * SightRangeCm + 1.;
};

bool HasSight(UWorld& World, const AVehicleRig& Observer, const FVector& Origin, const FVector& Point,
    const AActor* Actor, const UPrimitiveComponent* Component = nullptr)
{
    FCollisionQueryParams Params(SCENE_QUERY_STAT(InfantryAISight), true, &Observer);
    FHitResult Hit;
    if (!World.LineTraceSingleByChannel(Hit, Origin, Point, ECC_Visibility, Params)) return true;
    return Component ? Hit.GetComponent() == Component : Hit.GetActor() == Actor;
}

FVector2D AvoidObstacles(UWorld& World, const AVehicleRig& Vehicle, const FVector2D& Velocity,
    FRMUCInfantryAI& AI, int32 RobotNumber)
{
    if (Velocity.IsNearlyZero(.01)) return FVector2D::ZeroVector;
    const FVector2D Forward = Velocity.GetSafeNormal();
    const FVector2D Side(-Forward.Y, Forward.X);
    const FVector Start = Vehicle.GetActorLocation() + FVector(0., 0., 17.);
    FCollisionQueryParams Params(SCENE_QUERY_STAT(InfantryAIAvoidance), false, &Vehicle);
    auto Clearance = [&](const FVector2D& Direction)
    {
        FHitResult Hit;
        const FVector End = Start + FVector(Direction.X, Direction.Y, 0.).GetSafeNormal() * 100.;
        return World.LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params) && Hit.ImpactNormal.Z < .65
            ? double(Hit.Time) : 1.;
    };
    const double Centre = Clearance(Forward);
    const double Left = Clearance(Forward + Side * .56);
    const double Right = Clearance(Forward - Side * .56);
    if (Centre >= .87 && FMath::Min(Left, Right) >= .74)
    {
        AI.DodgeSide = 0.;
        return Velocity;
    }
    const double Scale = FMath::Clamp((Centre - .32) / .55, 0., 1.);
    if (Centre < .74)
    {
        if (AI.DodgeSide == 0. || (AI.DodgeSide > 0. ? Left : Right) < .23)
            AI.DodgeSide = FMath::Abs(Left - Right) < .12 ? (RobotNumber == 3 ? 1. : -1.)
                                                        : (Left > Right ? 1. : -1.);
        const double Space = AI.DodgeSide > 0. ? Left : Right;
        return Velocity * (Scale * .68) + Side * (AI.DodgeSide * FMath::Clamp((Space - .18) * 1.35, 0., .83));
    }
    const double Dodge = FMath::Clamp((Left - Right) * .83, -.48, .48);
    return Velocity * Scale + Side * Dodge;
}
}

bool ARMUCGameReferee::IsInfantryAiControlledFor(const AVehicleRig* Robot) const
{
    const FMatchRobotSlot* Slot = FindSlot(Robot);
    return Slot && Slot->bAiControlled;
}

void ARMUCGameReferee::StopInfantryAI(FMatchRobotSlot& Slot)
{
    if (!Slot.bAiControlled) return;
    if (AVehicleRig* Vehicle = Slot.Vehicle.Get(); Vehicle && !IsRosCompetitor(Vehicle))
    {
        Vehicle->SetExternalVelocityTarget(FVector2D::ZeroVector, 0., true);
        SetRosFlywheels(Vehicle, false);
    }
    FRMUCInfantryAI& AI = Slot.InfantryAI;
    AI.bWasActive = AI.bDriving = AI.bVisibleEnemy = AI.bVisibleOutpost = false;
    AI.bBraking = true;
    AI.bFlywheels = false;
    AI.DodgeSide = 0.;
    AI.bResupplying = true;
    AI.Mode = Mode::Resupply;
    AI.TargetId = AI.RouteGoal = INDEX_NONE;
    AI.RecoverUntil = 0.;
}

void ARMUCGameReferee::TryInfantryAIResupply(FMatchRobotSlot& Slot)
{
    auto& AI = Slot.InfantryAI;
    auto& Robot = Slot.State;
    const auto& Points = Slot.PointEffects;
    if (Robot.IsWeak() || !Points.CanPurchaseAmmo() || MatchElapsedSeconds < AI.NextPurchase) return;
    AI.NextPurchase = MatchElapsedSeconds + .42;
    const unsigned Team = FRMUCFieldRules::Index(Slot.Team);
    constexpr int32 Rounds = FRMUCSupplyRules::LocalAmmoAmount;
    const int32 Unlocked = FMath::Clamp(FMath::FloorToInt(MatchElapsedSeconds / 60.), 0, 6) * 50;
    if (Points.bSupply && Unlocked - TeamInfantryFreeAmmoClaimed[Team] >= Rounds)
    {
        TeamInfantryFreeAmmoClaimed[Team] += Rounds;
        Robot.ProjectileAllowance17mm += Rounds;
        AI.FreeRounds += Rounds;
        return;
    }
    if (TeamGold[Team] < 200 + FRMUCSupplyRules::LocalAmmoCost) return;
    if (Robot.OwnLogistics.PurchaseAmmo(Points, true, TeamGold[Team], TeamPurchasedAmmo17mm[Team], Robot.ProjectileAllowance17mm,
            FRMUCSupplyRules::EPurchaseMode::Local, MatchElapsedSeconds) == FRMUCSupplyRules::EPurchase::Accepted)
    {
        AI.LocalPaid += Rounds;
        SyncSharedTeamState();
    }
}

void ARMUCGameReferee::StepInfantryAI(double DeltaSeconds)
{
#if WITH_DEV_AUTOMATION_TESTS
    const double Began = InfantryAIEvidencePath.IsEmpty() ? 0. : FPlatformTime::Seconds();
    double DecisionEnd = 0.;
    double ShotSeconds = 0.;
#endif
    if (InfantryAICount > 0)
    {
        int32 ThinkIndex = INDEX_NONE;
        for (int32 Offset = 0; Offset < MatchRobots.Num(); ++Offset)
        {
            const int32 Index = (NextInfantryAISeat + Offset) % MatchRobots.Num();
            FMatchRobotSlot& Slot = MatchRobots[Index];
            if (!Slot.bAiControlled) continue;
            AVehicleRig* Vehicle = Slot.Vehicle.Get();
            auto& AI = Slot.InfantryAI;
            if (GameProgress != ERMUCGameProgress::Running || Slot.State.Hp <= 0 ||
                !Vehicle || IsRosCompetitor(Vehicle) || !IsChassisPoweredFor(Vehicle))
            {
                if (AI.bWasActive || !AI.bBraking) StopInfantryAI(Slot);
                continue;
            }
            if (!AI.bWasActive)
            {
                AI.bWasActive = true;
                AI.NextThink = MatchElapsedSeconds + double(AI.CrewIndex) * .01;
                AI.LastThink = MatchElapsedSeconds;
                AI.ProgressOrigin = FVector2D(Vehicle->GetActorLocation());
                AI.ProgressAt = MatchElapsedSeconds;
            }
            if (ThinkIndex == INDEX_NONE && DeltaSeconds > 0. && MatchElapsedSeconds + 1.e-8 >= AI.NextThink)
                ThinkIndex = Index;
        }
        // Bound expensive queries to one seat per world update and rotate the
        // first chance among the four contenders when a frame runs late.
        if (ThinkIndex != INDEX_NONE)
        {
            auto& Slot = MatchRobots[ThinkIndex];
            auto& AI = Slot.InfantryAI;
            const double Step = FMath::Clamp(MatchElapsedSeconds - AI.LastThink, .005, .1);
            AI.LastThink = MatchElapsedSeconds;
            AI.NextThink = MatchElapsedSeconds + ThinkInterval;
            NextInfantryAISeat = (ThinkIndex + 1) % MatchRobots.Num();
            FTransform ShotMuzzle;
            if (StepInfantryAISeat(Slot, Step, ShotMuzzle))
            {
#if WITH_DEV_AUTOMATION_TESTS
                if (Began > 0.) DecisionEnd = FPlatformTime::Seconds();
#endif
                // Use the common referee firing path after the UE-only control
                // decision. Its existing projectile and audio cost is sampled
                // separately from planning and control.
                RequestRosShot(Slot.Vehicle.Get(), ShotMuzzle);
#if WITH_DEV_AUTOMATION_TESTS
                if (Began > 0.) ShotSeconds = FPlatformTime::Seconds() - DecisionEnd;
#endif
            }
        }
    }
#if WITH_DEV_AUTOMATION_TESTS
    if (Began > 0.) RecordInfantryAIEvidence((DecisionEnd > 0. ? DecisionEnd : FPlatformTime::Seconds()) - Began, ShotSeconds);
#endif
}

bool ARMUCGameReferee::StepInfantryAISeat(FMatchRobotSlot& Slot, double DeltaSeconds, FTransform& ShotMuzzle)
{
    auto& AI = Slot.InfantryAI;
    auto& Robot = Slot.State;
    auto* Vehicle = Slot.Vehicle.Get();
    check(Vehicle);
    const double Now = MatchElapsedSeconds;
    ++AI.LogicTicks;
    const bool bRed = Slot.Team == FieldTeam::Red;
    const FieldTeam Enemy = FRMUCFieldRules::Other(Slot.Team);
    const int32 Home = bRed ? 0 : PerSide;
    const auto& OwnSite = FieldRules.Get(Slot.Team);
    const auto& EnemySite = FieldRules.Get(Enemy);
    if (AI.LastBaseHp >= 0 && OwnSite.BaseHp < AI.LastBaseHp) AI.DefendUntil = Now + 9.;
    AI.LastBaseHp = OwnSite.BaseHp;
    const auto& Control = OwnSite.OutpostControl;
    const bool bDefend = Now < AI.DefendUntil || Control.Controller != Slot.Team ||
        !Control.bFull || Control.TransitionSeconds > 0.;
    const bool bEnemyPost = EnemySite.OutpostHp > 0 && EnemySite.OutpostOwner != Slot.Team;
    const FVector2D Position(Vehicle->GetActorLocation());
    const USceneCaptureComponent2D* Muzzle = Vehicle->GetCameras().FindRef(TEXT("aim_camera"));
    const FVector Eye = Muzzle ? Muzzle->GetComponentLocation() : Vehicle->GetActorLocation();
    auto& World = *GetWorld();

    const int32 ReadyAmmo = AI.bHasDeparted ? 40 : 50;
    if (Robot.Hp < 80 || Robot.ProjectileAllowance17mm <= 0) AI.bResupplying = true;
    if (AI.bResupplying && Robot.Hp >= 160 && Robot.ProjectileAllowance17mm >= ReadyAmmo)
    {
        AI.bResupplying = false;
        AI.bHasDeparted = true;
    }
    FVisibleTarget Target;
    AI.bVisibleEnemy = AI.bVisibleOutpost = false;
    if (!AI.bResupplying)
    {
        for (const auto& Other : MatchRobots)
        {
            if (Other.Team == Slot.Team || Other.State.Hp <= 0) continue;
            const auto* EnemyVehicle = Other.Vehicle.Get();
            if (!EnemyVehicle) continue;
            const FVector Point = EnemyVehicle->GetActorLocation() + FVector(0., 0., 20.);
            const double Range = FVector::DistSquared(Eye, Point);
            if (Range >= Target.DistanceSquared || !HasSight(World, *Vehicle, Eye, Point, EnemyVehicle)) continue;
            Target = {Point, EnemyVehicle->GetCommandOriginVelocity(), Other.ProtocolRobotId(), Range};
        }
        AI.bVisibleEnemy = Target.Id != INDEX_NONE;
        if (!AI.bVisibleEnemy)
        {
            for (const FieldTeam Site : FRMUCFieldRules::Teams)
            {
                const auto& Tower = FieldRules.Get(Site);
                if (Tower.OutpostHp <= 0 || Tower.OutpostOwner == Slot.Team) continue;
                for (int32 Face = 0; Face < 3; ++Face)
                {
                    const auto* Plate = FieldVisuals.GetInfantryOutpostPlate(Site == FieldTeam::Red, Face);
                    if (!Plate) continue;
                    const FVector Point = Plate->GetComponentLocation();
                    const double Range = FVector::DistSquared(Eye, Point);
                    if (Range >= Target.DistanceSquared ||
                        FVector::DotProduct((Eye - Point).GetSafeNormal(), Plate->GetForwardVector()) < .35 ||
                        !HasSight(World, *Vehicle, Eye, Point, nullptr, Plate)) continue;
                    Target = {Point, FVector::ZeroVector, 1000 + int32(FRMUCFieldRules::Index(Site)), Range};
                }
            }
            AI.bVisibleOutpost = Target.Id != INDEX_NONE;
        }
    }

    FVector2D Velocity = FVector2D::ZeroVector;
    double Gyro = 0.;
    int32 Goal = INDEX_NONE;
    if (AI.bResupplying)
    {
        AI.Mode = Mode::Resupply;
        const bool bOwnFullPost = AI.bHasDeparted && Slot.PointEffects.bFullOutpost && Control.Controller == Slot.Team &&
            Control.bFull && Slot.PhysicalContacts[FRMUCFieldRules::Index(Slot.Team)].Outpost.value_or(false);
        if ((Slot.PointEffects.bSupply || bOwnFullPost) && Robot.ProjectileAllowance17mm < ReadyAmmo)
            TryInfantryAIResupply(Slot);
        if (!bOwnFullPost) Goal = Home + Supply;
    }
    else if (bDefend)
    {
        AI.Mode = Mode::Defend;
        Goal = Home + (Slot.RobotNumber == 3 ? Defend : InnerFlank);
    }
    else if (AI.bVisibleEnemy)
    {
        AI.Mode = Mode::Engage;
        const FVector2D Offset = FVector2D(Target.Point) - Position;
        const FVector2D Forward = Offset.GetSafeNormal();
        const FVector2D Side(-Forward.Y, Forward.X);
        const double Radial = FMath::Clamp((Offset.Size() - 525.) * .011, -.85, .85);
        const double Phase = Now + double(AI.CrewIndex) * 1.38;
        Velocity = Forward * Radial + Side * (.55 * FMath::Sin(Phase * 1.2));
        if (Offset.Size() >= 360. && Offset.Size() <= 690. && FMath::Sin(Phase * 1.8) > .57) Gyro = .85;
    }
    else if (bEnemyPost && Slot.RobotNumber == 3)
    {
        AI.Mode = Mode::AssaultOutpost;
        Goal = Home + Mid;
    }
    else
    {
        AI.Mode = Mode::Patrol;
        const int32 CentrePatrol[] = {Mid, RampCrest, InnerFlank};
        const int32 FlankPatrol[] = {InnerFlank, RampInner, RampOuter, RampCrest};
        const int32 Count = Slot.RobotNumber == 3 ? UE_ARRAY_COUNT(CentrePatrol) : UE_ARRAY_COUNT(FlankPatrol);
        const int32* Patrol = Slot.RobotNumber == 3 ? CentrePatrol : FlankPatrol;
        Goal = Home + Patrol[AI.PatrolLeg % Count];
        if (FVector2D::DistSquared(Position, WorldNode(Goal)) < FMath::Square(26.))
            Goal = Home + Patrol[++AI.PatrolLeg % Count];
    }
    if (Goal != INDEX_NONE) Velocity = RouteVelocity(AI, Position, Goal, Slot.RobotNumber);
    else AI.RouteGoal = INDEX_NONE;

    const bool bWantsMotion = !Velocity.IsNearlyZero(.08);
    if (bWantsMotion && AI.RecoverUntil <= Now) AI.ProgressDirection = Velocity.GetSafeNormal();
    if (!AI.bDriving || FVector2D::DistSquared(Position, AI.ProgressOrigin) >= FMath::Square(15.))
    {
        AI.ProgressOrigin = Position;
        AI.ProgressAt = Now;
    }
    if (AI.RecoverUntil <= Now && bWantsMotion && AI.bDriving && Now - AI.ProgressAt >= 1.2)
    {
        AI.RecoverUntil = Now + .6;
        AI.ProgressAt = Now;
        AI.RouteGoal = INDEX_NONE;
        ++AI.StuckRecoveries;
    }
    const FRotator Heading(0., Vehicle->GetActorRotation().Yaw, 0.);
    if (AI.RecoverUntil > Now)
    {
        const FVector2D Back = -AI.ProgressDirection;
        const FVector2D Side(-Back.Y, Back.X);
        Velocity = Back * .55 + Side * (Slot.RobotNumber == 3 ? .45 : -.45);
        Gyro = 0.;
    }
    Velocity = AvoidObstacles(World, *Vehicle, Velocity, AI, Slot.RobotNumber).GetClampedToMaxSize(1.8);
    if (Robot.ChassisBufferEnergyJ < 28.)
    {
        Velocity = Velocity.GetClampedToMaxSize(.85);
        Gyro = 0.;
    }
    if (Robot.ChassisBufferEnergyJ < 15.)
    {
        const FVector2D Current = FVector2D(Vehicle->GetCommandOriginVelocity()) * .01;
        Velocity = Velocity.GetClampedToMaxSize(FMath::Max(0., FVector2D::DotProduct(Current, Velocity.GetSafeNormal())));
    }
    const FVector Local = Heading.UnrotateVector(FVector(Velocity.X, Velocity.Y, 0.));
    AI.bDriving = bWantsMotion;
    AI.bBraking = Velocity.IsNearlyZero(.04) && FMath::IsNearlyZero(Gyro);
    if (Vehicle->SetExternalVelocityTarget(FVector2D(Local.X, -Local.Y), Gyro, AI.bBraking, Gyro != 0.))
        Vehicle->RenewExternalDriveLease(.25);

    if (Target.Id != AI.TargetId)
    {
        AI.TargetId = Target.Id;
        AI.EngagedAt = Now;
        AI.ReactionDelay = AI.Random.FRandRange(.25f, .35f);
    }
    AI.bCooling = AI.bCooling ? Robot.BarrelHeat > 120. : Robot.BarrelHeat >= 165.;
    auto SetFlywheels = [&](bool bOn)
    {
        if (AI.bFlywheels == bOn) return;
        AI.bFlywheels = bOn;
        SetRosFlywheels(Vehicle, bOn);
    };
    if (!Muzzle || Target.Id == INDEX_NONE)
    {
        SetFlywheels(false);
        return false;
    }
    // Assist in the simulated world only. The physical pellet is spawned from
    // the actual turret pose, so motion, scatter and cover all affect misses.
    const double FlightTime = FMath::Sqrt(Target.DistanceSquared) / FMath::Max(1., LaunchSpeedCmS);
    const FVector Aim = Target.Point + Target.Velocity * (.6 * FlightTime) -
        FVector(0., 0., .5 * World.GetGravityZ() * FlightTime * FlightTime);
    const FRotator LocalAim = Vehicle->GetActorTransform().InverseTransformVectorNoScale(Aim - Eye).Rotation();
    const double Phase = .91 * Now + 1.731 * double(AI.CrewIndex);
    const double Yaw = FMath::DegreesToRadians(-LocalAim.Yaw + 2.65 * FMath::Sin(1.3 * Phase) + .4 * FMath::Sin(2.93 * Phase));
    const double Pitch = FMath::DegreesToRadians(-LocalAim.Pitch + 2.55 * FMath::Sin(1.7 * Phase) + .35 * FMath::Cos(2.1 * Phase));
    double CurrentYaw, CurrentPitch;
    Vehicle->GetGimbalTargets(CurrentYaw, CurrentPitch);
    FString Error;
    Vehicle->SetGimbal(CurrentYaw + FMath::Clamp(FMath::FindDeltaAngleRadians(CurrentYaw, Yaw),
                          -UE_DOUBLE_PI * DeltaSeconds, UE_DOUBLE_PI * DeltaSeconds),
                      FMath::Clamp(CurrentPitch + FMath::Clamp(Pitch - CurrentPitch,
                              -FMath::DegreesToRadians(120.) * DeltaSeconds, FMath::DegreesToRadians(120.) * DeltaSeconds),
                          FMath::DegreesToRadians(-59.), FMath::DegreesToRadians(44.)), Error);
    const bool bCanFire = !AI.bCooling && !Robot.ShooterLocked() && Robot.ProjectileAllowance17mm > 0 &&
        Robot.BarrelHeat + HeatPerShot < Slot.HeatLimit() && Now >= AI.EngagedAt + AI.ReactionDelay;
    const bool bAligned = FVector::DotProduct(Muzzle->GetForwardVector(), (Aim - Eye).GetSafeNormal()) > .9945;
    SetFlywheels(bCanFire && bAligned);
    if (bCanFire && bAligned && Now >= AI.NextShot)
    {
        ShotMuzzle = Muzzle->GetComponentTransform();
        AI.NextShot = Now + AI.Random.FRandRange(.16f, .20f);
        return true;
    }
    return false;
}

#if WITH_DEV_AUTOMATION_TESTS
void ARMUCGameReferee::RecordInfantryAIEvidence(double ElapsedWallSeconds, double ShotSeconds)
{
    if (GFrameCounter != InfantryAIFrame)
    {
        if (InfantryAIFrame != 0) InfantryAIFrameSamples.Add(InfantryAIFrameSeconds * 1000.);
        InfantryAIFrame = GFrameCounter;
        InfantryAIFrameSeconds = 0.;
    }
    InfantryAIFrameSeconds += ElapsedWallSeconds;
    InfantryAIShotSeconds += ShotSeconds;
    InfantryAIPeakShotMs = FMath::Max(InfantryAIPeakShotMs, ShotSeconds * 1000.);
    if (MatchElapsedSeconds < NextInfantryAIEvidenceAt) return;
    NextInfantryAIEvidenceAt = MatchElapsedSeconds + .5;
    auto State = MakeShared<FJsonObject>();
    State->SetNumberField(TEXT("sim_s"), MatchElapsedSeconds);
    State->SetNumberField(TEXT("wall_s"), FPlatformTime::Seconds());
    State->SetStringField(TEXT("phase"), GameProgress == ERMUCGameProgress::Running ? TEXT("Running") : TEXT("Waiting"));
    TArray<TSharedPtr<FJsonValue>> Seats;
    for (const FMatchRobotSlot& Slot : MatchRobots)
    {
        const auto* Vehicle = Slot.Vehicle.Get();
        if (!Vehicle) continue;
        const auto& Robot = Slot.State;
        const auto& AI = Slot.InfantryAI;
        auto Row = MakeShared<FJsonObject>();
        const FVector Position = Vehicle->GetActorLocation() * .01;
        Row->SetNumberField(TEXT("id"), Slot.ProtocolRobotId());
        Row->SetBoolField(TEXT("ai"), Slot.bAiControlled);
        Row->SetBoolField(TEXT("ros"), IsRosCompetitor(Vehicle));
        Row->SetNumberField(TEXT("x_m"), Position.X);
        Row->SetNumberField(TEXT("y_m"), -Position.Y);
        Row->SetNumberField(TEXT("hp"), Robot.Hp);
        Row->SetNumberField(TEXT("ammo"), Robot.ProjectileAllowance17mm);
        Row->SetNumberField(TEXT("shots"), Robot.TotalShotsFired);
        Row->SetNumberField(TEXT("heat"), Robot.BarrelHeat);
        Row->SetNumberField(TEXT("buffer_j"), Robot.ChassisBufferEnergyJ);
        Row->SetNumberField(TEXT("power_w"), Robot.ChassisPowerW);
        Row->SetNumberField(TEXT("power_limit_w"), Slot.BasePowerLimitW());
        Row->SetNumberField(TEXT("gold"), TeamGold[FRMUCFieldRules::Index(Slot.Team)]);
        Row->SetStringField(TEXT("state"), Slot.bAiControlled ? ModeNames[int32(AI.Mode)] : TEXT(""));
        Row->SetNumberField(TEXT("local_paid"), AI.LocalPaid);
        Row->SetNumberField(TEXT("free_rounds"), AI.FreeRounds);
        Row->SetNumberField(TEXT("logic_ticks"), AI.LogicTicks);
        Row->SetNumberField(TEXT("stuck_recoveries"), AI.StuckRecoveries);
        Row->SetBoolField(TEXT("visible_enemy"), AI.bVisibleEnemy);
        Row->SetBoolField(TEXT("visible_outpost"), AI.bVisibleOutpost);
        Row->SetBoolField(TEXT("braking"), AI.bBraking);
        Row->SetBoolField(TEXT("supply"), Slot.PointEffects.bSupply);
        Seats.Add(MakeShared<FJsonValueObject>(Row));
    }
    State->SetArrayField(TEXT("seats"), Seats);
    auto CPU = MakeShared<FJsonObject>();
    double Total = 0.;
    int32 Busy = 0;
    for (double Sample : InfantryAIFrameSamples)
    {
        Total += Sample;
        Busy += Sample > 0.0005 ? 1 : 0;
    }
    const int32 Count = InfantryAIFrameSamples.Num();
    InfantryAIFrameSamples.Sort();
    CPU->SetNumberField(TEXT("frames"), Count);
    CPU->SetNumberField(TEXT("samples"), Count);
    CPU->SetNumberField(TEXT("busy_frames"), Busy);
    CPU->SetNumberField(TEXT("mean_ms"), Count ? Total / Count : 0.);
    CPU->SetNumberField(TEXT("p95_ms"), Count ? InfantryAIFrameSamples[FMath::CeilToInt(Count * .95) - 1] : 0.);
    CPU->SetNumberField(TEXT("max_ms"), Count ? InfantryAIFrameSamples.Last() : 0.);
    CPU->SetNumberField(TEXT("shot_total_ms"), InfantryAIShotSeconds * 1000.);
    CPU->SetNumberField(TEXT("shot_peak_ms"), InfantryAIPeakShotMs);
    State->SetObjectField(TEXT("cpu"), CPU);
    InfantryAIFrameSamples.Reset();
    InfantryAIShotSeconds = InfantryAIPeakShotMs = 0.;
    if (const auto* Viewport = Cast<URMSimViewportClient>(GetWorld()->GetGameViewport()))
        State->SetObjectField(TEXT("rendering"), Viewport->RenderState());
    FString Line;
    auto Writer = TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Line);
    if (!FJsonSerializer::Serialize(State, Writer) ||
        !FFileHelper::SaveStringToFile(Line + LINE_TERMINATOR, *InfantryAIEvidencePath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM, &IFileManager::Get(), FILEWRITE_Append))
    {
        if (++InfantryAIEvidenceWriteErrors == 1)
            UE_LOG(LogTemp, Warning, TEXT("INFANTRY_AI_EVIDENCE write failed path=%s"), *InfantryAIEvidencePath);
    }
}
#endif
