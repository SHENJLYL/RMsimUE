#include "VehicleRig.h"
#include "Engine/World.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Math/RotationMatrix.h"

namespace
{
constexpr double GroundGapCm = 2.0;
constexpr double MaxStepCm = 5.0;
constexpr double MinNormalZ = 0.8191520443; // cos(35 degrees), preview limit, not measured traction.
}

TArray<AVehicleRig::FPartCollider> AVehicleRig::GetPartColliders() const
{
    TArray<FPartCollider> Result;
    for(const auto* Mesh:VisualMeshes)if(IsValid(Mesh)&&Mesh->GetStaticMesh())
        Result.Add({Mesh->GetStaticMesh()->GetBoundingBox(),GetCollisionVisualTransform(Mesh).GetRelativeTransform(GetActorTransform())});
    return Result;
}

bool AVehicleRig::OverlapsTerrainParts(const FTransform& Pose) const
{
    if(!GetWorld())return true;
    const auto Parts=GetPartColliders();if(Parts.IsEmpty())return true;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimTerrainPartOverlap),false,this);
    for(const auto& Part:Parts)
    {
        const FTransform World=Part.Relative*Pose;
        if(GetWorld()->OverlapBlockingTestByChannel(World.TransformPosition(Part.Bounds.GetCenter()),World.GetRotation(),ECC_Pawn,
            FCollisionShape::MakeBox(Part.Bounds.GetExtent()*World.GetScale3D().GetAbs()),Query))return true;
    }
    return false;
}

bool AVehicleRig::TerrainPose(const FTransform& Candidate, bool Initial, FTransform& OutPose,
    TArray<FVector>& Contacts, FVector& Normal, FString& Error) const
{
    auto Fail = [&Error](const TCHAR* Reason) { Error = Reason; return false; };
    if (!GetWorld() || WheelVisuals.Num() < 3) return Fail(TEXT("terrain following requires at least three wheel supports"));
    Contacts.Reset();
    TArray<FVector> LocalCentres;
    const FTransform Current = GetActorTransform();
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimTerrainSupport), true, this);
    for (const FWheelVisual& Wheel : WheelVisuals)
    {
        const FVector Local = Current.InverseTransformPosition(Joints[Wheel.JointName].Child->GetComponentLocation());
        LocalCentres.Add(Local);
        const FVector Centre = Candidate.TransformPosition(Local);
        const double BottomZ = Centre.Z - Wheel.RadiusMetres * 100.0 / FMath::Max(Candidate.GetRotation().GetUpVector().Z, MinNormalZ);
        const FVector Start(Centre.X, Centre.Y, BottomZ + (Initial ? 200.0 : MaxStepCm));
        const FVector End(Centre.X, Centre.Y, BottomZ - (Initial ? 200.0 : 30.0));
        FHitResult Hit;
        if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Query))
            return Fail(TEXT("missing ground beneath a wheel"));
        if (Hit.ImpactNormal.Z < MinNormalZ) return Fail(TEXT("support surface is too steep"));
        if (!Initial && FMath::Abs(Hit.ImpactPoint.Z - (BottomZ - GroundGapCm)) > MaxStepCm)
            return Fail(TEXT("wheel support exceeds the 5cm step/drop limit"));
        Contacts.Add(Hit.ImpactPoint);
    }

    // Least-squares support plane; wheel layout comes from the imported rig, never assumed axle dimensions.
    FVector Mean = FVector::ZeroVector;
    for (const FVector& P : Contacts) Mean += P;
    Mean /= Contacts.Num();
    double XX=0, XY=0, YY=0, XZ=0, YZ=0;
    for (const FVector& P : Contacts)
    {
        const FVector D=P-Mean;
        XX+=D.X*D.X; XY+=D.X*D.Y; YY+=D.Y*D.Y; XZ+=D.X*D.Z; YZ+=D.Y*D.Z;
    }
    const double Determinant=XX*YY-XY*XY;
    if (Determinant < 1.e-6) return Fail(TEXT("wheel support layout is degenerate"));
    const double A=(XZ*YY-YZ*XY)/Determinant, B=(YZ*XX-XZ*XY)/Determinant;
    Normal=FVector(-A,-B,1).GetSafeNormal();
    if (Normal.Z < MinNormalZ) return Fail(TEXT("support plane exceeds the slope limit"));
    for (const FVector& P : Contacts)
        if (FMath::Abs(P.Z-Mean.Z-A*(P.X-Mean.X)-B*(P.Y-Mean.Y)) > MaxStepCm)
            return Fail(TEXT("terrain is too uneven for the rigid preview chassis"));

    const FVector Forward=FRotator(0,Candidate.Rotator().Yaw,0).Vector();
    const FQuat Rotation=FRotationMatrix::MakeFromZX(Normal,Forward).ToQuat();
    FVector Location=Candidate.GetLocation();
    Location.Z=-DBL_MAX;
    for (int32 I=0; I<LocalCentres.Num(); ++I)
    {
        const FVector Offset=Rotation.RotateVector(LocalCentres[I]*Candidate.GetScale3D());
        const double SurfaceZ=Mean.Z+A*(Location.X+Offset.X-Mean.X)+B*(Location.Y+Offset.Y-Mean.Y);
        Location.Z=FMath::Max(Location.Z,SurfaceZ+WheelVisuals[I].RadiusMetres*100.0/Normal.Z+GroundGapCm-Offset.Z);
    }
    OutPose=FTransform(Rotation,Location,Candidate.GetScale3D());
    return true;
}

bool AVehicleRig::SnapToTerrain(FString& Error)
{
    FTransform Pose; TArray<FVector> Contacts; FVector Normal;
    if (!TerrainPose(GetActorTransform(),true,Pose,Contacts,Normal,Error)) return false;
    if (OverlapsTerrainParts(Pose))
    { Error=TEXT("terrain-supported spawn overlaps map geometry"); return false; }
    SetActorTransform(Pose,false,nullptr,ETeleportType::TeleportPhysics);
    GroundContacts=MoveTemp(Contacts); GroundNormal=Normal; TerrainBlockReason.Reset(); bTerrainFollowing=true;
    return true;
}

bool AVehicleRig::MoveOnTerrain(const FVector& LocalDeltaCm, double DeltaYawDegrees)
{
    TerrainBlockReason.Reset();
    const FTransform Before=GetActorTransform();
    const FBox Bounds=GetLocalCollisionBounds();
    if (!Bounds.IsValid || OverlapsWorld(Before,Bounds)) return false;
    const auto Parts=GetPartColliders();
    // Bounded substeps prevent skipping narrow barriers/edges. This is not a suspension/contact-force solve.
    const int32 Steps=FMath::Max(1,FMath::Max(FMath::CeilToInt(LocalDeltaCm.Size2D()/5.0),FMath::CeilToInt(FMath::Abs(DeltaYawDegrees)/2.0)));
    if (Steps>2000) { TerrainBlockReason=TEXT("motion exceeds the preview substep budget"); return false; }
    if (LocalDeltaCm.IsNearlyZero() && FMath::IsNearlyZero(DeltaYawDegrees)) return true;
    TArray<FVector> WheelPositions;
    for (const FWheelVisual& Wheel : WheelVisuals)
        WheelPositions.Add(Before.InverseTransformPosition(Joints[Wheel.JointName].Child->GetComponentLocation()));
    bool Accepted=true;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimTerrainSweep), false, this);
    Query.bFindInitialOverlaps=true;
    for (int32 I=0; I<Steps; ++I)
    {
        const FTransform Current=GetActorTransform();
        FTransform Candidate=Current;
        const FVector Delta=FRotator(0,Current.Rotator().Yaw,0).RotateVector(LocalDeltaCm/Steps);
        Candidate.AddToTranslation(Delta);
        Candidate.SetRotation((FQuat(FVector::UpVector,FMath::DegreesToRadians(DeltaYawDegrees/Steps))*Current.GetRotation()).GetNormalized());
        FTransform Supported; TArray<FVector> Contacts; FVector Normal;
        if (!TerrainPose(Candidate,false,Supported,Contacts,Normal,TerrainBlockReason)) { Accepted=false; break; }
        FHitResult Hit;bool Blocked=OverlapsTerrainParts(Supported);
        for(const auto& Part:Parts)
        {
            if(Blocked)break;
            const FTransform StartPose=Part.Relative*Current,EndPose=Part.Relative*Supported;
            const FQuat MidRotation=FQuat::Slerp(StartPose.GetRotation(),EndPose.GetRotation(),.5);
            const FVector Extent=Part.Bounds.GetExtent()*StartPose.GetScale3D().GetAbs();
            const double Angle=StartPose.GetRotation().AngularDistance(EndPose.GetRotation());
            const double Radius=Part.Relative.TransformPosition(Part.Bounds.GetCenter()).Size()+Extent.Size();
            const double Padding=2*Radius*FMath::Sin(Angle*.25);
            Blocked=GetWorld()->SweepSingleByChannel(Hit,StartPose.TransformPosition(Part.Bounds.GetCenter()),
                EndPose.TransformPosition(Part.Bounds.GetCenter()),MidRotation,ECC_Pawn,FCollisionShape::MakeBox(Extent+FVector(Padding)),Query);
        }
        if (Blocked)
        {
            Accepted=false; bLastSweepHit=true; LastSweepFraction=static_cast<double>(I)/Steps;
            LastBlockingActor=Hit.GetActor() ? Hit.GetActor()->GetName() : TEXT("candidate_overlap");
            LastImpactPoint=Hit.ImpactPoint; TerrainBlockReason=TEXT("terrain motion blocked by map geometry"); break;
        }
        SetActorTransform(Supported,false,nullptr,ETeleportType::TeleportPhysics);
        GroundContacts=MoveTemp(Contacts); GroundNormal=Normal;
    }
    RollWheels(Before,WheelPositions);
    return Accepted;
}
