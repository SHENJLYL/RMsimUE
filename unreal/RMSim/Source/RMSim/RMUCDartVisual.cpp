#include "RMUCDartVisual.h"
#include "RMUCArmorPresentation.h"

#include "Algo/BinarySearch.h"
#include "CollisionQueryParams.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/HitResult.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Math/RotationMatrix.h"
#include "ProceduralMeshComponent.h"

namespace
{
constexpr double TrailLifetime = .62;
constexpr double TrailRelease = .38;
constexpr double TrailExitX = -17.8;
constexpr double NoseTipX = 22.4;
constexpr double SettleSeconds = .17;
constexpr double HideAfterSeconds = .26;
constexpr int32 TrailSegments = 32;
const FVector LandingSupports[] = {
    {NoseTipX, 0., 0.}, {0., 0., -2.18}, {10.25, 0., -2.3}, {-17.9, 0., -2.},
    {-13.8, 0., -7.1}, {-16.4, 0., -7.1}, {-13.8, -7.1, 0.}, {-13.8, 7.1, 0.}, {-13.8, 0., 7.1}
};

template <class T>
T* AddComponent(AActor& Owner, USceneComponent* Parent = nullptr)
{
    auto* Component = NewObject<T>(&Owner);
    Owner.AddInstanceComponent(Component);
    Component->SetMobility(EComponentMobility::Movable);
    if (Parent) Component->SetupAttachment(Parent);
    else Owner.SetRootComponent(Component);
    return Component;
}

template <class T>
T* AddMesh(AActor& Owner, USceneComponent& Parent)
{
    auto* Mesh = AddComponent<T>(Owner, &Parent);
    Mesh->SetCollisionProfileName(TEXT("NoCollision"));
    Mesh->SetGenerateOverlapEvents(false);
    Mesh->SetCanEverAffectNavigation(false);
    Mesh->SetCastShadow(false);
    Mesh->SetReceivesDecals(false);
    Mesh->SetAffectDynamicIndirectLighting(false);
    Mesh->SetAffectDistanceFieldLighting(false);
    return Mesh;
}

struct FMesh
{
    TArray<FVector> Vertices;
    TArray<FVector> Normals;
    TArray<int32> Indices;

    void AddRing(const FVector& Centre, const FMatrix& Frame, double Radius, double Slope, int32 Sides)
    {
        const int32 Start = Vertices.Num();
        for (int32 I = 0; I < Sides; ++I)
        {
            const double Angle = 2. * PI * I / Sides;
            const FVector Radial(0., FMath::Cos(Angle), FMath::Sin(Angle));
            Vertices.Add(Centre + FVector(Frame.TransformVector(Radial)) * Radius);
            Normals.Add(FVector(Frame.TransformVector(FVector(-Slope, Radial.Y, Radial.Z))).GetSafeNormal());
            if (Start == 0) continue;
            const int32 A = Start - Sides + I;
            const int32 B = Start - Sides + (I + 1) % Sides;
            Indices.Append({A, A + Sides, B + Sides, A, B + Sides, B});
        }
    }

    void AddRibbonPair(const FVector& Centre, const FVector& Tangent, double HalfWidth, bool bBuildIndices)
    {
        const int32 Start = Vertices.Num();
        const FVector Vertical = FVector::VectorPlaneProject(FVector::UpVector, Tangent).GetSafeNormal();
        const FVector WidthAxis = FQuat(Tangent, FMath::DegreesToRadians(24.)).RotateVector(Vertical);
        const FVector Normal = FVector::CrossProduct(WidthAxis, Tangent).GetSafeNormal();
        Vertices.Append({Centre - WidthAxis * HalfWidth, Centre + WidthAxis * HalfWidth});
        Normals.Append({Normal, Normal});
        if (Start > 0 && bBuildIndices)
        {
            // Duplicate winding so the single strip reads from either side of the arena.
            Indices.Append({Start - 2, Start - 1, Start + 1, Start - 2, Start + 1, Start,
                Start - 2, Start + 1, Start - 1, Start - 2, Start, Start + 1});
        }
    }

    void AddFace(std::initializer_list<FVector> Face)
    {
        const FVector* P = Face.begin();
        const FVector Normal = -FVector::CrossProduct(P[1] - P[0], P[2] - P[0]).GetSafeNormal();
        const int32 Start = Vertices.Num();
        for (const FVector& Vertex : Face)
        {
            Vertices.Add(Vertex);
            Normals.Add(Normal);
        }
        for (int32 I = 1; I + 1 < int32(Face.size()); ++I)
            Indices.Append({Start, Start + I, Start + I + 1});
    }

    void Apply(UProceduralMeshComponent& Mesh, int32 Section) const
    {
        if (Mesh.GetNumSections() > Section)
            Mesh.UpdateMeshSection_LinearColor(Section, Vertices, Normals, {}, {}, {});
        else
            Mesh.CreateMeshSection_LinearColor(Section, Vertices, Indices, Normals, {}, {}, {}, false);
    }
};

void AddNose(UProceduralMeshComponent& Mesh)
{
    const FVector2D Profile[] = {{10.7, 2.25}, {13.2, 2.22}, {16.2, 1.93},
        {18.8, 1.45}, {20.9, .85}, {22.1, .37}, {NoseTipX, 0.}};
    FMesh Nose;
    for (int32 I = 0; I < UE_ARRAY_COUNT(Profile); ++I)
    {
        const auto& Before = Profile[FMath::Max(I - 1, 0)];
        const auto& After = Profile[FMath::Min(I + 1, int32(UE_ARRAY_COUNT(Profile)) - 1)];
        Nose.AddRing(FVector(Profile[I].X, 0., 0.), FMatrix::Identity, Profile[I].Y,
            (After.Y - Before.Y) / (After.X - Before.X), 24);
    }
    Nose.Apply(Mesh, 0);
}
}

namespace
{
void AddFins(UProceduralMeshComponent& Mesh)
{
    const FVector2D Outline[] = {{-5., 1.5}, {-12.0, 6.5}, {-13.8, 7.1},
        {-16.4, 7.1}, {-17.7, 5.8}, {-16.8, 1.5}};
    FMesh Faces, Edges;
    for (int32 Fin = 0; Fin < 4; ++Fin)
    {
        const FQuat Rotation(FVector::ForwardVector, Fin * HALF_PI);
        FVector Front[UE_ARRAY_COUNT(Outline)], Back[UE_ARRAY_COUNT(Outline)];
        for (int32 I = 0; I < UE_ARRAY_COUNT(Outline); ++I)
        {
            const double Thickness = Outline[I].Y < 2. ? .46 : .24;
            Front[I] = Rotation.RotateVector(FVector(Outline[I].X, Thickness, Outline[I].Y));
            Back[I] = Rotation.RotateVector(FVector(Outline[I].X, -Thickness, Outline[I].Y));
        }
        for (int32 I = 0; I < UE_ARRAY_COUNT(Outline); ++I)
        {
            const int32 Next = (I + 1) % UE_ARRAY_COUNT(Outline);
            Edges.AddFace({Front[I], Back[I], Back[Next], Front[Next]});
            if (I > 0 && Next > 0)
            {
                Faces.AddFace({Front[0], Front[I], Front[Next]});
                Faces.AddFace({Back[0], Back[Next], Back[I]});
            }
        }
    }
    Faces.Apply(Mesh, 0);
    Edges.Apply(Mesh, 1);
}
}

bool FRMUCDartVisual::Spawn(AActor& Owner, bool bRed)
{
    Destroy();
    UWorld* World = Owner.GetWorld();
    if (!World) return false;
    auto* Cylinder = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
    auto* Basic = LoadObject<UMaterialInterface>(nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    if (!Cylinder || !Basic) return false;

    FActorSpawnParameters Params;
    Params.Owner = &Owner;
    auto* Display = World->SpawnActor<AActor>(FVector::ZeroVector, FRotator::ZeroRotator, Params);
    if (!Display) return false;
    Display->SetActorEnableCollision(false);
    Display->Tags.Add(FName(TEXT("rmuc_dart_visual")));

    auto* Root = AddComponent<USceneComponent>(*Display);
    Root->RegisterComponent();
    auto* BodyRoot = AddComponent<USceneComponent>(*Display, Root);
    BodyRoot->RegisterComponent();

    TeamColor = RMUCArmorPresentation::TeamColor(bRed);
    const auto Paint = [&](const FLinearColor& Color)
    {
        auto* PaintMaterial = UMaterialInstanceDynamic::Create(Basic, Display);
        PaintMaterial->SetVectorParameterValue(TEXT("Color"), Color);
        return PaintMaterial;
    };
    auto* Shell = Paint(FLinearColor(.24f, .29f, .35f));
    auto* Metal = Paint(FLinearColor(.56f, .62f, .69f));
    auto* Graphite = Paint(FLinearColor(.055f, .075f, .10f));
    auto* TeamFinish = Paint(TeamColor * .52f);
    auto* Lamp = RMUCArmorPresentation::MakeEmitter(*Display, bRed, 2.8f);
    const FQuat AlongX = FQuat::FindBetweenNormals(FVector::UpVector, FVector::ForwardVector);
    const auto AddBand = [&](double X, double Diameter, double Length, UMaterialInterface* Material)
    {
        auto* Part = AddMesh<UStaticMeshComponent>(*Display, *BodyRoot);
        Part->SetStaticMesh(Cylinder);
        Part->SetMaterial(0, Material);
        Part->SetRelativeTransform(FTransform(AlongX, FVector(X, 0., 0.), FVector(Diameter, Diameter, Length) / 100.));
        Part->RegisterComponent();
    };

    AddBand(-1.8, 4.36, 26.5, Shell);
    AddBand(7.5, 4.66, 3.4, Graphite);
    AddBand(9.4, 4.76, .8, Metal);
    AddBand(10.25, 4.60, 1.2, Lamp ? Lamp : TeamFinish);
    AddBand(-6.8, 4.54, 1.7, TeamFinish);
    AddBand(-12.4, 4.71, 1.95, Metal);
    AddBand(-15.8, 4.16, 3.0, Graphite);
    AddBand(-17.1, 3.45, 1.1, Metal);
    AddBand(-17.65, 2.1, .45, Lamp ? Lamp : TeamFinish);

    auto* Nose = AddMesh<UProceduralMeshComponent>(*Display, *BodyRoot);
    AddNose(*Nose);
    Nose->SetMaterial(0, TeamFinish);
    Nose->RegisterComponent();
    auto* Fins = AddMesh<UProceduralMeshComponent>(*Display, *BodyRoot);
    AddFins(*Fins);
    Fins->SetMaterial(0, Graphite);
    Fins->SetMaterial(1, Metal);
    Fins->RegisterComponent();
    BodyRoot->SetVisibility(false, true);

    auto* Wake = AddMesh<UProceduralMeshComponent>(*Display, *Root);
    Wake->SetVisibility(false);
    if (auto* Glow = RMUCArmorPresentation::MakeEmitter(*Display, bRed))
    {
        Wake->SetMaterial(0, Glow);
        Wake->RegisterComponent();
        Trail = Wake;
    }
    else Wake->DestroyComponent();

    Actor = Display;
    Body = BodyRoot;
    return true;
}

void FRMUCDartVisual::ShowAt(const FVector& Position, const FVector& Direction, double Now)
{
    auto* BodyRoot = Body.Get();
    if (!BodyRoot || Position.ContainsNaN() || Direction.IsNearlyZero() || Direction.ContainsNaN()) return;
    const FQuat Rotation = Direction.Rotation().Quaternion();
    BodyRoot->SetWorldLocationAndRotation(Position, Rotation);
    if (!BodyRoot->IsVisible()) BodyRoot->SetVisibility(true, true);
    auto* Wake = Trail.Get();
    if (!Wake) return;
    if (History.IsEmpty()) Wake->SetWorldLocation(Position);
    const FTrailSample Sample{Now, Position + Rotation.RotateVector(FVector(TrailExitX, 0., 0.)) - Wake->GetComponentLocation()};
    if (!History.IsEmpty() && Now <= History.Last().Time) History.Last() = Sample;
    else History.Add(Sample);
    FadeTrail(Now);
}

void FRMUCDartVisual::FadeTrail(double Now)
{
    auto* Wake = Trail.Get();
    if (!Wake || History.IsEmpty()) return;
    const double End = History.Last().Time;
    const double Fade = FMath::Clamp((Now - End) / TrailRelease, 0., 1.);
    if (Fade >= 1.)
    {
        Wake->SetVisibility(false);
        History.Reset();
        if (!Body.IsValid() || !Body->IsVisible()) Destroy();
        return;
    }
    const double Oldest = End - (1. - Fade) * TrailLifetime;
    if (Now - LastTrailUpdate < 1. / 120.) return;
    const int32 First = Algo::LowerBoundBy(History, Oldest, &FTrailSample::Time);
    if (First > 1) History.RemoveAt(0, First - 1, EAllowShrinking::No);
    if (History.Num() < 2) return;
    LastTrailUpdate = Now;
    const double Start = FMath::Max(Oldest, History[0].Time);
    const auto PointAt = [&](double Time)
    {
        const int32 After = Algo::LowerBoundBy(History, Time, &FTrailSample::Time);
        if (After <= 0) return History[0].Position;
        if (After >= History.Num()) return History.Last().Position;
        const auto& A = History[After - 1];
        const auto& B = History[After];
        return FMath::Lerp(A.Position, B.Position, (Time - A.Time) / (B.Time - A.Time));
    };
    // Game time keeps the wake anchored in 3D with a frame-rate independent lifetime.
    FMesh Strip;
    const bool bBuildIndices = Wake->GetNumSections() == 0;
    Strip.Vertices.Reserve((TrailSegments + 1) * 2);
    Strip.Normals.Reserve((TrailSegments + 1) * 2);
    if (bBuildIndices) Strip.Indices.Reserve(TrailSegments * 12);
    for (int32 I = 0; I <= TrailSegments; ++I)
    {
        const double Time = FMath::Lerp(Start, End, double(I) / TrailSegments);
        const FVector Tangent = (PointAt(FMath::Min(Time + .008, End)) -
            PointAt(FMath::Max(Time - .008, Start))).GetSafeNormal();
        const double Freshness = FMath::Clamp((Time - Oldest) / TrailLifetime, 0., 1.);
        const double HalfWidth = .04 + 2.05 * Freshness;
        Strip.AddRibbonPair(PointAt(Time), Tangent, HalfWidth, bBuildIndices);
    }
    Strip.Apply(*Wake, 0);
    const float Freshness = FMath::Clamp(float((End - Oldest) / TrailLifetime), 0.f, 1.f);
    const FLinearColor Glow = FMath::Lerp(TeamColor, FLinearColor::White, .23f);
    auto* Material = CastChecked<UMaterialInstanceDynamic>(Wake->GetMaterial(0));
    Material->SetVectorParameterValue(RMUCArmorPresentation::LampColor, Glow * (3.5f * Freshness * Freshness));
    if (!Wake->IsVisible()) Wake->SetVisibility(true);
}

bool FRMUCDartVisual::Land(const FHitResult& Contact, double Now)
{
    const auto* BodyRoot = Body.Get();
    if (!BodyRoot || !Contact.GetComponent() || Contact.ImpactNormal.IsNearlyZero()) return false;

    LandingAt = Now;
    LandingSurface = Contact.GetComponent();
    LandingPoint = Contact.ImpactPoint;
    LandingNormal = Contact.ImpactNormal.GetSafeNormal();
    IncomingRotation = BodyRoot->GetComponentQuat();
    IncomingTip = BodyRoot->GetComponentLocation() + IncomingRotation.RotateVector(FVector(NoseTipX, 0., 0.));
    const FVector Tangent = FVector::VectorPlaneProject(IncomingRotation.GetForwardVector(), LandingNormal).GetSafeNormal();
    RestingRotation = (Tangent.IsNearlyZero() ? FRotationMatrix::MakeFromZ(LandingNormal)
        : FRotationMatrix::MakeFromXZ(Tangent, LandingNormal)).ToQuat();
    return true;
}

void FRMUCDartVisual::Update(double Now)
{
    auto* BodyRoot = Body.Get();
    if (BodyRoot && LandingAt >= 0.)
    {
        const double Age = Now - LandingAt;
        if (Age >= HideAfterSeconds)
        {
            Finish(Now);
            return;
        }
        const double Turn = FMath::SmoothStep(.045, SettleSeconds, Age);
        const FQuat Rotation = FQuat::Slerp(IncomingRotation, RestingRotation, Turn);
        const FVector Tip(NoseTipX, 0., 0.);
        const FVector Position = FMath::Lerp(IncomingTip, LandingPoint, FMath::SmoothStep(0., .07, Age)) -
            Rotation.RotateVector(Tip * (1. - Turn));
        auto* Surface = LandingSurface.Get();
        const FVector Reach = LandingNormal * 18.;
        const FCollisionQueryParams Query(SCENE_QUERY_STAT(RMUCDartSupport), true);
        double Clearance = 0.;
        bool bSupported = false;
        for (const FVector& Support : LandingSupports)
        {
            const FVector Sample = Position + Rotation.RotateVector(Support);
            FHitResult Ground;
            if (!Surface || !Surface->LineTraceComponent(Ground, Sample + Reach, Sample - Reach, Query) ||
                FVector::DotProduct(Ground.ImpactNormal, LandingNormal) <= .5) continue;
            Clearance = FMath::Max(Clearance, .12 + FVector::DotProduct(Ground.ImpactPoint - Sample, LandingNormal));
            bSupported = true;
        }
        // The original nose contact still supports the start of the rotation at thin edges.
        if (!bSupported && Turn < 1.)
            Clearance = FMath::Max(0., .12 + FVector::DotProduct(LandingPoint - Position - Rotation.RotateVector(Tip), LandingNormal));
        BodyRoot->SetWorldLocationAndRotation(Position + Clearance * LandingNormal, Rotation);
#if WITH_DEV_AUTOMATION_TESTS
        bLandingSupported = bSupported && Clearance > 0.;
#endif
    }
    FadeTrail(Now);
}

void FRMUCDartVisual::Finish(double Now)
{
    LandingAt = -1.;
    LandingSurface.Reset();
    if (auto* BodyRoot = Body.Get()) BodyRoot->SetVisibility(false, true);
    if (History.Num() < 2) Destroy();
    else FadeTrail(Now);
}

void FRMUCDartVisual::Destroy()
{
    if (auto* Display = Actor.Get()) Display->Destroy();
    Actor.Reset();
    Body.Reset();
    Trail.Reset();
    History.Reset();
    LastTrailUpdate = -1.;
    LandingAt = -1.;
    LandingSurface.Reset();
}
