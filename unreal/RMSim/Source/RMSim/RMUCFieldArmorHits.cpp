#include "RMUCGameReferee.h"
#include "RMUCArmorPresentation.h"
#include "RMUCUpperBaseArmor.h"

#include "Components/StaticMeshComponent.h"
#include "Engine/HitResult.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "ProceduralMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "VehicleRig.h"

namespace
{
struct FArmorFace
{
    FVector CentreM;
    FVector Normal;
    double HalfWidthM;
    double HalfHeightM;

    bool Contains(const FVector& ImpactM, const FVector& ImpactNormal) const
    {
        const FVector Side = FVector::CrossProduct(FVector::UpVector, Normal).GetSafeNormal();
        const FVector Up = FVector::CrossProduct(Normal, Side).GetSafeNormal();
        const FVector Offset = ImpactM - CentreM;
        return FMath::Abs(FVector::DotProduct(Offset, Normal)) <= 0.016
            && FMath::Abs(FVector::DotProduct(Offset, Side)) <= HalfWidthM
            && FMath::Abs(FVector::DotProduct(Offset, Up)) <= HalfHeightM
            && FMath::Abs(FVector::DotProduct(ImpactNormal, Normal)) >= 0.96;
    }
};

// Source frame of rmuc2027 V1.0.0 full-map-collision.glb after map import translation.
// See Official Rule Manual Figures 4-11 and 4-34. Planar patches measured on
// source occurrence 2603236 (material 12).
// Contact still requires the corresponding native blocking triangle and normal.
const FArmorFace OutpostArmor[] = {
    {{2.963479, 3.656870, 1.463643}, {-0.482963, -0.843005, -0.236833}, 0.065082, 0.057620},
    {{2.962845, 4.133481, 1.553378}, {-0.482963,  0.829455, -0.280628}, 0.065081, 0.057672},
    {{3.373133, 3.890812, 1.355896}, { 0.965926, -0.006775, -0.258730}, 0.065442, 0.058385},
};

// AIM Sentry AM02 faces 33, 38, 35, 34 measured on the visible CAD-derived body
// in its imported local UE frame. The 4 mm depth tolerance includes the recessed LEDs.
const FArmorFace SentryArmor[] = {
    {{ .228321,  0.,       .1407}, { .965926,  0.,      .258819}, .07068, .0635},
    {{ 0.,      -.234825,  .1407}, { 0.,      -.965926, .258819}, .07055, .0635},
    {{-.228327,  0.,       .1407}, {-.965926,  0.,      .258819}, .07055, .0635},
    {{ 0.,       .228323,  .1407}, { 0.,       .965926, .258819}, .07055, .0635},
};
}

void ARMUCGameReferee::BindSentryArmorPresentation(AVehicleRig& Vehicle, FVehicleArmorLight& Armor)
{
    using namespace RMUCArmorPresentation;
    if (Vehicle.GetVehicleId() != TEXT("aim_sentry")) return;
    TInlineComponentArray<UStaticMeshComponent*> Meshes(&Vehicle);
    for (auto* Body : Meshes)
    {
        if (!Body || !Body->IsVisible() || !Body->GetStaticMesh()) continue;
        if (Body->GetStaticMesh()->GetFName() != FName(TEXT("body"))) continue;
        if (!SentrySticker) SentrySticker = LoadSticker(TEXT("sentry"));
        UMaterialInstanceDynamic* Finish = MakeFinish(Vehicle, SentrySticker);
        if (const int32 Slot = Body->GetMaterialIndex(SentryLampSlot); Slot != INDEX_NONE)
        {
            if (auto* Lamp = MakeEmitter(Vehicle, true))
            {
                Body->SetMaterial(Slot, Lamp);
                Armor.NativeBars = Lamp;
            }
        }
        int32 Faces = 0;
        for (const FArmorFace& Face : SentryArmor)
        {
            if (auto* Cover = Mount(Vehicle, *Body, Face.CentreM * 100., Face.Normal, .025, SentryTag, Finish))
            {
                ++Faces;
                Armor.SceneLights.Add(AddPointLight(Vehicle, *Cover, FVector(6.5, 0, 0), TeamColor(true), 45.f));
            }
        }
        UE_LOG(LogRMSim, Display, TEXT("RMUC_ARMOR_PRESENTATION kind=sentry vehicle=%s faces=%d native_bars=%d"),
            *Vehicle.GetName(), Faces, Armor.NativeBars.IsValid());
    }
}

int32 ARMUCGameReferee::ClassifyVehicleArmorHit(const FHitResult& Hit, const AVehicleRig* Vehicle)
{
    const auto* Body = Cast<UStaticMeshComponent>(Hit.GetComponent());
    if (!Vehicle || Vehicle->GetVehicleId() != TEXT("aim_sentry") || Hit.GetActor() != Vehicle ||
        !Hit.bBlockingHit || Hit.bStartPenetrating || !Body || !Body->GetStaticMesh() ||
        Body->GetStaticMesh()->GetFName() != FName(TEXT("body")))
    {
        return INDEX_NONE;
    }

    const FTransform& Transform = Body->GetComponentTransform();
    const FVector PointM = .01 * Transform.InverseTransformPosition(Hit.ImpactPoint);
    const FVector Normal = Transform.InverseTransformVectorNoScale(Hit.ImpactNormal).GetSafeNormal();
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(SentryArmor); ++Index)
    {
        const FArmorFace& Face = SentryArmor[Index];
        if (FMath::Abs(FVector::DotProduct(PointM - Face.CentreM, Face.Normal)) <= .004 &&
            FVector::DotProduct(Normal, Face.Normal) >= .975 && Face.Contains(PointM, Normal))
        {
            return Index;
        }
    }
    return INDEX_NONE;
}

bool ARMUCGameReferee::ClassifyFieldStructureHit(
    const FHitResult& Hit,
    const AStaticMeshActor* BaseCollision,
    const AStaticMeshActor* CentrePlatformCollision,
    bool& bTargetRed,
    bool& bOutpost,
    const UPrimitiveComponent* CentreVisibility)
{
    if (!Hit.bBlockingHit || Hit.bStartPenetrating)
    {
        return false;
    }

    const bool bHitOutpostGroup = CentrePlatformCollision && Hit.GetActor() == CentrePlatformCollision;
    const bool bHitBaseGroup = BaseCollision && Hit.GetActor() == BaseCollision;
    if (!bHitOutpostGroup && !bHitBaseGroup)
    {
        return false;
    }

    const AStaticMeshActor* Source = bHitOutpostGroup ? CentrePlatformCollision : BaseCollision;
    const UStaticMeshComponent* Mesh = Source->GetStaticMeshComponent();
    if (!Mesh || (Hit.GetComponent() != Mesh && !(bHitOutpostGroup && CentreVisibility &&
        Hit.GetComponent() == CentreVisibility)))
    {
        return false;
    }

    const FTransform& Transform = Mesh->GetComponentTransform();
    const FVector LocalCm = Transform.InverseTransformPosition(Hit.ImpactPoint);
    const FVector LocalNormal = Transform.InverseTransformVectorNoScale(Hit.ImpactNormal);
    const bool bRedCandidate = LocalCm.X >= 0.0;
    if (bHitBaseGroup)
    {
        if (!RMUCUpperBaseArmor::Contains(LocalCm, LocalNormal, bRedCandidate ? 0 : 1))
        {
            return false;
        }
        bTargetRed = bRedCandidate;
        bOutpost = false;
        return true;
    }

    const double TeamSign = bRedCandidate ? 1.0 : -1.0;
    const FVector RedFrameM(LocalCm.X * TeamSign * 0.01, LocalCm.Y * -TeamSign * 0.01, LocalCm.Z * 0.01);
    const FVector RedFrameNormal(LocalNormal.X * TeamSign, LocalNormal.Y * -TeamSign, LocalNormal.Z);
    for (const FArmorFace& Face : OutpostArmor)
    {
        if (Face.Contains(RedFrameM, RedFrameNormal))
        {
            bTargetRed = bRedCandidate;
            bOutpost = bHitOutpostGroup;
            return true;
        }
    }
    return false;
}
