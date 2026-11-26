#include "RMUCArmorPresentation.h"

#include "Components/PointLightComponent.h"
#include "Engine/Texture2D.h"
#include "GameFramework/Actor.h"
#include "ImageUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Math/RotationMatrix.h"
#include "Misc/Paths.h"
#include "ProceduralMeshComponent.h"

namespace RMUCArmorPresentation
{
namespace
{
constexpr double HalfWidthCm = 6.75;
constexpr double HalfHeightCm = 6.25;
constexpr double CornerCm = .76;
const FName StickerParameter(TEXT("ArmorSticker"));

struct FSection
{
    TArray<FVector> Vertices;
    TArray<int32> Indices;
    TArray<FVector2D> UV;

    void Add(const FVector& P, bool bFace)
    {
        Vertices.Add(P);
        // The exterior camera looks along -X. Positive local Y is its screen left.
        UV.Add(bFace ? FVector2D(.5 - P.Y / (2 * HalfWidthCm), .5 - P.Z / (2 * HalfHeightCm)) : FVector2D::ZeroVector);
    }

    void Quad(const FVector& A, const FVector& B, const FVector& C, const FVector& D, bool bFace)
    {
        const int32 Start = Vertices.Num();
        for (const FVector& P : {A, B, C, D}) Add(P, bFace);
        Indices.Append({Start, Start + 1, Start + 2, Start, Start + 2, Start + 3});
    }

    void Apply(UProceduralMeshComponent& Comp, int32 Section)
    {
        TArray<FVector> Normals;
        TArray<FProcMeshTangent> Tangents;
        Normals.Init(FVector::ForwardVector, Vertices.Num());
        Tangents.Init(FProcMeshTangent(-FVector::RightVector, false), Vertices.Num());
        Comp.CreateMeshSection_LinearColor(Section, Vertices, Indices, Normals, UV, {}, Tangents, false);
    }
};
}

FLinearColor TeamColor(bool bRed)
{
    return bRed ? FLinearColor(.91f, .11f, .028f, 1.f) : FLinearColor(.029f, .22f, 1.05f, 1.f);
}

float LampLevel(bool bAlive, double FlashSeconds)
{
    if (!bAlive) return 0.f;
    return FlashSeconds > 0. && (FMath::CeilToInt(FlashSeconds * 24.) & 1) == 0 ? .18f : 1.f;
}

UMaterialInstanceDynamic* MakeEmitter(AActor& Owner, bool bRed, float Gain)
{
    auto* Source = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/RMSim/Maps/rmuc2027/ArmorPresentation/TeamEmitter.TeamEmitter"), nullptr, LOAD_NoWarn);
    auto* Emitter = Source ? UMaterialInstanceDynamic::Create(Source, &Owner) : nullptr;
    if (Emitter) Emitter->SetVectorParameterValue(LampColor, TeamColor(bRed) * Gain);
    return Emitter;
}

UPointLightComponent* AddPointLight(AActor& Owner, USceneComponent& Parent, const FVector& LocalPosition,
    const FLinearColor& Color, float RadiusCm, float Candela)
{
    auto* Light = NewObject<UPointLightComponent>(&Owner);
    Owner.AddInstanceComponent(Light);
    Light->SetupAttachment(&Parent);
    Light->SetMobility(EComponentMobility::Movable);
    Light->SetRelativeLocation(LocalPosition);
    Light->SetAttenuationRadius(RadiusCm);
    Light->SetUseInverseSquaredFalloff(true);
    Light->SetIntensityUnits(ELightUnits::Candelas);
    Light->SetCastShadows(false);
    Light->SetVolumetricScatteringIntensity(0.f);
    Light->SetSourceRadius(1.5f);
    SetPointLight(Light, Color, Candela);
    Light->RegisterComponent();
    return Light;
}

void SetPointLight(UPointLightComponent* Light, const FLinearColor& Color, float Candela)
{
    if (!Light) return;
    const bool bOn = Candela > .001f;
    Light->SetVisibility(bOn);
    Light->SetIntensity(bOn ? Candela : 0.f);
    if (bOn)
    {
        const float Peak = FMath::Max3(Color.R, Color.G, Color.B);
        Light->SetLightColor(Peak > 1.f ? Color / Peak : Color);
    }
}

UTexture2D* LoadSticker(const TCHAR* Pattern)
{
    const FString Path = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(),
        TEXT("../../assets/ui/armor_stickers"), FString(Pattern) + TEXT("_sticker.png")));
    UTexture2D* Texture = FPaths::FileExists(Path) ? FImageUtils::ImportFileAsTexture2D(Path) : nullptr;
    if (!Texture)
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_ARMOR_PRESENTATION_MISSING pattern=%s prepare tools/unreal/Prepare-RmucArmorPresentation.ps1"), Pattern);
        return nullptr;
    }
    Texture->NeverStream = true;
    Texture->SRGB = true;
    Texture->Filter = TF_Bilinear;
    Texture->UpdateResource();
    return Texture;
}

UMaterialInstanceDynamic* MakeFinish(AActor& Owner, UTexture2D* Sticker)
{
    if (!Sticker) return nullptr;
    UMaterialInterface* Base = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/RMSim/Maps/rmuc2027/ArmorPresentation/NeutralArmorFace.NeutralArmorFace"), nullptr, LOAD_NoWarn);
    if (!Base) return nullptr;
    UMaterialInstanceDynamic* Instance = UMaterialInstanceDynamic::Create(Base, &Owner);
    Instance->SetTextureParameterValue(StickerParameter, Sticker);
    return Instance;
}

void SetLamp(UMaterialInstanceDynamic* Material, bool bRed, bool bAlive, double FlashSeconds)
{
    if (!Material) return;
    const float Level = LampLevel(bAlive, FlashSeconds);
    Material->SetVectorParameterValue(LampColor, Level > 0 ? TeamColor(bRed) * Level : FLinearColor(.008f, .008f, .008f));
}

UProceduralMeshComponent* Mount(AActor& Owner, USceneComponent& Parent, const FVector& CentreCm,
    const FVector& Normal, double CoverDepthCm, FName Tag,
    UMaterialInstanceDynamic* Finish)
{
    if (!Finish) return nullptr;
    UProceduralMeshComponent* Comp = NewObject<UProceduralMeshComponent>(&Owner);
    Owner.AddInstanceComponent(Comp);
    Comp->ComponentTags.Add(Tag);
    Comp->SetMobility(EComponentMobility::Movable);
    Comp->SetupAttachment(&Parent);
    Comp->SetRelativeTransform(FTransform(FRotationMatrix::MakeFromXZ(Normal, FVector::UpVector).ToQuat(),
        CentreCm + CoverDepthCm * Normal));
    Comp->SetCollisionProfileName(TEXT("NoCollision"));
    Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Comp->SetGenerateOverlapEvents(false);
    Comp->SetCanEverAffectNavigation(false);
    Comp->SetCastShadow(false);
    Comp->SetReceivesDecals(false);
    Comp->SetAffectDynamicIndirectLighting(false);
    Comp->SetAffectDistanceFieldLighting(false);

    if (Tag == SentryTag)
    {
        // Measured from the four planar AM02 faces of the pinned AIM body FBX.
        // The waist leaves the original recessed LEDs visible, including their bevels.
        constexpr double Wide = 6.70, Waist = 6.14;
        constexpr double WaistZ = 2.44, FlangeZ = 2.96, ChamferZ = 5.72, EdgeZ = 6.20;
        constexpr double HoleY = 4.75, HoleZ = 5.22, HoleRadius = .39, HoleBox = .48;
        FSection Face;
        const auto Band = [&](double Sign, double Y0, double Y1, double Z0, double Z1)
        {
            Face.Quad({0., Y0, Sign * Z0}, {0., Y1, Sign * Z0},
                {0., Y1, Sign * Z1}, {0., Y0, Sign * Z1}, true);
        };
        Band(1., -Waist, Waist, -WaistZ, WaistZ);
        for (const double Sign : {1., -1.})
        {
            Face.Quad({0., -Waist, Sign * WaistZ}, {0., Waist, Sign * WaistZ},
                {0., Wide, Sign * FlangeZ}, {0., -Wide, Sign * FlangeZ}, true);
            Band(Sign, -Wide, Wide, FlangeZ, HoleZ - HoleBox);
            Band(Sign, -Wide, Wide, HoleZ + HoleBox, ChamferZ);
            Band(Sign, -Wide, -HoleY - HoleBox, HoleZ - HoleBox, HoleZ + HoleBox);
            Band(Sign, -HoleY + HoleBox, HoleY - HoleBox, HoleZ - HoleBox, HoleZ + HoleBox);
            Band(Sign, HoleY + HoleBox, Wide, HoleZ - HoleBox, HoleZ + HoleBox);
            for (const double CentreY : {-HoleY, HoleY})
            {
                const auto Radial = [=](int32 Index, bool bOuter)
                {
                    const double Angle = 2. * PI * (Index % 24) / 24.;
                    const double Y = FMath::Cos(Angle), Z = FMath::Sin(Angle);
                    const double Radius = bOuter ? HoleBox / FMath::Max(FMath::Abs(Y), FMath::Abs(Z)) : HoleRadius;
                    return FVector(0., CentreY + Y * Radius, Sign * (HoleZ + Z * Radius));
                };
                for (int32 I = 0; I < 24; ++I)
                    Face.Quad(Radial(I, false), Radial(I, true), Radial(I + 1, true), Radial(I + 1, false), true);
            }
            Face.Quad({0., -Wide, Sign * ChamferZ}, {0., Wide, Sign * ChamferZ},
                {0., Wide - .50, Sign * EdgeZ}, {0., -Wide + .50, Sign * EdgeZ}, true);
        }
        Face.Apply(*Comp, 0);
    }
    else
    {
        const double W = HalfWidthCm;
        const double H = HalfHeightCm, C = CornerCm;
        const FVector Rim[] = {{0,W,H-C}, {0,W-C,H}, {0,-W+C,H}, {0,-W,H-C},
            {0,-W,-H+C}, {0,-W+C,-H}, {0,W-C,-H}, {0,W,-H+C}};
        FSection Face;
        for (const FVector& P : Rim) Face.Add(P, true);
        for (int32 I = 1; I + 1 < UE_ARRAY_COUNT(Rim); ++I)
            Face.Indices.Append({0, I + 1, I});
        const FVector Back(-CoverDepthCm - .04, 0, 0);
        for (int32 I = 0; I < UE_ARRAY_COUNT(Rim); ++I)
        {
            const FVector& A = Rim[I];
            const FVector& B = Rim[(I + 1) % UE_ARRAY_COUNT(Rim)];
            Face.Quad(A, B, B + Back, A + Back, false);
        }
        Face.Apply(*Comp, 0);
    }
    Comp->SetMaterial(0, Finish);
    Comp->RegisterComponent();
    return Comp;
}
}
