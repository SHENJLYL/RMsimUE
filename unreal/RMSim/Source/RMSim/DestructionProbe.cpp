#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "WTFoliageCrushComponent.h"
#include "RosCoordinates.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "ProceduralMeshComponent.h"

bool AImportLabGameMode::TickDestructionValidation(double DeltaSeconds)
{
    const TCHAR* Command=FCommandLine::Get();
    const bool Geometry=FParse::Param(Command,TEXT("DestructionProbe"));
    const bool Cross=FParse::Param(Command,TEXT("DestructionDriveCross"));
    const bool Fast=Cross||FParse::Param(Command,TEXT("DestructionDriveFast"));
    const bool Hold=FParse::Param(Command,TEXT("DestructionDriveHold"));
    const bool Foliage=FParse::Param(Command,TEXT("DestructionDriveFoliage"));
    if(!Geometry&&!Fast&&!Hold&&!Foliage)return false;
    if(bDestructionValidationFinished)return true;
    auto Fail=[&](const FString& Reason)
    {
        bDestructionValidationFinished=true;
        if(Rig)Rig->SetContactDrive(0,0,true);
        UE_LOG(LogRMSim,Error,TEXT("WT_DESTRUCTION_VALIDATION_FAILED %s"),*Reason);
        FPlatformMisc::RequestExitWithStatus(true,2);
        return true;
    };
    if(!Rig||!Rig->IsContactPhysics()||!SoftFoliage||!GetWorld())
        return Fail(TEXT("native saved-scene contact vehicle and destruction component are required"));
    if(!FMath::IsFinite(DeltaSeconds)||DeltaSeconds<=0.)return true;

    auto Capture=[&](const FString& Phase)
    {
        if(CaptureDirectory.IsEmpty())return true;
        if(!CapturePngs(Phase))return false;
        ++DestructionCaptureCount;
        UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_CAPTURE phase=%s simulation_seconds=%.6f post_break_seconds=%.6f"),
            *Phase,DestructionProbeSeconds,DestructionFirstBreakSeconds<0.?-1.:DestructionProbeSeconds-DestructionFirstBreakSeconds);
        return true;
    };

    if(!bDestructionValidationInitialized)
    {
        if((Geometry&&(Fast||Hold||Foliage))||(Foliage&&(Fast||Hold)))
            return Fail(TEXT("geometry, hard-object and foliage validation modes must be separate"));
        bDestructionExpectFixed=FParse::Param(Command,TEXT("DestructionExpectFixed"));
        if((Geometry||Foliage)&&bDestructionExpectFixed)return Fail(TEXT("fixed-object validation requires a hard-object drive mode"));
        double X=0,Y=0,Z=0;
        const bool HasX=FParse::Value(Command,TEXT("DestructionTargetX="),X);
        const bool HasY=FParse::Value(Command,TEXT("DestructionTargetY="),Y);
        const bool HasZ=FParse::Value(Command,TEXT("DestructionTargetZ="),Z);
        if((!Geometry||HasX||HasY||HasZ)&&(!HasX||!HasY||!HasZ||
            !FMath::IsFinite(X)||!FMath::IsFinite(Y)||!FMath::IsFinite(Z)))
            return Fail(TEXT("finite DestructionTargetX/Y/Z in ROS metres are required"));
        DestructionTargetCm=HasX?RosCoordinates::Position(FVector(X,Y,Z)):Rig->GetActorLocation();
        DestructionApproach=(DestructionTargetCm-Rig->GetActorLocation()).GetSafeNormal2D();
        if(DestructionApproach.IsNearlyZero())
        {
            if(!Geometry)return Fail(TEXT("driving target must be separated from the initial chassis position"));
            DestructionApproach=Rig->GetActorForwardVector().GetSafeNormal2D();
        }
        FParse::Value(Command,TEXT("DestructionExpectMesh="),DestructionExpectedMesh);
        FParse::Value(Command,TEXT("DestructionExpectedInstance="),DestructionExpectedInstance);
        if(DestructionExpectedInstance<INDEX_NONE)return Fail(TEXT("invalid expected instance index"));
        DestructionDriveSpeed=Fast?4.5:1.0;
        FParse::Value(Command,TEXT("DestructionDriveSpeed="),DestructionDriveSpeed);
        const double ProfileMax=Rig->GetDriveParameter(TEXT("MaxForwardSpeedMps"));
        if(!Geometry&&(!FMath::IsFinite(DestructionDriveSpeed)||DestructionDriveSpeed<=0.||
            !FMath::IsFinite(ProfileMax)||ProfileMax<=0.))return Fail(TEXT("invalid drive speed or vehicle speed ceiling"));
        if(!Geometry)DestructionDriveSpeed=FMath::Min(DestructionDriveSpeed,ProfileMax);
        DestructionMotionGeneration=Rig->GetMotionGeneration();
        DestructionInitialPosition=Rig->GetActorLocation();
        DestructionContactCursor=0;DestructionProbeSeconds=0;
        if(Overview&&OverviewCapture)
        {
            const FVector Side=FVector::CrossProduct(FVector::UpVector,DestructionApproach).GetSafeNormal();
            const FVector Look=DestructionTargetCm+FVector(0,0,35.);
            const FVector Eye=DestructionTargetCm-DestructionApproach*150.+Side*90.+FVector(0,0,180.);
            const FRotator Rotation=(Look-Eye).Rotation();
            Overview->SetActorLocationAndRotation(Eye,Rotation);
            Overview->GetCameraComponent()->SetProjectionMode(ECameraProjectionMode::Perspective);
            Overview->GetCameraComponent()->SetFieldOfView(55.f);
            OverviewCapture->ProjectionType=ECameraProjectionMode::Perspective;
            OverviewCapture->FOVAngle=55.f;OverviewCapture->SetWorldLocationAndRotation(Eye,Rotation);
            if(APlayerController* Player=GetWorld()->GetFirstPlayerController())Player->SetViewTarget(Overview);
        }
        else if(!CaptureDirectory.IsEmpty())return Fail(TEXT("overview camera/capture is unavailable"));
        Rig->SetContactDrive(0,0,true);
        bDestructionValidationInitialized=true;
        UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_VALIDATION_BEGIN mode=%s expected_fixed=%d target_cm=(%.3f,%.3f,%.3f) approach=(%.6f,%.6f,%.6f) speed_mps=%.3f expected_mesh=%s expected_instance=%d cross_requested=%d"),
            Geometry?TEXT("geometry"):Foliage?TEXT("foliage"):Fast?TEXT("fast"):TEXT("hold"),bDestructionExpectFixed?1:0,
            DestructionTargetCm.X,DestructionTargetCm.Y,DestructionTargetCm.Z,
            DestructionApproach.X,DestructionApproach.Y,DestructionApproach.Z,DestructionDriveSpeed,
            *DestructionExpectedMesh,DestructionExpectedInstance,Cross?1:0);
        return true;
    }
    if(Rig->GetMotionGeneration()!=DestructionMotionGeneration)
        return Fail(TEXT("vehicle reset during validation; movement must remain genuine Chaos contact"));
    if(!bDestructionProbeStarted)
    {
        Rig->SetContactDrive(0,0,true);
        if(++DestructionWarmFrames<3)return true;
        if(!Capture(TEXT("before")))return Fail(TEXT("before-movement PNG capture failed"));
        bDestructionProbeStarted=true;
        if(Geometry)
        {
            TArray<FString> Outcomes;
            if(!SoftFoliage->ProbeOneOfEach(Outcomes))
                return Fail(TEXT("source geometry/instance isolation probe failed"));
            DestructionFirstBreakSeconds=0.;
            UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_PROBE_INIT %s"),*FString::Join(Outcomes,TEXT(",")));
        }
        return true;
    }
    DestructionProbeSeconds+=DeltaSeconds;

    // A separate cursor observes the same immutable event stream consumed by damage logic.
    TArray<FVehicleObstacleContact> Contacts;
    Rig->GetObstacleContactsSince(DestructionContactCursor,Contacts);
    double ThisFrameStrongest=0.;FVector ThisFrameNormal=FVector::ZeroVector;
    for(const auto& Contact:Contacts)
    {
        if(Contact.MotionGeneration!=DestructionMotionGeneration||!Contact.OtherComponent.IsValid()||
            Contact.ImpulseNs<=0.)continue;
        auto* Static=Cast<UStaticMeshComponent>(Contact.OtherComponent.Get());
        if(!Static||!Static->GetStaticMesh())continue;
        const FString Name=Static->GetStaticMesh()->GetName();
        if(!DestructionExpectedMesh.IsEmpty()&&Name!=DestructionExpectedMesh)continue;
        if(DestructionExpectedInstance!=INDEX_NONE&&Contact.InstanceIndex!=DestructionExpectedInstance)continue;
        if(FVector::DistSquared2D(Contact.PointCm,DestructionTargetCm)>FMath::Square(100.)||
            FMath::Abs(Contact.PointCm.Z-DestructionTargetCm.Z)>150.)continue;
        ++DestructionTargetContacts;
        const double NormalSpeed=FMath::Max(0.,FVector::DotProduct(Contact.PreImpactVelocityCmS,Contact.IntoObstacleNormal)/100.);
        DestructionPeakImpulseNs=FMath::Max(DestructionPeakImpulseNs,Contact.ImpulseNs);
        DestructionPeakNormalSpeedMps=FMath::Max(DestructionPeakNormalSpeedMps,NormalSpeed);
        if(FMath::Abs(Contact.IntoObstacleNormal.Z)<=.45&&Contact.ImpulseNs>ThisFrameStrongest)
        {ThisFrameStrongest=Contact.ImpulseNs;ThisFrameNormal=Contact.IntoObstacleNormal;}
        UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_PROBE_CONTACT mesh=%s instance=%d sequence=%llu impulse_Ns=%.6f normal_speed_mps=%.6f point=(%.3f,%.3f,%.3f) into=(%.6f,%.6f,%.6f)"),
            *Name,Contact.InstanceIndex,static_cast<unsigned long long>(Contact.Sequence),Contact.ImpulseNs,NormalSpeed,
            Contact.PointCm.X,Contact.PointCm.Y,Contact.PointCm.Z,
            Contact.IntoObstacleNormal.X,Contact.IntoObstacleNormal.Y,Contact.IntoObstacleNormal.Z);
    }
    if(DestructionFirstBreakSeconds<0.&&!ThisFrameNormal.IsNearlyZero())DestructionImpactNormal=ThisFrameNormal;

    if(!Geometry&&bDestructionExpectFixed&&SoftFoliage->BreakCount()>0)
        return Fail(TEXT("protected fixture or an unintended nearby object was broken"));
    if(Foliage&&SoftFoliage->BreakCount()>0)
        return Fail(TEXT("foliage fixture produced an unintended hard-object break"));
    if(Foliage&&SoftFoliage->CrushCount()>0&&DestructionFirstBreakSeconds<0.)
    {
        DestructionFirstBreakSeconds=DestructionProbeSeconds;
        Rig->SetContactDrive(0,0,true);
        UE_LOG(LogRMSim,Display,TEXT("WT_FOLIAGE_OBSERVE_BEGIN seconds=%.6f patches=%d body_travel_cm=%.6f"),
            DestructionProbeSeconds,SoftFoliage->CrushCount(),FVector::Dist2D(DestructionInitialPosition,Rig->GetActorLocation()));
    }
    if(!Geometry&&!Foliage&&!bDestructionExpectFixed&&SoftFoliage->BreakCount()>0&&DestructionFirstBreakSeconds<0.)
    {
        if(DestructionTargetContacts==0||DestructionImpactNormal.IsNearlyZero())
            return Fail(TEXT("break occurred without an observed exact target contact and side-impact normal"));
        DestructionFirstBreakSeconds=DestructionProbeSeconds;
        Rig->SetContactDrive(0,0,true);
        UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_OBSERVE_BEGIN seconds=%.6f broken=%d contact_normal=(%.6f,%.6f,%.6f)"),
            DestructionProbeSeconds,SoftFoliage->BreakCount(),DestructionImpactNormal.X,DestructionImpactNormal.Y,DestructionImpactNormal.Z);
    }

    const bool Observing=Geometry||DestructionFirstBreakSeconds>=0.;
    if(Observing)
    {
        Rig->SetContactDrive(0,0,true);
        if(!Foliage)
        {
        for(TActorIterator<AActor> It(GetWorld());It;++It)
        {
            AActor* Actor=*It;if(!Actor->ActorHasTag(TEXT("WT_Debris")))continue;
            auto* Body=Cast<UProceduralMeshComponent>(Actor->GetRootComponent());
            if(!Body||!Body->IsSimulatingPhysics())return Fail(TEXT("tagged debris lacks a live Chaos rigid body"));
            int32 Index=DestructionDebrisObservations.IndexOfByPredicate([&](const auto& O){return O.Actor.Get()==Actor;});
            if(Index==INDEX_NONE)
            {
                FDestructionDebrisObservation O;O.Actor=Actor;O.FirstPosition=Body->GetComponentLocation();
                Index=DestructionDebrisObservations.Add(O);
                UE_LOG(LogRMSim,Display,TEXT("WT_DEBRIS_OBSERVED id=%d actor=%s initial_cm=(%.6f,%.6f,%.6f)"),
                    Index,*Actor->GetName(),O.FirstPosition.X,O.FirstPosition.Y,O.FirstPosition.Z);
            }
            auto& O=DestructionDebrisObservations[Index];
            const FVector Velocity=Body->GetPhysicsLinearVelocity();
            const FVector Position=Body->GetComponentLocation();
            if(Velocity.ContainsNaN()||Position.ContainsNaN())return Fail(TEXT("nonfinite debris physics state"));
            const double ForwardVelocity=FVector::DotProduct(Velocity,DestructionImpactNormal.GetSafeNormal2D());
            const double ForwardDisplacement=FVector::DotProduct(Position-O.FirstPosition,DestructionImpactNormal.GetSafeNormal2D());
            O.MaxForwardCm=FMath::Max(O.MaxForwardCm,ForwardDisplacement);
            if(O.FirstVelocitySeconds<0.&&Velocity.Size()>.5)
            {
                O.FirstVelocity=Velocity;O.FirstVelocitySeconds=DestructionProbeSeconds;
                O.bReverseInitialMotion=ForwardVelocity<-1.;
                UE_LOG(LogRMSim,Display,TEXT("WT_DEBRIS_FIRST_PHYSICS id=%d seconds=%.6f velocity_cm_s=(%.6f,%.6f,%.6f) dot_contact_cm_s=%.6f"),
                    Index,DestructionProbeSeconds,Velocity.X,Velocity.Y,Velocity.Z,ForwardVelocity);
            }
            if(ForwardVelocity>1.&&ForwardDisplacement>.03)O.bPositiveMotion=true;
        }
        }
        const double Post=DestructionProbeSeconds-DestructionFirstBreakSeconds;
        constexpr double Times[]={.10,.30,.60,1.2,2.5,4.5,8.3};
        const TCHAR* Phases[]={TEXT("post_010"),TEXT("post_030"),TEXT("post_060"),TEXT("post_120"),TEXT("post_250"),TEXT("post_450"),TEXT("post_830")};
        while(DestructionCaptureIndex<static_cast<int32>(UE_ARRAY_COUNT(Times))&&Post>=Times[DestructionCaptureIndex])
        {
            if(!Capture(Phases[DestructionCaptureIndex]))return Fail(TEXT("post-fracture PNG capture failed"));
            ++DestructionCaptureIndex;
        }
        if(Post>=8.5)
        {
            int32 Live=0,Positive=0,Reverse=0;
            for(const auto& O:DestructionDebrisObservations)
            {Live+=O.Actor.IsValid()?1:0;Positive+=O.bPositiveMotion?1:0;Reverse+=O.bReverseInitialMotion?1:0;}
            if(Live>0||SoftFoliage->ActiveAnimations()>0)return Fail(TEXT("debris/foliage survived the delayed cleanup deadline"));
            if(Geometry)
            {
                UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_PROBE_PASSED soft=%d hard=%d live=%d captures=%d scope=geometry_identity_and_lifecycle"),
                    SoftFoliage->CrushCount(),SoftFoliage->BreakCount(),Live,DestructionCaptureCount);
            }
            else if(Foliage)
            {
                const double Travel=FVector::Dist2D(DestructionInitialPosition,Rig->GetActorLocation());
                if(SoftFoliage->CrushCount()<1||SoftFoliage->BreakCount()!=0||!FMath::IsFinite(Travel)||Travel<=.5)
                    return Fail(TEXT("foliage response requires measured vehicle travel and a local crushed patch"));
                UE_LOG(LogRMSim,Display,TEXT("WT_FOLIAGE_DRIVE_PASSED seconds=%.6f patches=%d live=%d captures=%d body_travel_cm=%.6f hard_broken=%d scope=soft_foliage_motion_and_lifecycle body_travel_metric=planar_displacement"),
                    DestructionProbeSeconds,SoftFoliage->CrushCount(),SoftFoliage->ActiveAnimations(),DestructionCaptureCount,Travel,SoftFoliage->BreakCount());
            }
            else
            {
                if(DestructionDebrisObservations.IsEmpty()||Positive!=DestructionDebrisObservations.Num()||Reverse>0)
                    return Fail(FString::Printf(TEXT("debris forward motion not established: observed=%d positive=%d reverse_initial=%d"),
                        DestructionDebrisObservations.Num(),Positive,Reverse));
                UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_DRIVE_PASSED mode=%s seconds=%.6f broken=%d target_contacts=%d expected_mesh=%s expected_instance=%d max_event_impulse_Ns=%.6f max_normal_speed_mps=%.6f debris=%d positive_motion=%d reverse_initial=%d live=%d captures=%d post_break_braked=true"),
                    Fast?TEXT("fast_observation"):TEXT("hold_observation"),DestructionProbeSeconds,SoftFoliage->BreakCount(),
                    DestructionTargetContacts,*DestructionExpectedMesh,DestructionExpectedInstance,DestructionPeakImpulseNs,DestructionPeakNormalSpeedMps,
                    DestructionDebrisObservations.Num(),Positive,Reverse,Live,DestructionCaptureCount);
            }
            bDestructionValidationFinished=true;FPlatformMisc::RequestExitWithStatus(false,0);
        }
        return true;
    }

    if(bDestructionExpectFixed&&DestructionProbeSeconds>=12.)
    {
        Rig->SetContactDrive(0,0,true);
        if(DestructionTargetContacts==0)return Fail(TEXT("protected fixture was never physically contacted"));
        if(!Capture(TEXT("protected_after")))return Fail(TEXT("protected-object PNG capture failed"));
        UE_LOG(LogRMSim,Display,TEXT("WT_DESTRUCTION_FIXED_PASSED seconds=%.6f broken=%d actual_target_contacts=%d expected_mesh=%s expected_instance=%d max_event_impulse_Ns=%.6f max_normal_speed_mps=%.6f captures=%d"),
            DestructionProbeSeconds,SoftFoliage->BreakCount(),DestructionTargetContacts,*DestructionExpectedMesh,DestructionExpectedInstance,
            DestructionPeakImpulseNs,DestructionPeakNormalSpeedMps,DestructionCaptureCount);
        bDestructionValidationFinished=true;FPlatformMisc::RequestExitWithStatus(false,0);return true;
    }
    if(DestructionProbeSeconds>=20.)return Fail(Foliage?
        TEXT("actual vehicle travel did not produce a foliage patch within20s"):
        TEXT("actual vehicle contact did not produce the requested break within20s"));
    // Drive through the selected contact location; the physics solver alone moves the vehicle.
    // All break modes stop immediately at the first observed break for comparable visual evidence.
    const FVector Aim=DestructionTargetCm+DestructionApproach*100.;
    const FVector Toward=(Aim-Rig->GetActorLocation()).GetSafeNormal2D();
    Rig->SetContactPlanarDrive(FVector2D(Toward.X,-Toward.Y)*DestructionDriveSpeed,0,true,false);
    return true;
}
