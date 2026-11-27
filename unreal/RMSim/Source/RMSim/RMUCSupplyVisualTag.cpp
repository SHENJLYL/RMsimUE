#include "RMUCGameReferee.h"
#include "VehicleRig.h"

#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Dom/JsonObject.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
const TCHAR* AssetPath = TEXT("../../assets/maps/rmuc2027/supply-tags");
constexpr double TagHalfWidthCm = 17.0;
constexpr double MaxRollDegrees = 15.0;
constexpr double CentreHalfExtent = .5;  // half of the image in each axis

bool ReadTagPosition(const FJsonObject& Placement, const TCHAR* Key, FVector& Centre)
{
    const TArray<TSharedPtr<FJsonValue>>* Coordinates = nullptr;
    FVector PositionM;
    if (!Placement.TryGetArrayField(Key, Coordinates) || Coordinates->Num() != 3 ||
        !(*Coordinates)[0]->TryGetNumber(PositionM.X) || !(*Coordinates)[1]->TryGetNumber(PositionM.Y) ||
        !(*Coordinates)[2]->TryGetNumber(PositionM.Z) || PositionM.ContainsNaN())
    {
        return false;
    }
    Centre = FVector(PositionM.X, -PositionM.Y, PositionM.Z) * 100.0;
    return true;
}

}

void ARMUCGameReferee::InstallSupplyVisualTags()
{
    const FString Directory = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(), AssetPath));
    auto* Dark = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/RMSim/Maps/rmuc2027/SupplyTags/SupplyTagSurface.SupplyTagSurface"));
    auto* Tile = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    auto* Light = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    FString Contents;
    TSharedPtr<FJsonObject> Placement;
    double SizeM = 0.0;
    if (!Dark || !Light || !Tile ||
        !FFileHelper::LoadFileToString(Contents, *FPaths::Combine(Directory, TEXT("placement.json"))) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Contents), Placement) ||
        !Placement.IsValid() || !Placement->TryGetNumberField(TEXT("tag_size_m"), SizeM) ||
        !FMath::IsNearlyEqual(SizeM * 50.0, TagHalfWidthCm))
    {
        UE_LOG(LogTemp, Warning, TEXT("RMUC_SUPPLY_VISUAL_TAG invalid placement"));
        return;
    }
    for (const FieldTeam Team : FRMUCFieldRules::Teams)
    {
        const bool bRed = Team == FieldTeam::Red;
        const TCHAR* Key = bRed ? TEXT("red") : TEXT("blue");
        FVector Centre;
        if (!ReadTagPosition(*Placement, Key, Centre)) continue;
        TArray<FString> Pattern;
        if (!Placement->TryGetStringArrayField(FString(Key) + TEXT("_pattern"), Pattern) || Pattern.Num() != 7 ||
            Pattern.ContainsByPredicate([](const FString& Row) {
                if (Row.Len() != 7) return true;
                for (const TCHAR Cell : Row)
                    if (Cell != '0' && Cell != '1') return true;
                return false;
            }))
        {
            UE_LOG(LogTemp, Warning, TEXT("RMUC_SUPPLY_VISUAL_TAG invalid pattern side=%s"), Key);
            continue;
        }

        auto* Plate = NewObject<USceneComponent>(this);
        AddInstanceComponent(Plate);
        Plate->SetupAttachment(GetRootComponent());
        Plate->RegisterComponent();
        Plate->SetWorldLocation(Centre);
        const double Sign = bRed ? 1.0 : -1.0;
        auto* Black = UMaterialInstanceDynamic::Create(Dark, this);
        Black->SetVectorParameterValue(TEXT("TagInk"), FLinearColor(.002f, .002f, .002f));
        auto MakeInk = [&](UMaterialInterface* Material)
        {
            auto* Mesh = NewObject<UInstancedStaticMeshComponent>(this);
            AddInstanceComponent(Mesh);
            Mesh->SetupAttachment(Plate);
            Mesh->SetStaticMesh(Tile);
            Mesh->SetMaterial(0, Material);
            Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
            Mesh->SetCanEverAffectNavigation(false);
            Mesh->SetCastShadow(false);
            Mesh->RegisterComponent();
            return Mesh;
        };
        auto* Back = MakeInk(Black);
        Back->AddInstance(FTransform(FQuat::Identity, FVector::ZeroVector, FVector(.004, .34, .34)));
        auto* Ink = MakeInk(Light);
        auto Print = [&](double Left, double Right, double Bottom, double Top)
        {
            Ink->AddInstance(FTransform(FQuat::Identity, FVector(Sign*.25, -Sign*(Left+Right)*.5, (Top+Bottom)*.5),
                FVector(.004, (Right-Left)*.01, (Top-Bottom)*.01)));
        };
        Print(-17., 17., 14., 17.);
        Print(-17., 17., -17., -14.);
        Print(-17., -14., -14., 14.);
        Print(14., 17., -14., 14.);
        for (int32 Row = 0; Row < 7; ++Row)
            for (int32 Col = 0; Col < 7; ++Col)
                if (Pattern[Row][Col] == '1')
                    Print(-14. + 4.*Col, -10. + 4.*Col, 10. - 4.*Row, 14. - 4.*Row);
        SupplyTagMeshes[FRMUCFieldRules::Index(Team)] = Plate;
        UE_LOG(LogTemp, Display, TEXT("RMUC_SUPPLY_VISUAL_TAG side=%s centre_cm=%s size_cm=34"), Key, *Centre.ToString());
    }
}

bool ARMUCGameReferee::IsSupplyVisualTagVisible(const AVehicleRig& Vehicle, FieldTeam Team,
    const FVector* ReaderSurfaceM) const
{
    const bool bRed = Team == FieldTeam::Red;
    const auto* Zone = FieldZones.Find(ERMUCFieldZoneKind::Supply,
        bRed ? ERMUCFieldZoneSide::Red : ERMUCFieldZoneSide::Blue);
    const auto* Plate = SupplyTagMeshes[FRMUCFieldRules::Index(Team)].Get();
    const auto* Camera = Vehicle.GetCameras().FindRef(TEXT("aim_camera"));
    if (!Zone || !Plate || !Camera) return false;

#if WITH_DEV_AUTOMATION_TESTS
    static const bool bProbe = FParse::Param(FCommandLine::Get(), TEXT("RefereeSupplyVisionProbe"));
    static bool bReported = false;
    const bool bReport = bProbe && !bReported && &Vehicle == Rig && MatchElapsedSeconds >= .35;
    if (bReport) bReported = true;
#endif

    FVector SurfaceM = FVector::ZeroVector;
    if (!ReaderSurfaceM)
    {
        ObserveFieldPoints(Vehicle, nullptr, &SurfaceM);
        ReaderSurfaceM = &SurfaceM;
    }
    const bool bInSupply = Zone->Contains(*ReaderSurfaceM);
#if WITH_DEV_AUTOMATION_TESTS
    if (!bInSupply && !bReport) return false;
#else
    if (!bInSupply) return false;
#endif

    const FVector CameraCentre = Camera->GetComponentLocation();
    const FVector Centre = Plate->GetComponentLocation();
    const double Sign = bRed ? 1.0 : -1.0;
    const FTransform Optical = Camera->GetComponentTransform();
    const FVector Local = Optical.InverseTransformPositionNoScale(Centre);
    const FRotator Rotation = Optical.Rotator();
#if WITH_DEV_AUTOMATION_TESTS
    if (bReport)
        UE_LOG(LogTemp, Display, TEXT("RMUC_SUPPLY_VISUAL_PROBE region=%d surface_m=%s camera_cm=%s camera_rot=%s tag_in_camera_cm=%s"),
            bInSupply, *ReaderSurfaceM->ToString(), *CameraCentre.ToString(), *Rotation.ToString(), *Local.ToString());
#endif
    if (!bInSupply) return false;
    if ((CameraCentre.X - Centre.X) * Sign <= 0.0 || Local.X <= 0.0 ||
        FMath::Abs(FRotator::NormalizeAxis(Rotation.Roll)) > MaxRollDegrees)
    {
        return false;
    }

    const double SensorFov = Vehicle.GetCameraSensorFovDegrees(TEXT("aim_camera"));
    const double TanHorizontal = FMath::Tan(FMath::DegreesToRadians(SensorFov * .5));
    const double Aspect = Camera->TextureTarget && Camera->TextureTarget->SizeY > 0
        ? double(Camera->TextureTarget->SizeX) / Camera->TextureTarget->SizeY : 16.0 / 9.0;
    const double TanVertical = TanHorizontal / Aspect;
    if (TanVertical <= 0.0 || FMath::Abs(Local.Y / Local.X) > CentreHalfExtent * TanHorizontal ||
        FMath::Abs(Local.Z / Local.X) > CentreHalfExtent * TanVertical)
    {
        return false;
    }

    const FVector Right(0.0, -Sign * TagHalfWidthCm, 0.0);
    const FVector Up(0.0, 0.0, TagHalfWidthCm);
    FCollisionQueryParams Params(SCENE_QUERY_STAT(RMSimSupplyVisualTag), true, &Vehicle);
    Params.AddIgnoredActor(this);
    for (const FVector& Sample : {Centre, Centre-Right+Up, Centre+Right+Up, Centre+Right-Up, Centre-Right-Up})
    {
        const FVector P = Optical.InverseTransformPositionNoScale(Sample);
        if (P.X <= 0.0 || FMath::Abs(P.Y / P.X) > TanHorizontal || FMath::Abs(P.Z / P.X) > TanVertical)
            return false;
        FHitResult Occlusion;
        if (GetWorld()->LineTraceSingleByChannel(Occlusion, CameraCentre, Sample, ECC_Visibility, Params))
        {
#if WITH_DEV_AUTOMATION_TESTS
            if (bReport)
                UE_LOG(LogTemp, Display, TEXT("RMUC_SUPPLY_VISUAL_PROBE occlusion_actor=%s component=%s hit_cm=%s"),
                    *GetNameSafe(Occlusion.GetActor()), *GetNameSafe(Occlusion.GetComponent()),
                    *Occlusion.ImpactPoint.ToString());
#endif
            return false;
        }
    }
    return true;
}
