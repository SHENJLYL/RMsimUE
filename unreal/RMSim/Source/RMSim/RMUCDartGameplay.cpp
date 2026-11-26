#include "RMUCGameReferee.h"
#include "Engine/HitResult.h"
#include "Engine/World.h"
#include "VehicleRig.h"
#if WITH_DEV_AUTOMATION_TESTS
#include "Components/SpotLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/GameViewportClient.h"
#include "Engine/SceneCapture2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "HAL/FileManager.h"
#include "ImageUtils.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "ProceduralMeshComponent.h"
#include "RenderingThread.h"
#endif

namespace
{
constexpr double FlightAnimationSeconds = 2.2;
constexpr double VisualArcHeightCm = 300.;
constexpr double DetectorRadiusCm = 16.;
constexpr double LaunchCadenceSeconds = 2.6;
constexpr double RandomFixedPlacementSeconds = .55;
constexpr double DetectorTravelCm = 23.;

#if WITH_DEV_AUTOMATION_TESTS
bool CaptureDartFrame(UWorld& World, const FString& Directory, const TCHAR* Name,
    const FVector& Centre, const FVector& Offset, float Fov,
    const UStaticMeshComponent* StationDetail = nullptr, bool bIsolate = false)
{
    if (!IFileManager::Get().MakeDirectory(*Directory, true)) return false;
    const FVector CameraPosition = Centre + Offset;
    auto* CameraActor = World.SpawnActor<ASceneCapture2D>(CameraPosition, (-Offset).Rotation());
    if (!CameraActor) return false;
    auto* Camera = CameraActor->GetCaptureComponent2D();
    Camera->bCaptureEveryFrame = false;
    Camera->bCaptureOnMovement = false;
    Camera->bAlwaysPersistRenderingState = true;
    Camera->FOVAngle = Fov;
    Camera->CaptureSource = SCS_FinalColorLDR;
    Camera->TextureTarget = NewObject<UTextureRenderTarget2D>(Camera);
    Camera->TextureTarget->RenderTargetFormat = RTF_RGBA8_SRGB;
    Camera->TextureTarget->InitAutoFormat(StationDetail ? 1920 : 1200, StationDetail ? 1280 : 675);
    if (StationDetail && bIsolate)
    {
        Camera->PrimitiveRenderMode = ESceneCapturePrimitiveRenderMode::PRM_UseShowOnlyList;
        auto* Tray = Cast<UStaticMeshComponent>(StationDetail->GetAttachParent());
        auto* Station = Tray ? Cast<UStaticMeshComponent>(Tray->GetAttachParent()) : nullptr;
        if (Station)
        {
            Camera->ShowOnlyComponent(Station);
            TArray<USceneComponent*> Children;
            Station->GetChildrenComponents(true, Children);
            for (USceneComponent* Child : Children)
                if (auto* Mesh = Cast<UStaticMeshComponent>(Child)) Camera->ShowOnlyComponent(Mesh);
        }
        // Inspection light on the capture only. Match lighting does not depend on this actor.
        auto* Fill = NewObject<USpotLightComponent>(CameraActor);
        CameraActor->AddInstanceComponent(Fill);
        Fill->SetupAttachment(CameraActor->GetRootComponent());
        Fill->SetMobility(EComponentMobility::Movable);
        Fill->SetIntensity(5200.f);
        Fill->SetAttenuationRadius(670.f);
        Fill->SetOuterConeAngle(56.f);
        Fill->SetCastShadows(true);
        Fill->RegisterComponent();
    }
    Camera->CaptureScene();
    FlushRenderingCommands();
    Camera->CaptureScene();
    FlushRenderingCommands();
    FImage Frame;
    TArray64<uint8> Bytes;
    const bool bSaved = FImageUtils::GetRenderTargetImage(Camera->TextureTarget, Frame) &&
        FImageUtils::CompressImage(Bytes, TEXT("PNG"), Frame) &&
        FFileHelper::SaveArrayToFile(Bytes, *FPaths::Combine(Directory, FString(Name) + TEXT(".png")));
    CameraActor->Destroy();
    return bSaved;
}
#endif
}

void ARMUCGameReferee::ClearDartFlights()
{
    FieldVisuals.SetBaseDartOffset(true, FVector::ZeroVector);
    FieldVisuals.SetBaseDartOffset(false, FVector::ZeroVector);
    for (auto& Flight : DartFlights)
    {
        Flight.Visual.Destroy();
        Flight = {};
    }
}

void ARMUCGameReferee::StepDartFlights()
{
    const double VisualNow = GetWorld()->GetTimeSeconds();
#if WITH_DEV_AUTOMATION_TESTS
    static const FString TrayEvidence = []()
    {
        FString Output;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeDartTrayEvidence="), Output);
        return Output;
    }();
    if (!TrayEvidence.IsEmpty() && GameProgress == ERMUCGameProgress::Preparation)
    {
        const auto* Tray = FieldVisuals.GetDartTray(bFriendlyRed);
        const auto* Launcher = FieldVisuals.GetDartLauncher(bFriendlyRed);
        const auto* Gate = FieldVisuals.GetDartGate(bFriendlyRed);
        if (!Tray || !Launcher || !Gate)
        {
            UE_LOG(LogRMSim, Error, TEXT("RMUC_DART_TRAY_PROOF missing mesh"));
            FPlatformMisc::RequestExitWithStatus(false, 2);
            return;
        }
        const int32 Team = bFriendlyRed ? 0 : 1;
        const double Side = bFriendlyRed ? 1. : -1.;
        static int Phase = 0;
        static bool bImages = true, bExtended = false;
        static FVector Start = FVector::ZeroVector;
        if (Phase == 0 && VisualNow >= 1.)
        {
            Start = Launcher->GetComponentLocation();
            bImages = CaptureDartFrame(*GetWorld(), TrayEvidence, TEXT("tray-stowed"),
                Launcher->Bounds.Origin + FVector(0., 0., 62.), FVector(Side * 170., -Side * 225., 146.), 53.f, Launcher, true);
            bImages &= CaptureDartFrame(*GetWorld(), TrayEvidence, TEXT("station-front-open"),
                Launcher->Bounds.Origin + FVector(0., 0., 80.), FVector(-Side * 320., -Side * 110., 131.), 52.f, Launcher, true);
            ToggleOwnDartTray();
            Phase = 1;
        }
        else if (Phase == 1 && DartTrayFraction[Team] >= .999)
        {
            bExtended = Launcher->GetAttachParent() == Tray && Tray->GetRelativeLocation().Equals(FVector(100. * Side, 0., 0.), .2) &&
                (Launcher->GetComponentLocation() - Start).Equals(FVector(100. * Side, 0., 0.), .2) &&
                Gate->GetRelativeLocation().IsNearlyZero();
            bImages &= CaptureDartFrame(*GetWorld(), TrayEvidence, TEXT("tray-extended"),
                Launcher->Bounds.Origin + FVector(-Side * 64., 0., 86.),
                FVector(Side * 308., -Side * 285., 181.), 53.f, Launcher, true);
            bImages &= CaptureDartFrame(*GetWorld(), TrayEvidence, TEXT("tray-top-clearance"),
                Launcher->Bounds.Origin, FVector(0., 0., 290.), 48.f, Launcher, true);
            ToggleOwnDartTray();
            Phase = 2;
        }
        else if (Phase == 2 && DartTrayFraction[Team] <= .001)
        {
            const bool bRetracted = Tray->GetRelativeLocation().IsNearlyZero(.2) && Launcher->GetComponentLocation().Equals(Start, .2);
            const bool bPassed = bExtended && bRetracted && bImages;
            UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_TRAY_PROOF passed=%d team=%s extended=%d retracted=%d images=%d"),
                bPassed, bFriendlyRed ? TEXT("red") : TEXT("blue"), bExtended, bRetracted, bImages);
            FPlatformMisc::RequestExitWithStatus(false, bPassed ? 0 : 2);
            Phase = 3;
        }
        return;
    }
#endif
    if (GameProgress != ERMUCGameProgress::Running)
    {
        if (GameProgress != ERMUCGameProgress::GameOver) return;
        for (auto& Flight : DartFlights)
        {
            if (Flight.bFlying) Flight.Visual.Finish(VisualNow);
            Flight.bFlying = false;
            Flight.Visual.Update(VisualNow);
        }
        return;
    }
#if WITH_DEV_AUTOMATION_TESTS
    static const FString Evidence = []()
    {
        FString Output;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeDartEvidence="), Output);
        return Output;
    }();
    static int ProofStage = 0;
    static const FString ArtEvidence = []()
    {
        FString Output;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeDartArtEvidence="), Output);
        return Output;
    }();
    static const auto ArtTarget = []()
    {
        int32 Value = 0;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeDartArtTarget="), Value);
        return ERMUCDartTarget(FMath::Clamp(Value, 0, int32(ERMUCDartTarget::LateMovingBase)));
    }();
    static bool bArtGateRequested = false, bArtPlacementMoved = false, bArtImages = true;
    static bool bArtMotionStart = false, bArtMotion = false, bArtMotionEnd = false;
    static bool bArtTrail = false, bArtImpact = false, bArtResting = false, bArtAfterglow = false;
    static double ArtHitAt = -1.;
    static FVector ArtImpact = FVector::ZeroVector;
    if (!ArtEvidence.IsEmpty() && !bArtGateRequested && MatchElapsedSeconds >= 1.)
    {
        bArtGateRequested = true;
        if (ArtTarget != ERMUCDartTarget::Outpost)
            FieldRules.HitOutpost(FRMUCFieldRules::Other(FriendlyTeam()), FriendlyTeam(), OutpostMaxHp);
        MatchElapsedSeconds = 30.;
        StageRemainSeconds = 390.;
        auto& Station = bFriendlyRed ? RedDart : BlueDart;
        Station.Update(MatchElapsedSeconds, ERMUCDartPhase::Running);
        if (!Station.SelectTarget(ArtTarget)) FPlatformMisc::RequestExitWithStatus(false, 2);
        RequestOwnDartGate();
    }
    if (!Evidence.IsEmpty())
    {
        const FieldTeam Other = FRMUCFieldRules::Other(FriendlyTeam());
        const auto& Defending = FieldRules.Get(Other);
        const auto& Points = FieldPointEffects[FRMUCFieldRules::Index(Other)];
        auto& OwnStation = bFriendlyRed ? RedDart : BlueDart;
        const auto AdvanceIdleClock = [this](double Time)
        {
            MatchElapsedSeconds = Time;
            StageRemainSeconds = 420. - Time;
            RedDart.Update(Time, ERMUCDartPhase::Running);
            BlueDart.Update(Time, ERMUCDartPhase::Running);
        };
        if (ProofStage == 0 && MatchElapsedSeconds >= 1. && Points.bBase)
        {
            AdvanceIdleClock(30.);
            RequestOwnDartGate();
            ++ProofStage;
        }
        else if (ProofStage == 1 && (bFriendlyRed ? RedAttackDamage : BlueAttackDamage) >= 1000 &&
            OwnStation.GainPointSuppressionRemaining(ERMUCDartTarget::Outpost) == 0.)
        {
            AdvanceIdleClock(240.);
            if (OwnStation.SelectTarget(ERMUCDartTarget::RandomMovingBase))
            {
                RequestOwnDartGate();
                ++ProofStage;
            }
        }
        else if (ProofStage == 2 && Defending.BaseHp < FRMUCFieldRules::BaseMaxHp && !Points.bBase)
        {
            FScreenshotRequest::RequestScreenshot(FPaths::Combine(Evidence, TEXT("suppression-hud.png")), true, false);
            ++ProofStage;
        }
        else if (ProofStage == 3 && Points.bBase && OwnStation.DartsRemaining() == 0)
        {
            FScreenshotRequest::RequestScreenshot(FPaths::Combine(Evidence, TEXT("restored-hud.png")), true, false);
            ++ProofStage;
        }
    }
#endif
    for (const FieldTeam Sender : FRMUCFieldRules::Teams)
    {
        const FieldTeam Receiver = FRMUCFieldRules::Other(Sender);
        const bool bRed = Sender == FieldTeam::Red;
        auto& Station = bRed ? RedDart : BlueDart;
        auto& Flight = DartFlights[FRMUCFieldRules::Index(Sender)];
        const auto UpdateBaseHead = [&]
        {
            const FVector Offset = Flight.DetectorPosition - Flight.DetectorAnchor;
            Flight.BaseHeadOffset = Offset.Y;
            FieldVisuals.SetBaseDartOffset(!bRed, Offset);
        };
#if WITH_DEV_AUTOMATION_TESTS
        const bool bArt = !ArtEvidence.IsEmpty() && Sender == FriendlyTeam();
        const double Inward = bRed ? 1. : -1.;
        const auto VerifyCarriage = [&](const FVector& Offset)
        {
            const auto* Guide = Cast<USpotLightComponent>(BaseGuideLights[FRMUCFieldRules::Index(Receiver)].Get());
            const auto* Head = FieldVisuals.GetBaseDartHead(!bRed);
            if (!Guide || !Head || Guide->GetAttachParent() != Head) return false;
            const FVector LensCentre(bRed ? -1149.635 : 1149.475, 0., 124.227);
            const FVector Axis(Inward * .96815, 0., .25038);
            const auto& HeadFrame = Head->GetComponentTransform();
            const FVector EmitterDelta = HeadFrame.InverseTransformPosition(Guide->GetComponentLocation()) - LensCentre;
            return EmitterDelta.Equals(Axis * .85, .05) &&
                Guide->GetForwardVector().Equals(HeadFrame.TransformVectorNoScale(Axis), .001) &&
                FieldVisuals.VerifyBaseDartOffset(!bRed, Offset);
        };
#endif
        const auto IsTargetLive = [&](ERMUCDartTarget Target)
        {
            const auto& Site = FieldRules.Get(Receiver);
            return Target == ERMUCDartTarget::Outpost
                ? Site.OutpostOwner != Sender && Site.OutpostHp > 0 : Site.BaseHp > 0;
        };
        if (!Flight.bFlying)
        {
#if WITH_DEV_AUTOMATION_TESTS
            if (bArt && ArtHitAt >= 0.)
            {
                const double SinceImpact = VisualNow - ArtHitAt;
                // Read back the camera at exact animation times even if a prior capture blocks rendering.
                if (SinceImpact >= .20 && !bArtResting)
                {
                    Flight.Visual.Update(ArtHitAt + .20);
                    const auto* Body = Flight.Visual.Body.Get();
                    bArtResting = Body && Body->IsVisible() && Flight.Visual.bLandingSupported &&
                        FMath::Abs(FVector::DotProduct(Body->GetForwardVector(), Flight.Visual.LandingNormal)) < .01;
                    if (bArtResting)
                        bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("settled"), ArtImpact,
                            FVector(Inward * 155., 125., 105.), 39.f);
                }
                if (SinceImpact >= .30 && !bArtAfterglow)
                {
                    Flight.Visual.Update(ArtHitAt + .30);
                    const auto* Wake = Flight.Visual.Trail.Get();
                    const auto* Body = Flight.Visual.Body.Get();
                    bArtAfterglow = Wake && Wake->IsVisible() && Body && !Body->IsVisible();
                    if (bArtAfterglow)
                        bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("afterglow"), ArtImpact,
                            FVector(Inward * 370., 245., 145.), 47.f);
                }
                if (SinceImpact >= 1.7)
                {
                    Flight.Visual.Update(VisualNow);
                    const bool bExpired = !Flight.Visual.Actor.IsValid();
                    const double Armor = BaseArmorTravel[FRMUCFieldRules::Index(Receiver)];
                    const bool bMoving = FRMUCDartStation::VirtualDetectorSettleSeconds(ArtTarget) > 0.;
                    if (bMoving)
                        bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("armor-open"), Flight.DetectorAnchor - FVector(0., 0., 54.),
                            FVector(Inward * 270., 200., 140.), 47.f);
                    const bool bPassed = bArtImages && bArtMotionStart && bArtMotion && bArtMotionEnd &&
                        bArtTrail && bArtImpact && bArtResting && bArtAfterglow && bExpired &&
                        (ArtTarget != ERMUCDartTarget::RandomFixedBase || bArtPlacementMoved) &&
                        (bMoving ? Armor >= .999 : Armor == 0.);
                    UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_ART passed=%d mode=%d placement=%d motion=%d trail=%d impact=%d settled=%d afterglow=%d expired=%d armor=%.3f images=%d"),
                        bPassed, int32(ArtTarget), bArtPlacementMoved, bArtMotion, bArtTrail, bArtImpact, bArtResting, bArtAfterglow, bExpired, Armor, bArtImages);
                    FPlatformMisc::RequestExitWithStatus(false, bPassed ? 0 : 2);
                }
                continue;
            }
#endif
            Flight.Visual.Update(VisualNow);
            const auto Target = Station.CurrentDetectorTarget();
            if ((Station.State() != ERMUCDartState::Opening && Station.DetectorWindowRemaining() <= 0.) ||
                !IsTargetLive(Target))
            {
                Flight.bDetectorPrepared = false;
                continue;
            }
            const bool bBase = Target != ERMUCDartTarget::Outpost;
            if (!Flight.bDetectorPrepared || Flight.DisplayTarget != Target)
            {
                FVector Exit;
                if (!FieldVisuals.GetDartSceneAnchors(bRed, !bBase, Exit, Flight.DetectorAnchor)) continue;
                Flight.DisplayTarget = Target;
                Flight.SelectedAt = MatchElapsedSeconds;
                Flight.FixedOffset = Target == ERMUCDartTarget::RandomFixedBase
                    ? (FMath::RandBool() ? DetectorTravelCm : -DetectorTravelCm) : 0.;
                Flight.PlacementStart = Flight.BaseHeadOffset;
                Flight.DetectorPosition = Flight.DetectorAnchor;
                Flight.bDetectorPrepared = true;
            }
            const double SelectionAge = MatchElapsedSeconds - Flight.SelectedAt;
            const bool bInitialPlacement = bBase && Flight.LaunchedAt < Flight.SelectedAt;
            if (bInitialPlacement)
            {
                const double Blend = FMath::SmoothStep(0., RandomFixedPlacementSeconds, SelectionAge);
                Flight.DetectorPosition = Flight.DetectorAnchor +
                    FVector(0., FMath::Lerp(Flight.PlacementStart, Flight.FixedOffset, Blend), 0.);
                UpdateBaseHead();
            }
#if WITH_DEV_AUTOMATION_TESTS
            if (bArt && Target == ERMUCDartTarget::RandomFixedBase && !bArtPlacementMoved && SelectionAge >= .22 &&
                SelectionAge < RandomFixedPlacementSeconds)
            {
                const double Shift = FMath::Abs(Flight.DetectorPosition.Y - Flight.DetectorAnchor.Y);
                bArtPlacementMoved = Shift > 0. && Shift < DetectorTravelCm &&
                    VerifyCarriage(Flight.DetectorPosition - Flight.DetectorAnchor);
                if (bArtPlacementMoved)
                    bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("positioning"), Flight.DetectorAnchor,
                        FVector(Inward * 190., 100., 100.), 40.f);
            }
#endif
            if (Station.State() != ERMUCDartState::Open || Station.DartsRemaining() <= 0 ||
                MatchElapsedSeconds + 1.e-6 < Flight.NextLaunch ||
                (bInitialPlacement && Flight.PlacementStart != Flight.FixedOffset && SelectionAge < RandomFixedPlacementSeconds)) continue;
            FVector Start, Detector;
            if (!FieldVisuals.GetDartSceneAnchors(bRed, !bBase, Start, Detector)) continue;
            if (!Flight.Visual.Spawn(*this, bRed)) continue;
            if (!Station.TryLaunch(Flight.Target)) { Flight.Visual.Destroy(); continue; }
            Flight.Start = Flight.Previous = Start;
            Flight.DetectorStart = Flight.DetectorPosition;
            Flight.Destination = Flight.DetectorStart;
            if (FRMUCDartStation::VirtualDetectorSettleSeconds(Target) > 0.)
            {
                const double NextOffset = Flight.DetectorStart.Y > Detector.Y ? -DetectorTravelCm : DetectorTravelCm;
                Flight.Destination = Detector + FVector(0., NextOffset, 0.);
            }
            Flight.LaunchedAt = MatchElapsedSeconds;
            Flight.NextLaunch = MatchElapsedSeconds + LaunchCadenceSeconds;
            Flight.bFlying = true;
            Flight.Visual.ShowAt(Start, Flight.Destination - Start + FVector(0., 0., 4. * VisualArcHeightCm), VisualNow);
#if WITH_DEV_AUTOMATION_TESTS
            if (bArt)
            {
                const auto* Launcher = FieldVisuals.GetDartLauncher(bRed);
                if (Launcher)
                {
                    bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("launcher"),
                        Launcher->Bounds.Origin, FVector(-Inward * 174., 29., -12.), 48.f);
                    if (FParse::Param(FCommandLine::Get(), TEXT("RefereeDartLauncherReview")))
                    {
                        const FVector Focus = Launcher->Bounds.Origin + FVector(0., 0., 36.);
                        const FVector SideAbove(Inward * 85., -Inward * 217., 181.);
                        bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("launcher-side-overhead"),
                            Focus, SideAbove, 57.f, Launcher);
                        bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("launcher-station-detail"),
                            Focus, SideAbove, 57.f, Launcher, true);
                        bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("launcher-deck-detail"),
                            Launcher->Bounds.Origin, FVector(Inward * 70., -Inward * 142., 121.), 55.f, Launcher, true);
                        UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_LAUNCHER_REVIEW team=%s gate=%.3f"),
                            bRed ? TEXT("red") : TEXT("blue"), Station.GateOpenFraction());
                    }
                    UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_LAUNCHER_PROOF team=%s start=%s visible=%d"),
                        bRed ? TEXT("red") : TEXT("blue"), *Start.ToCompactString(), Launcher->IsVisible());
                }
            }
#endif
            UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_LAUNCH team=%s target=%d remaining=%d time=%.3f"),
                bRed ? TEXT("red") : TEXT("blue"), int32(Flight.Target), Station.DartsRemaining(), MatchElapsedSeconds);
            continue;
        }

        const double Age = MatchElapsedSeconds - Flight.LaunchedAt;
        const double Progress = FMath::Clamp(Age / FlightAnimationSeconds, 0., 1.);
        const double SettleSeconds = FRMUCDartStation::VirtualDetectorSettleSeconds(Flight.Target);
        const double DetectorProgress = SettleSeconds > 0.
            ? FMath::SmoothStep(SettleSeconds - .6, SettleSeconds, Age) : 1.;
        const FVector Detector = FMath::Lerp(Flight.DetectorStart, Flight.Destination, DetectorProgress);
        Flight.DetectorPosition = Detector;
        if (Flight.Target != ERMUCDartTarget::Outpost) UpdateBaseHead();
        // An authored in-game arc. The landing cue is a virtual detector volume.
        const FVector Position = FMath::Lerp(Flight.Start, Flight.Destination, Progress) +
            FVector(0., 0., VisualArcHeightCm * 4. * Progress * (1. - Progress));
        const FVector Step = Position - Flight.Previous;
        if (Progress < .12 && !Step.IsNearlyZero() && FieldVisuals.TraceDartExit(bRed, Flight.Previous, Position))
        {
            Flight.Visual.Finish(VisualNow);
            Flight.bFlying = false;
            UE_LOG(LogRMSim, Warning, TEXT("RMUC_DART_EXIT_BLOCKED team=%s"), bRed ? TEXT("red") : TEXT("blue"));
#if WITH_DEV_AUTOMATION_TESTS
            if (bArt) FPlatformMisc::RequestExitWithStatus(false, 2);
#endif
            continue;
        }
        if (!Step.IsNearlyZero()) Flight.Visual.ShowAt(Position, Step, VisualNow);
#if WITH_DEV_AUTOMATION_TESTS
        if (bArt && !bArtTrail && Age >= .94 && Age < 1.18)
        {
            auto* Wake = Flight.Visual.Trail.Get();
            const FProcMeshSection* Strip = Wake ? Wake->GetProcMeshSection(0) : nullptr;
            const auto& History = Flight.Visual.History;
            bArtTrail = Wake && Wake->IsVisible() && Wake->GetNumSections() == 1 && Strip &&
                Strip->ProcVertexBuffer.Num() == 66 && Strip->ProcIndexBuffer.Num() == 384 &&
                History.Num() >= 2 && History.Last().Time - History[0].Time >= .60 &&
                Wake->GetCollisionEnabled() == ECollisionEnabled::NoCollision && Wake->Bounds.SphereRadius > 50. &&
                Flight.Visual.Body.IsValid() && Flight.Visual.Body->IsVisible();
            if (bArtTrail)
            {
                const FVector Side = FVector::CrossProduct(FVector::UpVector, Step.GetSafeNormal()).GetSafeNormal();
                bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("dart-model"), Position,
                    Side * 66. + FVector(0., 0., 22.) + Step.GetSafeNormal() * 22., 46.f);
                bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("dart-trail"),
                    Position - Step.GetSafeNormal() * 285., Side * 505. + FVector(0., 0., 160.), 62.f);
            }
        }
        const double MotionSample = SettleSeconds > 0. ? SettleSeconds - .3 : .30;
        if (bArt && Age >= .10 && Age < .20)
            bArtMotionStart = Flight.Target == ERMUCDartTarget::RandomMovingBase
                ? DetectorProgress > 0. && DetectorProgress < 1. : Detector.Equals(Flight.DetectorStart);
        if (bArt && Age >= 1.9 && Age < 2.0)
            bArtMotionEnd = Detector.Equals(Flight.Destination);
        if (bArt && !bArtMotion && Age >= MotionSample && Age < MotionSample + .15)
        {
            const bool bProgressCorrect = SettleSeconds > 0. ? DetectorProgress > 0. && DetectorProgress < 1.
                : Detector.Equals(Flight.DetectorStart) && Detector.Equals(Flight.Destination);
            bArtMotion = bProgressCorrect && (Flight.Target == ERMUCDartTarget::Outpost ||
                VerifyCarriage(Detector - Flight.DetectorAnchor));
            if (bArtMotion)
                bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("detector"), Flight.DetectorAnchor,
                    FVector(Inward * 195., 115., 110.), 42.f);
        }
#endif
        const FVector Nearest = FMath::ClosestPointOnSegment(Detector, Flight.Previous, Position);
        const bool bIntersected = Progress >= .8 && FVector::DistSquared(Nearest, Detector) <= FMath::Square(DetectorRadiusCm);
        Flight.Previous = Position;
        if (!bIntersected && Progress < 1.) continue;
        const bool bAccepted = bIntersected && IsTargetLive(Flight.Target)
            && ConfirmDartDetector(bRed, Flight.Target);
        FHitResult Contact;
        const bool bLanded = FieldVisuals.TraceDartLanding(bRed, Flight.Target == ERMUCDartTarget::Outpost, Detector, Contact)
            && Flight.Visual.Land(Contact, VisualNow);
        if (!bLanded) Flight.Visual.Finish(VisualNow);
#if WITH_DEV_AUTOMATION_TESTS
        if (bArt)
        {
            ArtHitAt = VisualNow;
            ArtImpact = bLanded ? Contact.ImpactPoint : Detector;
            const int32 DefenderHp = Flight.Target == ERMUCDartTarget::Outpost ?
                FieldRules.Get(Receiver).OutpostHp : FieldRules.Get(Receiver).BaseHp;
            bArtImpact = bAccepted && bLanded && Contact.GetComponent();
            UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_ART_CONTACT mode=%d accepted=%d defender_hp=%d contact=%d normal=%s mesh=%s"),
                int32(ArtTarget), bAccepted, DefenderHp, bLanded, *Contact.ImpactNormal.ToCompactString(),
                *GetNameSafe(Contact.GetComponent()));
            if (bArtImpact)
                bArtImages &= CaptureDartFrame(*GetWorld(), ArtEvidence, TEXT("impact"), ArtImpact,
                    FVector(Inward * 190., 110., 108.), 42.f);
        }
#endif
        if (!bAccepted) Station.ResolveVirtualFlight(Flight.Target, false);
        UE_LOG(LogRMSim, Display, TEXT("RMUC_DART_ARRIVAL team=%s target=%d detector=%d accepted=%d flight_s=%.3f"),
            bRed ? TEXT("red") : TEXT("blue"), int32(Flight.Target), bIntersected, bAccepted, Age);
        Flight.bFlying = false;
    }
}
