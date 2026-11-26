#include "RMUCGameReferee.h"
#include "RMUCUpperBaseArmor.h"

#include "VehicleRig.h"
#include "Components/BoxComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "EngineUtils.h"

#if WITH_DEV_AUTOMATION_TESTS
bool ARMUCGameReferee::VerifyProjectileQueries()
{
    if (!Rig || !EnemyRig || GameProgress != ERMUCGameProgress::Running) return false;

    FCollisionQueryParams Params(SCENE_QUERY_STAT(RMUCProjectileProof), true, Rig);
    Params.AddIgnoredActor(this);
    const auto FindHit = [&](const FVector& Start, const FVector& End, FHitResult& Hit)
    {
        return GetWorld()->SweepSingleByChannel(Hit, Start, End, FQuat::Identity,
            ECC_GameTraceChannel2, FCollisionShape::MakeSphere(.85f), Params) && !Hit.bStartPenetrating;
    };
    const auto Hp = [&]() -> TArray<int32>
    {
        return {Competitor(FieldTeam::Red).Hp, Competitor(FieldTeam::Blue).Hp, FieldRules.Get(FieldTeam::Red).OutpostHp,
            FieldRules.Get(FieldTeam::Blue).OutpostHp, FieldRules.Get(FieldTeam::Red).BaseHp,
            FieldRules.Get(FieldTeam::Blue).BaseHp};
    };
    const auto Exercise = [&](const TCHAR* Label, const FVector& Start, const FVector& End,
        const FHitResult& Hit, bool bExpectDamage, double Speed = 3000., bool bExpectImmediateRemoval = false) -> bool
    {
        const auto Before = Hp();
        FProjectile Shot;
        Shot.Position = Start;
        Shot.Velocity = (End - Start).GetSafeNormal() * Speed;
        Shot.bFromRed = bFriendlyRed;
        Shot.Visual = NewObject<UStaticMeshComponent>(this);
        Shot.Visual->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        AddInstanceComponent(Shot.Visual);
        Shot.Visual->RegisterComponent();
        const TWeakObjectPtr<UStaticMeshComponent> Visual = Shot.Visual;
        Projectiles.Add(Shot);
        StepProjectiles(FVector::Dist(Start, Hit.Location) / Speed + .004);
        const bool bBounced = Projectiles.Num() == 1 && Projectiles[0].BounceCount >= 1 &&
            FVector::DotProduct(Projectiles[0].Velocity, Hit.ImpactNormal) > 0;
        const bool bExpectedMotion = bExpectImmediateRemoval ? Projectiles.IsEmpty() : bBounced;
        const auto After = Hp();
        bool bOneTargetDamaged = false;
        int32 ChangedTargets = 0;
        for (int32 Index = 0; Index < After.Num(); ++Index)
        {
            ChangedTargets += After[Index] != Before[Index] ? 1 : 0;
            bOneTargetDamaged |= After[Index] < Before[Index];
        }
        const bool bDamageCorrect = bExpectDamage ? bOneTargetDamaged && ChangedTargets == 1 : Before == After;
        for (int32 Step = 0; Step < 180 && !Projectiles.IsEmpty(); ++Step) StepProjectiles(1. / 60.);
        const bool bCleanedUp = Projectiles.IsEmpty() && (!Visual.IsValid() || !Visual->IsRegistered());
        const bool bPassed = bExpectedMotion && bDamageCorrect && bCleanedUp;
        UE_LOG(LogTemp, Display, TEXT("RMUC_PROJECTILE_PROOF case=%s passed=%d bounce=%d hp_changes=%d cleanup=%d component=%s"),
            Label, bPassed, bBounced, ChangedTargets, bCleanedUp, *GetNameSafe(Hit.GetComponent()));
        return bPassed;
    };

    FHitResult Hit;
    bool bFloor = false;
    for (int32 Offset : {90, 110, -100, -130})
    {
        const FVector Start = Rig->GetActorLocation() + FVector(Offset, 0, 65);
        const FVector End = Start - FVector(0, 0, 100);
        if (!FindHit(Start, End, Hit)) continue;
        auto* Mesh = Cast<UStaticMeshComponent>(Hit.GetComponent());
        if (!Mesh || !Mesh->GetStaticMesh() || !Mesh->GetStaticMesh()->GetName().Equals(TEXT("collision_floor"))) continue;
        bFloor = Exercise(TEXT("floor"), Start, End, Hit, false);
        break;
    }

    bool bVehicleBody = false;
    const FTransform EnemyPose = EnemyRig->GetActorTransform();
    const FVector BodyStart = EnemyPose.TransformPosition(FVector(3, 0, 68));
    const FVector BodyEnd = EnemyPose.TransformPosition(FVector(3, 0, 7));
    if (FindHit(BodyStart, BodyEnd, Hit) && Hit.GetActor() == EnemyRig &&
        ClassifyVehicleArmorHit(Hit, EnemyRig) == INDEX_NONE)
        bVehicleBody = Exercise(TEXT("vehicle_body"), BodyStart, BodyEnd, Hit, false);

    bool bArmor = true;
    const FVector Faces[] = {{22.8321,0,14.07}, {0,-23.4825,14.07}, {-22.8327,0,14.07}, {0,22.8323,14.07}};
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(Faces); ++Index)
    {
        const FVector& Face = Faces[Index];
        FVector Normal = Face.GetSafeNormal2D() * .965926;
        Normal.Z = .258819;
        const FVector Across = FVector::CrossProduct(FVector::UpVector, Normal).GetSafeNormal();
        const FVector Up = FVector::CrossProduct(Normal, Across).GetSafeNormal();
        const FVector Samples[] = {Face, Face + 5.8 * Across, Face - 5.8 * Across, Face + 4.8 * Up};
        for (int32 Sample = 0; Sample < UE_ARRAY_COUNT(Samples); ++Sample)
        {
            const FVector Start = EnemyPose.TransformPosition(Samples[Sample] + 20. * Normal);
            const FVector End = EnemyPose.TransformPosition(Samples[Sample] - 4. * Normal);
            if (FindHit(Start, End, Hit) && Hit.GetActor() == EnemyRig && ClassifyVehicleArmorHit(Hit, EnemyRig) == Index)
                bArmor &= Exercise(*FString::Printf(TEXT("vehicle_armor_%d_sample_%d"), Index, Sample), Start, End, Hit, true);
            else
            {
                UE_LOG(LogTemp, Warning, TEXT("RMUC_PROJECTILE_PROOF armor_fixture=%d sample=%d component=%s"),
                    Index, Sample, *GetNameSafe(Hit.GetComponent()));
                bArmor = false;
            }
        }
    }

    bool bGlass = false, bOutpost = false;
    for (TActorIterator<AActor> Actor(GetWorld()); Actor && !(bGlass && bOutpost); ++Actor)
    {
        TInlineComponentArray<UPrimitiveComponent*> Components(*Actor);
        for (auto* Component : Components)
        {
            if (!bGlass && Component->ComponentHasTag(TEXT("projectile_glass")))
            {
                auto* Glass = Cast<UInstancedStaticMeshComponent>(Component);
                FTransform Panel;
                if (!Glass || !Glass->GetInstanceTransform(2, Panel, true)) continue;
                const FVector Scale = Panel.GetScale3D();
                const FVector Normal = Panel.GetRotation().RotateVector(Scale.X < Scale.Y ? FVector::ForwardVector : FVector::RightVector);
                const FVector Start = Panel.GetLocation() + 25. * Normal;
                const FVector End = Panel.GetLocation() - 10. * Normal;
                if (FindHit(Start, End, Hit) && Hit.GetComponent() == Glass)
                    bGlass = Exercise(TEXT("perimeter_glass"), Start, End, Hit, false);
            }
            if (!bOutpost)
            {
                auto* Plate = Cast<UBoxComponent>(Component);
                if (!Plate) continue;
                bool bRed = false;
                FHitResult Binding;
                Binding.Component = Plate;
                Binding.bBlockingHit = true;
                Binding.ImpactNormal = Plate->GetForwardVector();
                if (!FieldVisuals.GetOutpostTarget(Binding, bRed) || bRed == bFriendlyRed) continue;
                const FVector Centre = Plate->GetComponentLocation();
                const FVector Normal = Plate->GetForwardVector();
                const FVector Start = Centre + 22. * Normal;
                const FVector End = Centre - 3. * Normal;
                if (FindHit(Start, End, Hit) && Hit.GetComponent() == Plate)
                    bOutpost = Exercise(TEXT("outpost_armor"), Start, End, Hit, true);
            }
        }
    }

    bool bSmallBase = false;
    if (IsValid(CollisionBaseActor) && CollisionBaseActor->GetStaticMeshComponent())
    {
        const FieldTeam Enemy = FRMUCFieldRules::Other(FriendlyTeam());
        const int32 Site = int32(FRMUCFieldRules::Index(Enemy));
        const FTransform Frame = CollisionBaseActor->GetStaticMeshComponent()->GetComponentTransform();
        const auto& Face = RMUCUpperBaseArmor::RedFaces[0];
        const FVector Centre = Frame.TransformPosition(RMUCUpperBaseArmor::TeamPoint(Face.Point, Site));
        const FVector Normal = Frame.TransformVectorNoScale(RMUCUpperBaseArmor::TeamNormal(Face.Normal, Site));
        const auto TestSmall = [&](const TCHAR* Label, const FVector& Direction, bool bDamage, double Speed, bool bExpectImmediateRemoval = false)
        {
            // Synthetic contact fixture inside UE. Exercise the same projectile integrator and referee.
            const FVector Start = Centre + 38. * Direction;
            const FVector End = Centre - 3. * Direction;
            FHitResult Contact;
            bool bRed = false, bLarge = false;
            const bool bIdentified = FindHit(Start, End, Contact) && FieldVisuals.GetBaseTarget(Contact, bRed, &bLarge)
                && bRed == (Enemy == FieldTeam::Red) && !bLarge;
            if (!bIdentified)
                UE_LOG(LogTemp, Warning, TEXT("RMUC_BASE_SMALL_PROOF case=%s recognized=0 component=%s"), Label, *GetNameSafe(Contact.GetComponent()));
            return bIdentified && Exercise(Label, Start, End, Contact, bDamage, Speed, bExpectImmediateRemoval);
        };
        const bool bShield = FieldRules.BaseProtected(Enemy) && TestSmall(TEXT("base_small_protected"), Normal, false, LaunchSpeedCmS);
        std::array<FRMUCFieldRules::FSiteActivation, 2> Fixtures{};
        Fixtures[Site].OutpostPosition = FRMUCFieldRules::FActivation{bFriendlyRed, !bFriendlyRed};
        FieldRules.Step(FRMUCFieldRules::ControlTransitionSeconds, MatchElapsedSeconds, Fixtures);
        // This fixture contacts the underside of the rim and the projectile is removed in the same step.
        const bool bRim = TestSmall(TEXT("base_small_rim"), Normal.GetSafeNormal2D(), false, LaunchSpeedCmS, true);
        const bool bWeak = TestSmall(TEXT("base_small_low_impact"), Normal, false, LaunchSpeedCmS * .4);
        const int32 Before = FieldRules.Get(Enemy).BaseHp;
        const bool bDirect = TestSmall(TEXT("base_small_effective"), Normal, true, LaunchSpeedCmS);
        const int32 Lost = Before - FieldRules.Get(Enemy).BaseHp;
        bSmallBase = !FieldRules.BaseProtected(Enemy) && !FieldRules.Get(Enemy).bBaseArmorOpen &&
            bShield && bRim && bWeak && bDirect && Lost == 20;
        UE_LOG(LogTemp, Display, TEXT("RMUC_BASE_SMALL_PROOF passed=%d shield=%d rim=%d low_impact=%d effective=%d hp_lost=%d"),
            bSmallBase, bShield, bRim, bWeak, bDirect, Lost);
    }

    const int32 ShotsBefore = Own().TotalShotsFired;
    FireProjectile(FTransform(FRotator(-65, 0, 0), Rig->GetActorLocation() + FVector(65, 0, 75)));
    const bool bAcceptedShot = Own().TotalShotsFired == ShotsBefore + 1 && Projectiles.Num() == 1;
    for (int32 Step = 0; Step < 180 && !Projectiles.IsEmpty(); ++Step) StepProjectiles(1. / 60.);
    UE_LOG(LogTemp, Display, TEXT("RMUC_PROJECTILE_PROOF floor=%d glass=%d vehicle_body=%d four_armor_faces=%d outpost=%d accepted_shot=%d"),
        bFloor, bGlass, bVehicleBody, bArmor, bOutpost, bAcceptedShot);
    return bFloor && bGlass && bVehicleBody && bArmor && bOutpost && bSmallBase && bAcceptedShot && Projectiles.IsEmpty();
}
#endif
