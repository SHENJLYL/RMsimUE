#include "WTFoliageCrushComponent.h"
#include "WTBreakableGeometry.h"
#include "VehicleRig.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Misc/ConfigCacheIni.h"
#include "PhysicsEngine/BodyInstance.h"

namespace
{
constexpr double ScanInterval=.06;
constexpr double ContactGap=.10;
constexpr int32 MaxDebris=32,MaxLeafPatches=20,MaxRemainders=512;

AActor* SpawnLeaves(UWorld* World,const FTransform& Pose,
    const TArray<FProcMeshSection>& Sections,const TArray<UMaterialInterface*>& Materials,
    UProceduralMeshComponent*& Out)
{
    Out=nullptr;
    if(!World)return nullptr;
    AActor* Actor=World->SpawnActor<AActor>();
    if(!Actor)return nullptr;
    auto* Mesh=NewObject<UProceduralMeshComponent>(Actor);
    Actor->SetRootComponent(Mesh);Actor->AddInstanceComponent(Mesh);
    Mesh->SetMobility(EComponentMobility::Movable);
    Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Mesh->SetGenerateOverlapEvents(false);Mesh->SetCanEverAffectNavigation(false);
    Mesh->RegisterComponent();Actor->SetActorTransform(Pose);
    for(int32 S=0;S<Sections.Num();++S)
    {
        FProcMeshSection Copy=Sections[S];Copy.bEnableCollision=false;
        Mesh->SetProcMeshSection(S,Copy);
        if(Materials.IsValidIndex(S))Mesh->SetMaterial(S,Materials[S]);
    }
    Out=Mesh;return Actor;
}

void AppendTriangle(FProcMeshSection& Section,const FProcMeshSection& Source,int32 Offset)
{
    for(int32 K=0;K<3;++K)
    {
        const FProcMeshVertex& V=Source.ProcVertexBuffer[Source.ProcIndexBuffer[Offset+K]];
        Section.ProcIndexBuffer.Add(Section.ProcVertexBuffer.Num());
        Section.ProcVertexBuffer.Add(V);Section.SectionLocalBox+=V.Position;
    }
    Section.bSectionVisible=true;Section.bEnableCollision=false;
}
// Expand a touched triangle to its small, welded leaf card within one material.
// Shared edges join the two sides of a card; touching a single branch vertex cannot
// propagate damage through a plant. Large connected surfaces stay anchored.
void CompleteLeafCards(const FProcMeshSection& Section,const FTransform& Original,TArray<bool>& Touch)
{
    const int32 Triangles=Section.ProcIndexBuffer.Num()/3;
    if(Triangles==0)return;
    TArray<int32> Parent,WeldedIndex;Parent.SetNumUninitialized(Triangles);
    WeldedIndex.SetNumUninitialized(Section.ProcVertexBuffer.Num());
    TMap<FIntVector,int32> Welded;TMap<uint64,int32> EdgeOwner;
    TArray<FVector> Normals;Normals.SetNumUninitialized(Triangles);
    for(int32 I=0;I<Triangles;++I)Parent[I]=I;
    for(int32 I=0;I<Section.ProcVertexBuffer.Num();++I)
    {
        const FVector P=Section.ProcVertexBuffer[I].Position*1000.;
        const FIntVector Key(FMath::RoundToInt(P.X),FMath::RoundToInt(P.Y),FMath::RoundToInt(P.Z));
        if(const int32* Found=Welded.Find(Key))WeldedIndex[I]=*Found;
        else {WeldedIndex[I]=I;Welded.Add(Key,I);}
    }
    auto Root=[&](int32 I)
    {
        int32 R=I;while(Parent[R]!=R)R=Parent[R];
        while(Parent[I]!=I){const int32 Next=Parent[I];Parent[I]=R;I=Next;}
        return R;
    };
    for(int32 T=0;T<Triangles;++T)
    {
        const uint32 A=Section.ProcIndexBuffer[3*T],B=Section.ProcIndexBuffer[3*T+1],C=Section.ProcIndexBuffer[3*T+2];
        Normals[T]=FVector::CrossProduct(Section.ProcVertexBuffer[B].Position-Section.ProcVertexBuffer[A].Position,
            Section.ProcVertexBuffer[C].Position-Section.ProcVertexBuffer[A].Position).GetSafeNormal();
        for(int32 K=0;K<3;++K)
        {
            const int32 V0=WeldedIndex[Section.ProcIndexBuffer[3*T+K]],V1=WeldedIndex[Section.ProcIndexBuffer[3*T+(K+1)%3]];
            const uint64 Edge=(uint64(FMath::Min(V0,V1))<<32)|uint32(FMath::Max(V0,V1));
            if(const int32* Other=EdgeOwner.Find(Edge))
            {
                if(FMath::Abs(FVector::DotProduct(Normals[T],Normals[*Other]))>.90)
                    Parent[Root(T)]=Root(*Other);
            }
            else EdgeOwner.Add(Edge,T);
        }
    }
    TArray<int32> Sizes;Sizes.Init(0,Triangles);
    TArray<bool> Any;Any.Init(false,Triangles);
    TArray<FBox> Bounds;Bounds.Init(FBox(ForceInit),Triangles);
    for(int32 T=0;T<Triangles;++T)
    {
        const int32 R=Root(T);++Sizes[R];Any[R]=Any[R]||Touch[T];
        for(int32 K=0;K<3;++K)Bounds[R]+=Original.TransformPosition(
            Section.ProcVertexBuffer[Section.ProcIndexBuffer[3*T+K]].Position);
    }
    for(int32 T=0;T<Triangles;++T)
    {
        const int32 R=Root(T);
        Touch[T]=Any[R]&&Sizes[R]<=8&&Bounds[R].GetSize().GetMax()<=120.;
    }
}

double DistanceToSweep2D(const FVector& Point,const FVector& From,const FVector& To)
{
    FVector D=To-From;D.Z=0;
    const double T=D.SizeSquared()>1.e-6?FMath::Clamp(FVector::DotProduct(Point-From,D)/D.SizeSquared(),0.,1.):0.;
    return FVector::Dist2D(Point,From+D*T);
}
bool HasTriangles(const TArray<FProcMeshSection>& Sections)
{
    for(const auto& S:Sections)if(S.ProcIndexBuffer.Num()>0)return true;
    return false;
}
}

bool UWTFoliageCrushComponent::IsSoftShrub(const FString& Name)
{
    // Exact visual-only plant parts. Flower-bed bases and real tree trunks are excluded.
    static const TSet<FString> Names={
        TEXT("prop_bush_flowers_a"),TEXT("prop_bush_flowers_b"),TEXT("prop_bush_flowers_c"),
        TEXT("prop_tree_poplar_bush_medium_a"),TEXT("prop_tree_poplar_bush_medium_b"),
        TEXT("prop_tree_tilia_bush_a"),TEXT("prop_tree_tilia_bush_b"),
        TEXT("prop_flower_bed_b_flowers_a"),TEXT("prop_flower_bed_c_flowers_a")};
    return Names.Contains(Name);
}

bool UWTFoliageCrushComponent::ClassifyHard(const FString& Name,EKind& Kind)
{
    // The original h panel has 8-11 cm timber rails; preserve it for this 20 kg vehicle.
    // Partial-rail geometry support is reserved for a separately authored weak asset.
    if(Name==TEXT("prop_wood_fence_h_plank")){Kind=EKind::WoodBoard;return true;}
    return false; // Masonry balustrades, metal, gates, posts and timber assemblies stay fixed.
}

void UWTFoliageCrushComponent::Initialize(UWorld* World)
{
    Groups.Reset();Leaves.Reset();Debris.Reset();Remainders.Reset();
    TotalCrushed=0;TotalBroken=0;ScanAccumulator=0;ContactCursor=0;MotionGeneration=MAX_uint64;
    if(!World || !World->GetOutermost()->GetName().Contains(TEXT("wt_vlaanderen")))return;
    auto Read=[&](const TCHAR* Key,double& Value,double Low,double High)
    {
        GConfig->GetDouble(TEXT("RMSim.Destruction"),Key,Value,GGameIni);
        return FMath::IsFinite(Value)&&Value>=Low&&Value<=High;
    };
    if(!Read(TEXT("RailImpactMps"),RailImpactMps,1.,10.) ||
       !Read(TEXT("BoardImpactMps"),BoardImpactMps,1.,10.) ||
       !Read(TEXT("RailImpulseNs"),RailImpulseNs,1.,100.) ||
       !Read(TEXT("BoardImpulseNs"),BoardImpulseNs,1.,100.) ||
       !Read(TEXT("RailPushNewtons"),RailPushNewtons,20.,500.) ||
       !Read(TEXT("BoardPushNewtons"),BoardPushNewtons,20.,500.) ||
       !Read(TEXT("RailPushSeconds"),RailPushSeconds,.5,10.) ||
       !Read(TEXT("BoardPushSeconds"),BoardPushSeconds,.5,10.))
    {UE_LOG(LogTemp,Error,TEXT("WT_DESTRUCTION_INVALID_CONFIG"));return;}
    int32 Soft=0,Hard=0,Excluded=0;
    for(TActorIterator<AActor> It(World);It;++It)
    {
        TArray<UHierarchicalInstancedStaticMeshComponent*> Components;
        It->GetComponents<UHierarchicalInstancedStaticMeshComponent>(Components);
        for(auto* Visual:Components)
        {
            if(!Visual || !Visual->GetStaticMesh() ||
               Visual->GetCollisionEnabled()!=ECollisionEnabled::NoCollision)continue;
            const FString Name=Visual->GetStaticMesh()->GetName();
            if(Name.StartsWith(TEXT("col_")))continue;
            EKind Kind=EKind::Soft;
            const bool Plant=IsSoftShrub(Name);
            if(!Plant && !ClassifyHard(Name,Kind))
            {
                if(Name.StartsWith(TEXT("prop_wood_")) || Name.StartsWith(TEXT("prop_sicily_villa_railing_")) ||
                   Name.StartsWith(TEXT("prop_sicily_fence_cafe_")) ||
                   Name.StartsWith(TEXT("prop_sicily_gardens_wood_planks_")))
                    Excluded+=Visual->GetInstanceCount();
                continue;
            }
            UHierarchicalInstancedStaticMeshComponent* Collider=nullptr;
            for(auto* Other:Components)
                if(Other && Other!=Visual && Other->GetStaticMesh() &&
                   Other->GetStaticMesh()->GetName()==TEXT("col_")+Name &&
                   Other->GetInstanceCount()==Visual->GetInstanceCount())
                {Collider=Other;break;}
            if(!Plant && !Collider)continue;
            FGroup Group;Group.Visual=Visual;Group.Collider=Collider;Group.Kind=Kind;
            Groups.Add(MoveTemp(Group));
            if(Plant)
            {
                Soft+=Visual->GetInstanceCount();
                if(Collider)Collider->SetCollisionEnabled(ECollisionEnabled::NoCollision);
            }
            else Hard+=Visual->GetInstanceCount();
        }
    }
    UE_LOG(LogTemp,Display,TEXT("WT_DESTRUCTION_READY policy=local_contact_v2 groups=%d soft_nonblocking=%d local_wood_candidates=%d protected_wood_and_structures=%d"),
        Groups.Num(),Soft,Hard,Excluded);
}

bool UWTFoliageCrushComponent::EnsureGeometry(FGroup& Group)
{
    if(Group.bGeometryTried)return Group.Template.Num()>0;
    Group.bGeometryTried=true;
    auto* Visual=Group.Visual.Get();
    if(!Visual || !WTBreakableGeometry::CopySections(Visual->GetStaticMesh(),Group.Template,Group.Materials))
    {
        UE_LOG(LogTemp,Warning,TEXT("WT_DESTRUCTION_GEOMETRY_UNAVAILABLE mesh=%s; retaining original geometry"),
            Visual&&Visual->GetStaticMesh()?*Visual->GetStaticMesh()->GetName():TEXT("invalid"));
        return false;
    }
    return true;
}

bool UWTFoliageCrushComponent::CrushPatch(FGroup& Group,int32 Index,
    const FVector& From,const FVector& To,const FBox& VehicleBounds,const FVector& Direction)
{
    auto* Visual=Group.Visual.Get();
    if(!Visual || !EnsureGeometry(Group) || Leaves.Num()>=MaxLeafPatches ||
       Remainders.Num()>=MaxRemainders || Direction.IsNearlyZero() || !VehicleBounds.IsValid)return false;
    FTransform Original;
    const FSoftRemainder* Existing=Group.SoftRemainders.Find(Index);
    if(Existing)Original=Existing->Original;
    else if(!Visual->GetInstanceTransform(Index,Original,true))return false;
    const auto& Input=Existing?Existing->Sections:Group.Template;
    TArray<FProcMeshSection> Keep,Fall;Keep.SetNum(Input.Num());Fall.SetNum(Input.Num());
    const FString Name=Visual->GetStaticMesh()->GetName();
    const bool Woody=Name.StartsWith(TEXT("prop_tree_poplar_")) || Name.StartsWith(TEXT("prop_bush_flowers_"));
    const FVector CenterOffset=VehicleBounds.GetCenter()-To;
    const FVector Samples[3]={From+CenterOffset,(From+To)*.5+CenterOffset,To+CenterOffset};
    int32 Touched=0,Total=0;
    for(int32 S=0;S<Input.Num();++S)
    {
        const auto& Section=Input[S];
        TArray<bool> Touches;Touches.Init(false,Section.ProcIndexBuffer.Num()/3);
        for(int32 I=0;I+2<Section.ProcIndexBuffer.Num();I+=3)
        {
            ++Total;
            bool Touch=false;
            // Verified woody slot zero stays anchored and visible.
            if(!(Woody && S==0))
            {
                const FVector A=Original.TransformPosition(Section.ProcVertexBuffer[Section.ProcIndexBuffer[I]].Position);
                const FVector B=Original.TransformPosition(Section.ProcVertexBuffer[Section.ProcIndexBuffer[I+1]].Position);
                const FVector C=Original.TransformPosition(Section.ProcVertexBuffer[Section.ProcIndexBuffer[I+2]].Position);
                const double MinZ=FMath::Min3(A.Z,B.Z,C.Z),MaxZ=FMath::Max3(A.Z,B.Z,C.Z);
                if(MinZ<=VehicleBounds.Max.Z+5. && MaxZ>=VehicleBounds.Min.Z-3.)
                    for(const FVector& P:Samples)
                        if(FVector::DistSquared(P,FMath::ClosestPointOnTriangleToPoint(P,A,B,C))<FMath::Square(30.))
                        {Touch=true;break;}
            }
            Touches[I/3]=Touch;
        }
        if(!(Woody && S==0))CompleteLeafCards(Section,Original,Touches);
        for(int32 I=0;I+2<Section.ProcIndexBuffer.Num();I+=3)
        {
            AppendTriangle(Touches[I/3]?Fall[S]:Keep[S],Section,I);
            Touched+=Touches[I/3]?1:0;
        }
    }
    if(Touched==0)return false;
    UProceduralMeshComponent *KeepMesh=nullptr,*FallMesh=nullptr;
    AActor* KeepActor=SpawnLeaves(GetWorld(),Original,Keep,Group.Materials,KeepMesh);
    AActor* FallActor=SpawnLeaves(GetWorld(),Original,Fall,Group.Materials,FallMesh);
    if(!KeepActor || !FallActor)
    {if(KeepActor)KeepActor->Destroy();if(FallActor)FallActor->Destroy();return false;}
    if(Existing && Existing->Actor.IsValid())Existing->Actor->Destroy();
    else
    {
        FTransform Hidden=Original;Hidden.SetScale3D(FVector::ZeroVector);
        Visual->UpdateInstanceTransform(Index,Hidden,true,true,true);
    }
    FSoftRemainder Remaining;Remaining.Actor=KeepActor;Remaining.Original=Original;Remaining.Sections=MoveTemp(Keep);
    Group.SoftRemainders.Add(Index,MoveTemp(Remaining));Remainders.Add(KeepActor);
    FLeafFall State;State.Actor=FallActor;State.Mesh=FallMesh;State.Original=Original;State.Initial=MoveTemp(Fall);
    State.Direction=Direction.GetSafeNormal2D();State.SweepFrom=From;State.SweepTo=To;
    State.GroundZ=VehicleBounds.Min.Z;
    Leaves.Add(MoveTemp(State));++TotalCrushed;
    UE_LOG(LogTemp,Display,TEXT("WT_FOLIAGE_PATCH mesh=%s instance=%d touched_triangles=%d retained_triangles=%d dir=(%.3f,%.3f,%.3f)"),
        *Name,Index,Touched,Total-Touched,Direction.X,Direction.Y,Direction.Z);
    return true;
}

void UWTFoliageCrushComponent::AdvanceLeaves(double Seconds)
{
    for(int32 I=Leaves.Num()-1;I>=0;--I)
    {
        auto& State=Leaves[I];auto* Actor=State.Actor.Get();auto* Mesh=State.Mesh.Get();
        if(!Actor || !Mesh){Leaves.RemoveAtSwap(I);continue;}
        const double PreviousElapsed=State.Elapsed;
        State.Elapsed+=Seconds;
        if(State.Elapsed>=6.)
        {Actor->Destroy();Leaves.RemoveAtSwap(I);continue;}
        // Settle once at two seconds; the remaining lifetime only waits for disposal.
        if(PreviousElapsed>=2.)continue;
        // Damped bend with zero initial velocity; roots stay put, lower leaves settle toward the ground.
        const double T=FMath::Min(State.Elapsed,2.);
        const double Response=1.-FMath::Exp(-6.*T)*(1.+6.*T);
        const FVector Axis=FVector::CrossProduct(FVector::UpVector,State.Direction).GetSafeNormal();
        for(int32 S=0;S<State.Initial.Num();++S)
        {
            const auto& Section=State.Initial[S];
            if(Section.ProcVertexBuffer.IsEmpty())continue;
            TArray<FVector> Positions,Normals;TArray<FProcMeshTangent> Tangents;
            Positions.Reserve(Section.ProcVertexBuffer.Num());Normals.Reserve(Section.ProcVertexBuffer.Num());
            Tangents.Reserve(Section.ProcVertexBuffer.Num());
            for(const auto& V:Section.ProcVertexBuffer)
            {
                const FVector W=State.Original.TransformPosition(V.Position);
                const double Height=FMath::Max(0.,W.Z-State.GroundZ);
                const double Locality=FMath::Clamp(1.-DistanceToSweep2D(W,State.SweepFrom,State.SweepTo)/95.,0.,1.);
                const double Angle=1.25*Response*FMath::Sqrt(Locality)*FMath::Clamp(Height/35.,0.,1.);
                const FQuat Bend(Axis,Angle);
                FVector New=W+State.Direction*(Height*FMath::Sin(Angle));
                New.Z=W.Z-Height*(1.-FMath::Cos(Angle));
                Positions.Add(State.Original.InverseTransformPosition(New));
                const FVector N=State.Original.TransformVectorNoScale(V.Normal);
                Normals.Add(State.Original.InverseTransformVectorNoScale(Bend.RotateVector(N)).GetSafeNormal());
                const FVector Tangent=State.Original.TransformVectorNoScale(V.Tangent.TangentX);
                Tangents.Add(FProcMeshTangent(
                    State.Original.InverseTransformVectorNoScale(Bend.RotateVector(Tangent)).GetSafeNormal(),V.Tangent.bFlipTangentY));
            }
            // Empty attribute arrays preserve the section's original UV0-3 and vertex colors.
            const TArray<FVector2D> KeepUV;const TArray<FColor> KeepColors;
            Mesh->UpdateMeshSection(S,Positions,Normals,KeepUV,KeepUV,KeepUV,KeepUV,KeepColors,Tangents);
        }
    }
}

bool UWTFoliageCrushComponent::BreakLocal(FGroup& Group,int32 Index,const FVector& Point,
    const FVector& Direction,double ImpulseNs,double SpeedMps,const AVehicleRig* Rig)
{
    auto* Visual=Group.Visual.Get();auto* Collider=Group.Collider.Get();
    if(!Visual || !Collider || Group.Removed.Contains(Index) ||
       Debris.Num()+2>MaxDebris || Remainders.Num()>=MaxRemainders)return false;
    FTransform Original,CollisionPose;
    if(!Visual->GetInstanceTransform(Index,Original,true) ||
       !Collider->GetInstanceTransform(Index,CollisionPose,true) ||
       !Original.Equals(CollisionPose,.01))return false;
    FBodyInstance* Body=Collider->GetBodyInstance(NAME_None,false,Index);
    if(!Body || Body->GetCollisionEnabled()==ECollisionEnabled::NoCollision)return false;
    const bool Fence=Group.Kind==EKind::WoodRail;
    if(!WTBreakableGeometry::Create(GetWorld(),Visual->GetStaticMesh(),Original,Point,Direction,
        ImpulseNs,SpeedMps,Fence,const_cast<AVehicleRig*>(Rig),Debris,Remainders))return false;
    // Disable this exact physics body. Never alter its transform or neighboring body indices.
    Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    FTransform Hidden=Original;Hidden.SetScale3D(FVector::ZeroVector);
    Visual->UpdateInstanceTransform(Index,Hidden,true,true,true);
    Group.Removed.Add(Index);Group.Pressure.Remove(Index);++TotalBroken;
    UE_LOG(LogTemp,Display,TEXT("WT_DESTRUCTION_EVENT kind=%s mesh=%s instance=%d point=(%.2f,%.2f,%.2f) dir=(%.3f,%.3f,%.3f) impulse_Ns=%.3f normal_speed_mps=%.3f remaining_structure=solid fragments=source_geometry"),
        Fence?TEXT("local_rail"):TEXT("local_board"),*Visual->GetStaticMesh()->GetName(),Index,
        Point.X,Point.Y,Point.Z,Direction.X,Direction.Y,Direction.Z,ImpulseNs,SpeedMps);
    return true;
}

void UWTFoliageCrushComponent::Advance(const AVehicleRig* Rig,double Seconds)
{
    if(!Rig || !Rig->IsContactPhysics() || !FMath::IsFinite(Seconds) || Seconds<=0.)return;
    const double Dt=FMath::Min(Seconds,.1);
    AdvanceLeaves(Dt);
    Debris.RemoveAll([](const auto& A){return !A.IsValid();});
    Remainders.RemoveAll([](const auto& A){return !A.IsValid();});
    const double Now=GetWorld()->GetTimeSeconds();
    const FVector Position=Rig->GetActorLocation();
    if(MotionGeneration!=Rig->GetMotionGeneration())
    {
        MotionGeneration=Rig->GetMotionGeneration();PreviousVehicle=Position;
        for(auto& Group:Groups)Group.Pressure.Reset();
    }
    struct FHitBatch { FVector Point,Normal;double Impulse=0,StrongestImpulse=0,Speed=0,Time=0; };
    TMap<uint64,FHitBatch> Hits;
    TArray<FVehicleObstacleContact> Events;
    Rig->GetObstacleContactsSince(ContactCursor,Events);
    for(const auto& E:Events)
    {
        if(E.MotionGeneration!=MotionGeneration || Now-E.TimeSeconds>ContactGap ||
           E.InstanceIndex<0 || E.ImpulseNs<=0. || FMath::Abs(E.IntoObstacleNormal.Z)>.45)continue;
        for(int32 G=0;G<Groups.Num();++G)
        {
            auto& Group=Groups[G];
            if(Group.Kind==EKind::Soft || Group.Collider.Get()!=E.OtherComponent.Get() ||
               Group.Removed.Contains(E.InstanceIndex))continue;
            auto* Visual=Group.Visual.Get();FTransform Pose;
            if(!Visual || !Visual->GetInstanceTransform(E.InstanceIndex,Pose,true))continue;
            const uint64 Key=(uint64(G)<<32)|uint32(E.InstanceIndex);
            auto& Batch=Hits.FindOrAdd(Key);
            const double Speed=FMath::Max(0.,FVector::DotProduct(E.PreImpactVelocityCmS,E.IntoObstacleNormal)/100.);
            if(Batch.Impulse>0. && FVector::DotProduct(Batch.Normal,E.IntoObstacleNormal)<.7)continue;
            if(E.ImpulseNs>Batch.StrongestImpulse)
            {Batch.Point=E.PointCm;Batch.Normal=E.IntoObstacleNormal;Batch.StrongestImpulse=E.ImpulseNs;}
            Batch.Impulse+=E.ImpulseNs;Batch.Speed=FMath::Max(Batch.Speed,Speed);
            Batch.Time=FMath::Max(Batch.Time,E.TimeSeconds);
            break;
        }
    }
    const FVector Wanted=Rig->GetRequestedDriveWorldVelocityCmS()/100.;
    const double VehicleSpeedCmS=Rig->GetCommandOriginVelocity().Size2D();
    // Cancel intent every frame, including frames without new collision callbacks.
    for(auto& Group:Groups)
        for(auto It=Group.Pressure.CreateIterator();It;++It)
            if(VehicleSpeedCmS>=60. || FVector::DotProduct(Wanted,It.Value().LastNormal)<.5)
                It.RemoveCurrent();
    for(const auto& Pair:Hits)
    {
        auto& Group=Groups[int32(Pair.Key>>32)];const int32 Index=int32(uint32(Pair.Key));
        const auto& H=Pair.Value;const bool Rail=Group.Kind==EKind::WoodRail;
        FTransform Pose;auto* Visual=Group.Visual.Get();
        if(!Visual || !Visual->GetInstanceTransform(Index,Pose,true) ||
           !WTBreakableGeometry::CanBreakAt(Visual->GetStaticMesh(),Pose,H.Point,Rail))continue;
        const double SpeedThreshold=Rail?RailImpactMps:BoardImpactMps;
        const double ImpulseThreshold=Rail?RailImpulseNs:BoardImpulseNs;
        const bool Impact=H.Speed>=SpeedThreshold && H.Impulse>=ImpulseThreshold;
        auto& Pressure=Group.Pressure.FindOrAdd(Index);
        const double Interval=Pressure.LastContact>=0.?H.Time-Pressure.LastContact:-1.;
        const bool SameFace=Pressure.LastContact>=0. &&
            FVector::DotProduct(Pressure.LastNormal,H.Normal)>=.7;
        const bool Duplicate=Pressure.LastContact>=0. && FMath::Abs(Interval)<=1.e-6;
        const bool OutOfOrder=Pressure.LastContact>=0. && Interval<-1.e-6;
        const bool Continuous=SameFace && Interval>1.e-6 && Interval<=ContactGap+1.e-6;
        const bool Intent=FVector::DotProduct(Wanted,H.Normal)>=.5 && VehicleSpeedCmS<60.;
        // Collision impulse belongs to the interval between contact events, not this game tick.
        const double Force=Continuous?H.Impulse/Interval:0.;
        const bool Pushing=Intent && Continuous && Force>=(Rail?RailPushNewtons:BoardPushNewtons);
        if(!Intent)
        {
            Pressure.Seconds=0.;Pressure.ImpulseNs=0.;
            Pressure.LastContact=-1.;Pressure.LastNormal=FVector::ZeroVector;
        }
        // Duplicate or older samples cannot add time or move the time baseline backwards.
        else if(!OutOfOrder && (!Duplicate || !SameFace))
        {
            if(Pushing){Pressure.Seconds+=Interval;Pressure.ImpulseNs+=H.Impulse;}
            else {Pressure.Seconds=0.;Pressure.ImpulseNs=0.;}
            // First contact, a gap or a new face establishes a baseline without adding pressure time.
            Pressure.LastContact=H.Time;Pressure.LastNormal=H.Normal;
        }
        const double Hold=Rail?RailPushSeconds:BoardPushSeconds;
        const bool Sustained=Pushing && Pressure.Seconds>=Hold;
        if(Impact || Sustained)
        {
            const double PressureTime=Pressure.Seconds;
            // Only this frame's impulse powers debris. Accumulated fatigue is never launch momentum.
            if(BreakLocal(Group,Index,H.Point,H.Normal,H.Impulse,H.Speed,Rig))
                UE_LOG(LogTemp,Display,TEXT("WT_DESTRUCTION_THRESHOLD mode=%s instance=%d normal_speed_mps=%.3f impulse_Ns=%.3f force_N=%.2f continuous_seconds=%.3f"),
                    Impact?TEXT("impact"):TEXT("pressure"),Index,H.Speed,H.Impulse,Force,PressureTime);
        }
    }
    // Consume queued contacts before expiring their previous pressure state.
    for(auto& Group:Groups)
        for(auto It=Group.Pressure.CreateIterator();It;++It)
            if(It.Value().LastContact<0. || Now-It.Value().LastContact>ContactGap+1.e-6)
                It.RemoveCurrent();

    ScanAccumulator+=Dt;
    if(ScanAccumulator<ScanInterval)return;
    ScanAccumulator=0;
    const FVector Motion=Position-PreviousVehicle;
    const double Travel=Motion.Size2D();
    const FVector Velocity=Rig->GetCommandOriginVelocity();
    if(Travel>.5 && Travel<100. && Velocity.Size2D()>15. && FVector::DotProduct(Motion,Velocity)>0.)
    {
        FBox VehicleBounds=Rig->GetFoliageContactBounds();
        if(!VehicleBounds.IsValid){PreviousVehicle=Position;return;}
        FHitResult Ground;
        FCollisionQueryParams GroundQuery(SCENE_QUERY_STAT(WTFoliageGround),false);
        GroundQuery.AddIgnoredActor(Rig);
        FVector GroundStart=VehicleBounds.GetCenter();GroundStart.Z=VehicleBounds.Min.Z+12.;
        if(GetWorld()->LineTraceSingleByObjectType(Ground,GroundStart,GroundStart-FVector(0,0,120.),
            FCollisionObjectQueryParams(ECC_WorldStatic),GroundQuery) && Ground.ImpactNormal.Z>.5)
            VehicleBounds.Min.Z=Ground.ImpactPoint.Z;
        for(auto& Group:Groups)
        {
            if(Group.Kind!=EKind::Soft)continue;
            auto* Visual=Group.Visual.Get();if(!Visual)continue;
            TArray<int32> Candidates=Visual->GetInstancesOverlappingSphere(Position,90.f,true);
            for(const auto& Existing:Group.SoftRemainders)
                if(Existing.Value.Actor.IsValid() &&
                   Existing.Value.Actor->GetComponentsBoundingBox().ComputeSquaredDistanceToPoint(Position)<FMath::Square(90.))
                    Candidates.AddUnique(Existing.Key);
            for(int32 Index:Candidates)
                CrushPatch(Group,Index,PreviousVehicle,Position,VehicleBounds,Motion.GetSafeNormal2D());
        }
    }
    PreviousVehicle=Position;
}

int32 UWTFoliageCrushComponent::ActiveAnimations() const
{
    int32 Count=Leaves.Num();for(const auto& Actor:Debris)if(Actor.IsValid())++Count;return Count;
}

bool UWTFoliageCrushComponent::ProbeOneOfEach(TArray<FString>& Outcomes)
{
    Outcomes.Reset();EKind Kind;
    for(const TCHAR* Name:{TEXT("prop_sicily_villa_railing_a"),TEXT("prop_sicily_fence_cafe_a"),
        TEXT("prop_sicily_gardens_wood_planks_a"),TEXT("prop_wood_debris_f"),
        TEXT("prop_wood_fence_m_pole"),TEXT("prop_wood_gates_b"),TEXT("prop_russia_rails_sleeper_a"),
        TEXT("prop_wood_fence_h_unknown"),TEXT("prop_wood_fence_h")})
        if(ClassifyHard(Name,Kind))return false;
    Outcomes.Add(TEXT("protected_materials_and_structures:9"));
    bool SeenRail=false,SeenBoard=false;
    for(auto& Group:Groups)
    {
        if(Group.Kind==EKind::Soft)continue;
        bool& Seen=Group.Kind==EKind::WoodRail?SeenRail:SeenBoard;
        if(Seen)continue;
        auto* Visual=Group.Visual.Get();auto* Collider=Group.Collider.Get();FTransform Original;
        if(!Visual || !Collider || !Visual->GetInstanceTransform(0,Original,true))return false;
        const FBox B=Visual->GetStaticMesh()->GetBoundingBox();
        FVector Local=B.GetCenter();
        Local.Z=B.Min.Z+B.GetSize().Z*(Group.Kind==EKind::WoodRail?.48:.40);
        const int32 PairAxis=B.GetSize().X>B.GetSize().Y?0:1;
        if(Group.Kind==EKind::WoodBoard)Local[PairAxis]=B.Max[PairAxis]-1.;
        const FVector Point=Original.TransformPosition(Local);
        FVector LocalNormal=FVector::ZeroVector;LocalNormal[PairAxis]=1.;
        const FVector Dir=Original.TransformVectorNoScale(LocalNormal).GetSafeNormal2D();
        FTransform NeighborBefore,NeighborAfter;
        const bool Neighbor=Visual->GetInstanceCount()>1;
        if(Neighbor)Visual->GetInstanceTransform(1,NeighborBefore,true);
        if(!BreakLocal(Group,0,Point,Dir,12.,4.,nullptr))return false;
        if(Neighbor && (!Visual->GetInstanceTransform(1,NeighborAfter,true) || !NeighborBefore.Equals(NeighborAfter)))return false;
        auto* Body=Collider->GetBodyInstance(NAME_None,false,0);
        if(!Body || Body->GetCollisionEnabled()!=ECollisionEnabled::NoCollision)return false;
        if(Neighbor)
        {
            auto* OtherBody=Collider->GetBodyInstance(NAME_None,false,1);
            if(!OtherBody || OtherBody->GetCollisionEnabled()==ECollisionEnabled::NoCollision)return false;
        }
        Outcomes.Add(Group.Kind==EKind::WoodRail?TEXT("rail_local_fracture_neighbor_preserved"):TEXT("board_local_fracture_neighbor_preserved"));
        Seen=true;
    }
    return !SeenRail && SeenBoard;
}

void UWTFoliageCrushComponent::EndPlay(const EEndPlayReason::Type Reason)
{
    for(auto& State:Leaves)if(State.Actor.IsValid())State.Actor->Destroy();
    for(auto& Actor:Debris)if(Actor.IsValid())Actor->Destroy();
    for(auto& Actor:Remainders)if(Actor.IsValid())Actor->Destroy();
    Leaves.Reset();Debris.Reset();Remainders.Reset();Groups.Reset();
    Super::EndPlay(Reason);
}
