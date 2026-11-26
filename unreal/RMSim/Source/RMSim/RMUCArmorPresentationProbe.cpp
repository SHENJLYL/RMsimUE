#include "RMUCGameReferee.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "RMUCArmorPresentation.h"
#include "Components/PointLightComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "HAL/FileManager.h"
#include "ImageUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ProceduralMeshComponent.h"
#include "RenderingThread.h"
#include "VehicleRig.h"

namespace
{
TArray<UProceduralMeshComponent*> GetFaces(AActor& Owner, FName Tag)
{
    TInlineComponentArray<UProceduralMeshComponent*> Components(&Owner);
    TArray<UProceduralMeshComponent*> Faces;
    for (auto* Component : Components)
        if (Component->ComponentHasTag(Tag)) Faces.Add(Component);
    Faces.Sort([](const UProceduralMeshComponent& A, const UProceduralMeshComponent& B) {
        return A.GetUniqueID() < B.GetUniqueID();
    });
    return Faces;
}

bool HasSentryStickerProfile(UProceduralMeshComponent& Face)
{
    const FProcMeshSection* Mesh = Face.GetProcMeshSection(0);
    if (!Mesh || Mesh->ProcIndexBuffer.IsEmpty()) return false;
    for (const auto& Vertex : Mesh->ProcVertexBuffer)
        if (!FMath::IsNearlyZero(Vertex.Position.X, .001)) return false;

    const auto Contains = [Mesh](double Y, double Z)
    {
        const auto Project = [](const FVector& P) { return FVector2D(P.Y, P.Z); };
        for (int32 I = 0; I + 2 < Mesh->ProcIndexBuffer.Num(); I += 3)
        {
            const auto A = Project(Mesh->ProcVertexBuffer[Mesh->ProcIndexBuffer[I]].Position);
            const auto B = Project(Mesh->ProcVertexBuffer[Mesh->ProcIndexBuffer[I + 1]].Position);
            const auto C = Project(Mesh->ProcVertexBuffer[Mesh->ProcIndexBuffer[I + 2]].Position);
            if (FMath::Abs(FVector2D::CrossProduct(B - A, C - A)) < 1.e-8) continue;
            const auto Weights = FMath::GetBaryCentric2D(FVector2D(Y, Z), A, B, C);
            if (FMath::Min3(Weights.X, Weights.Y, Weights.Z) >= -1.e-6) return true;
        }
        return false;
    };
    for (const double YSign : {-1., 1.})
    {
        if (!Contains(YSign * 6., 0.) || Contains(YSign * 6.4, 0.)) return false;
        for (const double ZSign : {-1., 1.})
            if (!Contains(YSign * 6.45, ZSign * 4.) || Contains(YSign * 4.75, ZSign * 5.22)) return false;
    }
    return true;
}

bool ReadLamp(const UProceduralMeshComponent& Face, FLinearColor& Color)
{
    const UMaterialInterface* Material = Face.GetMaterial(0);
    if (Face.ComponentHasTag(RMUCArmorPresentation::SentryTag))
    {
        const auto* Body = Cast<UStaticMeshComponent>(Face.GetAttachParent());
        if (!Body) return false;
        Material = Body->GetMaterial(Body->GetMaterialIndex(RMUCArmorPresentation::SentryLampSlot));
    }
    const auto* Dynamic = Cast<UMaterialInstanceDynamic>(Material);
    return Dynamic && Dynamic->GetVectorParameterValue(FMaterialParameterInfo(RMUCArmorPresentation::LampColor), Color);
}
}

bool ARMUCGameReferee::VerifyArmorPresentation(const FString& Directory)
{
    if (!Rig || !EnemyRig || !bPhysicalMatch || !IFileManager::Get().MakeDirectory(*Directory, true)) return false;
    USceneCaptureComponent2D* const* AimCamera = Rig->GetCameras().Find(TEXT("aim_camera"));
    USceneCaptureComponent2D* Camera = AimCamera ? *AimCamera : nullptr;
    if (!Camera || !Camera->TextureTarget) return false;
    const FTransform SavedCamera = Camera->GetRelativeTransform();

    using namespace RMUCArmorPresentation;
    const auto Red = GetFaces(*VehicleFor(FieldTeam::Red), SentryTag);
    const auto Blue = GetFaces(*VehicleFor(FieldTeam::Blue), SentryTag);
    TArray<UProceduralMeshComponent*> Posts;
    for (TActorIterator<AActor> It(GetWorld()); It; ++It)
        Posts.Append(GetFaces(**It, OutpostTag));
    Posts.StableSort([](const UProceduralMeshComponent& A, const UProceduralMeshComponent& B) {
        return A.GetAttachParent()->GetRelativeLocation().X > B.GetAttachParent()->GetRelativeLocation().X;
    });
    if (Red.Num() != 4 || Blue.Num() != 4 || Posts.Num() != 6)
    {
        UE_LOG(LogTemp, Error, TEXT("RMUC_ARMOR_PROOF missing faces red=%d blue=%d posts=%d"), Red.Num(), Blue.Num(), Posts.Num());
        return false;
    }

    int32 Captures = 0;
    const auto CaptureAt = [&](const FVector& Origin, const FVector& Centre, const FString& Label) -> bool
    {
        Camera->SetWorldLocationAndRotation(Origin, (Centre - Origin).Rotation());
        // Discard the first frame at a new pose so temporal exposure and material updates settle.
        Camera->CaptureScene();
        FlushRenderingCommands();
        Camera->CaptureScene();
        FlushRenderingCommands();
        FImage Frame;
        TArray64<uint8> Bytes;
        if (!FImageUtils::GetRenderTargetImage(Camera->TextureTarget, Frame) ||
            !FImageUtils::CompressImage(Bytes, TEXT("PNG"), Frame) ||
            !FFileHelper::SaveArrayToFile(Bytes, *FPaths::Combine(Directory, Label + TEXT(".png"))))
            return false;
        ++Captures;
        return true;
    };
    const auto Capture = [&](const UProceduralMeshComponent& Face, const FString& Label, double Skew = 0.) -> bool
    {
        const FVector Centre = Face.GetComponentLocation();
        const FVector Origin = Centre + Face.GetForwardVector() * 151. + Face.GetRightVector() * Skew;
        return Face.GetCollisionEnabled() == ECollisionEnabled::NoCollision && Face.GetAttachParent()
            && CaptureAt(Origin, Centre, Label);
    };

    bool bPassed = true;
    for (int32 Side = 0; Side < 2; ++Side)
    {
        const auto& Vehicle = Side == 0 ? Red : Blue;
        const TCHAR* Team = Side == 0 ? TEXT("red") : TEXT("blue");
        for (int32 I = 0; I < Vehicle.Num(); ++I)
        {
            const bool bProfile = HasSentryStickerProfile(*Vehicle[I]);
            UE_LOG(LogTemp, Display, TEXT("RMUC_ARMOR_STICKER_PROFILE team=%s face=%d passed=%d"), Team, I, bProfile);
            bPassed &= bProfile;
            bPassed &= Capture(*Vehicle[I], FString::Printf(TEXT("sentry-%s-%d"), Team, I));
        }
        for (int32 I = 0; I < 3; ++I)
            bPassed &= Capture(*Posts[Side * 3 + I], FString::Printf(TEXT("outpost-%s-%d"), Team, I));
    }
    bPassed &= Capture(*Red[0], TEXT("sentry-red-oblique"), 33.);
    bPassed &= Capture(*Blue[0], TEXT("sentry-blue-oblique"), -33.);
    for (const FieldTeam Team : FRMUCFieldRules::Teams)
    {
        const bool bRed = Team == FieldTeam::Red;
        TInlineComponentArray<UStaticMeshComponent*> Parts(VehicleFor(Team));
        int32 OriginalWindows = 0;
        for (const auto* Part : Parts)
        {
            if (!Part || !Part->IsVisible() || !Part->GetStaticMesh()) continue;
            for (const TCHAR* Name : {TEXT("aim_sentry_mid360_blue_glass"), TEXT("aim_sentry_radar_glass")})
            {
                const int32 Slot = Part->GetMaterialIndex(FName(Name));
                if (Slot == INDEX_NONE) continue;
                bPassed &= Part->GetMaterial(Slot) == Part->GetStaticMesh()->GetMaterial(Slot);
                ++OriginalWindows;
            }
        }
        bPassed &= OriginalWindows >= 2;
        FTransform Yaw;
        if (VehicleFor(Team)->GetSensorParentWorld(TEXT("yaw_link"), Yaw))
        {
            const FVector Centre = Yaw.TransformPosition(FVector(20.351, -7.376, 17.595));
            bPassed &= CaptureAt(Centre + Yaw.TransformVectorNoScale(FVector(73, -68, 36)), Centre,
                bRed ? TEXT("radar-red") : TEXT("radar-blue"));
        }
        else bPassed = false;
    }
    UPointLightComponent* Spill = nullptr;
    for (USceneComponent* Child : Posts[0]->GetAttachChildren())
        if (auto* Light = Cast<UPointLightComponent>(Child)) { Spill = Light; break; }
    if (Spill && Spill->IsVisible() && Spill->Intensity > 0.)
    {
        const float Original = Spill->Intensity;
        bPassed &= Capture(*Posts[0], TEXT("direct-light-on"));
        SetPointLight(Spill, TeamColor(true), 0.f);
        bPassed &= Capture(*Posts[0], TEXT("direct-light-off"));
        SetPointLight(Spill, TeamColor(true), Original);
    }
    else bPassed = false;

    const FVector ShotCentre = Red[0]->GetComponentLocation() + Red[0]->GetForwardVector() * 34. + FVector(0, 0, 7);
    const int32 FirstProjectile = Projectiles.Num();
    for (const FieldTeam Team : FRMUCFieldRules::Teams)
    {
        const bool bRed = Team == FieldTeam::Red;
        const FVector Position = ShotCentre + Red[0]->GetRightVector() * (bRed ? -5. : 5.);
        bPassed &= RequestRosShot(VehicleFor(Team), FTransform(FRotator::ZeroRotator, Position)) == TEXT("accepted");
        if (Projectiles.Num() > FirstProjectile)
        {
            const FProjectile& Shot = Projectiles.Last();
            bPassed &= Shot.bFromRed == bRed && Shot.Light.IsValid() && Shot.Light->Intensity > 0.f;
        }
    }
    bPassed &= Projectiles.Num() == FirstProjectile + 2;
    bPassed &= CaptureAt(ShotCentre + Red[0]->GetForwardVector() * 67., ShotCentre, TEXT("team-projectiles"));

    const FTransform Local = Posts[0]->GetRelativeTransform();
    const FTransform World = Posts[0]->GetComponentTransform();
    FieldRules.Step(.35, MatchElapsedSeconds + .35, {});
    StepFieldDevices(0.);
    const bool bFollowed = Local.Equals(Posts[0]->GetRelativeTransform(), .001)
        && !World.Equals(Posts[0]->GetComponentTransform(), .005);
    bPassed &= bFollowed;
    bPassed &= Capture(*Posts[0], TEXT("outpost-red-rotated"), 28.);

    const auto CheckColor = [](const UProceduralMeshComponent& Face, bool bRed, bool bAlive)
    {
        FLinearColor Color;
        if (!ReadLamp(Face, Color)) return false;
        return bAlive ? (bRed ? Color.R > Color.B * 3 : Color.B > Color.R * 3)
                      : FMath::Max3(Color.R, Color.G, Color.B) < .02f;
    };
    bPassed &= CheckColor(*Posts[0], true, true) && CheckColor(*Posts[3], false, true)
        && CheckColor(*Red[0], true, true) && CheckColor(*Blue[0], false, true);
    bPassed &= FieldRules.HitOutpost(FieldTeam::Red, FieldTeam::Blue, FRMUCFieldRules::OutpostMaxHp).bDestroyed;
    StepFieldDevices(0.);
    bPassed &= CheckColor(*Posts[0], false, false) && FieldRules.Get(FieldTeam::Red).OutpostRotor.bStopRequested;
    bPassed &= Capture(*Posts[0], TEXT("outpost-red-destroyed"));
    std::array<FRMUCFieldRules::FSiteActivation, 2> Activation{};
    Activation[0].OutpostPosition = FRMUCFieldRules::FActivation{false, true};
    FieldRules.Step(FRMUCFieldRules::ControlTransitionSeconds, MatchElapsedSeconds + 6., Activation);
    StepFieldDevices(0.);
    bPassed &= FieldRules.Get(FieldTeam::Red).OutpostOwner == FieldTeam::Blue
        && FieldRules.Get(FieldTeam::Red).OutpostHp == 500 && CheckColor(*Posts[0], false, true);
    bPassed &= Capture(*Posts[0], TEXT("outpost-red-captured-by-blue"));

    FLinearColor Before, Flash;
    bPassed &= ReadLamp(*Red[0], Before);
    bPassed &= ApplySentryDamage(FieldTeam::Red, FieldTeam::Blue, 0, 20, ERMUCHpDeductionReason::ArmorHit);
    Competitor(FieldTeam::Red).OwnLedFlash = .24;
    RefreshDynamicMaterials();
    bPassed &= ReadLamp(*Red[0], Flash) && Flash.R < Before.R * .5f;
    bPassed &= Capture(*Red[0], TEXT("sentry-red-hit-flash"));
    bPassed &= ApplySentryDamage(FieldTeam::Red, FieldTeam::Blue, 0, SentryMaxHp, ERMUCHpDeductionReason::ArmorHit);
    RefreshDynamicMaterials();
    bPassed &= CheckColor(*Red[0], false, false);
    bPassed &= Capture(*Red[0], TEXT("sentry-red-eliminated"));
    Camera->SetRelativeTransform(SavedCamera);
    UE_LOG(LogTemp, Display, TEXT("RMUC_ARMOR_PROOF passed=%d aim_camera_frames=%d rotated=%d red_vehicle=4 blue_vehicle=4 outpost_faces=6"),
        bPassed, Captures, bFollowed);
    return bPassed;
}
#endif
