#include "RMUCGameReferee.h"
#include "RMUCArmorPresentation.h"

#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Components/PointLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "RMUCPhysicalAudio.h"
#include "VehicleRig.h"

namespace
{
constexpr ECollisionChannel ProjectileChannel = ECC_GameTraceChannel2;
constexpr float PelletRadiusCm = .85f;
constexpr double MaxLifetimeSeconds = 2.2;
constexpr double MaxStepSeconds = .01;
constexpr int32 MaxBounces = 4;
constexpr double NormalRestitution = .42;
constexpr double TangentRetention = .78;
constexpr double ContactSkinCm = .10;
constexpr double RestSpeedCmS = 45.;
}

void ARMUCGameReferee::BindProjectileVehicleSurfaces(AVehicleRig* Vehicle)
{
    if (!Vehicle) return;
    TArray<UStaticMeshComponent*> Meshes;
    Vehicle->GetComponents<UStaticMeshComponent>(Meshes);
    for (UStaticMeshComponent* Mesh : Meshes)
    {
        if (!Mesh || !Mesh->GetStaticMesh()) continue;
        if (Mesh->IsVisible())
        {
            // Visual components use their complex triangles. Collision proxies continue to own driving physics.
            if (Mesh->GetCollisionEnabled() == ECollisionEnabled::NoCollision)
            {
                Mesh->SetCollisionResponseToAllChannels(ECR_Ignore);
                Mesh->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
            }
            Mesh->SetCollisionResponseToChannel(ProjectileChannel, ECR_Block);
        }
        else
        {
            Mesh->SetCollisionResponseToChannel(ProjectileChannel, ECR_Ignore);
        }
    }
}

void ARMUCGameReferee::FireProjectile(const FTransform& MuzzleTransform)
{
    RequestRosShot(Rig, MuzzleTransform);
}

void ARMUCGameReferee::SetRosFlywheels(AVehicleRig* Robot, bool bRequested)
{
    const auto Side = TeamFor(Robot);
    if (!Side) return;
    const auto& Shooter = Competitor(*Side);
    const auto& Points = FieldPointEffects[FRMUCFieldRules::Index(*Side)];
    URMUCPhysicalAudio::SetFlywheelsRequested(Robot, bRequested && GameProgress == ERMUCGameProgress::Running &&
        !Shooter.ShooterLocked() && (Shooter.ProjectileAllowance17mm > 0 || Shooter.OwnLogistics.FortressReserve(Points) > 0));
}

FString ARMUCGameReferee::RequestRosShot(AVehicleRig* Robot, const FTransform& MuzzleTransform)
{
    const auto Side = TeamFor(Robot);
    if (!Side) return TEXT("invalid_robot");
    if (MuzzleTransform.ContainsNaN()) return TEXT("invalid_muzzle");
    if (GameProgress != ERMUCGameProgress::Running) return TEXT("match_not_running");
    auto& Shooter = Competitor(*Side);
    const auto& Points = FieldPointEffects[FRMUCFieldRules::Index(*Side)];
    if (Shooter.Hp <= 0) return TEXT("dead");
    if (Shooter.WeakTimer > 0.0) return TEXT("weak");
    if (Shooter.bOverheatMatchLocked) return TEXT("severe_overheat");
    if (Shooter.bOverheatLocked) return TEXT("overheat");
    if (Shooter.bOverspeedMatchLocked) return TEXT("overspeed_match_lock");
    if (Shooter.OverspeedLockTimer > 0.0) return TEXT("overspeed");
    if (Shooter.ProjectileAllowance17mm <= 0 && Shooter.OwnLogistics.FortressReserve(Points) <= 0) return TEXT("no_ammo");
    if (MatchElapsedSeconds + 1e-8 < Shooter.NextShotTime) return TEXT("fire_interval");
    const bool bRed = *Side == FieldTeam::Red;

    if (!Shooter.OwnLogistics.ConsumeFortressRound(Points)) --Shooter.ProjectileAllowance17mm;
    Shooter.OwnLogistics.MarkCombat(MatchElapsedSeconds);
    ++Shooter.TotalShotsFired;
    // RMUC 2027 §5.1.3: Shot is allowed to cross BarrelHeatLimit, then triggers overheat lock + FPV video cut until heat cools to 0!
    Shooter.BarrelHeat += HeatPerShot;
    Shooter.NextShotTime = MatchElapsedSeconds + ShotInterval;

    if (Shooter.BarrelHeat >= BarrelHeatLimit + SevereOverheat17mmOffset)
    {
        Shooter.bOverheatMatchLocked = true;
        Shooter.bOverheatLocked = true;
        PushCombatBroadcast(bRed ? 1 : -1,
            bRed ? TEXT("RED #7 SENTRY") : TEXT("BLUE #7 SENTRY"),
            TEXT("SEVERE OVERHEAT"), 0, TEXT("MATCH LOCKED"), 4.0);
    }
    else if (Shooter.BarrelHeat > BarrelHeatLimit)
    {
        Shooter.bOverheatLocked = true;
        PushCombatBroadcast(bRed ? 1 : -1,
            bRed ? TEXT("RED #7 SENTRY") : TEXT("BLUE #7 SENTRY"),
            TEXT("OVERHEAT LOCK"), 0, TEXT("VIDEO CUT -> COOL TO 0"), 3.5);
    }

    // RMUC 2027 Table 5-3: 17mm overspeed penalty (>25 m/s -> 15s, >=+5 -> 20s, >=+10 -> match lock)
    const double OverspeedMs = (LaunchSpeedCmS - SpeedLimit17mmCmS) * 0.01;
    if (OverspeedMs >= 10.0)
    {
        Shooter.bOverspeedMatchLocked = true;
        PushCombatBroadcast(bRed ? 1 : -1,
            bRed ? TEXT("RED #7 SENTRY") : TEXT("BLUE #7 SENTRY"),
            TEXT("OVERSPEED >=+10"), 0, TEXT("MATCH LOCKED"), 4.0);
    }
    else if (OverspeedMs >= 5.0)
    {
        Shooter.OverspeedLockTimer = FMath::Max(Shooter.OverspeedLockTimer, 20.0);
        PushBannerEvent(TEXT("OVERSPEED PENALTY — SHOOTER LOCKED 20s"), 3.0);
    }
    else if (OverspeedMs > 1e-4)
    {
        Shooter.OverspeedLockTimer = FMath::Max(Shooter.OverspeedLockTimer, 15.0);
        PushBannerEvent(TEXT("OVERSPEED PENALTY — SHOOTER LOCKED 15s"), 3.0);
    }

    const FVector Forward = MuzzleTransform.GetRotation().GetForwardVector().GetSafeNormal();
    FProjectile Pellet;
    // aim_camera is already on the source muzzle axis, 2 cm ahead of its face.
    Pellet.Position = MuzzleTransform.GetLocation();
    Pellet.Velocity = Forward * LaunchSpeedCmS;
    Pellet.bFromRed = bRed;
    URMUCPhysicalAudio::PlayFeeder(Robot, Pellet.Position);

    auto* Sphere = NewObject<UStaticMeshComponent>(this);
    Sphere->SetupAttachment(RootComponent);
    Sphere->SetMobility(EComponentMobility::Movable);
    Sphere->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Sphere.Sphere")));
    Sphere->SetWorldLocation(Pellet.Position);
    Sphere->SetRelativeScale3D(FVector(2. * PelletRadiusCm / 100.));
    Sphere->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Sphere->SetCastShadow(false);
    const FLinearColor TeamLight = bRed ? FLinearColor(1.85f, .013f, .070f) : FLinearColor(.019f, .082f, 1.90f);
    if (auto* Mat = RMUCArmorPresentation::MakeEmitter(*this, bRed))
    {
        Mat->SetVectorParameterValue(RMUCArmorPresentation::LampColor, TeamLight);
        Sphere->SetMaterial(0, Mat);
    }
    else if (auto* Base = LoadObject<UMaterialInterface>(
        nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"), nullptr, LOAD_NoWarn))
    {
        if (auto* Fallback = Sphere->CreateDynamicMaterialInstance(0, Base))
            Fallback->SetVectorParameterValue(TEXT("Color"), TeamLight);
    }
    AddInstanceComponent(Sphere);
    Sphere->RegisterComponent();
    Pellet.Visual = Sphere;
    Pellet.Light = RMUCArmorPresentation::AddPointLight(*this, *Sphere, FVector::ZeroVector, TeamLight, 25.f, .55f);
    Projectiles.Add(Pellet);
    return TEXT("accepted");
}

ARMUCGameReferee::EProjectileHit ARMUCGameReferee::ApplyProjectileDamage(
    const FProjectile& Pellet, const FHitResult& Hit, double NormalSpeedCmS)
{
    if (GameProgress != ERMUCGameProgress::Running || Pellet.bDamageCredited || !Hit.bBlockingHit ||
        Hit.bStartPenetrating || NormalSpeedCmS <= 1200.) return EProjectileHit::None;

    const FieldTeam Attacker = Pellet.bFromRed ? FieldTeam::Red : FieldTeam::Blue;
    if (auto* Vehicle = Cast<AVehicleRig>(Hit.GetActor()))
    {
        const auto Target = TeamFor(Vehicle);
        if (!Target || *Target == Attacker) return EProjectileHit::None;
        const int32 ArmorId = ClassifyVehicleArmorHit(Hit, Vehicle);
        if (ArmorId == INDEX_NONE || !ApplySentryDamage(*Target, Attacker, ArmorId, 20, ERMUCHpDeductionReason::ArmorHit))
            return EProjectileHit::None;
        return EProjectileHit::Armor;
    }

    bool bTargetRed = false;
    int32 Plate = INDEX_NONE;
    double OffsetCm = 0.;
    if (FieldVisuals.GetRuneTarget(Hit, bTargetRed, Plate, &OffsetCm))
    {
        const int32 Ring = FMath::Clamp(11 - FMath::CeilToInt(OffsetCm / 1.5), 1, 10);
        if (bTargetRed != Pellet.bFromRed || !RuneRules.Hit(bTargetRed, Plate, Ring)) return EProjectileHit::None;
        RecordHit(Attacker, 0);
        return EProjectileHit::Rune;
    }

    bool bOutpost = FieldVisuals.GetOutpostTarget(Hit, bTargetRed);
    const bool bBaseArmor = !bOutpost && FieldVisuals.GetBaseTarget(Hit, bTargetRed);
    if (!bOutpost && !bBaseArmor && !ClassifyFieldStructureHit(Hit,
        FieldVisuals.IsBaseArmorReady() ? nullptr : CollisionBaseActor,
        FieldVisuals.OutpostsAreReady() ? nullptr : CollisionCentrePlatform, bTargetRed, bOutpost,
        FieldVisuals.GetCentreVisibilityComponent())) return EProjectileHit::None;

    const auto& Attack = RuneRules.Get(Pellet.bFromRed);
    const FieldTeam Site = bTargetRed ? FieldTeam::Red : FieldTeam::Blue;
    const FieldTeam DefendingTeam = bOutpost ? FieldRules.Get(Site).OutpostOwner : Site;
    const auto& Defence = RuneRules.Get(DefendingTeam == FieldTeam::Red);
    const double AttackMultiplier = Attack.AttackPercent > 0 ? Attack.AttackPercent / 100. : 1.;
    const int32 Damage = FMath::RoundToInt(20. * AttackMultiplier * (1. - Defence.DefencePercent / 100.));
    const int32 Lost = ApplyFieldStructureHit(bTargetRed, bOutpost, Pellet.bFromRed, Damage);
    if (Lost <= 0) return EProjectileHit::None;
    RecordHit(Attacker, Lost);
    return EProjectileHit::Armor;
}

void ARMUCGameReferee::StepProjectiles(double DeltaSeconds)
{
    if (DeltaSeconds <= 0. || !GetWorld()) return;
    const FVector Gravity(0., 0., -GravityCmS2);
    const FCollisionShape Shape = FCollisionShape::MakeSphere(PelletRadiusCm);
    for (int32 Index = Projectiles.Num() - 1; Index >= 0; --Index)
    {
        FProjectile& Pellet = Projectiles[Index];
        bool bAlive = GameProgress == ERMUCGameProgress::Running;
        double Remaining = FMath::Min(DeltaSeconds, MaxLifetimeSeconds - Pellet.Age);
        while (bAlive && Remaining > UE_SMALL_NUMBER)
        {
            const double Step = FMath::Min(Remaining, MaxStepSeconds);
            const FVector End = Pellet.Position + Pellet.Velocity * Step + Gravity * (.5 * Step * Step);
            FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimRMUCProjectile), true);
            Query.bReturnPhysicalMaterial = true;
            // Let a reflected pellet hit its own vehicle. Ignore the muzzle carrier only just after emission.
            if (Pellet.BounceCount == 0 && Pellet.Age < .02)
                Query.AddIgnoredActor(VehicleFor(Pellet.bFromRed ? FieldTeam::Red : FieldTeam::Blue));
            FHitResult Hit;
            if (!GetWorld()->SweepSingleByChannel(Hit, Pellet.Position, End, FQuat::Identity,
                ProjectileChannel, Shape, Query))
            {
                Pellet.Position = End;
                Pellet.Velocity += Gravity * Step;
                Pellet.Age += Step;
                Remaining -= Step;
                continue;
            }

#if WITH_DEV_AUTOMATION_TESTS
            static const bool bTraceProjectiles = FParse::Param(FCommandLine::Get(), TEXT("RefereeProjectileTrace"));
            if (bTraceProjectiles && Pellet.BounceCount == 0)
                UE_LOG(LogRMSim, Display, TEXT("RMUC_PROJECTILE_SURFACE team=%s actor=%s component=%s start_overlap=%d from=%s impact=%s normal=%s"),
                    Pellet.bFromRed ? TEXT("red") : TEXT("blue"), *GetNameSafe(Hit.GetActor()),
                    *GetNameSafe(Hit.GetComponent()), Hit.bStartPenetrating, *Pellet.Position.ToCompactString(),
                    *Hit.ImpactPoint.ToCompactString(), *Hit.ImpactNormal.ToCompactString());
#endif

            // Do not turn an overlap into a synthetic score or impact sound.
            if (Hit.bStartPenetrating || Hit.ImpactNormal.IsNearlyZero())
            {
                bAlive = false;
                break;
            }
            const double TimeToHit = Step * FMath::Clamp(double(Hit.Time), 0., 1.);
            const FVector Incoming = Pellet.Velocity + Gravity * TimeToHit;
            const FVector Normal = Hit.ImpactNormal.GetSafeNormal();
            const UPrimitiveComponent* Surface = Hit.GetComponent();
            const FVector SurfaceVelocity = Surface ? Surface->GetComponentVelocity() : FVector::ZeroVector;
            const FVector Relative = Incoming - SurfaceVelocity;
            const double ClosingSpeed = -FVector::DotProduct(Relative, Normal);
            if (ClosingSpeed <= 0.)
            {
                bAlive = false;
                break;
            }

            const EProjectileHit Result = ApplyProjectileDamage(Pellet, Hit, ClosingSpeed);
            Pellet.bDamageCredited |= Result != EProjectileHit::None;
            URMUCPhysicalAudio::PlayImpact(GetWorld(), Hit, ClosingSpeed, Result == EProjectileHit::Armor);
            // Reflect the normal component and preserve only part of the tangential motion.
            const FVector Tangent = Relative + ClosingSpeed * Normal;
            Pellet.Velocity = SurfaceVelocity + TangentRetention * Tangent + NormalRestitution * ClosingSpeed * Normal;
            Pellet.Position = Hit.Location + ContactSkinCm * Normal;
            // Consume a small interval on a zero-time hit to keep corner contacts bounded.
            const double Consumed = FMath::Max(TimeToHit, .0001);
            Pellet.Age += Consumed;
            Remaining -= Consumed;
            bAlive = ++Pellet.BounceCount <= MaxBounces && Pellet.Velocity.SizeSquared() > FMath::Square(RestSpeedCmS);
        }

        if (!bAlive || Pellet.Age >= MaxLifetimeSeconds - UE_SMALL_NUMBER)
        {
            if (auto* Light = Pellet.Light.Get()) Light->DestroyComponent();
            if (Pellet.Visual) Pellet.Visual->DestroyComponent();
            Projectiles.RemoveAtSwap(Index);
        }
        else if (Pellet.Visual)
        {
            Pellet.Visual->SetWorldLocation(Pellet.Position);
        }
    }
}
