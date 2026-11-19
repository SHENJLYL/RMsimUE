#include "ImportLabGameMode.h"
#include "TrainingRange.h"
#include "VehicleRig.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"

void AImportLabGameMode::TickCombatProbe()
{
    auto* PC=GetWorld()->GetFirstPlayerController();
    if(!Training||!Rig||!PC||!GetAimCapture()){Abort(TEXT("combat probe scene unavailable"));return;}
    if(!ControlPendingCapture.IsEmpty())
    {
        auto* View=CastChecked<ACameraActor>(PC->GetViewTarget());
        OverviewCapture->SetWorldTransform(View->GetActorTransform());OverviewCapture->FOVAngle=View->GetCameraComponent()->FieldOfView;
        if(++ControlCaptureWarmFrames<24)return;
        if(!CaptureControlView(ControlPendingCapture)){Abort(TEXT("combat capture failed"));return;}
        ControlPendingCapture.Reset();ControlCaptureWarmFrames=0;return;
    }
    const auto Before=ManualControlState();FManualControlInput Input;
    auto Step=[this](const FManualControlInput& Frame,int32 Count){for(int32 I=0;I<Count;++I)ApplyManualInput(Frame,1./60);};
    auto AimAt=[&](int32 Target)
    {
        for(int32 I=0;I<60;++I)
        {
            const FVector Muzzle=GetAimCapture()->GetComponentLocation();
            const FVector Delta=Training->GetTargetPoint(Target)-Muzzle;
            const double Distance=Delta.Size2D(),V=Training->GetLaunchSpeed(),G=Training->GetGravity();
            const double Discriminant=V*V*V*V-G*(G*Distance*Distance+2*Delta.Z*V*V);
            if(Discriminant<0||Distance<1)return false;
            const double Rise=(V*V-FMath::Sqrt(Discriminant))/G;
            AimWorldTarget=FVector(Training->GetTargetPoint(Target).X,Training->GetTargetPoint(Target).Y,Muzzle.Z+Rise);
            bAimTargetValid=true;bProbeAimWorldTargetOverride=true;
            ApplyManualInput(FManualControlInput(),1./60);
        }
        return true;
    };
    auto ShootTarget=[&](int32 Target)
    {
        if(!AimAt(Target))return false;
        FManualControlInput Shot;Shot.Fire=true;Step(Shot,1);Step(FManualControlInput(),30);return true;
    };
    const FTransform SkyMuzzle(FRotator(80,0,0),GetAimCapture()->GetComponentLocation());
    bool Passed=false;FString Name,Detail;
    switch(ControlProbePhase)
    {
    case 0:
        Name=TEXT("training_initial_state");Detail=TEXT("Three physical armor targets and a running fresh round");
        Passed=Training->GetStatus()==TEXT("running")&&Training->GetAmmo()==100&&Training->GetTargetHealth(0)==100;
        ControlPendingCapture=TEXT("training-start");break;
    case 1:
        Name=TEXT("shared_input_ballistic_hit");Detail=TEXT("Shared LMB path launches a swept ballistic shot; real armor hit deducts 20HP once");
        Passed=AimAt(0)&&FVector::Dist(Training->PredictImpact(GetAimCapture()->GetComponentTransform()),Training->GetTargetPoint(0))<15&&
            ShootTarget(0)&&Training->GetAmmo()==99&&Training->GetShots()==1&&Training->GetHits()==1&&Training->GetTargetHealth(0)==80;
        ControlPendingCapture=TEXT("armor-hit");break;
    case 2:
    {
        Training->ResetRound();
        for(int32 I=0;I<10;++I)Training->Advance(0,true,false,SkyMuzzle);
        Name=TEXT("fire_cadence");Detail=TEXT("Ten trigger calls at the same simulation instant consume exactly one round");
        Passed=Training->GetShots()==1&&Training->GetAmmo()==99&&Training->GetHeat()==10;
        break;
    }
    case 3:
    {
        Training->ResetRound();
        for(int32 I=0;I<120;++I)Training->Advance(1./60,true,false,SkyMuzzle);
        Training->Advance(0,true,false,SkyMuzzle); // At exactly 2s a cooled round may legally fire.
        const int32 Shots=Training->GetShots();const double Heat=Training->GetHeat();
        for(int32 I=0;I<20;++I)Training->Advance(0,true,false,SkyMuzzle);
        Name=TEXT("overheat_blocks_shots");Detail=TEXT("Two-second burst heat-limits shots below cadence maximum; repeated triggers cannot exceed heat capacity");
        Passed=Shots>=10&&Shots<20&&Training->GetShots()==Shots&&Heat<=100;
        break;
    }
    case 4:
    {
        const double Heat=Training->GetHeat();
        for(int32 I=0;I<60;++I)Training->Advance(1./60,false,false,SkyMuzzle);
        Name=TEXT("cooling_uses_sim_time");Detail=TEXT("One second without firing removes exactly 20 heat, clamped at zero");
        Passed=FMath::IsNearlyEqual(Training->GetHeat(),FMath::Max(0.,Heat-20),1.e-5);break;
    }
    case 5:
    {
        Training->ResetRound();
        for(int32 I=0;I<100;++I)
        {Training->Advance(0,true,false,SkyMuzzle);for(int32 J=0;J<4;++J)Training->Advance(.25,false,false,SkyMuzzle);}
        Training->Advance(0,true,false,SkyMuzzle);
        Name=TEXT("empty_magazine");Detail=TEXT("100 rounds consume the finite magazine; an additional trigger creates no projectile");
        Passed=Training->GetAmmo()==0&&Training->GetShots()==100&&Training->GetStatus()==TEXT("running");break;
    }
    case 6:
    {
        const int32 Epoch=Training->GetEpoch();Input.RestartRound=true;Step(Input,1);
        Name=TEXT("shared_reset");Detail=TEXT("R clears projectiles, ammo/heat/hits, restores targets and advances round epoch");
        Passed=Training->GetEpoch()==Epoch+1&&Training->GetAmmo()==100&&Training->GetHeat()==0&&Training->GetHits()==0&&Training->GetLiveProjectiles()==0;
        break;
    }
    case 7:
    {
        Input.Focused=false;Input.Fire=true;Input.RestartRound=true;Step(Input,60);
        Name=TEXT("unfocused_fire_rejected");Detail=TEXT("Unfocused LMB/R cannot shoot/reset; simulation time still advances");
        Passed=Training->GetShots()==0&&Training->GetAmmo()==100&&Training->State()->GetNumberField(TEXT("time"))>.9;
        Input.Focused=true;Input.RestartRound=false;Step(Input,1);
        Passed &= Training->GetShots()==0;
        break;
    }
    case 8:
    {
        Training->ResetRound();
        // Temporarily put the real existing box between muzzle and armor. No fake hit injection.
        if(!ProbeObstacle){Abort(TEXT("combat occlusion check needs flat map inspection obstacle"));return;}
        ControlReport->SetArrayField(TEXT("occluder_original_position"),{
            MakeShared<FJsonValueNumber>(ProbeObstacle->GetActorLocation().X),MakeShared<FJsonValueNumber>(ProbeObstacle->GetActorLocation().Y),MakeShared<FJsonValueNumber>(ProbeObstacle->GetActorLocation().Z)});
        ProbeObstacle->GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);
        ProbeObstacle->SetActorLocation((GetAimCapture()->GetComponentLocation()+Training->GetTargetPoint(0))*.5);
        Name=TEXT("place_real_occluder");Detail=TEXT("Registered map obstacle moved into shot path; wait actual frames before querying");Passed=true;
        ControlPendingCapture=TEXT("occluded");break;
    }
    case 9:
    {
        Name=TEXT("map_blocks_armor_damage");Detail=TEXT("Swept projectile strikes map obstacle first; target HP remains100");
        Passed=ShootTarget(0)&&Training->GetShots()==1&&Training->GetHits()==0&&Training->GetTargetHealth(0)==100&&Training->GetLastHitActor()==ProbeObstacle->GetName();
        const auto V=ControlReport->GetArrayField(TEXT("occluder_original_position"));
        ProbeObstacle->SetActorLocation(FVector(V[0]->AsNumber(),V[1]->AsNumber(),V[2]->AsNumber()));
        ControlPendingCapture=TEXT("clear-lane");break;
    }
    case 10:
    {
        Training->ResetRound();bool HitAll=true;
        for(int32 T=0;T<3;++T)for(int32 I=0;I<5;++I)HitAll &= ShootTarget(T);
        Name=TEXT("targets_and_victory");Detail=TEXT("15 actual armor hits disable all three targets and finish round; no projectiles remain");
        Passed=HitAll&&Training->GetHits()==15&&Training->GetTargetHealth(0)==0&&Training->GetTargetHealth(1)==0&&Training->GetTargetHealth(2)==0&&Training->GetStatus()==TEXT("victory")&&Training->GetLiveProjectiles()==0;
        ControlPendingCapture=TEXT("victory");break;
    }
    case 11:
    {
        const int32 Shots=Training->GetShots();Input.Fire=true;Step(Input,60);
        Name=TEXT("finished_round_rejects_fire");Detail=TEXT("Completed round cannot consume ammo or launch more projectiles");
        Passed=Training->GetShots()==Shots&&Training->GetLiveProjectiles()==0;break;
    }
    case 12:
    {
        Training->ResetRound();for(int32 I=0;I<721;++I)Training->Advance(.25,false,false,SkyMuzzle);
        Training->Advance(0,true,false,SkyMuzzle);
        Name=TEXT("round_timeout");Detail=TEXT("180 seconds of simulation reaches timeout and rejects firing without wall-clock waits");
        Passed=Training->GetStatus()==TEXT("timeout")&&Training->GetShots()==0;break;
    }
    case 13:
    {
        TArray<int32> Counts;TArray<double> Times;
        for(int32 Rate:{50,60,90,144})
        {
            Training->ResetRound();for(int32 I=0;I<Rate*5;++I)Training->Advance(1./Rate,true,false,SkyMuzzle);
            Counts.Add(Training->GetShots());
            const auto Events=Training->State()->GetArrayField(TEXT("events"));
            for(int32 I=Events.Num()-1;I>=0;--I)if(Events[I]->AsObject()->GetStringField(TEXT("type"))==TEXT("shot"))
            {Times.Add(Events[I]->AsObject()->GetNumberField(TEXT("sim_time")));break;}
        }
        Passed=Counts.Num()==4&&Times.Num()==4;
        for(int32 I=1;I<Counts.Num();++I)Passed &= Counts[I]==Counts[0]&&FMath::IsNearlyEqual(Times[I],Times[0],1.e-6);
        Name=TEXT("cadence_frame_partition");Detail=TEXT("5 seconds at 50/60/90/144Hz produce identical shot count and last-shot simulation timestamp");break;
    }
    case 14:
    {
        Training->ResetRound();
        if(!AimAt(1)){Abort(TEXT("cannot aim interpolation fixture"));return;}
        const FTransform Start=GetAimCapture()->GetComponentTransform();
        FTransform End=Start;End.SetRotation(FQuat(FVector::UpVector,-1.4)*Start.GetRotation());
        TArray<int32> Hits,Shots;
        for(int32 Pieces:{1,3})
        {
            Training->ResetRound();
            for(int32 I=0;I<Pieces;++I)
            {
                FTransform A,B;A.Blend(Start,End,double(I)/Pieces);B.Blend(Start,End,double(I+1)/Pieces);
                Training->AdvanceInterval(.15/Pieces,true,false,A,B);
            }
            Training->Advance(.25,false,false,End);Hits.Add(Training->GetHits());Shots.Add(Training->GetShots());
        }
        Name=TEXT("moving_muzzle_event_time");Detail=TEXT("Two shots in150ms muzzle slew: only interval-start shot hits;1/3 partitions give identical actual collision results");
        Passed=Hits.Num()==2&&Hits[0]==1&&Hits[1]==1&&Shots[0]==2&&Shots[1]==2;
        break;
    }
    default:
        if(!WriteControlReport(TEXT("passed"))){Abort(TEXT("cannot finish combat report"));return;}
        bReady=false;UE_LOG(LogRMSim,Display,TEXT("COMBAT_PROBE_COMPLETE %d checks"),ControlChecks.Num());
        FPlatformMisc::RequestExitWithStatus(false,0);return;
    }
    if(RecordControlCheck(Name,Passed,Before,Detail))++ControlProbePhase;
}
