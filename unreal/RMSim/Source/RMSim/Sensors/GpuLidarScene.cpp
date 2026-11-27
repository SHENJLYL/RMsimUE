#include "GpuLidarScene.h"

#include "../VehicleRig.h"
#include "Algo/Sort.h"
#include "Chaos/Convex.h"
#include "Chaos/TriangleMeshImplicitObject.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/PrimitiveComponent.h"
#include "EngineUtils.h"
#include "GlobalShader.h"
#include "Materials/MaterialInterface.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "PhysicsEngine/BodySetup.h"
#include "RenderGraphBuilder.h"
#include "RenderGraphUtils.h"
#include "RHIGPUReadback.h"
#include "ShaderParameterStruct.h"

namespace
{
constexpr uint32 CullBack = 1u;
constexpr uint32 PlaneShape = 2u;
constexpr uint32 DarkWindow = 4u;
constexpr uint32 BlueWindow = 8u;
constexpr uint32 RingCapacity = 4;
constexpr int32 BinCount = 12;

struct FMeshKey
{
    const UBodySetup* Setup=nullptr;
    TArray<const UMaterialInterface*> Materials;
    bool operator==(const FMeshKey& Other) const { return Setup==Other.Setup&&Materials==Other.Materials; }
    friend uint32 GetTypeHash(const FMeshKey& K)
    {
        uint32 Hash=PointerHash(K.Setup);
        for(const auto* M:K.Materials)Hash=HashCombineFast(Hash,PointerHash(M));
        return Hash;
    }
};

struct FReference
{
    FBox3f Bounds;
    FVector3f Centre;
    uint32 Index = 0;
};

FBox3f Union(TConstArrayView<FReference> Refs, bool bCentres)
{
    FBox3f Box(ForceInit);
    for (const FReference& Ref : Refs)
    {
        if (bCentres) Box += Ref.Centre;
        else Box += Ref.Bounds;
    }
    return Box;
}

float Area(const FBox3f& Box)
{
    const FVector3f S = Box.GetSize();
    return S.X*S.Y + S.X*S.Z + S.Y*S.Z;
}

int32 Split(TArrayView<FReference> Refs, const FBox3f& Centres, bool bSah)
{
    const FVector3f Size = Centres.GetSize();
    int32 Axis = Size.X >= Size.Y ? 0 : 1;
    if (Size.Z > Size[Axis]) Axis = 2;
    if (Size[Axis] <= UE_SMALL_NUMBER) return Refs.Num()/2;
    int32 SplitBin = INDEX_NONE;
    float BestCost = TNumericLimits<float>::Max();
    if (bSah)
    {
        struct FBin { FBox3f Box{ForceInit}; int32 Count = 0; };
        for (int32 Candidate=0; Candidate<3; ++Candidate)
        {
            if (Size[Candidate] <= UE_SMALL_NUMBER) continue;
            FBin Bins[BinCount];
            const float Scale = BinCount/Size[Candidate];
            for (const FReference& Ref : Refs)
            {
                const int32 B = FMath::Clamp(int32((Ref.Centre[Candidate]-Centres.Min[Candidate])*Scale),0,BinCount-1);
                Bins[B].Box += Ref.Bounds;
                ++Bins[B].Count;
            }
            FBox3f Left(ForceInit), Right(ForceInit);
            float RightCost[BinCount]{};
            int32 RightCount=0,LeftCount=0;
            for(int32 B=BinCount-1;B>0;--B)
            {
                Right += Bins[B].Box; RightCount += Bins[B].Count;
                RightCost[B-1] = RightCount ? RightCount*Area(Right) : 0.f;
            }
            for(int32 B=0;B<BinCount-1;++B)
            {
                Left += Bins[B].Box; LeftCount += Bins[B].Count;
                if (!LeftCount || LeftCount==Refs.Num()) continue;
                const float Cost = LeftCount*Area(Left)+RightCost[B];
                if (Cost<BestCost){BestCost=Cost;Axis=Candidate;SplitBin=B;}
            }
        }
    }
    if(SplitBin!=INDEX_NONE)
    {
        const float Plane=Centres.Min[Axis]+Size[Axis]*float(SplitBin+1)/BinCount;
        int32 Left=0;
        for(int32 Right=0;Right<Refs.Num();++Right)
            if(Refs[Right].Centre[Axis]<Plane) Swap(Refs[Left++],Refs[Right]);
        if(Left>0&&Left<Refs.Num())return Left;
    }
    Algo::Sort(Refs,[Axis](const FReference& A,const FReference& B){return A.Centre[Axis]<B.Centre[Axis];});
    return Refs.Num()/2;
}

template<typename AddLeaf>
void BuildTree(TArray<FGpuLidarNode>& Nodes, TArrayView<FReference> Refs, int32 LeafSize, int32 Depth, const AddLeaf& Add)
{
    const int32 Here=Nodes.AddDefaulted();
    const FBox3f Box=Union(Refs,false);
    Nodes[Here].Min=Box.Min-FVector3f(.001f);
    Nodes[Here].Max=Box.Max+FVector3f(.001f);
    if(Refs.Num()<=LeafSize)
    {
        // All leaves contain 1..4 entries. The other 29 bits address the primitive or instance.
        Nodes[Here].FirstAndCount=(Add(Refs)<<3)|uint32(Refs.Num());
    }
    else
    {
        // Median partitions bound build depth even for heavily overlapping primitives.
        const int32 At=Split(Refs,Union(Refs,true),LeafSize>1&&Depth<24);
        BuildTree(Nodes,Refs.Left(At),LeafSize,Depth+1,Add);
        BuildTree(Nodes,Refs.RightChop(At),LeafSize,Depth+1,Add);
    }
    Nodes[Here].Escape=Nodes.Num();
}

bool BlocksLidar(const UPrimitiveComponent& Component)
{
    return Component.IsRegistered() && Component.IsQueryCollisionEnabled() && Component.HasValidPhysicsState() &&
        Component.GetCollisionResponseToChannel(ECC_GameTraceChannel1)==ECR_Block;
}

FVector3f ReadVertex(const Chaos::FTriangleMeshImplicitObject& Mesh,int32 Index)
{
    return FVector3f(Mesh.Particles().GetX(Index));
}

class FMid360TraceCS final : public FGlobalShader
{
    DECLARE_GLOBAL_SHADER(FMid360TraceCS);
    SHADER_USE_PARAMETER_STRUCT(FMid360TraceCS,FGlobalShader);
public:
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        SHADER_PARAMETER(uint32, RayCount)
        SHADER_PARAMETER(uint32, ArenaEnd)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<LidarNode>, GeometryNodes)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<LidarPrimitive>, Primitives)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<float4>, Planes)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<LidarNode>, InstanceNodes)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<LidarInstance>, Instances)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<uint2>, Snapshots)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<LidarRay>, Rays)
        SHADER_PARAMETER_RDG_BUFFER_UAV(RWStructuredBuffer<LidarHit>, Hits)
    END_SHADER_PARAMETER_STRUCT()

    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& Params)
    {
        return IsFeatureLevelSupported(Params.Platform, ERHIFeatureLevel::SM5);
    }
};
IMPLEMENT_GLOBAL_SHADER(FMid360TraceCS,"/RMSimShaders/Private/Mid360TraceCS.usf","MainCS",SF_Compute);
}

struct FGpuLidarScene::FGeometry
{
    TArray<FGpuLidarNode> Nodes;
    TArray<FGpuLidarPrimitive> Primitives;
    TArray<FVector4f> Planes;
    uint32 ArenaEnd=0;
};

struct FGpuLidarScene::FDynamic
{
    TWeakObjectPtr<UPrimitiveComponent> Component;
    int32 BodyIndex=INDEX_NONE;
    uint32 Begin=0,End=0,Vehicle=0;
    FBox Bounds;
    FTransform FromBody=FTransform::Identity;
};

struct FGpuLidarScene::FFrame
{
    TArray<FGpuLidarNode> InstanceNodes;
    TArray<FGpuLidarInstance> Instances;
    TArray<FTransform> Poses;
    TArray<FUintVector2> Snapshots;
    TArray<FGpuLidarRay> Rays;
    struct FWork { int32 Count; FResults Complete; };
    TArray<FWork> Work;
    TArray<FGpuLidarHit> Hits;
    TAtomic<bool> Ready{false};
};

struct FGpuLidarRenderState
{
    struct FSlot
    {
        TUniquePtr<FRHIGPUBufferReadback> Readback;
        TUniqueFunction<void(FRHIGPUBufferReadback&)> Ready;
        FRenderQueryRHIRef Begin, End;
        int32 Count=0;
    };
    FSlot Slots[RingCapacity];
    TRefCountPtr<FRDGPooledBuffer> Nodes,Primitives,Planes;
    uint32 ArenaEnd=0;
    bool bClosing=false;
    bool bProfileGpuTiming=false;
    int32 TimingCount=0;
    double TimingSumUs=0.,TimingMaxUs=0.;

    void Poll()
    {
        for(auto& Slot:Slots)
        {
            if(!Slot.Ready||!Slot.Readback->IsReady())continue;
            if(bProfileGpuTiming)
            {
                uint64 Begin=0,End=0;
                if(Slot.Begin&&Slot.End&&RHIGetRenderQueryResult(Slot.Begin.GetReference(),Begin,false)&&
                    RHIGetRenderQueryResult(Slot.End.GetReference(),End,false)&&End>=Begin)
                {
                    TimingSumUs+=double(End-Begin);TimingMaxUs=FMath::Max(TimingMaxUs,double(End-Begin));
                    if(++TimingCount==240)
                    {
                        UE_LOG(LogTemp,Display,TEXT("RMSIM_GPU_LIDAR_DISPATCH samples=%d gpu_mean_ms=%.6f gpu_max_ms=%.6f"),
                            TimingCount,TimingSumUs/(1000.*TimingCount),TimingMaxUs/1000.);
                        TimingCount=0;TimingSumUs=TimingMaxUs=0.;
                    }
                }
            }
            Slot.Ready(*Slot.Readback);
            Slot.Ready.Reset();Slot.Count=0;
        }
    }
};

FGpuLidarScene::FGpuLidarScene()
    : Geometry(MakeUnique<FGeometry>()), RenderState(MakeShared<FGpuLidarRenderState,ESPMode::ThreadSafe>()),
      Building(MakeUnique<FFrame>())
{
    RenderState->bProfileGpuTiming=FParse::Param(FCommandLine::Get(),TEXT("ProfileSensorTiming"));
}

FGpuLidarScene::~FGpuLidarScene()
{
    UE_LOG(LogTemp,Display,TEXT("RMSIM_GPU_LIDAR_ENDPLAY queued=%d"),InFlight.Num());
    const auto State=MoveTemp(RenderState);
    ENQUEUE_RENDER_COMMAND(RMSimGpuLidarShutdown)([State](FRHICommandListImmediate&)
    {
        State->bClosing=true;
        for(auto& Slot:State->Slots)Slot.Ready.Reset();
    });
}

uint32 FGpuLidarScene::VehicleId(const AVehicleRig* Rig) const
{
    return VehicleIds.FindRef(Rig);
}

bool FGpuLidarScene::Initialize(UWorld& World, const TMap<const USceneComponent*, int32>& NativeBodyIndices, FString& Error)
{
    if (!GDynamicRHI||GMaxRHIFeatureLevel<ERHIFeatureLevel::SM5)
    {
        Error=TEXT("GPU LiDAR requires a compute-enabled RHI");
        return false;
    }
    struct FSource
    {
        TArray<FGpuLidarPrimitive> Primitives;
        TArray<FReference> Refs;
        TArray<FPlane> ExactPlanes;

        void Add(const FGpuLidarPrimitive& Primitive,const FBox3f& Bounds,const FPlane& Plane)
        {
            Refs.Add({Bounds,Bounds.GetCenter(),uint32(Primitives.Add(Primitive))});
            ExactPlanes.Add(Plane);
        }
    };
    struct FMesh {uint32 Begin=0,End=0; FBox Bounds{ForceInit};};
    TMap<FMeshKey,FMesh> Cache;
    TMap<FName,uint32> MaterialIds;
    TArray<UPrimitiveComponent*> DynamicComponents;
    FSource Arena;
    FSource Local;

    auto Bake=[&](UPrimitiveComponent& Component, const FTransform& Transform, FSource& Out)->bool
    {
        UBodySetup* Body=Component.GetBodySetup();
        if(!Body||!Component.GetBodyInstance()||!Component.GetBodyInstance()->IsValidBodyInstance())
        {
            Error=FString::Printf(TEXT("No inspectable cooked collision for LiDAR blocker %s"),*Component.GetPathName());
            return false;
        }
        auto AddTriangle=[&](const FVector& A,const FVector& B,const FVector& C,uint32 Material,uint32 Flags)
        {
            const FVector WA=Transform.TransformPosition(A),WB=Transform.TransformPosition(B),WC=Transform.TransformPosition(C);
            FBox3f Bounds(ForceInit);
            Bounds+=FVector3f(WA);Bounds+=FVector3f(WB);Bounds+=FVector3f(WC);
            Out.Add({FVector3f(WA),FVector3f(WB-WA),FVector3f(WC-WA),Material,Flags,0},Bounds,FPlane(WA,WB,WC));
        };
        // Complex queries use the cooked Chaos faces and the same external face remap as FHitResult.
        if(Body->GetCollisionTraceFlag()!=CTF_UseSimpleAsComplex)
        {
            for(const auto& Mesh:Body->TriMeshGeometries)
            {
                if(!Mesh)continue;
                const auto& J=Mesh->Elements();
                for(int32 I=0;I<J.GetNumTriangles();++I)
                {
                    const auto ReadIndex=[&](int32 Vertex)
                    {
                        return J.RequiresLargeIndices() ? J.GetLargeIndexBuffer()[I][Vertex] : int32(J.GetSmallIndexBuffer()[I][Vertex]);
                    };
                    int32 Section=0;
                    const auto* M=Component.GetMaterialFromCollisionFaceIndex(Mesh->GetExternalFaceIndexFromInternal(I),Section);
                    uint32 Material=0,Flags=Mesh->GetCullsBackFaceRaycast()?CullBack:0;
                    if(M)
                    {
                        const auto Name=M->GetFName();
                        if(const uint32* Existing=MaterialIds.Find(Name))Material=*Existing;
                        else {Material=uint32(Materials.Add(Name));MaterialIds.Add(Name,Material);}
                        if(Name==FName(TEXT("aim_sentry_radar_glass")))Flags|=DarkWindow;
                        if(Name==FName(TEXT("aim_sentry_mid360_blue_glass")))Flags|=BlueWindow;
                    }
                    AddTriangle(FVector(ReadVertex(*Mesh,ReadIndex(0))),FVector(ReadVertex(*Mesh,ReadIndex(1))),
                        FVector(ReadVertex(*Mesh,ReadIndex(2))),Material,Flags);
                }
            }
            return true;
        }

        auto AddPlanes=[&](const TArray<FPlane>& LocalPlanes,const FBox& LocalBounds)
        {
            const uint32 First=Geometry->Planes.Num();
            const FMatrix M=Transform.ToMatrixWithScale().InverseFast().GetTransposed();
            for(const FPlane& Plane:LocalPlanes)
            {
                const FVector N=M.TransformVector(FVector(Plane)).GetSafeNormal();
                const FVector P=Transform.TransformPosition(FVector(Plane)*Plane.W);
                ExactSolidPlanes.Add(FPlane(P,N));
                Geometry->Planes.Add(FVector4f(FVector3f(N),float(FVector::DotProduct(N,P))));
            }
            const FBox3f Bounds(LocalBounds.TransformBy(Transform));
            FGpuLidarPrimitive Shape{};
            // Solid collision receives the default optical material. This field indexes its plane span.
            Shape.Material=First;Shape.Flags=PlaneShape;Shape.PlaneCount=LocalPlanes.Num();
            Out.Add(Shape,Bounds,FPlane(0.,0.,0.,0.));
        };
        const auto& Agg=Body->AggGeom;
        if(Agg.GetElementCount()!=Agg.BoxElems.Num()+Agg.ConvexElems.Num())
        {
            Error=FString::Printf(TEXT("Unsupported simple query shape in %s"),*Component.GetPathName());
            return false;
        }
        for(const auto& Box:Agg.BoxElems)
        {
            if(!CollisionEnabledHasQuery(Box.GetCollisionEnabled()))continue;
            const FVector Half(Box.X*.5,Box.Y*.5,Box.Z*.5);
            const FTransform Pose=Box.GetTransform();
            TArray<FPlane> Planes;
            for(int32 Axis=0;Axis<3;++Axis)
                for(double Side : {-1.,1.})
                {
                    FVector N=FVector::ZeroVector;N[Axis]=Side;
                    Planes.Add(FPlane(Pose.TransformPosition(N*Half),Pose.TransformVectorNoScale(N)));
                }
            AddPlanes(Planes,FBox(-Half,Half).TransformBy(Pose));
        }
        for(const auto& Elem:Agg.ConvexElems)
        {
            if(!CollisionEnabledHasQuery(Elem.GetCollisionEnabled()))continue;
            const auto& Convex=Elem.GetChaosConvexMesh();
            if(!Convex){Error=TEXT("Missing cooked convex LiDAR shape: ")+Component.GetPathName();return false;}
            TArray<FPlane> Planes;
            for(int32 Face=0;Face<Convex->NumPlanes();++Face)
            {
                Chaos::FVec3 N,P;
                Convex->GetPlaneNX(Face,N,P);
                Planes.Add(FPlane(FVector(P),FVector(N)));
            }
            const auto Bounds=Convex->BoundingBox();
            AddPlanes(Planes,FBox(Bounds.Min(),Bounds.Max()));
        }
        return true;
    };
    auto AddGeometry=[&](FSource& Source)->FMesh
    {
        FMesh Result;
        Result.Begin=Geometry->Nodes.Num();
        if(!Source.Refs.IsEmpty())
        {
            Result.Bounds=FBox(Union(Source.Refs,false));
            BuildTree(Geometry->Nodes,Source.Refs,4,0,[&](TConstArrayView<FReference> Refs)
            {
                const uint32 First=Geometry->Primitives.Num();
                for(const auto& Ref:Refs)
                {
                    Geometry->Primitives.Add(Source.Primitives[Ref.Index]);
                    ExactTrianglePlanes.Add(Source.ExactPlanes[Ref.Index]);
                }
                return First;
            });
        }
        Result.End=Geometry->Nodes.Num();
        return Result;
    };

    int32 StaticCount=0;
    for(TActorIterator<AActor> It(&World);It;++It)
    {
        TInlineComponentArray<UPrimitiveComponent*> Components(*It);
        const auto* Vehicle=Cast<AVehicleRig>(*It);
        if(Vehicle&&!VehicleIds.Contains(Vehicle))VehicleIds.Add(Vehicle,VehicleIds.Num()+1);
        for(auto* Component:Components)
        {
            if(!BlocksLidar(*Component))continue;
            if(Cast<UInstancedStaticMeshComponent>(Component))
            {Error=TEXT("Instanced query geometry is unsupported: ")+Component->GetPathName();return false;}
            if(Vehicle||Component->Mobility==EComponentMobility::Movable)DynamicComponents.Add(Component);
            else
            {
                if(!Bake(*Component,Component->GetComponentTransform(),Arena))return false;
                ++StaticCount;
            }
        }
    }
    Geometry->ArenaEnd=AddGeometry(Arena).End;
    Arena=FSource{};
    for(auto* Component:DynamicComponents)
    {
        FMeshKey Key;
        Key.Setup=Component->GetBodySetup();
        for(int32 Slot=0;Slot<Component->GetNumMaterials();++Slot)Key.Materials.Add(Component->GetMaterial(Slot));
        const FMesh* Shape=Cache.Find(Key);
        if(!Shape)
        {
            Local.Refs.Reset();Local.Primitives.Reset();Local.ExactPlanes.Reset();
            if(!Bake(*Component,FTransform::Identity,Local))return false;
            Shape=&Cache.Add(MoveTemp(Key),AddGeometry(Local));
        }
        if(Shape->Begin==Shape->End)continue;
        const USceneComponent* Anchor=Component;
        const int32* Index=nullptr;
        for(;Anchor;Anchor=Anchor->GetAttachParent())
            if((Index=NativeBodyIndices.Find(Anchor)))break;
        if(Component->GetOwner()->IsA<AVehicleRig>()&&!Index)
        {
            Error=TEXT("No historical snapshot binding for vehicle blocker ")+Component->GetPathName();
            return false;
        }
        const FTransform FromBody=Index?Component->GetComponentTransform().GetRelativeTransform(Anchor->GetComponentTransform()):FTransform::Identity;
        Dynamic.Add({Component,Index?*Index:INDEX_NONE,Shape->Begin,Shape->End,
            VehicleId(Cast<AVehicleRig>(Component->GetOwner())),Shape->Bounds,FromBody});
    }
    UE_LOG(LogTemp,Display,TEXT("RMSIM_GPU_LIDAR_SCENE static_components=%d arena_nodes=%u dynamic_components=%d distinct_links=%d nodes=%d primitives=%d"),
        StaticCount,Geometry->ArenaEnd,Dynamic.Num(),Cache.Num(),Geometry->Nodes.Num(),Geometry->Primitives.Num());
    return true;
}

FVector FGpuLidarScene::RefineImpactPoint(const FGpuLidarHit& Hit,const FVector& Origin,const FVector& Direction,
    TConstArrayView<FTransform> InstancePoses) const
{
    // The compute pass selects the first visible cooked face. Evaluate that face's
    // plane with the original double-precision pulse and pose to avoid rounding distant returns.
    if(Hit.DistanceCm<=0.f)return Origin;
    const FPlane* Plane=nullptr;
    if(Hit.Plane!=MAX_uint32)
    {
        if(ExactSolidPlanes.IsValidIndex(Hit.Plane))Plane=&ExactSolidPlanes[Hit.Plane];
    }
    else if(ExactTrianglePlanes.IsValidIndex(Hit.Primitive))Plane=&ExactTrianglePlanes[Hit.Primitive];
    const FVector Approximate=Origin+Direction*double(Hit.DistanceCm);
    if(!Plane)return Approximate;
    const FTransform* Pose=InstancePoses.IsValidIndex(Hit.Instance)?&InstancePoses[Hit.Instance]:nullptr;
    const FVector Position=Pose?Pose->InverseTransformPosition(Origin):Origin;
    const FVector Vector=Pose?Pose->InverseTransformVector(Direction):Direction;
    const double Denominator=FVector::DotProduct(FVector(*Plane),Vector);
    return FMath::Abs(Denominator)<=UE_DOUBLE_SMALL_NUMBER?Approximate:
        Origin-Direction*(Plane->PlaneDot(Position)/Denominator);
}

void FGpuLidarScene::CaptureSnapshot(const TArray<FTransform>& NativeBodies,bool bReplayedChaos)
{
    TRACE_CPUPROFILER_EVENT_SCOPE(RMSimGpuLidarSnapshot);
    bHasReferenceSnapshot=bReplayedChaos;
    TArray<FReference,TInlineAllocator<128>> Refs;
    TArray<FGpuLidarInstance,TInlineAllocator<128>> Pending;
    TArray<FTransform,TInlineAllocator<128>> NativePoses;
    for(const FDynamic& Item:Dynamic)
    {
        const auto* Component=Item.Component.Get();
        if(!Component||!BlocksLidar(*Component))continue;
        const FTransform Pose=NativeBodies.IsValidIndex(Item.BodyIndex) ? Item.FromBody*NativeBodies[Item.BodyIndex] : Component->GetComponentTransform();
        const FMatrix Inv=Pose.ToMatrixWithScale().InverseFast();
        const auto Column=[&](int32 I){return FVector4f(float(Inv.M[0][I]),float(Inv.M[1][I]),float(Inv.M[2][I]),float(Inv.M[3][I]));};
        const uint32 Index=Pending.Add({Column(0),Column(1),Column(2),Item.Begin,Item.End,Item.Vehicle,uint32(Pose.GetDeterminant()<0.)});
        NativePoses.Add(Pose);
        const FBox3f Box(Item.Bounds.TransformBy(Pose));
        Refs.Add({Box,Box.GetCenter(),Index});
    }
    const uint32 Root=Building->InstanceNodes.Num();
    if(!Refs.IsEmpty())BuildTree(Building->InstanceNodes,Refs,1,0,[&](TConstArrayView<FReference> Leaf)
    {
        Building->Poses.Add(NativePoses[Leaf[0].Index]);
        return uint32(Building->Instances.Add(Pending[Leaf[0].Index]));
    });
    Building->Snapshots.Add(FUintVector2(Root,Building->InstanceNodes.Num()));
}

#if WITH_DEV_AUTOMATION_TESTS
TArray<FGpuLidarRay> FGpuLidarScene::MakeVehicleCoverageRays(uint32 Vehicle) const
{
    TArray<FGpuLidarRay> Rays;
    if(Building->Snapshots.IsEmpty())return Rays;
    const auto& Top=Building->Snapshots.Last();
    const FVector3f Directions[]={{0,0,1},{1,0,.25f},{-1,0,.25f},{0,1,.12f},{0,-1,.12f}};
    for(uint32 I=Top.X;I<Top.Y;++I)
    {
        const auto& Node=Building->InstanceNodes[I];
        if(!(Node.FirstAndCount&7)||!Building->Instances[Node.FirstAndCount>>3].Vehicle)continue;
        const FVector3f Centre=(Node.Min+Node.Max)*.5f;
        for(const FVector3f& Raw:Directions)
        {
            const FVector3f Direction=Raw.GetSafeNormal();
            Rays.Add({Centre+Direction*80.f,0,-Direction,Vehicle,160.f});
        }
    }
    return Rays;
}
#endif

void FGpuLidarScene::AddRays(TArray<FGpuLidarRay>&& Rays, FResults&& Complete)
{
    check(!Building->Snapshots.IsEmpty());
    const uint32 Snapshot=Building->Snapshots.Num()-1;
    for(auto& Ray:Rays)Ray.Snapshot=Snapshot;
    Building->Work.Add({Rays.Num(),MoveTemp(Complete)});
    Building->Rays.Append(MoveTemp(Rays));
}
void FGpuLidarScene::Drain()
{
    TRACE_CPUPROFILER_EVENT_SCOPE(RMSimGpuLidarDrain);
    if(InFlight.IsEmpty())return;
    while(!InFlight.IsEmpty()&&InFlight[0]->Ready.Load())
    {
        const auto Frame=InFlight[0];
        InFlight.RemoveAt(0,1,EAllowShrinking::No);
        int32 Offset=0;
        const bool bComplete=Frame->Hits.Num()==Frame->Rays.Num();
        for(auto& Work:Frame->Work)
        {
            const TConstArrayView<FGpuLidarHit> View=Frame->Hits;
            Work.Complete(bComplete&&Work.Count ? View.Slice(Offset,Work.Count) : TConstArrayView<FGpuLidarHit>(),Frame->Poses);
            Offset+=Work.Count;
        }
    }
}

void FGpuLidarScene::Submit()
{
    TRACE_CPUPROFILER_EVENT_SCOPE(RMSimGpuLidarSubmit);
    if(Building->Work.IsEmpty())
    {
        Building->Instances.Reset();Building->Poses.Reset();Building->InstanceNodes.Reset();Building->Snapshots.Reset();
        if(!InFlight.IsEmpty())
        {
            const auto State=RenderState;
            ENQUEUE_RENDER_COMMAND(RMSimGpuLidarPoll)([State](FRHICommandListImmediate&){State->Poll();});
        }
        return;
    }
    if(InFlight.Num()>=RingCapacity)
    {
        // Preserve the original snapshot and scan order while the GPU catches up.
        // A stalled GPU cannot grow the queue or stale scan epochs without bound.
        const auto State=RenderState;
        ENQUEUE_RENDER_COMMAND(RMSimGpuLidarPoll)([State](FRHICommandListImmediate&){State->Poll();});
        if(Building->Rays.Num()>100000)
        {
            UE_LOG(LogTemp,Error,TEXT("RMSIM_GPU_LIDAR_BACKLOG_OVERFLOW rays=%d"),Building->Rays.Num());
            for(auto& Work:Building->Work)Work.Complete({},{});
            Building=MakeUnique<FFrame>();
        }
        return;
    }
    TSharedPtr<FFrame,ESPMode::ThreadSafe> Frame=MakeShareable(Building.Release());
    Building=MakeUnique<FFrame>();
    const int32 SlotIndex=NextSequence++%RingCapacity;
    InFlight.Add(Frame);
    if(Frame->Rays.IsEmpty())
    {
        Frame->Ready.Store(true);
        if(InFlight.Num()>1)
        {
            const auto State=RenderState;
            ENQUEUE_RENDER_COMMAND(RMSimGpuLidarPoll)([State](FRHICommandListImmediate&){State->Poll();});
        }
        return;
    }
    const TSharedPtr<FGeometry,ESPMode::ThreadSafe> Upload=MakeShareable(Geometry.Release());
    const auto State=RenderState;
    ENQUEUE_RENDER_COMMAND(RMSimGpuLidarTrace)([State,Frame,Upload,SlotIndex](FRHICommandListImmediate& Cmd)
    {
        if(State->bClosing)return;
        State->Poll();
        auto& Slot=State->Slots[SlotIndex];
        check(!Slot.Ready);
        if(!Slot.Readback)Slot.Readback=MakeUnique<FRHIGPUBufferReadback>(TEXT("RMSimMid360"));
        if(State->bProfileGpuTiming)
        {
            if(!Slot.Begin)Slot.Begin=RHICreateRenderQuery(RQT_AbsoluteTime);
            if(!Slot.End)Slot.End=RHICreateRenderQuery(RQT_AbsoluteTime);
        }
        FRDGBuilder Graph(Cmd);
        FRDGBufferRef Nodes,Primitives,Planes;
        if(Upload)
        {
            State->ArenaEnd=Upload->ArenaEnd;
            Nodes=CreateStructuredBuffer(Graph,TEXT("RMSim.Lidar.GeometryNodes"),Upload->Nodes);
            Primitives=CreateStructuredBuffer(Graph,TEXT("RMSim.Lidar.Primitives"),Upload->Primitives);
            Planes=CreateStructuredBuffer(Graph,TEXT("RMSim.Lidar.Planes"),Upload->Planes);
            Graph.QueueBufferExtraction(Nodes,&State->Nodes);
            Graph.QueueBufferExtraction(Primitives,&State->Primitives);
            Graph.QueueBufferExtraction(Planes,&State->Planes);
        }
        else
        {
            Nodes=Graph.RegisterExternalBuffer(State->Nodes);
            Primitives=Graph.RegisterExternalBuffer(State->Primitives);
            Planes=Graph.RegisterExternalBuffer(State->Planes);
        }
        auto* Params=Graph.AllocParameters<FMid360TraceCS::FParameters>();
        Params->RayCount=Frame->Rays.Num();
        Params->ArenaEnd=State->ArenaEnd;
        Params->GeometryNodes=Graph.CreateSRV(Nodes);
        Params->Primitives=Graph.CreateSRV(Primitives);
        Params->Planes=Graph.CreateSRV(Planes);
        Params->InstanceNodes=Graph.CreateSRV(CreateStructuredBuffer(Graph,TEXT("RMSim.Lidar.InstanceNodes"),Frame->InstanceNodes));
        Params->Instances=Graph.CreateSRV(CreateStructuredBuffer(Graph,TEXT("RMSim.Lidar.Instances"),Frame->Instances));
        Params->Snapshots=Graph.CreateSRV(CreateStructuredBuffer(Graph,TEXT("RMSim.Lidar.Snapshots"),Frame->Snapshots));
        Params->Rays=Graph.CreateSRV(CreateStructuredBuffer(Graph,TEXT("RMSim.Lidar.Rays"),Frame->Rays));
        const auto Buffer=Graph.CreateBuffer(FRDGBufferDesc::CreateStructuredDesc(sizeof(FGpuLidarHit),Params->RayCount),TEXT("RMSim.Lidar.Hits"));
        Params->Hits=Graph.CreateUAV(Buffer);
        const TShaderMapRef<FMid360TraceCS> Shader(GetGlobalShaderMap(GMaxRHIFeatureLevel));
        const FIntVector Groups(FMath::DivideAndRoundUp(Params->RayCount,64u),1,1);
        Graph.AddPass(RDG_EVENT_NAME("RMSimMid360Trace"),Params,ERDGPassFlags::Compute,
            [Params,Shader,Groups,Begin=Slot.Begin,End=Slot.End](FRHICommandList& Compute)
        {
            if(Begin)Compute.EndRenderQuery(Begin.GetReference());
            FComputeShaderUtils::Dispatch(Compute,Shader,*Params,Groups);
            if(End)Compute.EndRenderQuery(End.GetReference());
        });
        const uint32 Bytes=Params->RayCount*sizeof(FGpuLidarHit);
        AddEnqueueCopyPass(Graph,Slot.Readback.Get(),Buffer,Bytes);
        Graph.Execute();
        Slot.Ready=[Frame,Bytes](FRHIGPUBufferReadback& Readback)
        {
            if(const void* Src=Readback.Lock(Bytes))
            {
                Frame->Hits.SetNumUninitialized(Frame->Rays.Num());
                FMemory::Memcpy(Frame->Hits.GetData(),Src,Bytes);
                Readback.Unlock();
            }
            Frame->Ready.Store(true);
        };
    });
}
