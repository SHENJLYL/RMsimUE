#include "RMUCFieldDeviceVisuals.h"
#include "RMUCArmorPresentation.h"
#include "RMUCUpperBaseArmor.h"

#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Components/BoxComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/HitResult.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Math/RotationMatrix.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "PhysicsEngine/BodySetup.h"
#include "ProceduralMeshComponent.h"
#include "Serialization/JsonSerializer.h"

namespace
{
// UE centimetres counterpart of the pinned CAD occurrence used in prepare_rmuc_rune.py.
const FVector RunePivot(0., 0., 259.9);
const FVector RuneAxis = FVector(1., 1., 0.).GetSafeNormal();
constexpr double PlateRadiusCm = 70.;
constexpr double PlateAxialCm = 33.1;
constexpr double PlateFaceRadiusCm = 15.;
const FName LampColorParameter(TEXT("RuneLampColor"));
const FName PaintTintParameter(TEXT("RunePaintTint"));
const FLinearColor RuneNeutralTrim(.035f, .035f, .035f, 1.f);
const FLinearColor RuneNeutralHub(.305f, .305f, .305f, 1.f);

const FLinearColor DartGuideGreen(.035f, 1.f, .12f);

struct FRuneSurfaceGeometry
{
    TArray<FVector> Vertices;
    TArray<int32> Triangles;
    FVector Root, Radial, Tangent;

    FVector Point(const FVector2D& V) const { return Root + V.X * Radial + V.Y * Tangent; }

    void AddQuad(const FVector2D& A, const FVector2D& B, const FVector2D& C, const FVector2D& D)
    {
        const int32 Start = Vertices.Num();
        Vertices.Append({Point(A), Point(B), Point(C), Point(D)});
        Triangles.Append({Start, Start + 1, Start + 2, Start, Start + 2, Start + 3});
    }

    void AddStrip(const FVector2D& Start, const FVector2D& End, double Width)
    {
        const FVector2D Direction = (End - Start).GetSafeNormal();
        const auto HalfWidth = .5 * Width * FVector2D(-Direction.Y, Direction.X);
        AddQuad(Start - HalfWidth, End - HalfWidth, End + HalfWidth, Start + HalfWidth);
    }

    void AddRing(double Inner, double Outer)
    {
        constexpr int32 Sides = 36;
        const FVector2D Center(PlateRadiusCm, 0.);
        for (int32 Index = 0; Index < Sides; ++Index)
        {
            const double A = Index * 2. * PI / Sides;
            const double B = (Index + 1) * 2. * PI / Sides;
            const FVector2D U(FMath::Cos(A), FMath::Sin(A));
            const FVector2D V(FMath::Cos(B), FMath::Sin(B));
            AddQuad(Center + Inner * U, Center + Outer * U, Center + Outer * V, Center + Inner * V);
        }
    }
};

struct FOutpostPlate
{
    FVector Point;
    FVector Normal;
    double HalfWidthCm;
    double HalfHeightCm;
};

// Source CAD 2603236 armor face planes, converted from ROS metres to UE centimetres.
const FOutpostPlate RedOutpostPlates[] = {
    {{296.3479, -365.6870, 146.3643}, {-0.482963,  0.843005, -0.236833}, 6.5082, 5.7620},
    {{296.2845, -413.3481, 155.3378}, {-0.482963, -0.829455, -0.280628}, 6.5081, 5.7672},
    {{337.3133, -389.0812, 135.5896}, { 0.965926,  0.006775, -0.258730}, 6.5442, 5.8385},
};

FVector OutpostPivot(int32 Team)
{
    const double Sign = Team == 0 ? 1. : -1.;
    return FVector(309.9616 * Sign, -389.4873 * Sign, 149.);
}

FVector OutpostAxis(int32 Team)
{
    return FVector(0., Team == 0 ? -0.02617695 : 0.02617695, 0.99965732).GetSafeNormal();
}

bool ReadBaseVector(const FJsonObject& Object, const TCHAR* Name, FVector& Vector)
{
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    return Object.TryGetArrayField(Name, Values) && Values->Num() == 3
        && (*Values)[0]->TryGetNumber(Vector.X)
        && (*Values)[1]->TryGetNumber(Vector.Y)
        && (*Values)[2]->TryGetNumber(Vector.Z)
        && !Vector.ContainsNaN();
}

bool HasDeviceQueryMesh(UStaticMesh* Mesh)
{
    UBodySetup* Body = Mesh ? Mesh->GetBodySetup() : nullptr;
    if (!Body || Body->CollisionTraceFlag != CTF_UseComplexAsSimple) return false;
    Body->CreatePhysicsMeshes();
    return !Body->TriMeshGeometries.IsEmpty() && !Body->bFailedToCreatePhysicsMeshes;
}

bool ReplaceFixedVisual(UStaticMeshComponent& Component, UStaticMesh& Mesh)
{
    if (Component.GetStaticMesh() == &Mesh) return true;
    // Registered static components reject SetStaticMesh after the world begins play.
    const bool bWasRegistered = Component.IsRegistered();
    if (bWasRegistered) Component.UnregisterComponent();
    Component.SetStaticMesh(&Mesh);
    if (bWasRegistered) Component.RegisterComponent();
    const bool bReplaced = Component.GetStaticMesh() == &Mesh && (!bWasRegistered || Component.IsRegistered());
    UE_LOG(LogTemp, Display, TEXT("RMUC_FIXED_VISUAL_REPLACED mesh=%s replaced=%d"), *Mesh.GetName(), bReplaced);
    return bReplaced;
}

void SetDeviceQueryResponses(UPrimitiveComponent& Component)
{
    Component.SetCollisionResponseToAllChannels(ECR_Ignore);
    Component.SetCollisionResponseToChannel(ECC_Visibility, ECR_Block);
    Component.SetCollisionResponseToChannel(ECC_GameTraceChannel2, ECR_Block);
    Component.SetCollisionEnabled(ECollisionEnabled::QueryOnly);
    Component.SetGenerateOverlapEvents(false);
    Component.SetCanEverAffectNavigation(false);
}

void DisableRestPoseQueries(UStaticMeshComponent& Original)
{
    // Keep chassis contact and navigation on the original physical reference.
    Original.SetCollisionResponseToChannel(ECC_Visibility, ECR_Ignore);
    Original.SetCollisionResponseToChannel(ECC_GameTraceChannel2, ECR_Ignore);
}

UStaticMeshComponent* BindFixedQuery(AStaticMeshActor* Owner, UStaticMesh* Mesh)
{
    if (!Owner || !HasDeviceQueryMesh(Mesh)) return nullptr;
    UStaticMeshComponent* Original = Owner->GetStaticMeshComponent();
    auto* Query = NewObject<UStaticMeshComponent>(Owner);
    Owner->AddInstanceComponent(Query);
    Query->SetMobility(Original->GetMobility());
    Query->SetupAttachment(Original);
    Query->SetStaticMesh(Mesh);
    Query->SetHiddenInGame(true);
    Query->SetCastShadow(false);
    Query->SetCollisionObjectType(Original->GetCollisionObjectType());
    SetDeviceQueryResponses(*Query);
    Query->RegisterComponent();
    return Query;
}
}

bool FRMUCFieldDeviceVisuals::Bind(AActor& Owner, AStaticMeshActor* CentrePlatform, AStaticMeshActor* CentreCollision)
{
    if (IsRuneReady()) return true;
    UWorld* World = Owner.GetWorld();
    UStaticMeshComponent* CentreMesh = CentrePlatform ? CentrePlatform->GetStaticMeshComponent() : nullptr;
    if (!World || !CentreMesh)
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_RUNE_UNAVAILABLE missing centre-platform actor"));
        return false;
    }
    UStaticMesh* Fixed = LoadObject<UStaticMesh>(nullptr, TEXT("/Game/RMSim/Maps/rmuc2027/Rune/rune_fixed_centre_platform.rune_fixed_centre_platform"));
    UStaticMesh* Rotor = LoadObject<UStaticMesh>(nullptr, TEXT("/Game/RMSim/Maps/rmuc2027/Rune/rune_rotor.rune_rotor"));
    if (!Fixed || !Rotor)
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_RUNE_UNAVAILABLE derived CAD assets missing; run tools/unreal/Prepare-RmucRune.ps1"));
        return false;
    }
    TArray<int32> Slots[2][NumPlates];
    int32 SourcePaintLayers = 0;
    for (int32 Team = 0; Team < 2; ++Team)
    {
        for (int32 Index = 0; Index < NumPlates; ++Index)
        {
            const FString Name = FString::Printf(TEXT("Rune%s%d"), Team == 0 ? TEXT("Red") : TEXT("Blue"), Index);
            const FString PaintPrefix = Name + TEXT("_M");
            const auto& Materials = Rotor->GetStaticMaterials();
            for (int32 Slot = 0; Slot < Materials.Num(); ++Slot)
            {
                const FString SlotName = Materials[Slot].MaterialSlotName.ToString();
                if (SlotName.StartsWith(PaintPrefix, ESearchCase::CaseSensitive) && Rotor->GetMaterial(Slot))
                {
                    Slots[Team][Index].Add(Slot);
                    ++SourcePaintLayers;
                }
            }
            if (Slots[Team][Index].IsEmpty())
            {
                const int32 Legacy = Rotor->GetMaterialIndex(FName(*Name));
                if (Legacy != INDEX_NONE && Rotor->GetMaterial(Legacy)) Slots[Team][Index].Add(Legacy);
            }
            if (Slots[Team][Index].IsEmpty())
            {
                UE_LOG(LogTemp, Warning, TEXT("RMUC_RUNE_UNAVAILABLE derived rotor missing %s material slots"), *Name);
                return false;
            }
        }
    }

    FActorSpawnParameters Spawn;
    Spawn.Owner = &Owner;
    auto* Actor = World->SpawnActor<AStaticMeshActor>(FVector::ZeroVector, FRotator::ZeroRotator, Spawn);
    if (!Actor) return false;
    Actor->Tags.Add(FName(TEXT("rmuc_rune")));
    UStaticMeshComponent* RotorMesh = Actor->GetStaticMeshComponent();
    RotorMesh->SetMobility(EComponentMobility::Movable);
    RotorMesh->SetCollisionProfileName(TEXT("NoCollision"));
    RotorMesh->SetStaticMesh(Rotor);
    // Older rotor packages include inboard pieces that still use the CAD team paint.
    if (auto* Neutral = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/RMSim/Maps/rmuc2027/Rune/RuneLamp.RuneLamp")))
    {
        for (const FName SlotName : {FName(TEXT("RM27_M00")), FName(TEXT("RM27_M02")), FName(TEXT("RM27_M07"))})
            if (const int32 Slot = Rotor->GetMaterialIndex(SlotName); Slot != INDEX_NONE)
                RotorMesh->SetMaterial(Slot, Neutral);
    }
    if (HasDeviceQueryMesh(Rotor)) SetDeviceQueryResponses(*RotorMesh);
    else UE_LOG(LogTemp, Warning, TEXT("RMUC_RUNE_QUERY_UNAVAILABLE rebuild the derived rotor asset"));
    RotorMesh->AttachToComponent(CentreMesh, FAttachmentTransformRules::KeepRelativeTransform);
    RotorMesh->SetRelativeLocation(RunePivot);
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const double Side = Team == 0 ? 1. : -1.;
        for (int32 Index = 0; Index < NumPlates; ++Index)
        {
            Lamps[Team][Index].Reset();
            for (const int32 Slot : Slots[Team][Index])
            {
                UMaterialInstanceDynamic* Lamp = RotorMesh->CreateDynamicMaterialInstance(Slot);
                if (!Lamp)
                {
                    Actor->Destroy();
                    UE_LOG(LogTemp, Warning, TEXT("RMUC_RUNE_UNAVAILABLE failed to create rotor lamp materials"));
                    return false;
                }
                Lamp->SetVectorParameterValue(PaintTintParameter, RuneNeutralTrim);
                Lamps[Team][Index].Add(Lamp);
            }
            const FQuat AboutAxis(RuneAxis, Index * (2. * PI / NumPlates));
            auto* Plate = NewObject<UBoxComponent>(Actor);
            Actor->AddInstanceComponent(Plate);
            Plate->SetMobility(EComponentMobility::Movable);
            Plate->SetupAttachment(RotorMesh);
            Plate->SetRelativeLocation(Side * PlateAxialCm * RuneAxis + AboutAxis.RotateVector(FVector(0., 0., PlateRadiusCm)));
            Plate->SetRelativeRotation(AboutAxis * FQuat(FVector::UpVector, PI / 4.));
            Plate->SetBoxExtent(FVector(1.25, PlateFaceRadiusCm, PlateFaceRadiusCm));
            Plate->SetCollisionObjectType(ECC_WorldDynamic);
            SetDeviceQueryResponses(*Plate);
            Plate->RegisterComponent();
            Plates[Team][Index] = Plate;
        }
    }
    UStaticMesh* FixedWithOutposts = BindOutposts(*Actor, *CentreMesh);
    if (!ReplaceFixedVisual(*CentreMesh, *(FixedWithOutposts ? FixedWithOutposts : Fixed)))
    {
        Actor->Destroy();
        UE_LOG(LogTemp, Warning, TEXT("RMUC_RUNE_UNAVAILABLE fixed centre platform swap failed"));
        return false;
    }
    int32 BoundHubLamps = 0;
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const FName Name(Team == 0 ? TEXT("RuneHubRed") : TEXT("RuneHubBlue"));
        if (const int32 Slot = CentreMesh->GetMaterialIndex(Name); Slot != INDEX_NONE)
        {
            if (auto* Lamp = CentreMesh->CreateDynamicMaterialInstance(Slot))
            {
                Lamp->SetVectorParameterValue(PaintTintParameter, RuneNeutralHub);
                Lamp->SetVectorParameterValue(LampColorParameter, FLinearColor::Black);
                RuneHubLamps[Team] = Lamp;
                RuneHubSceneLights[Team] = RMUCArmorPresentation::AddPointLight(*Actor, *CentreMesh,
                    RunePivot + (Team == 0 ? 55. : -55.) * RuneAxis, RMUCArmorPresentation::TeamColor(Team == 0), 95.f);
                ++BoundHubLamps;
            }
        }
    }
    UE_LOG(LogTemp, Display, TEXT("RMUC_RUNE_HUB_LAMPS lamps=%d"), BoundHubLamps);
    BindCentreVisibility(CentreCollision, FixedWithOutposts != nullptr);
    Platform = CentrePlatform;
    Rune = RotorMesh;
    auto* TrackMaterial = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/RMSim/Maps/rmuc2027/Rune/RuneTrackGlow.RuneTrackGlow"));
    if (!TrackMaterial)
        UE_LOG(LogTemp, Warning, TEXT("RMUC_RUNE_LIGHT_MATERIAL_MISSING run Prepare-RmucRune.ps1 -RuneLightsOnly"));
    const int32 TrackSections = TrackMaterial ? BindRuneLights(*Actor, *RotorMesh, *TrackMaterial) : 0;
    RuneLightSeconds = -1.;
    UpdateRune(0., 0., {}, {});
    UE_LOG(LogTemp, Display, TEXT("RMUC_RUNE_APPEARANCE source_paint_layers=%d idle_emission=off neutral_trim=1"), SourcePaintLayers);
    UE_LOG(LogTemp, Display, TEXT("RMUC_RUNE_LIGHT_TRACKS sections=%d expected=%d collision=off"),
        TrackSections, 2 * NumPlates * RuneSectionCount);
    if (FParse::Param(FCommandLine::Get(), TEXT("RefereeFieldDeviceSelfTest"))) VerifyTargetQueries(Owner);
    UE_LOG(LogTemp, Display, TEXT("RMUC_RUNE_BOUND source=1354629 plate_count=%d pivot=%s"), 2 * NumPlates, *RunePivot.ToString());
    return true;
}

const UPrimitiveComponent* FRMUCFieldDeviceVisuals::GetCentreVisibilityComponent() const
{
    return CentreVisibility.Get();
}

bool FRMUCFieldDeviceVisuals::VerifyTargetQueries(AActor& Owner)
{
    UWorld* World = Owner.GetWorld();
    if (!World || !IsRuneReady() || !IsCentreVisibilitySplitReady())
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_FIELD_DEVICE_SELF_TEST failed=visibility_split_unavailable"));
        return false;
    }
    const bool bOutposts = OutpostsAreReady();
    const double SavedRune = RuneAngleRad;
    const FQuat SavedOutposts[2] = {
        bOutposts ? Outposts[0]->GetRelativeTransform().GetRotation() : FQuat::Identity,
        bOutposts ? Outposts[1]->GetRelativeTransform().GetRotation() : FQuat::Identity,
    };
    FCollisionQueryParams Params(SCENE_QUERY_STAT(RMSimFieldTargetSelfTest), true, &Owner);
    const auto Sweep = [&](const UBoxComponent& Plate, double Direction, const FVector2D& Offset, FHitResult& Hit)
    {
        const FVector Extent = Plate.GetScaledBoxExtent();
        const FVector Location = Plate.GetComponentLocation()
            + Offset.X * Extent.Y * Plate.GetRightVector() + Offset.Y * Extent.Z * Plate.GetUpVector();
        const FVector Normal = Plate.GetForwardVector() * Direction;
        const FVector Start = Location + 35. * Normal;
        const FVector End = Location - 4. * Normal;
        if (Offset.IsNearlyZero())
        {
            FHitResult AimHit;
            if (!World->LineTraceSingleByChannel(AimHit, Start, End, ECC_Visibility, Params)
                || AimHit.GetComponent() != &Plate) return false;
        }
        return World->SweepSingleByChannel(Hit, Start, End, FQuat::Identity, ECC_GameTraceChannel2,
            FCollisionShape::MakeSphere(.85f), Params);
    };
    int32 Passed = 0;
    int32 Tests = 0;
    for (const double Angle : {0.0, 0.37})
    {
        Rune->SetRelativeRotation(FQuat(RuneAxis, Angle));
        UpdateOutposts(Angle, -Angle);
        for (int32 Team = 0; Team < 2; ++Team)
        {
            for (int32 Index = 0; Index < NumPlates; ++Index)
            {
                const UBoxComponent* Plate = Plates[Team][Index].Get();
                for (const FVector2D& Offset : {FVector2D(0.), FVector2D(.90, 0.), FVector2D(-.90, 0.), FVector2D(.80, .80)})
                {
                    FHitResult Hit;
                    bool bRed = false;
                    int32 FoundIndex = INDEX_NONE;
                    const bool bExpected = Offset.SizeSquared() < 1.;
                    const bool bScored = Plate && Sweep(*Plate, Team == 0 ? 1. : -1., Offset, Hit)
                        && GetRuneTarget(Hit, bRed, FoundIndex) && bRed == (Team == 0) && FoundIndex == Index;
                    const bool bPassed = Plate && bScored == bExpected;
                    ++Tests;
                    Passed += bPassed ? 1 : 0;
                    if (!bPassed) UE_LOG(LogTemp, Warning, TEXT("RMUC_FIELD_DEVICE_PROBE_MISS rune team=%d face=%d angle=%.3f offset=%s actor=%s"),
                        Team, Index, Angle, *Offset.ToString(), *GetNameSafe(Hit.GetActor()));
                }
            }
            if (!bOutposts) continue;
            for (int32 Index = 0; Index < OutpostPlatesPerTeam; ++Index)
            {
                const UBoxComponent* Plate = OutpostPlates[Team][Index].Get();
                for (const FVector2D& Offset : {FVector2D(0.), FVector2D(.90, 0.), FVector2D(-.90, 0.), FVector2D(0., 1.40)})
                {
                    FHitResult Hit;
                    bool bRed = false;
                    const bool bExpected = Offset.SizeSquared() < 1.;
                    const bool bScored = Plate && Sweep(*Plate, 1., Offset, Hit) && Hit.GetComponent() == Plate
                        && GetOutpostTarget(Hit, bRed) && bRed == (Team == 0);
                    const bool bPassed = Plate && bScored == bExpected;
                    ++Tests;
                    Passed += bPassed ? 1 : 0;
                    if (!bPassed) UE_LOG(LogTemp, Warning, TEXT("RMUC_FIELD_DEVICE_PROBE_MISS outpost team=%d face=%d angle=%.3f offset=%s actor=%s"),
                        Team, Index, Angle, *Offset.ToString(), *GetNameSafe(Hit.GetActor()));
                }
            }
        }
    }
    Rune->SetRelativeRotation(FQuat(RuneAxis, SavedRune));
    if (bOutposts)
    {
        Outposts[0]->SetRelativeRotation(SavedOutposts[0]);
        Outposts[1]->SetRelativeRotation(SavedOutposts[1]);
    }
    UE_LOG(LogTemp, Display, TEXT("RMUC_FIELD_DEVICE_SELF_TEST passed=%d tests=%d"), Passed, Tests);
    return Passed == Tests;
}

void FRMUCFieldDeviceVisuals::BindCentreVisibility(AStaticMeshActor* CentreCollision, bool bIncludesOutposts)
{
    UStaticMeshComponent* Original = CentreCollision ? CentreCollision->GetStaticMeshComponent() : nullptr;
    if (!Original || Original->GetCollisionResponseToChannel(ECC_Visibility) != ECR_Block)
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_CENTRE_VISIBILITY_UNAVAILABLE missing original blocking component"));
        return;
    }
    const TCHAR* Path = bIncludesOutposts
        ? TEXT("/Game/RMSim/Maps/rmuc2027/Visibility/centre_rune_outposts_visibility.centre_rune_outposts_visibility")
        : TEXT("/Game/RMSim/Maps/rmuc2027/Visibility/centre_rune_visibility.centre_rune_visibility");
    UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, Path, nullptr, LOAD_NoWarn);
    UStaticMeshComponent* QueryMesh = BindFixedQuery(CentreCollision, Mesh);
    if (!QueryMesh)
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_CENTRE_VISIBILITY_UNAVAILABLE derived complex query asset unavailable"));
        return;
    }

    const FTransform& Frame = Original->GetComponentTransform();
    FCollisionQueryParams Params(SCENE_QUERY_STAT(RMSimCentreVisibilitySplit), true);
    const FVector DeckStart = Frame.TransformPosition(FVector(150., 0., 200.));
    const FVector DeckEnd = Frame.TransformPosition(FVector(150., 0., -50.));
    const FVector RestPlateStart = Frame.TransformPosition(FVector(58.8, 58.8, 329.9));
    const FVector RestPlateEnd = Frame.TransformPosition(FVector(17., 17., 329.9));
    FHitResult OriginalDeck, FixedDeck, OriginalPlate, FixedPlate;
    const bool bDeckPreserved = Original->LineTraceComponent(OriginalDeck, DeckStart, DeckEnd, Params)
        && QueryMesh->LineTraceComponent(FixedDeck, DeckStart, DeckEnd, Params)
        && OriginalDeck.ImpactPoint.Equals(FixedDeck.ImpactPoint, .5);
    const bool bRestPlateRemoved = Original->LineTraceComponent(OriginalPlate, RestPlateStart, RestPlateEnd, Params)
        && !QueryMesh->LineTraceComponent(FixedPlate, RestPlateStart, RestPlateEnd, Params);
    if (!bDeckPreserved || !bRestPlateRemoved)
    {
        QueryMesh->DestroyComponent();
        UE_LOG(LogTemp, Warning, TEXT("RMUC_CENTRE_VISIBILITY_UNAVAILABLE component checks deck=%d removed_rest_plate=%d"),
            bDeckPreserved, bRestPlateRemoved);
        return;
    }

    DisableRestPoseQueries(*Original);
    CentreVisibility = QueryMesh;
    UE_LOG(LogTemp, Display, TEXT("RMUC_CENTRE_VISIBILITY_BOUND outposts=%d deck_preserved=1 rune_rest_plate_removed=1"),
        bIncludesOutposts);
}

UStaticMesh* FRMUCFieldDeviceVisuals::BindOutposts(AActor& DeviceOwner, UStaticMeshComponent& CentreMesh)
{
    UStaticMesh* Fixed = LoadObject<UStaticMesh>(nullptr,
        TEXT("/Game/RMSim/Maps/rmuc2027/RuneOutposts/rune_outposts_fixed_centre_platform.rune_outposts_fixed_centre_platform"),
        nullptr, LOAD_NoWarn);
    UStaticMesh* Rotors[] = {
        LoadObject<UStaticMesh>(nullptr, TEXT("/Game/RMSim/Maps/rmuc2027/RuneOutposts/outpost_red_rotor.outpost_red_rotor"), nullptr, LOAD_NoWarn),
        LoadObject<UStaticMesh>(nullptr, TEXT("/Game/RMSim/Maps/rmuc2027/RuneOutposts/outpost_blue_rotor.outpost_blue_rotor"), nullptr, LOAD_NoWarn),
    };
    if (!Fixed || !Rotors[0] || !Rotors[1])
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_OUTPOST_VISUALS_UNAVAILABLE derived CAD assets missing; run tools/unreal/Prepare-RmucRune.ps1 -OutpostsOnly"));
        return nullptr;
    }
    UMaterialInterface* MarkerMaterial = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/RMSim/Maps/rmuc2027/Rune/RuneLamp.RuneLamp"), nullptr, LOAD_NoWarn);
    UTexture2D* Sticker = RMUCArmorPresentation::LoadSticker(TEXT("outpost"));
    for (int32 Team = 0; Team < 2; ++Team)
    {
        UStaticMeshComponent* Rotor = NewObject<UStaticMeshComponent>(&DeviceOwner);
        DeviceOwner.AddInstanceComponent(Rotor);
        Rotor->SetMobility(EComponentMobility::Movable);
        Rotor->SetupAttachment(&CentreMesh);
        Rotor->SetRelativeLocation(OutpostPivot(Team));
        Rotor->SetCollisionProfileName(TEXT("NoCollision"));
        Rotor->SetStaticMesh(Rotors[Team]);
        if (HasDeviceQueryMesh(Rotors[Team])) SetDeviceQueryResponses(*Rotor);
        else UE_LOG(LogTemp, Warning, TEXT("RMUC_OUTPOST_QUERY_UNAVAILABLE team=%d rebuild the derived rotor asset"), Team);
        Rotor->RegisterComponent();
        Outposts[Team] = Rotor;
        OutpostFaceLights[Team].Reset();
        OutpostGuideLights[Team] = RMUCArmorPresentation::AddPointLight(DeviceOwner, CentreMesh,
            OutpostPivot(Team) + FVector(0, 0, 68), DartGuideGreen, 68.f);
        OutpostMarkers[Team].Reset();
        if (MarkerMaterial)
        {
            // Both source occurrences use identical mesh colors. These front trim sections carry the team marker.
            for (const TCHAR* Section : {TEXT("RM27_M00"), TEXT("RM27_M07")})
            {
                const int32 Slot = Rotors[Team]->GetMaterialIndex(FName(Section));
                if (Slot != INDEX_NONE)
                    if (auto* Marker = Rotor->CreateDynamicMaterialInstance(Slot, MarkerMaterial))
                        OutpostMarkers[Team].Add(Marker);
            }
        }

        const FVector Mirror(Team == 0 ? 1. : -1., Team == 0 ? 1. : -1., 1.);
        UMaterialInstanceDynamic* ArmorLamp = RMUCArmorPresentation::MakeFinish(DeviceOwner, Sticker);
        int32 MountedArmor = 0;
        for (int32 Index = 0; Index < OutpostPlatesPerTeam; ++Index)
        {
            const FOutpostPlate& Face = RedOutpostPlates[Index];
            const FVector Normal = Face.Normal * Mirror;
            // Clearance above the CAD face includes the raised cover, measured at up to 3.45 mm.
            if (auto* Cover = RMUCArmorPresentation::Mount(DeviceOwner, *Rotor,
                Face.Point * Mirror - OutpostPivot(Team), Normal, .40,
                RMUCArmorPresentation::OutpostTag, ArmorLamp))
            {
                ++MountedArmor;
                OutpostFaceLights[Team].Add(RMUCArmorPresentation::AddPointLight(DeviceOwner, *Cover,
                    FVector(7., 0., 0.), RMUCArmorPresentation::TeamColor(Team == 0), 58.f));
            }
            UBoxComponent* Plate = NewObject<UBoxComponent>(&DeviceOwner);
            DeviceOwner.AddInstanceComponent(Plate);
            Plate->SetMobility(EComponentMobility::Movable);
            Plate->SetupAttachment(Rotor);
            Plate->SetRelativeLocation(Face.Point * Mirror - OutpostPivot(Team) + Normal * .7);
            Plate->SetRelativeRotation(FRotationMatrix::MakeFromXZ(Normal, FVector::UpVector).ToQuat());
            Plate->SetBoxExtent(FVector(.9, Face.HalfWidthCm, Face.HalfHeightCm));
            Plate->SetCollisionObjectType(ECC_WorldDynamic);
            SetDeviceQueryResponses(*Plate);
            Plate->RegisterComponent();
            OutpostPlates[Team][Index] = Plate;
        }
        OutpostArmorLamps[Team] = MountedArmor == OutpostPlatesPerTeam ? ArmorLamp : nullptr;
        UE_LOG(LogTemp, Display, TEXT("RMUC_ARMOR_PRESENTATION kind=outpost site=%d faces=%d"), Team, MountedArmor);
    }
    bOutpostAffiliationInitialized = false;
    UpdateOutpostAffiliation(true, false, true, true);
    UE_LOG(LogTemp, Display, TEXT("RMUC_OUTPOST_VISUALS_BOUND source=2603236 plates=6 collision=static_cad_plus_query_faces"));
    return Fixed;
}

void FRMUCFieldDeviceVisuals::UpdateOutposts(double RedAngleRad, double BlueAngleRad)
{
    const double Angles[] = {RedAngleRad, BlueAngleRad};
    for (int32 Team = 0; Team < 2; ++Team)
    {
        if (UStaticMeshComponent* Rotor = Outposts[Team].Get(); Rotor && FMath::IsFinite(Angles[Team]))
        {
            Rotor->SetRelativeRotation(FQuat(OutpostAxis(Team), -Angles[Team]));
        }
    }
}

void FRMUCFieldDeviceVisuals::UpdateOutpostAffiliation(bool bRedSiteOwnerRed, bool bBlueSiteOwnerRed, bool bRedSiteAlive, bool bBlueSiteAlive)
{
    if (!OutpostsAreReady()) return;
    const bool OwnerRed[] = {bRedSiteOwnerRed, bBlueSiteOwnerRed};
    const bool Alive[] = {bRedSiteAlive, bBlueSiteAlive};
    for (int32 Site = 0; Site < 2; ++Site)
    {
        if (bOutpostAffiliationInitialized && OutpostOwnerRed[Site] == OwnerRed[Site] && OutpostAlive[Site] == Alive[Site]) continue;
        OutpostOwnerRed[Site] = OwnerRed[Site];
        OutpostAlive[Site] = Alive[Site];
        RMUCArmorPresentation::SetLamp(OutpostArmorLamps[Site].Get(), OwnerRed[Site], Alive[Site]);
        const FLinearColor Color = RMUCArmorPresentation::TeamColor(OwnerRed[Site]);
        for (const auto& Light : OutpostFaceLights[Site])
            RMUCArmorPresentation::SetPointLight(Light.Get(), Color, Alive[Site] ? 2.55f : 0.f);
        RMUCArmorPresentation::SetPointLight(OutpostGuideLights[Site].Get(), DartGuideGreen, Alive[Site] ? 4.5f : 0.f);
        for (const auto& Candidate : OutpostMarkers[Site])
        {
            if (auto* Marker = Candidate.Get())
            {
                Marker->SetVectorParameterValue(PaintTintParameter, Alive[Site] ? Color * .55f : RuneNeutralTrim);
                Marker->SetVectorParameterValue(LampColorParameter, Color * (Alive[Site] ? 1.5f : 0.f));
            }
        }
        UE_LOG(LogTemp, Display, TEXT("RMUC_OUTPOST_APPEARANCE site=%s owner=%s alive=%d marker_slots=%d visible=%d"),
            Site == 0 ? TEXT("red") : TEXT("blue"), OwnerRed[Site] ? TEXT("red") : TEXT("blue"), Alive[Site],
            OutpostMarkers[Site].Num(), Outposts[Site]->IsVisible());
    }
    bOutpostAffiliationInitialized = true;
}

bool FRMUCFieldDeviceVisuals::GetOutpostTarget(const FHitResult& Hit, bool& bRed) const
{
    const UPrimitiveComponent* Component = Hit.GetComponent();
    if (!Component || !Hit.bBlockingHit || Hit.bStartPenetrating) return false;
    for (int32 Team = 0; Team < 2; ++Team)
    {
        for (const auto& Candidate : OutpostPlates[Team])
        {
            const UBoxComponent* Plate = Candidate.Get();
            if (Plate == Component && FVector::DotProduct(Hit.ImpactNormal, Plate->GetForwardVector()) > .8)
            {
                bRed = Team == 0;
                return true;
            }
        }
    }
    return false;
}

bool FRMUCFieldDeviceVisuals::UndoOutpostRotation(bool bRed, const FVector& ImpactPoint, const FVector& ImpactNormal,
    FVector& RestPoint, FVector& RestNormal) const
{
    const int32 Team = bRed ? 0 : 1;
    const UStaticMeshComponent* Rotor = Outposts[Team].Get();
    if (!Rotor || !Rotor->GetAttachParent()) return false;
    const FTransform& Rotated = Rotor->GetComponentTransform();
    const FTransform& Parent = Rotor->GetAttachParent()->GetComponentTransform();
    RestPoint = Parent.TransformPosition(Rotated.InverseTransformPosition(ImpactPoint) + OutpostPivot(Team));
    RestNormal = Parent.TransformVectorNoScale(Rotated.InverseTransformVectorNoScale(ImpactNormal));
    return true;
}

bool FRMUCFieldDeviceVisuals::BindDartGates(AActor& Owner, AStaticMeshActor* RedDartStation, AStaticMeshActor* BlueDartStation,
    AStaticMeshActor* RedDartCollision, AStaticMeshActor* BlueDartCollision)
{
    if (AreDartGatesReady()) return true;
    if (!RedDartStation || !BlueDartStation)
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_DART_VISUALS_UNAVAILABLE missing station actors"));
        return false;
    }
    UStaticMeshComponent* Stations[] = {RedDartStation->GetStaticMeshComponent(), BlueDartStation->GetStaticMeshComponent()};
    AStaticMeshActor* Collisions[] = {RedDartCollision, BlueDartCollision};
    const FString GuidePath = FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir(), TEXT("../../assets/maps/rmuc2027/dart-gate-kinematics.json"));
    FString GuideJson;
    TSharedPtr<FJsonObject> Guide;
    FVector Rail;
    double Stroke = 0., TrayStroke = 0.;
    if (!FFileHelper::LoadFileToString(GuideJson, *GuidePath) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(GuideJson), Guide) || !Guide ||
        !ReadBaseVector(*Guide, TEXT("rail_axis_red"), Rail) || !Rail.IsUnit() ||
        !Guide->TryGetNumberField(TEXT("travel_m"), Stroke) || Stroke <= 0. || Stroke > 1.50 ||
        !Guide->TryGetNumberField(TEXT("tray_extension_m"), TrayStroke) || TrayStroke <= 0. || TrayStroke > 1.05)
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_DART_VISUALS_UNAVAILABLE invalid guide data: %s"), *GuidePath);
        return false;
    }
    DartRedGateClosedCm = -Stroke * Rail * FVector(100., -100., 100.);
    DartTrayTravelCm = 100. * TrayStroke;
    UStaticMesh* Fixed[2];
    UStaticMesh* Moving[2];
    UStaticMesh* Trays[2];
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const TCHAR* TeamName = Team == 0 ? TEXT("red") : TEXT("blue");
        const FString FixedPath = FString::Printf(TEXT("/Game/RMSim/Maps/rmuc2027/Dart/dart_%s_fixed.dart_%s_fixed"), TeamName, TeamName);
        const FString MovingPath = FString::Printf(TEXT("/Game/RMSim/Maps/rmuc2027/Dart/dart_%s_gate.dart_%s_gate"), TeamName, TeamName);
        Fixed[Team] = LoadObject<UStaticMesh>(nullptr, *FixedPath);
        Moving[Team] = LoadObject<UStaticMesh>(nullptr, *MovingPath);
        const FString TrayPath = FString::Printf(TEXT("/Game/RMSim/Maps/rmuc2027/Dart/dart_%s_tray.dart_%s_tray"), TeamName, TeamName);
        Trays[Team] = LoadObject<UStaticMesh>(nullptr, *TrayPath);
        if (!Stations[Team] || !Fixed[Team] || !Moving[Team] || !Trays[Team])
        {
            UE_LOG(LogTemp, Warning, TEXT("RMUC_DART_VISUALS_UNAVAILABLE derived station assets missing; run tools/unreal/Prepare-RmucRune.ps1 -DartOnly"));
            return false;
        }
    }
    UStaticMesh* RedOriginal = Stations[0]->GetStaticMesh();
    if (!ReplaceFixedVisual(*Stations[0], *Fixed[0])) return false;
    if (!ReplaceFixedVisual(*Stations[1], *Fixed[1]))
    {
        if (RedOriginal) ReplaceFixedVisual(*Stations[0], *RedOriginal);
        return false;
    }
    const FString LauncherDescription = FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir(), TEXT("../../assets/maps/rmuc2027/dart-launcher/mount.json"));
    FString LauncherJson;
    TSharedPtr<FJsonObject> LauncherMount;
    FVector RedOrigin, RedForward, RedUp, ExitOffset;
    double LauncherScale = 0.;
    UStaticMesh* Launcher = nullptr;
    if (FFileHelper::LoadFileToString(LauncherJson, *LauncherDescription) &&
        FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(LauncherJson), LauncherMount) && LauncherMount &&
        ReadBaseVector(*LauncherMount, TEXT("red_origin_m"), RedOrigin) &&
        ReadBaseVector(*LauncherMount, TEXT("red_forward"), RedForward) && RedForward.IsUnit() &&
        ReadBaseVector(*LauncherMount, TEXT("red_up"), RedUp) && RedUp.IsUnit() &&
        ReadBaseVector(*LauncherMount, TEXT("source_exit_offset_m"), ExitOffset) &&
        LauncherMount->TryGetNumberField(TEXT("scale"), LauncherScale) && LauncherScale > 0. && LauncherScale <= 1.)
    {
        FString MeshAsset;
        if (!LauncherMount->TryGetStringField(TEXT("mesh_asset"), MeshAsset))
            MeshAsset = TEXT("/Game/RMSim/Maps/rmuc2027/Dart/dart_launcher_duxing_cad.dart_launcher_duxing_cad");
        Launcher = LoadObject<UStaticMesh>(nullptr, *MeshAsset);
    }
    if (!Launcher)
        UE_LOG(LogTemp, Warning, TEXT("RMUC_DART_LAUNCHER_UNAVAILABLE mount=%s"), *LauncherDescription);

    FVector SpacerSizeM;
    TArray<FVector> RedSpacerCentres;
    const TSharedPtr<FJsonObject>* SpacerSpec = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Centres = nullptr;
    if (Launcher && LauncherMount->TryGetObjectField(TEXT("spacer_pads"), SpacerSpec) && SpacerSpec &&
        ReadBaseVector(**SpacerSpec, TEXT("size_m"), SpacerSizeM) && SpacerSizeM.GetMin() > 0. &&
        (*SpacerSpec)->TryGetArrayField(TEXT("red_centres"), Centres))
    {
        for (const auto& Centre : *Centres)
        {
            const TSharedPtr<FJsonObject>* Entry = nullptr;
            FVector Position;
            if (Centre->TryGetObject(Entry) && Entry && ReadBaseVector(**Entry, TEXT("centre_m"), Position))
                RedSpacerCentres.Add(Position);
        }
    }
    auto* SpacerMesh = RedSpacerCentres.IsEmpty() ? nullptr :
        LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));

    for (int32 Team = 0; Team < 2; ++Team)
    {
        auto* Gate = NewObject<UStaticMeshComponent>(&Owner);
        Owner.AddInstanceComponent(Gate);
        Gate->SetMobility(EComponentMobility::Movable);
        Gate->SetupAttachment(Stations[Team]);
        Gate->SetCollisionProfileName(TEXT("NoCollision"));
        Gate->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Gate->SetStaticMesh(Moving[Team]);
        if (HasDeviceQueryMesh(Moving[Team]))
        {
            if (BindFixedQuery(Collisions[Team], Fixed[Team]))
            {
                DisableRestPoseQueries(*Collisions[Team]->GetStaticMeshComponent());
                SetDeviceQueryResponses(*Gate);
            }
            else
                UE_LOG(LogTemp, Warning, TEXT("RMUC_DART_FIXED_QUERY_UNAVAILABLE team=%d retained original station queries"), Team);
        }
        else
            UE_LOG(LogTemp, Warning, TEXT("RMUC_DART_GATE_QUERY_UNAVAILABLE team=%d rebuild derived dart assets"), Team);
        Gate->RegisterComponent();
        DartGates[Team] = Gate;

        auto* Tray = NewObject<UStaticMeshComponent>(&Owner);
        Owner.AddInstanceComponent(Tray);
        Tray->SetMobility(EComponentMobility::Movable);
        Tray->SetupAttachment(Stations[Team]);
        Tray->SetCollisionProfileName(TEXT("NoCollision"));
        Tray->SetStaticMesh(Trays[Team]);
        if (HasDeviceQueryMesh(Trays[Team])) SetDeviceQueryResponses(*Tray);
        Tray->SetCanEverAffectNavigation(false);
        Tray->RegisterComponent();
        DartTrays[Team] = Tray;

        if (!Launcher) continue;
        // The display and its spacer pads travel with the tray independently of the door.
        const double Sign = Team == 0 ? 1. : -1.;
        const FVector ToUnreal(Sign, -Sign, 1.);
        auto* Display = NewObject<UStaticMeshComponent>(&Owner);
        Owner.AddInstanceComponent(Display);
        Display->SetMobility(EComponentMobility::Movable);
        Display->SetupAttachment(Tray);
        Display->SetCollisionProfileName(TEXT("NoCollision"));
        Display->SetCanEverAffectNavigation(false);
        Display->SetStaticMesh(Launcher);
        Display->SetRelativeTransform(FTransform(
            FRotationMatrix::MakeFromXZ(RedForward * ToUnreal, RedUp * ToUnreal).Rotator(),
            RedOrigin * ToUnreal * 100., FVector(LauncherScale)));
        Display->RegisterComponent();
        DartLaunchers[Team] = Display;
        if (SpacerMesh)
        {
            for (const FVector& Centre : RedSpacerCentres)
            {
                auto* Spacer = NewObject<UStaticMeshComponent>(&Owner);
                Owner.AddInstanceComponent(Spacer);
                Spacer->SetMobility(EComponentMobility::Movable);
                Spacer->SetupAttachment(Tray);
                Spacer->SetCollisionProfileName(TEXT("NoCollision"));
                Spacer->SetCanEverAffectNavigation(false);
                Spacer->SetStaticMesh(SpacerMesh);
                Spacer->SetMaterial(0, Launcher->GetMaterial(0));
                Spacer->SetRelativeTransform(FTransform(Display->GetRelativeRotation(), Centre * ToUnreal * 100., SpacerSizeM));
                Spacer->RegisterComponent();
            }
            UE_LOG(LogTemp, Display, TEXT("RMUC_DART_DISPLAY_SPACERS team=%s count=%d"),
                Team == 0 ? TEXT("red") : TEXT("blue"), RedSpacerCentres.Num());
        }
        DartLauncherExitCm = ExitOffset * FVector(100., -100., 100.);
        UE_LOG(LogTemp, Display, TEXT("RMUC_DART_LAUNCHER_BOUND team=%s asset=%s location=%s size_cm=%s scale=%.3f"),
            Team == 0 ? TEXT("red") : TEXT("blue"), *Launcher->GetPathName(), *Display->GetComponentLocation().ToCompactString(),
            *(Launcher->GetBoundingBox().GetSize() * LauncherScale).ToCompactString(), LauncherScale);
    }
    UpdateDartGates(0., 0.);
    UE_LOG(LogTemp, Display, TEXT("RMUC_DART_VISUALS_BOUND source=3749800 gate_count=2 tray_count=2 guide_stroke_m=%.5f tray_stroke_m=%.3f collision=static_drive_and_moving_query"), Stroke, TrayStroke);
    return true;
}

bool FRMUCFieldDeviceVisuals::GetDartSceneAnchors(bool bRedShooter, bool bOutpost, FVector& Exit, FVector& Detector) const
{
    const int32 Shooter = bRedShooter ? 0 : 1;
    const int32 Site = 1 - Shooter;
    const auto* Gate = DartGates[Shooter].Get();
    if (!Gate) return false;
    const double ShooterInward = bRedShooter ? -1. : 1.;
    if (const auto* Launcher = GetDartLauncher(bRedShooter))
        Exit = Launcher->GetComponentTransform().TransformPosition(DartLauncherExitCm);
    else
        Exit = Gate->Bounds.Origin + FVector(ShooterInward * 42., 0., 12.);
    if (bOutpost)
    {
        auto* Tower = Outposts[Site].Get();
        if (!Tower) return false;
        const auto& Bounds = Tower->Bounds;
        const double Top = Bounds.Origin.Z + Bounds.BoxExtent.Z;
        Detector = FVector(Tower->GetComponentLocation().X, Tower->GetComponentLocation().Y, Top);
        FHitResult Surface;
        const FCollisionQueryParams MountQuery(SCENE_QUERY_STAT(RMUCDartDetectorMount), true);
        const FVector Above = Detector + FVector(0., 0., 50.);
        const FVector Below = Detector - FVector(0., 0., 2. * Bounds.BoxExtent.Z);
        auto* Fixed = CentreVisibility.Get();
        if ((Fixed && Fixed->LineTraceComponent(Surface, Above, Below, MountQuery)) ||
            Tower->LineTraceComponent(Surface, Above, Below, MountQuery))
            Detector = Surface.ImpactPoint;
    }
    else
    {
        const auto* Base = BaseVisibility.Get();
        if (!Base || !Base->GetAttachParent()) return false;
        const double Sign = Site == 0 ? 1. : -1.;
        Detector = Base->GetAttachParent()->GetComponentTransform().TransformPosition(FVector(1158. * Sign, 0., 132.));
    }
    return !Exit.ContainsNaN() && !Detector.ContainsNaN();
}

bool FRMUCFieldDeviceVisuals::TraceDartExit(bool bRed, const FVector& Exit, const FVector& NextPoint) const
{
    auto* Gate = DartGates[bRed ? 0 : 1].Get();
    if (!Gate || !GetDartLauncher(bRed)) return false;
    FHitResult Hit;
    const FCollisionQueryParams Query(SCENE_QUERY_STAT(RMUCDartExit), true);
    return Gate->LineTraceComponent(Hit, Exit, NextPoint, Query);
}

bool FRMUCFieldDeviceVisuals::TraceDartLanding(bool bRedShooter, bool bOutpost,
    const FVector& Detector, FHitResult& Contact) const
{
    const int32 Site = bRedShooter ? 1 : 0;
    const FVector Above = Detector + FVector(0., 0., 40.);
    const FVector Below = Detector - FVector(0., 0., 45.);
    const FCollisionQueryParams Query(SCENE_QUERY_STAT(RMUCDartLanding), true);
    const auto Trace = [&](UStaticMeshComponent* Mesh)
    {
        return Mesh && Mesh->LineTraceComponent(Contact, Above, Below, Query) && Contact.ImpactNormal.Z > .3;
    };
    if (bOutpost) return Trace(CentreVisibility.Get()) || Trace(Outposts[Site].Get());
    return Trace(BaseDartHeadQueries[Site].Get()) || Trace(BaseVisibility.Get());
}

UStaticMeshComponent* FRMUCFieldDeviceVisuals::GetBaseDartHead(bool bRed) const
{
    return BaseDartHeads[bRed ? 0 : 1].Get();
}

void FRMUCFieldDeviceVisuals::SetBaseDartOffset(bool bRed, const FVector& Offset)
{
    const int32 Team = bRed ? 0 : 1;
    for (UStaticMeshComponent* Head : {GetBaseDartHead(bRed), BaseDartHeadQueries[Team].Get()})
    {
        if (Head && Head->GetAttachParent())
            Head->SetRelativeLocation(Head->GetAttachParent()->GetComponentTransform().InverseTransformVector(Offset));
    }
}

#if WITH_DEV_AUTOMATION_TESTS
bool FRMUCFieldDeviceVisuals::VerifyBaseDartOffset(bool bRed, const FVector& Offset) const
{
    const int32 Team = bRed ? 0 : 1;
    const auto* Head = BaseDartHeads[Team].Get();
    const auto* Query = BaseDartHeadQueries[Team].Get();
    return Head && Query && Head->IsVisible() && Head->GetMaterialIndex(TEXT("RM27_M22")) != INDEX_NONE &&
        (Head->GetComponentLocation() - Head->GetAttachParent()->GetComponentLocation()).Equals(Offset) &&
        (Query->GetComponentLocation() - Query->GetAttachParent()->GetComponentLocation()).Equals(Offset);
}
#endif

void FRMUCFieldDeviceVisuals::UpdateDartGates(double RedOpenFraction, double BlueOpenFraction)
{
    if (!AreDartGatesReady()) return;
    const double OpenFractions[] = {RedOpenFraction, BlueOpenFraction};
    for (int32 Team = 0; Team < 2; ++Team)
    {
        if (!FMath::IsFinite(OpenFractions[Team])) continue;
        const double Open = FMath::Clamp(OpenFractions[Team], 0., 1.);
        const double Side = Team == 0 ? 1. : -1.;
        // The original CAD is the open stop. Traverse only on the fixed guide axis.
        DartGates[Team]->SetRelativeLocation((1. - Open) * DartRedGateClosedCm * FVector(Side, Side, 1.));
    }
}

void FRMUCFieldDeviceVisuals::UpdateDartTrays(double RedExtension, double BlueExtension)
{
    if (auto* Tray = DartTrays[0].Get()) Tray->SetRelativeLocation(FVector(DartTrayTravelCm * FMath::Clamp(RedExtension, 0., 1.), 0., 0.));
    if (auto* Tray = DartTrays[1].Get()) Tray->SetRelativeLocation(FVector(-DartTrayTravelCm * FMath::Clamp(BlueExtension, 0., 1.), 0., 0.));
}

bool FRMUCFieldDeviceVisuals::BindBaseArmor(AActor& Owner, AStaticMeshActor* VisualBase, AStaticMeshActor* CollisionBase)
{
    if (IsBaseArmorReady()) return true;
    UStaticMeshComponent* Visual = VisualBase ? VisualBase->GetStaticMeshComponent() : nullptr;
    UStaticMeshComponent* Contact = CollisionBase ? CollisionBase->GetStaticMeshComponent() : nullptr;
    if (!Visual || !Contact || Contact->GetCollisionResponseToChannel(ECC_Visibility) != ECR_Block) return false;

    const auto LoadMesh = [](const FString& Name)
    {
        return LoadObject<UStaticMesh>(nullptr, *FString::Printf(
            TEXT("/Game/RMSim/Maps/rmuc2027/BaseArmor/%s.%s"), *Name, *Name), nullptr, LOAD_NoWarn);
    };
    UStaticMesh* FixedVisual = LoadMesh(TEXT("base_fixed_visual"));
    UStaticMesh* FixedQuery = LoadMesh(TEXT("base_fixed_query"));
    UStaticMesh* Inner = LoadMesh(TEXT("base_inner_armor"));
    if (!FixedVisual || !Inner || !HasDeviceQueryMesh(FixedQuery))
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_BASE_ARMOR_UNAVAILABLE derived assets missing; run Prepare-RmucRune.ps1 -BaseArmorOnly"));
        return false;
    }

    const FString DescriptionPath = FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir(), TEXT("../../assets/maps/rmuc2027/base-armor-kinematics.json"));
    FString Text;
    TSharedPtr<FJsonObject> Description;
    const TArray<TSharedPtr<FJsonValue>>* Panels = nullptr;
    const TSharedPtr<FJsonObject>* Origins = nullptr;
    const TSharedPtr<FJsonObject>* Lower = nullptr;
    int32 SchemaVersion = 0;
    if (!FFileHelper::LoadFileToString(Text, *DescriptionPath) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Description) || !Description ||
        !Description->TryGetNumberField(TEXT("schema_version"), SchemaVersion) || SchemaVersion != 1 ||
        !Description->TryGetArrayField(TEXT("panels"), Panels) || Panels->Num() != 2 * BasePanelsPerTeam ||
        !Description->TryGetObjectField(TEXT("origins_m"), Origins) ||
        !Description->TryGetObjectField(TEXT("lower_armor"), Lower))
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_BASE_ARMOR_UNAVAILABLE invalid kinematics path=%s"), *DescriptionPath);
        return false;
    }

    double RadiusM, HeightM, WidthM, FaceHeightM, TiltDeg, Threshold;
    if (!(*Lower)->TryGetNumberField(TEXT("center_radius_m"), RadiusM) || RadiusM <= 0. ||
        !(*Lower)->TryGetNumberField(TEXT("center_z_m"), HeightM) ||
        !(*Lower)->TryGetNumberField(TEXT("face_width_m"), WidthM) || WidthM <= 0. ||
        !(*Lower)->TryGetNumberField(TEXT("face_height_m"), FaceHeightM) || FaceHeightM <= 0. ||
        !(*Lower)->TryGetNumberField(TEXT("tilt_up_deg"), TiltDeg) ||
        !(*Lower)->TryGetNumberField(TEXT("visible_from_fraction"), Threshold) ||
        Threshold <= 0. || Threshold > 1.) return false;

    UStaticMesh* MovingVisual[2][BasePanelsPerTeam]{};
    UStaticMesh* MovingQuery[2][BasePanelsPerTeam]{};
    UStaticMesh* DartVisual[2]{};
    UStaticMesh* DartQuery[2]{};
    FVector OriginCm[2];
    const FVector RosMirror(1., -1., 1.);
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const TCHAR* Side = Team == 0 ? TEXT("red") : TEXT("blue");
        const FString DartName = FString::Printf(TEXT("base_%s_dart_head"), Side);
        DartVisual[Team] = LoadMesh(DartName);
        DartQuery[Team] = LoadMesh(DartName + TEXT("_query"));
        if (!ReadBaseVector(**Origins, Side, OriginCm[Team])) return false;
        OriginCm[Team] *= 100. * RosMirror;
        for (int32 Index = 0; Index < BasePanelsPerTeam; ++Index)
        {
            const TSharedPtr<FJsonObject> Spec = (*Panels)[Team * BasePanelsPerTeam + Index]->AsObject();
            if (!Spec) return false;
            FBasePanel& Panel = BasePanels[Team][Index];
            FString AuthoredSide, MeshName;
            int32 AuthoredIndex = 0;
            if (!Spec->TryGetStringField(TEXT("side"), AuthoredSide) || AuthoredSide != Side ||
                !Spec->TryGetNumberField(TEXT("index"), AuthoredIndex) || AuthoredIndex != Index + 1 ||
                !Spec->TryGetStringField(TEXT("visual_mesh"), MeshName) ||
                !ReadBaseVector(*Spec, TEXT("pivot_m"), Panel.PivotCm) ||
                !ReadBaseVector(*Spec, TEXT("slide_axis_ros"), Panel.SlideAxis) || !Panel.SlideAxis.IsUnit() ||
                !ReadBaseVector(*Spec, TEXT("hinge_axis_ros"), Panel.HingeAxis) || !Panel.HingeAxis.IsUnit() ||
                !Spec->TryGetNumberField(TEXT("slide_open_m"), Panel.SlideOpenM) || Panel.SlideOpenM <= 0. ||
                !Spec->TryGetNumberField(TEXT("hinge_open_rad"), Panel.HingeOpenRad)) return false;
            Panel.PivotCm *= 100. * RosMirror;
            Panel.SlideAxis *= RosMirror;
            Panel.HingeAxis *= -RosMirror; // Angular axes change handedness with the map's Y reflection.
            MovingVisual[Team][Index] = LoadMesh(MeshName);
            MovingQuery[Team][Index] = LoadMesh(MeshName + TEXT("_query"));
            if (!MovingVisual[Team][Index] || !HasDeviceQueryMesh(MovingQuery[Team][Index])) return false;
        }
    }

    const auto AttachMesh = [](UStaticMeshComponent& Parent, UStaticMesh* Asset, bool bMoving, bool bQuery)
    {
        AActor* Actor = Parent.GetOwner();
        auto* Component = NewObject<UStaticMeshComponent>(Actor);
        Actor->AddInstanceComponent(Component);
        Component->SetMobility(bMoving ? EComponentMobility::Movable : Parent.GetMobility());
        Component->SetupAttachment(&Parent);
        Component->SetStaticMesh(Asset);
        Component->SetHiddenInGame(bQuery);
        Component->SetCastShadow(!bQuery);
        Component->SetCanEverAffectNavigation(false);
        Component->SetCollisionObjectType(Parent.GetCollisionObjectType());
        if (bQuery) SetDeviceQueryResponses(*Component);
        else Component->SetCollisionProfileName(TEXT("NoCollision"));
        Component->SetGenerateOverlapEvents(false);
        Component->RegisterComponent();
        return Component;
    };

    BaseLargeThreshold = Threshold;
    const double Tilt = FMath::DegreesToRadians(TiltDeg);
    if (!ReplaceFixedVisual(*Visual, *FixedVisual)) return false;
    UStaticMeshComponent* Fixed = AttachMesh(*Contact, FixedQuery, false, true);
    AttachMesh(*Visual, Inner, false, false);
    for (int32 Team = 0; Team < 2; ++Team)
    {
        if (DartVisual[Team] && HasDeviceQueryMesh(DartQuery[Team]))
        {
            BaseDartHeads[Team] = AttachMesh(*Visual, DartVisual[Team], true, false);
            BaseDartHeadQueries[Team] = AttachMesh(*Contact, DartQuery[Team], true, true);
        }
        else UE_LOG(LogTemp, Warning, TEXT("RMUC_BASE_DART_HEAD_UNAVAILABLE team=%d; refresh -BaseArmorOnly assets"), Team);
        for (int32 Index = 0; Index < BasePanelsPerTeam; ++Index)
        {
            FBasePanel& Panel = BasePanels[Team][Index];
            Panel.Visual = AttachMesh(*Visual, MovingVisual[Team][Index], true, false);
            Panel.Query = AttachMesh(*Contact, MovingQuery[Team][Index], true, true);

            const double Angle = PI + Index * (2. * PI / BasePanelsPerTeam);
            const double TeamSign = Team == 0 ? 1. : -1.;
            const FVector Radial(TeamSign * FMath::Cos(Angle), -TeamSign * FMath::Sin(Angle), 0.);
            const FVector Normal = Radial * FMath::Cos(Tilt) + FVector::UpVector * FMath::Sin(Tilt);
            FVector Point = OriginCm[Team] + 100. * RadiusM * Radial;
            Point.Z = 100. * HeightM;
            auto* Plate = NewObject<UBoxComponent>(CollisionBase);
            CollisionBase->AddInstanceComponent(Plate);
            Plate->SetMobility(EComponentMobility::Movable);
            Plate->SetupAttachment(Contact);
            Plate->SetRelativeLocation(Point - .4 * Normal);
            Plate->SetRelativeRotation(FRotationMatrix::MakeFromXZ(Normal, FVector::UpVector).ToQuat());
            Plate->SetBoxExtent(FVector(.5, 50. * WidthM, 50. * FaceHeightM));
            Plate->SetCollisionObjectType(ECC_WorldDynamic);
            SetDeviceQueryResponses(*Plate);
            Plate->SetCollisionEnabled(ECollisionEnabled::NoCollision);
            Plate->RegisterComponent();
            BaseLargePlates[Team][Index] = Plate;
        }
    }
    DisableRestPoseQueries(*Contact);
    BaseVisibility = Fixed;
    UpdateBaseArmor(0., 0.);
    if (FParse::Param(FCommandLine::Get(), TEXT("RefereeFieldDeviceSelfTest"))) VerifyBaseTargetQueries(Owner);
    UE_LOG(LogTemp, Display, TEXT("RMUC_BASE_ARMOR_BOUND source=4774244 corner_shells=6 upper_plates=6 lower_plates=6"));
    return true;
}

void FRMUCFieldDeviceVisuals::UpdateBaseArmor(double RedOpenFraction, double BlueOpenFraction)
{
    if (!IsBaseArmorReady()) return;
    const double Fractions[] = {RedOpenFraction, BlueOpenFraction};
    for (int32 Team = 0; Team < 2; ++Team)
    {
        if (!FMath::IsFinite(Fractions[Team])) continue;
        const double Fraction = FMath::Clamp(Fractions[Team], 0., 1.);
        if (BaseArmorProgress[Team] == Fraction) continue;
        BaseArmorProgress[Team] = Fraction;
        for (int32 Index = 0; Index < BasePanelsPerTeam; ++Index)
        {
            const FBasePanel& Panel = BasePanels[Team][Index];
            const FVector Location = Panel.PivotCm + 100. * Fraction * Panel.SlideOpenM * Panel.SlideAxis;
            const FQuat Rotation(Panel.HingeAxis, Fraction * Panel.HingeOpenRad);
            if (Panel.Visual.IsValid()) Panel.Visual->SetRelativeLocationAndRotation(Location, Rotation);
            if (Panel.Query.IsValid()) Panel.Query->SetRelativeLocationAndRotation(Location, Rotation);
            if (UBoxComponent* Plate = BaseLargePlates[Team][Index].Get())
                Plate->SetCollisionEnabled(Fraction >= BaseLargeThreshold
                    ? ECollisionEnabled::QueryOnly : ECollisionEnabled::NoCollision);
        }
    }
}

void FRMUCFieldDeviceVisuals::GetBaseJointPositions(bool bRed, TArray<double>& OutPositions) const
{
    OutPositions.Reset();
    if (!IsBaseArmorReady()) return;
    const int32 Team = bRed ? 0 : 1;
    for (const FBasePanel& Panel : BasePanels[Team])
    {
        OutPositions.Add(BaseArmorProgress[Team] * Panel.SlideOpenM);
        OutPositions.Add(BaseArmorProgress[Team] * Panel.HingeOpenRad);
    }
}

bool FRMUCFieldDeviceVisuals::GetBaseTarget(const FHitResult& Hit, bool& bRed, bool* bLarge) const
{
    const UPrimitiveComponent* Component = Hit.GetComponent();
    if (!Component || !Hit.bBlockingHit || Hit.bStartPenetrating || !IsBaseArmorReady()) return false;
    if (Component == BaseVisibility.Get())
    {
        const FTransform& Frame = Component->GetComponentTransform();
        const FVector Point = Frame.InverseTransformPosition(Hit.ImpactPoint);
        const FVector ImpactNormal = Frame.InverseTransformVectorNoScale(Hit.ImpactNormal);
        const int32 Team = Point.X >= 0. ? 0 : 1;
        if (RMUCUpperBaseArmor::Contains(Point, ImpactNormal, Team))
        {
            bRed = Team == 0;
            if (bLarge) *bLarge = false;
            return true;
        }
        return false;
    }
    for (int32 Team = 0; Team < 2; ++Team)
    {
        if (!IsBaseLargeArmorExposed(Team == 0)) continue;
        for (const auto& Candidate : BaseLargePlates[Team])
        {
            const UBoxComponent* Plate = Candidate.Get();
            if (Plate == Component && FVector::DotProduct(Plate->GetForwardVector(), Hit.ImpactNormal) > .8)
            {
                bRed = Team == 0;
                if (bLarge) *bLarge = true;
                return true;
            }
        }
    }
    return false;
}

bool FRMUCFieldDeviceVisuals::VerifyBaseTargetQueries(AActor& Owner)
{
    UWorld* World = Owner.GetWorld();
    if (!World || !IsBaseArmorReady()) return false;
    const double Saved[] = {BaseArmorProgress[0], BaseArmorProgress[1]};
    FCollisionQueryParams Params(SCENE_QUERY_STAT(RMSimBaseArmorSelfTest), true, &Owner);
    int32 Passed = 0;
    int32 Tests = 0;
    const FTransform Frame = BaseVisibility->GetComponentTransform();
    const FCollisionShape Pellet = FCollisionShape::MakeSphere(.85f);
    const auto Sweep = [&](FHitResult& Hit, const FVector& Point, const FVector& Direction)
    {
        return World->SweepSingleByChannel(Hit, Point + 62. * Direction, Point - 2. * Direction, FQuat::Identity,
            ECC_GameTraceChannel2, Pellet, Params);
    };
    const auto HitPlate = [&](const FVector& Point, const FVector& Normal, int32 Team, bool bLarge)
    {
        FHitResult Hit;
        bool bRed = false;
        bool bIsLarge = false;
        return Sweep(Hit, Point, Normal) && GetBaseTarget(Hit, bRed, &bIsLarge)
            && bRed == (Team == 0) && bLarge == bIsLarge;
    };
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const auto& Upper = RMUCUpperBaseArmor::RedFaces[0];
        const FVector Normal = RMUCUpperBaseArmor::TeamNormal(Upper.Normal, Team);
        const FVector Across = FVector::CrossProduct(FVector::UpVector, Normal).GetSafeNormal();
        const FVector Up = FVector::CrossProduct(Normal, Across).GetSafeNormal();
        const FVector Point = RMUCUpperBaseArmor::TeamPoint(Upper.Point, Team);
        const auto TraceUpper = [&](double Drop)
        {
            FHitResult Hit;
            bool bRed = false;
            bool bLarge = false;
            const FVector Start = Frame.TransformPosition(Point - Drop * Up + 8. * Normal);
            const FVector End = Frame.TransformPosition(Point - Drop * Up - 2. * Normal);
            return World->LineTraceSingleByChannel(Hit, Start, End, ECC_GameTraceChannel2, Params)
                && GetBaseTarget(Hit, bRed, &bLarge) && bRed == (Team == 0) && !bLarge;
        };
        // Narrow mapped strip tested with a ray so the 17 mm sweep cannot land inside the older boundary.
        const bool bMeasuredEdge = TraceUpper(6.25);
        const bool bOutside = !TraceUpper(6.45);
        Tests += 2;
        Passed += static_cast<int32>(bMeasuredEdge) + static_cast<int32>(bOutside);
        if (!bMeasuredEdge || !bOutside)
            UE_LOG(LogTemp, Warning, TEXT("RMUC_BASE_ARMOR_BOUNDARY_MISS team=%d edge=%d outside=%d"), Team, bMeasuredEdge, bOutside);
    }
    const double States[][2] = {{0., 0.}, {1., 0.}, {0., 1.}, {1., 1.}};
    for (const auto& State : States)
    {
        UpdateBaseArmor(State[0], State[1]);
        for (int32 Team = 0; Team < 2; ++Team)
        {
            const bool bOpen = State[Team] == 1.;
            for (int32 Index = 0; Index < BasePanelsPerTeam; ++Index)
            {
                const UBoxComponent* Large = BaseLargePlates[Team][Index].Get();
                const auto& Top = RMUCUpperBaseArmor::RedFaces[Index];
                const FVector TopPoint = Frame.TransformPosition(RMUCUpperBaseArmor::TeamPoint(Top.Point, Team));
                // Approach beneath the fixed detector beam above the upper plates.
                const double Sign = Team == 0 ? 1. : -1.;
                const FVector TopDirection = Frame.TransformVectorNoScale(
                    (Top.Normal * FVector(Sign, Sign, Index == 0 ? .60 : .30)).GetSafeNormal());
                const bool bUpper = HitPlate(TopPoint, TopDirection, Team, false);
                const bool bLower = Large && HitPlate(Large->GetComponentLocation(), Large->GetForwardVector(), Team, true);
                bool bEdge = false;
                if (Large)
                {
                    const FVector Direction = Large->GetForwardVector();
                    const FVector Point = Large->GetComponentLocation()
                        + .95 * Large->GetScaledBoxExtent().Y * Large->GetRightVector();
                    if (bOpen)
                    {
                        bEdge = HitPlate(Point, Direction, Team, true);
                    }
                    else
                    {
                        FHitResult Hit;
                        if (Sweep(Hit, Point, Direction))
                            for (const FBasePanel& Panel : BasePanels[Team])
                                bEdge |= Panel.Query.Get() == Hit.GetComponent();
                    }
                }
                Tests += 3;
                Passed += static_cast<int32>(bUpper) + static_cast<int32>(bLower == bOpen) + static_cast<int32>(bEdge);
                if (!bUpper || bLower != bOpen || !bEdge)
                    UE_LOG(LogTemp, Warning, TEXT("RMUC_BASE_ARMOR_PROBE_MISS team=%d face=%d open=%d upper=%d lower=%d shell_edge=%d"),
                        Team, Index, bOpen, bUpper, bLower, bEdge);
            }
        }
    }
    UpdateBaseArmor(Saved[0], Saved[1]);
    UE_LOG(LogTemp, Display, TEXT("RMUC_BASE_ARMOR_SELF_TEST passed=%d tests=%d"), Passed, Tests);
    return Passed == Tests;
}

int32 FRMUCFieldDeviceVisuals::BindRuneLights(AActor& DeviceOwner, UStaticMeshComponent& Rotor, UMaterialInterface& Material)
{
    int32 SectionCount = 0;
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const FVector Normal = RuneAxis * (Team == 0 ? 1. : -1.);
        for (int32 Index = 0; Index < NumPlates; ++Index)
        {
            FRuneArmLights& Arm = RuneLights[Team][Index];
            auto* Mesh = NewObject<UProceduralMeshComponent>(&DeviceOwner);
            DeviceOwner.AddInstanceComponent(Mesh);
            Mesh->SetupAttachment(&Rotor);
            Mesh->SetMobility(EComponentMobility::Movable);
            Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
            Mesh->SetCollisionResponseToAllChannels(ECR_Ignore);
            Mesh->SetGenerateOverlapEvents(false);
            Mesh->SetCanEverAffectNavigation(false);
            Mesh->SetCastShadow(false);
            Arm.Mesh = Mesh;

            FRuneSurfaceGeometry Geometry;
            Geometry.Root = (PlateAxialCm + .22) * Normal;
            Geometry.Radial = FQuat(RuneAxis, Index * (2. * PI / NumPlates)).RotateVector(FVector::UpVector);
            Geometry.Tangent = FVector::CrossProduct(Normal, Geometry.Radial);
            const double LightRadii[] = {24., 47., PlateRadiusCm};
            for (int32 I = 0; I < UE_ARRAY_COUNT(LightRadii); ++I)
                Arm.NearbyLights[I] = RMUCArmorPresentation::AddPointLight(DeviceOwner, Rotor,
                    Geometry.Root + LightRadii[I] * Geometry.Radial + 5. * Normal,
                    RMUCArmorPresentation::TeamColor(Team == 0), I == 2 ? 84.f : 59.f);
            const auto AddSection = [&](int32 Section)
            {
                TArray<FVector> Normals;
                Normals.Init(Normal, Geometry.Vertices.Num());
                Mesh->CreateMeshSection_LinearColor(Section, Geometry.Vertices, Geometry.Triangles, Normals, {}, {}, {}, false);
                Mesh->SetMeshSectionVisible(Section, false);
                if (auto* Lamp = UMaterialInstanceDynamic::Create(&Material, Mesh))
                {
                    Lamp->SetVectorParameterValue(LampColorParameter, FLinearColor::Black);
                    Mesh->SetMaterial(Section, Lamp);
                    Arm.Materials[Section] = Lamp;
                    ++SectionCount;
                }
                Geometry.Vertices.Reset();
                Geometry.Triangles.Reset();
            };

            // The active target face shows the rings and radial marks of the rule diagram.
            Geometry.AddRing(2., 3.5);
            Geometry.AddRing(6., 7.5);
            Geometry.AddRing(12., 13.5);
            Geometry.AddStrip({PlateRadiusCm - 14.5, 0.}, {PlateRadiusCm - 6.5, 0.}, 1.4);
            Geometry.AddStrip({PlateRadiusCm + 6.5, 0.}, {PlateRadiusCm + 14.5, 0.}, 1.4);
            Geometry.AddStrip({PlateRadiusCm, -14.5}, {PlateRadiusCm, -6.5}, 1.4);
            Geometry.AddStrip({PlateRadiusCm, 6.5}, {PlateRadiusCm, 14.5}, 1.4);
            AddSection(RuneTargetSection);

            Geometry.AddRing(.9, 2.2);
            Geometry.AddRing(14.2, 15.);
            AddSection(RuneFullSection);

            for (int32 Segment = 0; Segment < RuneFlowCount; ++Segment)
            {
                const double Radius = 19. + 7. * Segment;
                Geometry.AddStrip({Radius, -2.7}, {Radius + 3.4, 0.}, 1.15);
                Geometry.AddStrip({Radius + 3.4, 0.}, {Radius, 2.7}, 1.15);
                AddSection(RuneFlowSection + Segment);
            }
            for (int32 Segment = 0; Segment < RuneProgressCount; ++Segment)
            {
                const double Start = 16. + 7.5 * Segment;
                for (const double Side : {-1., 1.})
                    Geometry.AddStrip({Start, 5. * Side}, {Start + 6.6, 5. * Side}, 1.4);
                AddSection(RuneProgressSection + Segment);
            }
            Mesh->RegisterComponent();
        }
    }
    return SectionCount;
}

void FRMUCFieldDeviceVisuals::UpdateRune(double AngleRad, double MatchSeconds,
    const FRMUCRuneFaceVisualState& Red, const FRMUCRuneFaceVisualState& Blue)
{
    UStaticMeshComponent* Rotor = Rune.Get();
    if (!Rotor || !Platform.IsValid() || !FMath::IsFinite(AngleRad) || !FMath::IsFinite(MatchSeconds)) return;
    RuneAngleRad = FMath::Fmod(AngleRad, 2. * PI);
    Rotor->SetRelativeRotation(FQuat(RuneAxis, RuneAngleRad));
    const double Elapsed = FMath::Clamp(MatchSeconds - RuneLightSeconds, 0., .20);
    const float Fade = 1.f - static_cast<float>(FMath::Exp(-Elapsed * 19.));
    const bool bNewMatch = MatchSeconds < RuneLightSeconds;
    RuneLightSeconds = MatchSeconds;
    const FRMUCRuneFaceVisualState* Faces[] = {&Red, &Blue};
    for (int32 Team = 0; Team < 2; ++Team)
    {
        const auto& Face = *Faces[Team];
        const FLinearColor TeamColor = Team == 0
            ? FLinearColor(1.f, .065f, .019f) : FLinearColor(.008f, .032f, .70f);
        const float Breath = .68f + .32f * static_cast<float>(FMath::Sin(2. * PI * (1.4 * MatchSeconds + .33 * Team)));
        const double Wave = FMath::Frac(MatchSeconds / .68 + Team * .41);
        const int32 Progress = Face.bBig && !Face.bActivated ? FMath::Clamp(Face.Groups, 0, RuneProgressCount) : 0;
        const bool bLightsOff = Face.bBlanked || bNewMatch;
        const auto Blend = [&](float Previous, float Target)
        {
            if (bLightsOff) return 0.f;
            const float Level = FMath::Lerp(Previous, Target, Fade);
            return Level > .015f ? Level : 0.f;
        };
        if (auto* Hub = RuneHubLamps[Team].Get())
        {
            const float Goal = Face.bActivated || Face.Targets != 0 || Face.Confirmed != 0 ? 1.f : 0.f;
            const float Intensity = Blend(RuneHubIntensity[Team], Goal);
            if (Intensity != RuneHubIntensity[Team])
            {
                Hub->SetVectorParameterValue(PaintTintParameter, FMath::Lerp(RuneNeutralHub, TeamColor, Intensity));
                Hub->SetVectorParameterValue(LampColorParameter, (100.f * Intensity) * TeamColor);
                RMUCArmorPresentation::SetPointLight(RuneHubSceneLights[Team].Get(), TeamColor, 15.f * Intensity);
                RuneHubIntensity[Team] = Intensity;
            }
        }
        for (int32 Index = 0; Index < NumPlates; ++Index)
        {
            FRuneArmLights& Arm = RuneLights[Team][Index];
            const int32 Bit = 1 << Index;
            const bool bTarget = !Face.bActivated && (Face.Targets & Bit) != 0;
            const bool bAcknowledged = !Face.bActivated && (Face.Confirmed & Bit) != 0;
            const bool bFull = Face.bActivated || (!Face.bBig && (Face.Lit & Bit) != 0);
            const auto SetSection = [&](int32 Section, float Desired)
            {
                if (auto* Mat = Arm.Materials[Section].Get())
                {
                    float& Previous = Arm.Emission[Section];
                    const float Level = Blend(Previous, Desired);
                    if (FMath::IsNearlyEqual(Level, Previous, .001f)) return;
                    if ((Level > 0.f) != (Previous > 0.f))
                        if (auto* Mesh = Arm.Mesh.Get()) Mesh->SetMeshSectionVisible(Section, Level > 0.f);
                    Mat->SetVectorParameterValue(LampColorParameter, TeamColor * Level);
                    Previous = Level;
                }
            };

            // An acknowledged large target stays steady while the second target keeps its flow.
            SetSection(RuneTargetSection, bTarget ? 4.7f * Breath : (bAcknowledged ? 2.1f : 0.f));
            SetSection(RuneFullSection, bFull ? 5.5f : 0.f);
            for (int32 Segment = 0; Segment < RuneFlowCount; ++Segment)
            {
                const double Phase = double(Segment) / RuneFlowCount;
                const double Separation = FMath::Abs(Wave - Phase);
                const double Pulse = FMath::Clamp(1. - FMath::Min(Separation, 1. - Separation) / .36, 0., 1.);
                const float Strength = .16f + 5.8f * static_cast<float>(Pulse * Pulse * (3. - 2. * Pulse));
                SetSection(RuneFlowSection + Segment, bTarget ? Breath * Strength : 0.f);
            }
            for (int32 Segment = 0; Segment < RuneProgressCount; ++Segment)
                SetSection(RuneProgressSection + Segment, Segment < Progress ? 3.8f : 0.f);

            constexpr float FullTrimLighting = 3.8f;
            const float TargetTrimLighting = bFull ? FullTrimLighting :
                (bAcknowledged ? 1.4f : (bTarget ? 1.3f * Breath : 0.f));
            const float CadLevel = Blend(Arm.CadEmission, TargetTrimLighting);
            const float Near = FMath::Max(Arm.Emission[RuneFlowSection], Arm.Emission[RuneProgressSection]);
            const float Far = FMath::Max(Arm.Emission[RuneFlowSection + 3], Arm.Emission[RuneProgressSection + 3]);
            const float FaceLight = FMath::Max(Arm.Emission[RuneTargetSection], Arm.Emission[RuneFullSection]);
            const float Illumination[] = {
                1.8f * Near + .55f * Arm.Emission[RuneFullSection],
                1.8f * Far + .65f * Arm.Emission[RuneFullSection],
                3.2f * FaceLight
            };
            for (int32 I = 0; I < UE_ARRAY_COUNT(Illumination); ++I)
                RMUCArmorPresentation::SetPointLight(Arm.NearbyLights[I].Get(), TeamColor,
                    bLightsOff ? 0.f : Illumination[I]);
            if (!FMath::IsNearlyEqual(CadLevel, Arm.CadEmission, .001f))
            {
                const auto TrimColor = FMath::Lerp(RuneNeutralTrim, TeamColor, CadLevel / FullTrimLighting);
                for (const auto& Lamp : Lamps[Team][Index])
                {
                    if (auto* Mat = Lamp.Get())
                    {
                        Mat->SetVectorParameterValue(PaintTintParameter, TrimColor);
                        Mat->SetVectorParameterValue(LampColorParameter, TeamColor * CadLevel);
                    }
                }
                Arm.CadEmission = CadLevel;
            }
        }
    }
}

#if WITH_DEV_AUTOMATION_TESTS
bool FRMUCFieldDeviceVisuals::GetRuneFixtureFace(bool bRed, int32 Index, FTransform& Transform) const
{
    if (Index < 0 || Index >= NumPlates) return false;
    const UBoxComponent* Plate = Plates[bRed ? 0 : 1][Index].Get();
    if (!Plate || !Plate->IsRegistered()) return false;
    Transform = Plate->GetComponentTransform();
    return true;
}

FRMUCRuneLampEvidence FRMUCFieldDeviceVisuals::GetRuneLampEvidence(bool bRed) const
{
    FRMUCRuneLampEvidence Evidence;
    double FlowTotal = 0.;
    for (const FRuneArmLights& Arm : RuneLights[bRed ? 0 : 1])
    {
        for (const auto& Candidate : Arm.NearbyLights)
            if (const auto* Light = Candidate.Get(); Light && Light->IsVisible() && Light->Intensity > .001)
                ++Evidence.ActivePointLights;
        const auto* Mesh = Arm.Mesh.Get();
        if (!Mesh || !Mesh->IsRegistered()) continue;
        const auto Level = [&](int32 Section)
        {
            auto* Mat = Arm.Materials[Section].Get();
            if (!Mat || !Mesh->IsMeshSectionVisible(Section)) return 0.;
            const FLinearColor Color = Mat->K2_GetVectorParameterValue(LampColorParameter);
            return double(FMath::Max3(Color.R, Color.G, Color.B));
        };
        Evidence.Plate = FMath::Max(Evidence.Plate, Level(RuneTargetSection));
        Evidence.FullArm = FMath::Max(Evidence.FullArm, Level(RuneFullSection));
        for (int32 Segment = 0; Segment < RuneFlowCount; ++Segment)
        {
            const double Glow = Level(RuneFlowSection + Segment);
            Evidence.Flow = FMath::Max(Evidence.Flow, Glow);
            Evidence.FlowMoment += Glow * (Segment + .5) / RuneFlowCount;
            FlowTotal += Glow;
        }
        int32 VisibleProgress = 0;
        for (int32 Segment = 0; Segment < RuneProgressCount; ++Segment)
        {
            const double Glow = Level(RuneProgressSection + Segment);
            Evidence.Progress = FMath::Max(Evidence.Progress, Glow);
            if (Glow > .25) ++VisibleProgress;
        }
        Evidence.ProgressSegments = FMath::Max(Evidence.ProgressSegments, VisibleProgress);
    }
    if (FlowTotal > 0.) Evidence.FlowMoment /= FlowTotal;
    return Evidence;
}
#endif

bool FRMUCFieldDeviceVisuals::GetRuneTarget(const FHitResult& Hit, bool& bRed, int32& PlateIndex, double* PlateOffsetCm) const
{
    const UPrimitiveComponent* Component = Hit.GetComponent();
    if (!Component || !Hit.bBlockingHit || Hit.bStartPenetrating || !IsRuneReady()) return false;
    for (int32 Team = 0; Team < 2; ++Team)
    {
        for (int32 Index = 0; Index < NumPlates; ++Index)
        {
            const UBoxComponent* Plate = Plates[Team][Index].Get();
            if (Plate == Component && FVector::DotProduct(Hit.ImpactNormal, Plate->GetForwardVector()) * (Team == 0 ? 1. : -1.) > .5)
            {
                const FVector Local = Plate->GetComponentTransform().InverseTransformPosition(Hit.ImpactPoint);
                const double Distance = FMath::Sqrt(FMath::Square(Local.Y) + FMath::Square(Local.Z));
                if (Distance > PlateFaceRadiusCm) return false;
                bRed = Team == 0;
                PlateIndex = Index;
                if (PlateOffsetCm) *PlateOffsetCm = Distance;
                return true;
            }
        }
    }
    return false;
}
