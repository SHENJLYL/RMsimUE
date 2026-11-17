#include "ImportLabGameMode.h"
#include "VehicleRig.h"
#include "TrainingRange.h"
#include "RosBridgeComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "GameFramework/PlayerController.h"

void AImportLabGameMode::ResetContactVehicle(const FString& Reason,bool ResetTraining)
{
    if(!IsValid(Rig)||!Rig->IsContactPhysics())return;
    Rig->ResetContactPose(ContactSpawn);++ContactRecoveryCount;
    if(RosBridge)RosBridge->ResetEpoch();
    SpeedCmPerSecond=0;TurnDegreesPerSecond=0;bAimTargetValid=false;bHoldAimAfterFreeLook=false;
    bGyroMode=false;bPrecisionDrive=false;GyroSpeedRadS=Rig->GetDriveParameter(TEXT("GyroDefaultRadS"));
    bFreeLook=false;OrbitYaw=ContactSpawn.Rotator().Yaw;OrbitPitch=-12;
    if(ResetTraining&&Training)Training->ResetRound();
    UE_LOG(LogRMSim,Display,TEXT("CONTACT_RECOVERY reason=%s count=%d"),*Reason,ContactRecoveryCount);
}

void AImportLabGameMode::RecoverContactVehicleIfOutsideMap()
{
    if(!IsValid(Rig)||!Rig->IsContactPhysics())return;
    const double LowestMapZ=(StageMap.bMeshTerrain?StageMap.SourceBoundsMetres.Min.Z:StageMap.FloorZ-StageMap.FloorThickness)*100;
    if(Rig->GetActorLocation().Z<LowestMapZ-500)ResetContactVehicle(TEXT("fell_below_map"),true);
}

void AImportLabGameMode::TickContactProbe(double DeltaSeconds)
{
    if(FParse::Param(FCommandLine::Get(),TEXT("SuspensionProbe"))){TickSuspensionProbe(DeltaSeconds);return;}
    if(!Rig||!Rig->IsContactPhysics()){Abort(TEXT("contact probe lacks native physics vehicle"));return;}
    const bool Integration=FParse::Param(FCommandLine::Get(),TEXT("ContactIntegration"));
    if(Integration&&!Training){Abort(TEXT("contact integration requires training mode"));return;}
    UpdatePlayerView(GetWorld()->GetFirstPlayerController());
    if(!ControlPendingCapture.IsEmpty())
    {
        if(++ControlCaptureWarmFrames<24)return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("contact capture failed"));return;}
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    ContactPhaseTime+=DeltaSeconds;
    const auto State=ManualControlState();
    if(ContactSamples.IsEmpty()||ContactPhaseTime-FMath::Floor((ContactPhaseTime-DeltaSeconds)*4)/4>=.25)
    {
        auto Sample=MakeShared<FJsonObject>();Sample->SetNumberField(TEXT("phase"),ControlProbePhase);
        Sample->SetNumberField(TEXT("phase_time"),ContactPhaseTime);Sample->SetObjectField(TEXT("state"),State);
        ContactSamples.Add(MakeShared<FJsonValueObject>(Sample));ControlReport->SetArrayField(TEXT("samples"),ContactSamples);
    }
    if(Rig->GetActorTransform().ContainsNaN()||Rig->GetActorLocation().Z<ContactStart.GetLocation().Z-200||
        Rig->GetActorLocation().Z>ContactStart.GetLocation().Z+300)
    {Abort(TEXT("contact vehicle escaped physical height envelope"));return;}
    double DistanceCm=340;FParse::Value(FCommandLine::Get(),TEXT("ContactDistanceCm="),DistanceCm);
    double RouteSpeed=1.;FParse::Value(FCommandLine::Get(),TEXT("ContactSpeedMps="),RouteSpeed);
    if(!FMath::IsFinite(RouteSpeed)||RouteSpeed<=0||RouteSpeed>3.){Abort(TEXT("invalid contact route speed"));return;}
    ControlReport->SetNumberField(TEXT("route_command_speed_m_s"),RouteSpeed);
    const FVector Forward=ContactStart.GetRotation().GetForwardVector().GetSafeNormal2D();
    const FVector Side=FVector::CrossProduct(FVector::UpVector,Forward);
    const FVector Displacement=Rig->GetActorLocation()-ContactStart.GetLocation();
    const double Progress=FVector::DotProduct(Displacement,Forward),Drift=FVector::DotProduct(Displacement,Side);
    const double Direction=ControlProbePhase==3?-1.:1.;
    // Drive a centreline with wheel commands, including lateral correction and turn-in-place
    // when misaligned. This controller never imposes a chassis pose or contact exception.
    const double DesiredYaw=ContactStart.Rotator().Yaw+FMath::RadiansToDegrees(FMath::Atan2(-Direction*Drift,60.));
    const double YawError=FMath::FindDeltaAngleDegrees(Rig->GetActorRotation().Yaw,DesiredYaw);
    const double Steer=FMath::Clamp(-FMath::DegreesToRadians(YawError)*4,-2.,2.);
    const double Drive=RouteSpeed*FMath::Clamp(1.-FMath::Abs(YawError)/45.,0.,1.);
    FString Name,Detail;bool Passed=false;
    auto Finish=[&]()
    {
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot save contact report"));return;}
        UE_LOG(LogRMSim,Display,TEXT("CONTACT_PROBE_COMPLETE checks=%d actual_physics_samples=%d"),ControlChecks.Num(),ContactSamples.Num());
        bReady=false;FPlatformMisc::RequestExitWithStatus(false,0);
    };
    if(ControlProbePhase>=4&&!Integration){Finish();return;}
    switch(ControlProbePhase)
    {
    case 0:
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<3)return;
        Name=TEXT("physical_settle");Detail=FString::Printf(TEXT("Native bodies settle under gravity: speed %.3fcm/s upZ %.5f"),Rig->GetVelocity().Size(),Rig->GetActorUpVector().Z);
        Passed=Rig->GetVelocity().Size()<15&&Rig->GetActorUpVector().Z>.9;
        ContactStart=Rig->GetActorTransform();ControlPendingCapture=TEXT("contact-rest");break;
    case 1:
        Rig->SetContactDrive(Drive,Steer,false);
        if(Progress<DistanceCm&&ContactPhaseTime<35)return;
        Rig->SetContactDrive(0,0,true);
        Name=TEXT("physical_forward_route");Detail=FString::Printf(TEXT("Motor/contact driven route progress %.3f/%.3fcm, drift %.3fcm, time %.3fs"),Progress,DistanceCm,Drift,ContactPhaseTime);
        Passed=Progress>=DistanceCm&&FMath::Abs(Drift)<40&&Rig->GetActorUpVector().Z>.5;
        ControlPendingCapture=TEXT("contact-end");break;
    case 2:
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<2)return;
        Name=TEXT("physical_brake");Detail=FString::Printf(TEXT("Brake acts on physical wheels, measured speed %.4fcm/s"),Rig->GetVelocity().Size());
        Passed=Rig->GetVelocity().Size()<15;break;
    case 3:
        Rig->SetContactDrive(-Drive,Steer,false);
        if(Progress>10&&ContactPhaseTime<35)return;
        Rig->SetContactDrive(0,0,true);
        Name=TEXT("physical_reverse_route");Detail=FString::Printf(TEXT("Reverse crosses same source route: remaining %.3fcm, drift %.3fcm"),Progress,Drift);
        Passed=Progress<=10&&FMath::Abs(Drift)<50&&Rig->GetActorUpVector().Z>.5;
        ControlPendingCapture=TEXT("contact-return");break;
    case 4:
    case 5:
    {
        FManualControlInput Input;Input.Turn=ControlProbePhase==4?-1:1;Input.Steer=Input.Turn;
        ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<1.2)return;
        const double Turn=FMath::FindDeltaAngleDegrees(ContactStageStart.Rotator().Yaw,Rig->GetActorRotation().Yaw);
        Rig->SetContactDrive(0,0,true);
        Name=ControlProbePhase==4?TEXT("physical_shared_left_turn"):TEXT("physical_shared_right_turn");
        Detail=FString::Printf(TEXT("Shared Q/E path measured hull yaw change %.3f degrees"),Turn);
        Passed=Turn*Input.Turn>40&&FMath::Abs(Turn)<150&&Rig->GetActorUpVector().Z>.8;break;
    }
    case 6:
    {
        const FVector Muzzle=GetAimCapture()->GetComponentLocation(),Target=Training->GetTargetPoint(0),Delta=Target-Muzzle;
        const double Distance=Delta.Size2D(),V=Training->GetLaunchSpeed(),G=Training->GetGravity();
        const double Discriminant=V*V*V*V-G*(G*Distance*Distance+2*Delta.Z*V*V);
        if(Discriminant<0){Abort(TEXT("training target outside ballistic range"));return;}
        AimWorldTarget=FVector(Target.X,Target.Y,Muzzle.Z+(V*V-FMath::Sqrt(Discriminant))/G);
        bAimTargetValid=true;bHoldAimAfterFreeLook=true;
        FManualControlInput Input;Input.Brake=true;Input.Fire=ContactPhaseTime>1.&&Training->GetShots()==0;
        ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<2.)return;
        Name=TEXT("physical_shared_aim_fire");Detail=FString::Printf(TEXT("Physical chassis, shared aim/LMB: shots=%d hits=%d targetHP=%d"),Training->GetShots(),Training->GetHits(),Training->GetTargetHealth(0));
        Passed=Training->GetShots()==1&&Training->GetHits()==1&&Training->GetTargetHealth(0)==80;
        ControlPendingCapture=TEXT("contact-fire");break;
    }
    case 7:
    {
        const FVector WallDirection=ContactStageStart.GetRotation().GetForwardVector().GetSafeNormal2D();
        if(!ContactProbeWall)
        {
            FVector Position=ContactStageStart.GetLocation()+WallDirection*120;Position.Z=50;
            auto* Wall=GetWorld()->SpawnActor<AStaticMeshActor>(Position,FRotator(0,ContactStageStart.Rotator().Yaw,0));
            if(!Wall){Abort(TEXT("cannot create physical wall check"));return;}
            ContactProbeWall=Wall;
            Wall->SetActorLabel(TEXT("ContactProbeWall"));auto* Mesh=Wall->GetStaticMeshComponent();
            Mesh->SetMobility(EComponentMobility::Movable);
            Mesh->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube")));
            Mesh->SetWorldScale3D(FVector(.2,2,1.2));Mesh->SetCollisionProfileName(TEXT("BlockAll"));
            Mesh->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);Mesh->RecreatePhysicsState();
            UE_LOG(LogRMSim,Display,TEXT("CONTACT_WALL_CREATED location=%s bounds=%s"),*Mesh->GetComponentLocation().ToString(),*Mesh->Bounds.BoxExtent.ToString());
        }
        if(!ContactProbeWall->GetStaticMeshComponent()->IsPhysicsStateCreated())return;
        FManualControlInput Input;Input.Throttle=.25;ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<4.)return;
        const double Travel=FVector::DotProduct(Rig->GetActorLocation()-ContactStageStart.GetLocation(),WallDirection);
        Name=TEXT("physical_wall_stop");Detail=FString::Printf(TEXT("Shared throttle into real wall: travel %.3fcm, speed %.3fcm/s"),Travel,Rig->GetVelocity().Size());
        Passed=Travel>50&&Travel<111&&Rig->GetVelocity().Size()<15&&Rig->GetActorUpVector().Z>.8;
        Rig->SetContactDrive(0,0,true);ControlPendingCapture=TEXT("contact-wall");break;
    }
    case 8:
    {
        FManualControlInput Input;Input.Brake=true;Input.RestartRound=Training->GetEpoch()==1;
        ApplyManualInput(Input,DeltaSeconds);
        if(ContactPhaseTime<2)return;
        Name=TEXT("physical_shared_reset");Detail=TEXT("R restores all independent bodies, clears velocity, resets training once and settles at spawn");
        Passed=ContactRecoveryCount==1&&Training->GetEpoch()==2&&Training->GetShots()==0&&
            FVector::Dist2D(Rig->GetActorLocation(),ContactSpawn.GetLocation())<5&&Rig->GetVelocity().Size()<10;break;
    }
    case 9:
    {
        if(ContactRecoveryCount==1)
        {
            FTransform Outside=ContactSpawn;
            const double MinimumZ=(StageMap.bMeshTerrain?StageMap.SourceBoundsMetres.Min.Z:StageMap.FloorZ-StageMap.FloorThickness)*100;
            Outside.SetLocation(FVector(Outside.GetLocation().X,Outside.GetLocation().Y,MinimumZ-600));
            Rig->ResetContactPose(Outside); // Test-only fall injection, not a traversal result.
            RecoverContactVehicleIfOutsideMap();
        }
        Rig->SetContactDrive(0,0,true);
        if(ContactPhaseTime<2)return;
        Name=TEXT("physical_fall_recovery");Detail=TEXT("Injected below-map pose recovers through the live guard before KillZ, once only");
        Passed=ContactRecoveryCount==2&&Training->GetEpoch()==3&&
            FVector::Dist2D(Rig->GetActorLocation(),ContactSpawn.GetLocation())<5&&Rig->GetVelocity().Size()<10;
        ControlPendingCapture=TEXT("contact-recovered");break;
    }
    default:Finish();return;
    }
    if(!Passed){Rig->SetContactDrive(0,0,true);CaptureControlView(TEXT("contact-failed"));}
    if(RecordControlCheck(Name,Passed,State,Detail)){++ControlProbePhase;ContactPhaseTime=0;ContactStageStart=Rig->GetActorTransform();}
}
