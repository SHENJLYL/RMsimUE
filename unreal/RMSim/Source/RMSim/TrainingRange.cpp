#include "TrainingRange.h"
#include "VehicleRig.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/ConfigCacheIni.h"

ATrainingRange::ATrainingRange()
{
    RootComponent=CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    PrimaryActorTick.bCanEverTick=false;
}

UStaticMeshComponent* ATrainingRange::Visual(const TCHAR* MeshPath,const FVector& Scale,const FLinearColor& Color,bool Blocking)
{
    auto* C=NewObject<UStaticMeshComponent>(this);
    C->SetupAttachment(RootComponent);C->SetMobility(EComponentMobility::Movable);
    C->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,MeshPath));C->SetRelativeScale3D(Scale);
    C->SetCollisionProfileName(Blocking?TEXT("BlockAll"):TEXT("NoCollision"));
    C->SetCollisionEnabled(Blocking?ECollisionEnabled::QueryOnly:ECollisionEnabled::NoCollision);
    auto* M=UMaterialInstanceDynamic::Create(LoadObject<UMaterialInterface>(nullptr,TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")),this);
    M->SetVectorParameterValue(TEXT("Color"),Color);C->SetMaterial(0,M);
    AddInstanceComponent(C);C->RegisterComponent();return C;
}

bool ATrainingRange::Initialize(AVehicleRig* Vehicle,FString& Error)
{
    if(!Vehicle||!GetWorld()||!Targets.IsEmpty()){Error=TEXT("invalid range initialization");return false;}
    Rig=Vehicle;
    for(auto Item : {TPair<const TCHAR*,double*>(TEXT("RoundSeconds"),&RoundSeconds),
        {TEXT("ProjectileSpeedCm"),&LaunchSpeedCm},{TEXT("GravityCm"),&GravityCm},{TEXT("ShotInterval"),&ShotInterval},
        {TEXT("HeatPerShot"),&HeatPerShot},{TEXT("MaxHeat"),&MaxHeat},{TEXT("CoolingPerSecond"),&Cooling}})
        GConfig->GetDouble(TEXT("RMSim.Training"),Item.Key,*Item.Value,GGameIni);
    GConfig->GetInt(TEXT("RMSim.Training"),TEXT("StartingAmmo"),StartingAmmo,GGameIni);
    GConfig->GetInt(TEXT("RMSim.Training"),TEXT("TargetHealth"),TargetMaxHealth,GGameIni);
    GConfig->GetInt(TEXT("RMSim.Training"),TEXT("Damage"),Damage,GGameIni);
    if(!FMath::IsFinite(RoundSeconds)||RoundSeconds<=0||RoundSeconds>3600||!FMath::IsFinite(LaunchSpeedCm)||LaunchSpeedCm<100||LaunchSpeedCm>50000||
        !FMath::IsFinite(GravityCm)||GravityCm<0||GravityCm>5000||!FMath::IsFinite(ShotInterval)||ShotInterval<.02||ShotInterval>10||
        !FMath::IsFinite(HeatPerShot)||!FMath::IsFinite(MaxHeat)||!FMath::IsFinite(Cooling)||HeatPerShot<=0||MaxHeat<HeatPerShot||Cooling<=0||
        StartingAmmo<1||StartingAmmo>100000||TargetMaxHealth<1||TargetMaxHealth>100000||Damage<1||Damage>TargetMaxHealth)
    {Error=TEXT("invalid explicit training parameters");return false;}
    const FRotator Yaw(0,Rig->GetActorRotation().Yaw,0);
    for(int32 I=0;I<3;++I)
    {
        FVector P=Rig->GetActorLocation()+Yaw.RotateVector(FVector(280+I*60,(I-1)*170,0));
        FHitResult Ground;FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimTrainingGround),true,Rig);Query.AddIgnoredActor(this);
        if(!GetWorld()->LineTraceSingleByChannel(Ground,P+FVector(0,0,200),P-FVector(0,0,300),ECC_Visibility,Query))
        {Error=TEXT("training target has no ground at requested placement");return false;}
        P.Z=Ground.ImpactPoint.Z;
        auto* Stand=Visual(TEXT("/Engine/BasicShapes/Cube.Cube"),FVector(.08,.08,.5),FLinearColor(.1f,.12f,.13f),true);
        Stand->SetWorldLocation(P+FVector(0,0,25));
        FTarget Target;
        Target.Armor=Visual(TEXT("/Engine/BasicShapes/Cube.Cube"),FVector(.025,.40,.28),FLinearColor(.8f,.04f,.03f),true);
        Target.Armor->SetWorldLocationAndRotation(P+FVector(0,0,64),Yaw);
        Target.Material=CastChecked<UMaterialInstanceDynamic>(Target.Armor->GetMaterial(0));Targets.Add(Target);
    }
    ResetRound();return true;
}

void ATrainingRange::Event(const FString& Type,int32 Target,int32 Shot)
{
    LastEvent=Type;
    auto Entry=MakeShared<FJsonObject>();Entry->SetStringField(TEXT("type"),Type);Entry->SetNumberField(TEXT("epoch"),Epoch);
    Entry->SetNumberField(TEXT("sim_time"),SimTime);Entry->SetNumberField(TEXT("target"),Target);Entry->SetNumberField(TEXT("shot"),Shot);
    Events.Add(MakeShared<FJsonValueObject>(Entry));
    if(Events.Num()>512)Events.RemoveAt(0);
    UE_LOG(LogRMSim,Display,TEXT("TRAINING_EVENT epoch=%d time=%.4f type=%s target=%d shot=%d"),Epoch,SimTime,*Type,Target,Shot);
}

void ATrainingRange::ResetRound()
{
    for(FShot& Shot:Projectiles)if(Shot.Visual)Shot.Visual->DestroyComponent();Projectiles.Reset();Events.Reset();
    ++Epoch;SimTime=0;NextShotTime=0;Heat=0;Ammo=StartingAmmo;Shots=Hits=Score=0;Status=TEXT("running");
    LastHitActor.Reset();
    for(FTarget& Target:Targets)
    {
        Target.Health=TargetMaxHealth;Target.Armor->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
        Target.Material->SetVectorParameterValue(TEXT("Color"),FLinearColor(.8f,.04f,.03f));
    }
    Event(TEXT("round_started"));
}

void ATrainingRange::Finish(const FString& Result)
{
    Status=Result;for(FShot& Shot:Projectiles)if(Shot.Visual)Shot.Visual->DestroyComponent();Projectiles.Reset();Event(Result);
}

void ATrainingRange::FireShot(const FTransform& Muzzle)
{
    if(Ammo<=0){LastEvent=TEXT("empty");return;}
    if(Heat+HeatPerShot>MaxHeat+1.e-6){LastEvent=TEXT("overheat");return;}
    if(SimTime+1.e-8<NextShotTime)return;
    --Ammo;++Shots;Heat=FMath::Min(MaxHeat,Heat+HeatPerShot);NextShotTime=SimTime+ShotInterval;
    FShot Shot;Shot.Number=Shots;Shot.Position=Muzzle.GetLocation();Shot.Velocity=Muzzle.GetRotation().GetForwardVector()*LaunchSpeedCm;
    Shot.Visual=Visual(TEXT("/Engine/BasicShapes/Sphere.Sphere"),FVector(.025),FLinearColor(1.f,.65f,.05f),false);
    Shot.Visual->SetWorldLocation(Shot.Position);Projectiles.Add(Shot);Event(TEXT("shot"),-1,Shots);
}

void ATrainingRange::StepProjectiles(double Seconds)
{
    for(int32 I=Projectiles.Num()-1;I>=0;--I)
    {
        FShot& Shot=Projectiles[I];const FVector Gravity(0,0,-GravityCm);
        const FVector End=Shot.Position+Shot.Velocity*Seconds+Gravity*(.5*Seconds*Seconds);
        FHitResult Hit;FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimProjectile),true,Rig);
        const bool Blocked=GetWorld()->SweepSingleByChannel(Hit,Shot.Position,End,FQuat::Identity,ECC_Visibility,FCollisionShape::MakeSphere(.85f),Query);
        Shot.Age+=Seconds;
        if(Blocked)
        {
            LastHitActor=Hit.GetActor()?Hit.GetActor()->GetName():TEXT("unknown");
            bool ArmorHit=false;
            for(int32 T=0;T<Targets.Num();++T)if(Hit.GetComponent()==Targets[T].Armor&&Targets[T].Health>0)
            {
                FTarget& Target=Targets[T];Target.Health=FMath::Max(0,Target.Health-Damage);++Hits;ArmorHit=true;
                Event(TEXT("armor_hit"),T,Shot.Number);
                if(Target.Health==0)
                {
                    ++Score;Target.Armor->SetCollisionEnabled(ECollisionEnabled::NoCollision);
                    Target.Material->SetVectorParameterValue(TEXT("Color"),FLinearColor(.08f,.08f,.08f));Event(TEXT("target_disabled"),T,Shot.Number);
                }
                break;
            }
            if(!ArmorHit)Event(TEXT("world_hit"),-1,Shot.Number);
        }
        if(Blocked||Shot.Age>5)
        {Shot.Visual->DestroyComponent();Projectiles.RemoveAt(I);}
        else{Shot.Position=End;Shot.Velocity+=Gravity*Seconds;Shot.Visual->SetWorldLocation(End);}
    }
    if(Score==Targets.Num())Finish(TEXT("victory"));
}

void ATrainingRange::Advance(double Seconds,bool Fire,bool Reset,const FTransform& Muzzle)
{AdvanceInterval(Seconds,Fire,Reset,Muzzle,Muzzle);}

void ATrainingRange::AdvanceInterval(double Seconds,bool Fire,bool Reset,const FTransform& StartMuzzle,const FTransform& EndMuzzle)
{
    if(!FMath::IsFinite(Seconds)||Seconds<0||Seconds>.25||StartMuzzle.ContainsNaN()||EndMuzzle.ContainsNaN())return;
    if(Reset){ResetRound();return;}
    if(Status!=TEXT("running"))return;
    if(Fire&&Seconds==0)FireShot(EndMuzzle);
    double Remaining=Seconds;
    while(Remaining>1.e-8&&Status==TEXT("running"))
    {
        if(Fire)
        {
            FTransform Muzzle;Muzzle.Blend(StartMuzzle,EndMuzzle,FMath::Clamp((Seconds-Remaining)/Seconds,0.,1.));
            FireShot(Muzzle);
        }
        double Step=FMath::Min3(Remaining,1./240,RoundSeconds-SimTime);
        if(Fire&&Ammo>0)
        {
            const double ReadyIn=FMath::Max(NextShotTime-SimTime,(Heat+HeatPerShot-MaxHeat)/Cooling);
            if(ReadyIn>1.e-8)Step=FMath::Min(Step,ReadyIn);
        }
        if(Step<=1.e-8){Finish(TEXT("timeout"));break;}
        Remaining-=Step;SimTime+=Step;Heat=FMath::Max(0.,Heat-Cooling*Step);
        StepProjectiles(Step);
        if(Status==TEXT("running")&&SimTime>=RoundSeconds-1.e-8)Finish(TEXT("timeout"));
    }
}

FVector ATrainingRange::GetTargetPoint(int32 Index) const {return Targets.IsValidIndex(Index)?Targets[Index].Armor->GetComponentLocation():FVector::ZeroVector;}
int32 ATrainingRange::GetTargetHealth(int32 Index) const {return Targets.IsValidIndex(Index)?Targets[Index].Health:-1;}

FVector ATrainingRange::PredictImpact(const FTransform& Muzzle) const
{
    FVector P=Muzzle.GetLocation(),V=Muzzle.GetRotation().GetForwardVector()*LaunchSpeedCm;
    const FVector G(0,0,-GravityCm);constexpr double Step=1./120;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimImpactPreview),true,Rig);
    for(int32 I=0;I<600;++I)
    {
        const FVector End=P+V*Step+G*(.5*Step*Step);FHitResult Hit;
        if(GetWorld()->SweepSingleByChannel(Hit,P,End,FQuat::Identity,ECC_Visibility,FCollisionShape::MakeSphere(.85f),Query))return Hit.ImpactPoint;
        P=End;V+=G*Step;
    }
    return P;
}

FString ATrainingRange::Hud() const
{
    return FString::Printf(TEXT("TRAINING  %s  %.0fs  Ammo %d  Heat %.0f/%.0f  Targets %d/%d\nLMB fire   R restart  |  HP %d / %d / %d  |  %s"),
        *Status,FMath::Max(0.,RoundSeconds-SimTime),Ammo,Heat,MaxHeat,Score,Targets.Num(),GetTargetHealth(0),GetTargetHealth(1),GetTargetHealth(2),*LastEvent);
}

TSharedRef<FJsonObject> ATrainingRange::State(bool IncludeEvents) const
{
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("status"),Status);Result->SetNumberField(TEXT("epoch"),Epoch);
    Result->SetNumberField(TEXT("time"),SimTime);Result->SetNumberField(TEXT("ammo"),Ammo);Result->SetNumberField(TEXT("heat"),Heat);
    Result->SetNumberField(TEXT("shots"),Shots);Result->SetNumberField(TEXT("hits"),Hits);Result->SetNumberField(TEXT("score"),Score);
    Result->SetNumberField(TEXT("projectiles"),Projectiles.Num());Result->SetStringField(TEXT("last_event"),LastEvent);
    Result->SetStringField(TEXT("last_hit_actor"),LastHitActor);
    TArray<TSharedPtr<FJsonValue>> Health;for(const auto& Target:Targets)Health.Add(MakeShared<FJsonValueNumber>(Target.Health));
    Result->SetArrayField(TEXT("target_health"),Health);if(IncludeEvents)Result->SetArrayField(TEXT("events"),Events);
    auto Parameters=MakeShared<FJsonObject>();Parameters->SetNumberField(TEXT("round_seconds"),RoundSeconds);
    Parameters->SetNumberField(TEXT("launch_cm_s"),LaunchSpeedCm);Parameters->SetNumberField(TEXT("gravity_cm_s2"),GravityCm);
    Parameters->SetNumberField(TEXT("shot_interval"),ShotInterval);Parameters->SetNumberField(TEXT("heat_per_shot"),HeatPerShot);
    Parameters->SetNumberField(TEXT("cooling_per_second"),Cooling);Parameters->SetNumberField(TEXT("max_heat"),MaxHeat);
    Parameters->SetNumberField(TEXT("damage"),Damage);Parameters->SetNumberField(TEXT("target_health"),TargetMaxHealth);
    Result->SetObjectField(TEXT("demo_parameters"),Parameters);return Result;
}
