#include "WTFoliageCrushComponent.h"
#include "VehicleRig.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Misc/ConfigCacheIni.h"

namespace
{
constexpr float SoftTriggerCm=135.f;
constexpr float ContactProximityCm=54.f;
constexpr float ScanIntervalSeconds=.075f;
constexpr int32 MaxAnimated=48;
const TCHAR* KindText(int32 Kind)
{
    switch(Kind){case 0:return TEXT("soft_crush");case 1:return TEXT("wood_fence_bend");
        case 2:return TEXT("wood_fracture");default:return TEXT("metal_fence_bend");}
}
}

bool UWTFoliageCrushComponent::IsSoftShrub(const FString& Name)
{
    // Excludes trunked trees, flower-bed concrete/stone bases and rigid planters.
    return Name.StartsWith(TEXT("prop_bush_flowers_")) ||
        Name.StartsWith(TEXT("prop_tree_poplar_bush_")) ||
        Name.StartsWith(TEXT("prop_tree_tilia_bush_")) ||
        (Name.StartsWith(TEXT("prop_flower_bed_")) && Name.Contains(TEXT("_flowers_")));
}

bool UWTFoliageCrushComponent::ClassifyHard(const FString& Name,EKind& Kind)
{
    if(Name.StartsWith(TEXT("prop_sicily_gardens_wood_planks_")) ||
       Name.StartsWith(TEXT("prop_wood_fence_h_plank")) ||
       Name.StartsWith(TEXT("prop_wood_debris_")))
    {Kind=EKind::WoodFracture;return true;}
    if(Name.StartsWith(TEXT("prop_wood_fence_")) ||
       Name.StartsWith(TEXT("prop_wood_gates_")))
    {Kind=EKind::WoodBend;return true;}
    if(Name.StartsWith(TEXT("prop_sicily_fence_cafe_")) ||
       Name.StartsWith(TEXT("prop_sicily_villa_railing_")))
    {Kind=EKind::MetalBend;return true;}
    return false; // Rails, sleepers, concrete, masonry and fixed infrastructure remain separate.
}

void UWTFoliageCrushComponent::Initialize(UWorld* World)
{
    Groups.Reset();Falling.Reset();TotalCrushed=0;TotalBroken=0;ScanAccumulator=0;
    GConfig->GetDouble(TEXT("RMSim.Destruction"),TEXT("WoodImpactMps"),WoodImpactMps,GGameIni);
    GConfig->GetDouble(TEXT("RMSim.Destruction"),TEXT("MetalImpactMps"),MetalImpactMps,GGameIni);
    GConfig->GetDouble(TEXT("RMSim.Destruction"),TEXT("WoodPressureSeconds"),WoodPressureSeconds,GGameIni);
    GConfig->GetDouble(TEXT("RMSim.Destruction"),TEXT("MetalPressureSeconds"),MetalPressureSeconds,GGameIni);
    if(!FMath::IsFinite(WoodImpactMps)||WoodImpactMps<.2||WoodImpactMps>15 ||
       !FMath::IsFinite(MetalImpactMps)||MetalImpactMps<.2||MetalImpactMps>15 ||
       !FMath::IsFinite(WoodPressureSeconds)||WoodPressureSeconds<.1||WoodPressureSeconds>15 ||
       !FMath::IsFinite(MetalPressureSeconds)||MetalPressureSeconds<.1||MetalPressureSeconds>15)
    {UE_LOG(LogTemp,Error,TEXT("invalid RMSim.Destruction threshold"));return;}
    if(!World)return;
    int32 SoftInstances=0,SoftCollidersDisabled=0,HardInstances=0;
    for(TActorIterator<AActor> It(World);It;++It)
    {
        TArray<UHierarchicalInstancedStaticMeshComponent*> Components;
        It->GetComponents<UHierarchicalInstancedStaticMeshComponent>(Components);
        for(UHierarchicalInstancedStaticMeshComponent* Visual:Components)
        {
            if(!Visual || !Visual->GetStaticMesh() ||
               Visual->GetStaticMesh()->GetName().StartsWith(TEXT("col_")) ||
               Visual->GetCollisionEnabled()!=ECollisionEnabled::NoCollision)continue;
            const FString Name=Visual->GetStaticMesh()->GetName();
            EKind Kind=EKind::Soft;
            const bool Soft=IsSoftShrub(Name);
            if(!Soft&&!ClassifyHard(Name,Kind))continue;
            UHierarchicalInstancedStaticMeshComponent* Matched=nullptr;
            for(UHierarchicalInstancedStaticMeshComponent* Other:Components)
            {
                if(Other==Visual || !Other || !Other->GetStaticMesh())continue;
                if(Other->GetStaticMesh()->GetName()==TEXT("col_")+Name &&
                   Other->GetInstanceCount()==Visual->GetInstanceCount())
                {Matched=Other;break;}
            }
            if(!Soft && !Matched)continue; // Solid until native collider is explicitly matched.
            FGroup Group;Group.Visual=Visual;Group.Collider=Matched;Group.Kind=Kind;
            Groups.Add(MoveTemp(Group));
            if(Soft)
            {
                SoftInstances+=Visual->GetInstanceCount();
                if(Matched)
                {
                    SoftCollidersDisabled+=Matched->GetInstanceCount();
                    Matched->SetCollisionProfileName(TEXT("NoCollision"));
                    Matched->SetCollisionEnabled(ECollisionEnabled::NoCollision);
                }
            }
            else HardInstances+=Visual->GetInstanceCount();
        }
    }
    UE_LOG(LogTemp,Display,
        TEXT("WT_DESTRUCTION_READY groups=%d soft_visuals=%d soft_colliders_disabled=%d rigid_breakables=%d"),
        Groups.Num(),SoftInstances,SoftCollidersDisabled,HardInstances);
}

void UWTFoliageCrushComponent::SpawnSplinters(const FGroup& Group,
    const FTransform& Original,const FVector& Direction,int32 Count)
{
    UHierarchicalInstancedStaticMeshComponent* Source=Group.Visual.Get();
    UWorld* World=GetWorld();
    if(!Source || !Source->GetStaticMesh() || !World)return;
    UStaticMesh* Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));
    if(!Cube)return;
    const FBox Bound=Source->GetStaticMesh()->GetBoundingBox();
    const FVector WorldSize=Bound.GetSize()*Original.GetScale3D().GetAbs();
    const int32 LongAxis=WorldSize.X>=WorldSize.Y?0:1;
    const float LongSize=FMath::Clamp(static_cast<float>(WorldSize[LongAxis]),25.f,450.f);
    const float ShortHorizontal=FMath::Clamp(static_cast<float>(WorldSize[1-LongAxis]),4.f,50.f);
    const float Vertical=FMath::Clamp(static_cast<float>(WorldSize.Z),4.f,70.f);
    const FVector Normal=Direction.GetSafeNormal2D();
    UMaterialInterface* Wood=Source->GetStaticMesh()->GetMaterial(0);
    for(int32 I=0;I<Count;++I)
    {
        FVector Local=Bound.GetCenter();
        Local[LongAxis]+=(float(I)+.5f-float(Count)/2.f)*Bound.GetSize()[LongAxis]/Count;
        FTransform Pose=Original;
        Pose.SetLocation(Original.TransformPosition(Local));
        FActorSpawnParameters Params;
        Params.SpawnCollisionHandlingOverride=ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        AStaticMeshActor* Fragment=World->SpawnActor<AStaticMeshActor>(
            AStaticMeshActor::StaticClass(),Pose,Params);
        if(!Fragment)continue;
        UStaticMeshComponent* Body=Fragment->GetStaticMeshComponent();
        Body->SetMobility(EComponentMobility::Movable);
        Body->SetStaticMesh(Cube);
        if(Wood)Body->SetMaterial(0,Wood);
        Body->SetCollisionProfileName(TEXT("PhysicsActor"));
        const float ShardLength=LongSize/Count*.84f;
        const float Variation=I%2?.78f:1.0f;
        FVector Dimensions=LongAxis==0?
            FVector(ShardLength,ShortHorizontal*Variation,Vertical*.8f):
            FVector(ShortHorizontal*Variation,ShardLength,Vertical*.8f);
        Fragment->SetActorScale3D(Dimensions/100.f);
        Body->SetSimulatePhysics(true);
        Body->SetMassOverrideInKg(NAME_None,FMath::Clamp(ShardLength*.006f,.15f,1.5f),true);
        Body->AddImpulse(Normal*(48.f+I*18.f)+FVector(0,0,42.f+I*16.f));
        Body->AddAngularImpulseInRadians(
            FVector((I&1)?1.f:-1.f,0.4f,0.7f)*(8.f+2.f*I),NAME_None,true);
        Fragment->SetLifeSpan(3.7f);
    }
}

void UWTFoliageCrushComponent::Trigger(FGroup& Group,int32 Index,
    const FVector& VehicleLocationCm,const FVector& Direction,bool HasHit)
{
    UHierarchicalInstancedStaticMeshComponent* Source=Group.Visual.Get();
    if(!Source || !Source->GetStaticMesh() || Group.Removed.Contains(Index))return;
    FTransform Original;
    if(!Source->GetInstanceTransform(Index,Original,true))return;
    const FBox Local=Source->GetStaticMesh()->GetBoundingBox();
    const FVector Root=Original.TransformPosition(FVector(Local.GetCenter().X,Local.GetCenter().Y,Local.Min.Z));
    if(Group.Kind==EKind::Soft && FVector::DistSquared2D(Root,VehicleLocationCm)>FMath::Square(SoftTriggerCm))
        return;
    UWorld* World=GetWorld();
    if(!World)return;
    FActorSpawnParameters Params;
    Params.SpawnCollisionHandlingOverride=ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    AStaticMeshActor* Fallen=nullptr;
    if(Group.Kind!=EKind::WoodFracture)
    {
        Fallen=World->SpawnActor<AStaticMeshActor>(AStaticMeshActor::StaticClass(),Original,Params);
        if(!Fallen)return;
        UStaticMeshComponent* Mesh=Fallen->GetStaticMeshComponent();
        Mesh->SetMobility(EComponentMobility::Movable);
        Mesh->SetCollisionProfileName(TEXT("NoCollision"));
        Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Mesh->SetStaticMesh(Source->GetStaticMesh());
        Fallen->SetActorTransform(Original);
    }
    // Corresponding source visual and collision components use the same RMI1
    // ordering. Remove only this instance; other solid fences stay physical.
    FTransform Hidden=Original;
    Hidden.SetScale3D(Original.GetScale3D()*0.0001);
    Source->UpdateInstanceTransform(Index,Hidden,true,true,true);
    if(auto* Collider=Group.Collider.Get())
        Collider->UpdateInstanceTransform(Index,Hidden,true,true,true);
    Group.Removed.Add(Index);Group.PressureSeconds.Remove(Index);Group.ApproachPeakMps.Remove(Index);

    if(Group.Kind==EKind::WoodFracture || Group.Kind==EKind::WoodBend)
        SpawnSplinters(Group,Original,Direction,Group.Kind==EKind::WoodFracture?4:2);
    if(Fallen)
    {
        FFalling Animation;
        Animation.Actor=Fallen;Animation.OriginalWorld=Original;
        Animation.Root=Root;Animation.Travel=Direction.GetSafeNormal2D();
        Animation.Axis=FVector::CrossProduct(FVector::UpVector,Animation.Travel).GetSafeNormal();
        Animation.bWood=Group.Kind==EKind::WoodBend;
        Animation.AngleAmplitude=Group.Kind==EKind::Soft?1.25f:1.55f;
        Animation.Duration=Group.Kind==EKind::Soft?4.3f:5.1f;
        Animation.Hold=Group.Kind==EKind::Soft?3.1f:3.8f;
        Falling.Add(MoveTemp(Animation));
    }
    if(Group.Kind==EKind::Soft)++TotalCrushed;else ++TotalBroken;
    UE_LOG(LogTemp,Display,
        TEXT("WT_DESTRUCTION_EVENT kind=%s mesh=%s instance=%d hit=%d crushed=%d hard_broken=%d"),
        KindText(static_cast<int32>(Group.Kind)),*Source->GetStaticMesh()->GetName(),Index,
        HasHit?1:0,TotalCrushed,TotalBroken);
}

bool UWTFoliageCrushComponent::ProbeOneOfEach(TArray<FString>& Outcomes)
{
    Outcomes.Reset();
    bool Seen[4]={false,false,false,false};
    for(FGroup& Group:Groups)
    {
        const int32 Kind=static_cast<int32>(Group.Kind);
        if(Seen[Kind])continue;
        UHierarchicalInstancedStaticMeshComponent* Visual=Group.Visual.Get();
        if(!Visual || Visual->GetInstanceCount()<1)continue;
        UHierarchicalInstancedStaticMeshComponent* Collider=Group.Collider.Get();
        if(Group.Kind!=EKind::Soft &&
           (!Collider || Collider->GetCollisionEnabled()==ECollisionEnabled::NoCollision))return false;
        FTransform Before,After,OtherBefore,OtherAfter;
        if(!Visual->GetInstanceTransform(0,Before,true))return false;
        const bool CheckOther=Visual->GetInstanceCount()>1;
        if(CheckOther && !Visual->GetInstanceTransform(1,OtherBefore,true))return false;
        const FBox Bounds=Visual->GetStaticMesh()->GetBoundingBox();
        const FVector Root=Before.TransformPosition(FVector(Bounds.GetCenter().X,Bounds.GetCenter().Y,Bounds.Min.Z));
        const int32 PreviousCrush=TotalCrushed,PreviousBreak=TotalBroken;
        Trigger(Group,0,Root,FVector::ForwardVector,true);
        if(!Visual->GetInstanceTransform(0,After,true) ||
           After.GetScale3D().SizeSquared()>=Before.GetScale3D().SizeSquared()*.00001 ||
           (CheckOther && (!Visual->GetInstanceTransform(1,OtherAfter,true) ||
                           !OtherBefore.Equals(OtherAfter,1.e-5))))return false;
        if(Group.Kind==EKind::Soft)
        {
            if(TotalCrushed!=PreviousCrush+1 ||
               (Collider && Collider->GetCollisionEnabled()!=ECollisionEnabled::NoCollision))return false;
        }
        else
        {
            FTransform RemovedCollision;
            if(TotalBroken!=PreviousBreak+1 ||
               Collider->GetCollisionEnabled()==ECollisionEnabled::NoCollision ||
               !Collider->GetInstanceTransform(0,RemovedCollision,true) ||
               RemovedCollision.GetScale3D().SizeSquared()>=Before.GetScale3D().SizeSquared()*.00001)
                return false;
        }
        Seen[Kind]=true;
        Outcomes.Add(FString::Printf(TEXT("%s:%s"),KindText(Kind),*Visual->GetStaticMesh()->GetName()));
    }
    return Seen[0] && Seen[1] && Seen[2] && Seen[3];
}

void UWTFoliageCrushComponent::Advance(const AVehicleRig* Rig,double Seconds)
{
    if(!Rig || !Rig->IsContactPhysics())return;
    const float Dt=FMath::Clamp(static_cast<float>(Seconds),0.f,.1f);
    for(int32 I=Falling.Num()-1;I>=0;--I)
    {
        FFalling& State=Falling[I];
        AStaticMeshActor* Actor=State.Actor.Get();
        if(!Actor){Falling.RemoveAtSwap(I);continue;}
        State.Elapsed+=Dt;
        if(State.Elapsed>=State.Duration){Actor->Destroy();Falling.RemoveAtSwap(I);continue;}
        const float T=State.Elapsed;
        // Damped flexible response, with heavier fence sections buckling more
        // slowly than shrubs and delayed, shrinking disposal.
        const float Speed=State.bWood?3.1f:4.3f;
        const float Fall=1.f-FMath::Exp(-Speed*T);
        const float Rebound=(State.bWood?.06f:.11f)*FMath::Sin(10.f*T)*FMath::Exp(-2.7f*T);
        const FQuat Tilt(State.Axis,State.AngleAmplitude*(Fall+Rebound));
        const float Fade=T<=State.Hold?1.f:
            FMath::Clamp((State.Duration-T)/(State.Duration-State.Hold),.001f,1.f);
        FTransform Pose=State.OriginalWorld;
        Pose.SetRotation(Tilt*State.OriginalWorld.GetRotation());
        Pose.SetLocation(State.Root+
            Tilt.RotateVector(State.OriginalWorld.GetLocation()-State.Root)+
            State.Travel*(16.f*FMath::Min(T,1.f)));
        Pose.SetScale3D(State.OriginalWorld.GetScale3D()*Fade);
        Actor->SetActorTransform(Pose,false,nullptr,ETeleportType::TeleportPhysics);
    }

    ScanAccumulator+=Dt;
    if(ScanAccumulator<ScanIntervalSeconds)return;
    const float Step=FMath::Min(static_cast<float>(ScanAccumulator),.25f);
    ScanAccumulator=0;
    const FVector Position=Rig->GetActorLocation();
    const FVector Actual=Rig->GetCommandOriginVelocity().GetSafeNormal2D();
    const FVector ActualVelocity=Rig->GetCommandOriginVelocity();
    const FVector Wanted=Rig->GetRequestedDriveWorldVelocityCmS();
    const FVector Direction=ActualVelocity.SizeSquared2D()>FMath::Square(40.f)?
        Actual:Wanted.GetSafeNormal2D();
    int32 Started=0;
    for(FGroup& Group:Groups)
    {
        auto* Visual=Group.Visual.Get();
        if(!Visual)continue;
        const TArray<int32> Candidates=Visual->GetInstancesOverlappingSphere(Position,350.f,true);
        for(int32 Index:Candidates)
        {
            if(Group.Removed.Contains(Index))continue;
            FTransform Transform;
            if(!Visual->GetInstanceTransform(Index,Transform,true))continue;
            const FBox Box=Visual->GetStaticMesh()->GetBoundingBox().TransformBy(Transform);
            const FVector Closest=Box.GetClosestPointTo(Position);
            const FVector Toward=(Closest-Position).GetSafeNormal2D();
            const double Range2D=FVector::DistSquared2D(Closest,Position);
            if(Group.Kind==EKind::Soft)
            {
                if(ActualVelocity.SizeSquared2D()>=FMath::Square(40.f) &&
                   FVector::DotProduct(Direction,Toward)>-.3 &&
                   FVector::DistSquared2D(Position,Transform.TransformPosition(
                       FVector(Visual->GetStaticMesh()->GetBoundingBox().GetCenter().X,
                               Visual->GetStaticMesh()->GetBoundingBox().GetCenter().Y,
                               Visual->GetStaticMesh()->GetBoundingBox().Min.Z)))<FMath::Square(SoftTriggerCm))
                {
                    if(Falling.Num()<MaxAnimated){Trigger(Group,Index,Position,Direction,false);++Started;}
                }
                continue;
            }
            auto* Collider=Group.Collider.Get();
            if(!Collider)continue;
            double ImpulseNs=0;
            const bool Hit=Rig->GetRecentHitAgainst(Collider,Closest,100.,.22,ImpulseNs);
            const bool Near=Range2D<FMath::Square(ContactProximityCm) &&
                FMath::Abs(Closest.Z-Position.Z)<120.;
            const float SpeedMps=static_cast<float>(ActualVelocity.Size2D()/100.);
            const float RequestedMps=static_cast<float>(Wanted.Size2D()/100.);
            const float HardThreshold=static_cast<float>(Group.Kind==EKind::MetalBend?MetalImpactMps:WoodImpactMps);
            const float HoldThreshold=static_cast<float>(Group.Kind==EKind::MetalBend?MetalPressureSeconds:WoodPressureSeconds);
            const bool TowardObject=FVector::DotProduct(Wanted.GetSafeNormal2D(),Toward)>.25;
            // Capture approach speed BEFORE the solver arrests the vehicle at
            // the obstacle. The instantaneous post-contact speed may be zero.
            float& Peak=Group.ApproachPeakMps.FindOrAdd(Index);
            if(Range2D<FMath::Square(170.f) && TowardObject && RequestedMps>.5f)
                Peak=FMath::Max(Peak,SpeedMps);
            else if(Range2D>FMath::Square(270.f))Peak=0;
            const bool FastHit=Hit && (SpeedMps>=HardThreshold || Peak>=HardThreshold ||
                ImpulseNs>=(Group.Kind==EKind::MetalBend?42.0:22.0));
            // Pushing against an actual source collider at low speed builds
            // damage; no idle/time-only destruction and no guessed world pose.
            const bool Persistent=Near && TowardObject && RequestedMps>=.65f &&
                SpeedMps<FMath::Max(.55f,RequestedMps*.6f) && (Hit || Range2D<FMath::Square(22.f));
            float& Damage=Group.PressureSeconds.FindOrAdd(Index);
            Damage=Persistent?Damage+Step:FMath::Max(0.f,Damage-Step*.65f);
            if(FastHit || (Near && Damage>=HoldThreshold))
            {
                if(Falling.Num()<MaxAnimated && Started<6)
                {
                    UE_LOG(LogTemp,Display,
                        TEXT("WT_DESTRUCTION_THRESHOLD mode=%s speed_mps=%.3f peak_approach_mps=%.3f wanted_mps=%.3f impulse_Ns=%.3f pressure_seconds=%.3f contact=%d"),
                        FastHit?TEXT("impact"):TEXT("pressure"),SpeedMps,Peak,RequestedMps,ImpulseNs,Damage,Hit?1:0);
                    Trigger(Group,Index,Position,Direction,Hit);
                    ++Started;
                }
            }
            if(Started>=8)return;
        }
    }
}
