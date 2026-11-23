#include "WTBreakableGeometry.h"

#include "Components/PrimitiveComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Materials/MaterialInterface.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "PhysicsEngine/BodyInstance.h"
#include "StaticMeshResources.h"

namespace
{
constexpr double WeldCm = .001;
constexpr double CutEpsilonCm = .0001;
constexpr double WoodDensityKgM3 = 550.0;

struct FPart
{
    TArray<FProcMeshSection> Sections;
    FBox Bounds = FBox(ForceInit);
};

void AppendTriangle(FProcMeshSection& Section, const FProcMeshVertex& A,
    const FProcMeshVertex& B, const FProcMeshVertex& C)
{
    if(FVector::CrossProduct(B.Position-A.Position,C.Position-A.Position).SizeSquared()<1.e-12)return;
    const uint32 Base=static_cast<uint32>(Section.ProcVertexBuffer.Num());
    Section.ProcVertexBuffer.Add(A);Section.ProcVertexBuffer.Add(B);Section.ProcVertexBuffer.Add(C);
    Section.ProcIndexBuffer.Append({Base,Base+1,Base+2});
    Section.SectionLocalBox+=A.Position;Section.SectionLocalBox+=B.Position;Section.SectionLocalBox+=C.Position;
}

FBox PartBounds(const FPart& Part)
{
    FBox Result(ForceInit);
    for(const auto& S:Part.Sections)if(S.SectionLocalBox.IsValid)Result+=S.SectionLocalBox;
    return Result;
}

FProcMeshVertex Interpolate(const FProcMeshVertex& A,const FProcMeshVertex& B,double T)
{
    FProcMeshVertex V;
    V.Position=FMath::Lerp(A.Position,B.Position,T);
    V.Normal=FMath::Lerp(A.Normal,B.Normal,T).GetSafeNormal();
    V.Tangent.TangentX=FMath::Lerp(A.Tangent.TangentX,B.Tangent.TangentX,T).GetSafeNormal();
    V.Tangent.bFlipTangentY=A.Tangent.bFlipTangentY;
    V.Color=FColor(
        static_cast<uint8>(FMath::Clamp(FMath::RoundToInt(FMath::Lerp(double(A.Color.R),double(B.Color.R),T)),0,255)),
        static_cast<uint8>(FMath::Clamp(FMath::RoundToInt(FMath::Lerp(double(A.Color.G),double(B.Color.G),T)),0,255)),
        static_cast<uint8>(FMath::Clamp(FMath::RoundToInt(FMath::Lerp(double(A.Color.B),double(B.Color.B),T)),0,255)),
        static_cast<uint8>(FMath::Clamp(FMath::RoundToInt(FMath::Lerp(double(A.Color.A),double(B.Color.A),T)),0,255)));
    V.UV0=FMath::Lerp(A.UV0,B.UV0,T);V.UV1=FMath::Lerp(A.UV1,B.UV1,T);
    V.UV2=FMath::Lerp(A.UV2,B.UV2,T);V.UV3=FMath::Lerp(A.UV3,B.UV3,T);
    return V;
}

// Clip a single convex source member. Caps close the fracture, while every outer
// triangle retains its original attributes; no bounding-box visual is substituted.
FPart Clip(const FPart& Input,const FVector& PlanePoint,const FVector& KeepNormal)
{
    FPart Out;Out.Sections.SetNum(Input.Sections.Num());
    TArray<FProcMeshVertex> CutVertices;
    int32 CapSection=INDEX_NONE;
    auto AddCut=[&](const FProcMeshVertex& V)
    {
        for(const auto& Existing:CutVertices)
            if(Existing.Position.Equals(V.Position,WeldCm))return;
        CutVertices.Add(V);
    };
    for(int32 S=0;S<Input.Sections.Num();++S)
    {
        const auto& Src=Input.Sections[S];
        for(int32 T=0;T+2<Src.ProcIndexBuffer.Num();T+=3)
        {
            TArray<FProcMeshVertex,TInlineAllocator<5>> Polygon;
            for(int32 Edge=0;Edge<3;++Edge)
            {
                const auto& A=Src.ProcVertexBuffer[Src.ProcIndexBuffer[T+Edge]];
                const auto& B=Src.ProcVertexBuffer[Src.ProcIndexBuffer[T+(Edge+1)%3]];
                const double DA=FVector::DotProduct(A.Position-PlanePoint,KeepNormal);
                const double DB=FVector::DotProduct(B.Position-PlanePoint,KeepNormal);
                if(DA>=-CutEpsilonCm)Polygon.Add(A);
                if((DA>CutEpsilonCm&&DB<-CutEpsilonCm)||(DA<-CutEpsilonCm&&DB>CutEpsilonCm))
                {
                    const FProcMeshVertex V=Interpolate(A,B,DA/(DA-DB));
                    Polygon.Add(V);AddCut(V);CapSection=S;
                }
                else if(FMath::Abs(DA)<=CutEpsilonCm){AddCut(A);CapSection=S;}
            }
            for(int32 I=1;I+1<Polygon.Num();++I)
                AppendTriangle(Out.Sections[S],Polygon[0],Polygon[I],Polygon[I+1]);
        }
    }
    if(CapSection!=INDEX_NONE&&CutVertices.Num()>=3)
    {
        FVector Center=FVector::ZeroVector;
        for(const auto& V:CutVertices)Center+=V.Position;
        Center/=CutVertices.Num();
        const FVector Outward=-KeepNormal.GetSafeNormal();
        FVector U,V;Outward.FindBestAxisVectors(U,V);
        // Enforce U x V = outward, independent of FindBestAxisVectors handedness.
        V=FVector::CrossProduct(Outward,U).GetSafeNormal();
        CutVertices.Sort([&](const FProcMeshVertex& A,const FProcMeshVertex& B)
        {
            const FVector PA=A.Position-Center,PB=B.Position-Center;
            return FMath::Atan2(FVector::DotProduct(PA,V),FVector::DotProduct(PA,U))<
                   FMath::Atan2(FVector::DotProduct(PB,V),FVector::DotProduct(PB,U));
        });
        for(auto& P:CutVertices)
        {
            P.Normal=Outward;P.Tangent=FProcMeshTangent(U,false);
            // Source rough-wood material, with centimetre-scaled end-grain coordinates.
            P.UV0=FVector2D(FVector::DotProduct(P.Position-Center,U),
                FVector::DotProduct(P.Position-Center,V))*.025;
        }
        for(int32 I=1;I+1<CutVertices.Num();++I)
            AppendTriangle(Out.Sections[CapSection],CutVertices[0],CutVertices[I],CutVertices[I+1]);
    }
    Out.Bounds=PartBounds(Out);
    return Out;
}

bool BuildPartsUncached(UStaticMesh* Source,TArray<FPart>& Parts,TArray<UMaterialInterface*>& Materials)
{
    TArray<FProcMeshSection> Sections;
    if(!WTBreakableGeometry::CopySections(Source,Sections,Materials))return false;
    TArray<int32> Offsets,Parent;
    TArray<FVector> Positions;
    for(const auto& S:Sections)
    {
        Offsets.Add(Positions.Num());
        for(const auto& V:S.ProcVertexBuffer){Parent.Add(Parent.Num());Positions.Add(V.Position);}
    }
    auto Root=[&](int32 I)
    {
        int32 R=I;while(Parent[R]!=R)R=Parent[R];
        while(Parent[I]!=I){const int32 Next=Parent[I];Parent[I]=R;I=Next;}
        return R;
    };
    auto Join=[&](int32 A,int32 B){Parent[Root(A)]=Root(B);};
    TMap<FIntVector,int32> Welded;
    for(int32 I=0;I<Positions.Num();++I)
    {
        const FVector P=Positions[I]/WeldCm;
        const FIntVector Key(FMath::RoundToInt(P.X),FMath::RoundToInt(P.Y),FMath::RoundToInt(P.Z));
        if(const int32* Existing=Welded.Find(Key))Join(I,*Existing);else Welded.Add(Key,I);
    }
    for(int32 S=0;S<Sections.Num();++S)
    {
        const auto& Indices=Sections[S].ProcIndexBuffer;
        for(int32 T=0;T+2<Indices.Num();T+=3)
        {
            Join(Offsets[S]+Indices[T],Offsets[S]+Indices[T+1]);
            Join(Offsets[S]+Indices[T],Offsets[S]+Indices[T+2]);
        }
    }
    TMap<int32,int32> PartIndices;
    for(int32 S=0;S<Sections.Num();++S)
    {
        const auto& Src=Sections[S];
        for(int32 T=0;T+2<Src.ProcIndexBuffer.Num();T+=3)
        {
            const int32 Key=Root(Offsets[S]+Src.ProcIndexBuffer[T]);
            int32* PartIndex=PartIndices.Find(Key);
            if(!PartIndex)
            {
                const int32 NewIndex=Parts.AddDefaulted();
                Parts[NewIndex].Sections.SetNum(Sections.Num());PartIndices.Add(Key,NewIndex);
                PartIndex=PartIndices.Find(Key);
            }
            AppendTriangle(Parts[*PartIndex].Sections[S],
                Src.ProcVertexBuffer[Src.ProcIndexBuffer[T]],
                Src.ProcVertexBuffer[Src.ProcIndexBuffer[T+1]],
                Src.ProcVertexBuffer[Src.ProcIndexBuffer[T+2]]);
        }
    }
    for(auto& Part:Parts)Part.Bounds=PartBounds(Part);
    return !Parts.IsEmpty()&&Parts.Num()<=8;
}

bool BuildParts(UStaticMesh* Source,TArray<FPart>& Parts,TArray<UMaterialInterface*>& Materials)
{
    struct FSourceTopology
    {
        TArray<FPart> Parts;
        TArray<TWeakObjectPtr<UMaterialInterface>> Materials;
        bool Ready=false;
    };
    // Assets are immutable during play. Cache source topology, never instance pose
    // or contact state, and do not keep an unloaded map's meshes/materials alive.
    static TMap<TWeakObjectPtr<UStaticMesh>,FSourceTopology> Cache;
    if(!Source)return false;
    for(auto It=Cache.CreateIterator();It;++It)
        if(!It.Key().IsValid())It.RemoveCurrent();
    const TWeakObjectPtr<UStaticMesh> Key(Source);
    FSourceTopology* Entry=Cache.Find(Key);
    if(!Entry)
    {
        FSourceTopology NewEntry;TArray<UMaterialInterface*> SourceMaterials;
        NewEntry.Ready=BuildPartsUncached(Source,NewEntry.Parts,SourceMaterials);
        if(NewEntry.Ready)for(auto* Material:SourceMaterials)
        {
            NewEntry.Materials.Add(Material);
            if(!Material)NewEntry.Ready=false;
        }
        Cache.Add(Key,MoveTemp(NewEntry));Entry=Cache.Find(Key);
    }
    if(!Entry->Ready)return false;
    Materials.Reset();
    for(const auto& Material:Entry->Materials)
    {
        if(!Material.IsValid())return false;
        Materials.Add(Material.Get());
    }
    Parts=Entry->Parts;
    return true;
}

bool SelectMember(UStaticMesh* Source,const FTransform& Instance,const FVector& HitWorld,
    bool Fence,TArray<FPart>& Parts,TArray<UMaterialInterface*>& Materials,int32& Selected)
{
    if(!Source||Instance.ContainsNaN()||HitWorld.ContainsNaN())return false;
    const FString Expected=Fence?TEXT("prop_wood_fence_h"):TEXT("prop_wood_fence_h_plank");
    if(Source->GetName()!=Expected)return false;
    const FVector Scale=Instance.GetScale3D();
    if(Scale.GetMin()<.35||Scale.GetMax()>1.6||
        FVector::DotProduct(Instance.GetUnitAxis(EAxis::Z),FVector::UpVector)<.96)return false;
    if(!BuildParts(Source,Parts,Materials))return false;
    const FVector LocalHit=Instance.InverseTransformPosition(HitWorld);
    Selected=INDEX_NONE;
    if(Fence)
    {
        if(Parts.Num()!=5)return false;
        int32 Rails=0,Posts=0;double Lowest=DBL_MAX;
        for(int32 I=0;I<Parts.Num();++I)
        {
            const FVector Size=Parts[I].Bounds.GetSize();
            if(FMath::Max(Size.X,Size.Y)>140&&Size.Z<20&&FMath::Min(Size.X,Size.Y)<20)
            {
                ++Rails;
                if(Parts[I].Bounds.GetCenter().Z<Lowest){Lowest=Parts[I].Bounds.GetCenter().Z;Selected=I;}
            }
            else if(Size.Z>80&&FMath::Max(Size.X,Size.Y)<20)++Posts;
        }
        if(Rails!=3||Posts!=2||Selected==INDEX_NONE)return false;
        const FBox& Bounds=Parts[Selected].Bounds;
        const FVector Size=Bounds.GetSize();
        const int32 Axis=Size.X>Size.Y?0:1;
        // Impacts at frame/post ends and upper members stay structural.
        if(LocalHit[Axis]<Bounds.Min[Axis]+15/Scale[Axis]||
           LocalHit[Axis]>Bounds.Max[Axis]-15/Scale[Axis])return false;
        return FVector::Distance(HitWorld,
            Instance.TransformPosition(Bounds.GetClosestPointTo(LocalHit)))<=4.0;
    }
    if(Parts.Num()!=2)return false;
    for(int32 I=0;I<Parts.Num();++I)
    {
        const FBox& Bounds=Parts[I].Bounds;const FVector Size=Bounds.GetSize();
        if(Size.Z<80||Size.Z>160||FMath::Max(Size.X,Size.Y)>14)return false;
        if(FVector::Distance(HitWorld,Instance.TransformPosition(Bounds.GetClosestPointTo(LocalHit)))<=4.0&&
            LocalHit.Z>Bounds.Min.Z+8/Scale.Z&&LocalHit.Z<Bounds.Min.Z+Size.Z*.75)Selected=I;
    }
    return Selected!=INDEX_NONE;
}

double VolumeCm3(const FPart& Part)
{
    double Signed=0;const FVector Center=Part.Bounds.GetCenter();
    for(const auto& S:Part.Sections)for(int32 T=0;T+2<S.ProcIndexBuffer.Num();T+=3)
    {
        const FVector A=S.ProcVertexBuffer[S.ProcIndexBuffer[T]].Position-Center;
        const FVector B=S.ProcVertexBuffer[S.ProcIndexBuffer[T+1]].Position-Center;
        const FVector C=S.ProcVertexBuffer[S.ProcIndexBuffer[T+2]].Position-Center;
        Signed+=FVector::DotProduct(A,FVector::CrossProduct(B,C))/6.;
    }
    return FMath::Abs(Signed);
}

AActor* SpawnParts(UWorld* World,const TArray<FPart>& Parts,
    const TArray<UMaterialInterface*>& Materials,const FTransform& Instance,
    bool Dynamic,AActor* VehicleToIgnore,double& MassKg)
{
    FBox Bounds(ForceInit);for(const auto& P:Parts)Bounds+=P.Bounds;
    if(!Bounds.IsValid)return nullptr;
    const FVector Center=Bounds.GetCenter();
    FTransform Pose=Instance;Pose.SetLocation(Instance.TransformPosition(Center));
    FActorSpawnParameters Params;
    Params.SpawnCollisionHandlingOverride=ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    AActor* Created=World->SpawnActor<AActor>(AActor::StaticClass(),Pose,Params);
    if(!Created)return nullptr;
    auto* Mesh=NewObject<UProceduralMeshComponent>(Created);
    Mesh->SetMobility(EComponentMobility::Movable);
    Mesh->bUseAsyncCooking=false;Mesh->bUseComplexAsSimpleCollision=false;
    Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Mesh->SetCanEverAffectNavigation(!Dynamic);
    Mesh->SetGenerateOverlapEvents(false);
    Created->Tags.Add(Dynamic?TEXT("WT_Debris"):TEXT("WT_Remainder"));
    Created->SetRootComponent(Mesh);Created->AddInstanceComponent(Mesh);
    Mesh->RegisterComponent();Created->SetActorTransform(Pose);
    TArray<FProcMeshSection> Sections;Sections.SetNum(Materials.Num());
    TArray<TArray<FVector>> Hulls;
    double Volume=0;
    for(const FPart& P:Parts)
    {
        TArray<FVector> Points;
        for(int32 S=0;S<P.Sections.Num();++S)
        {
            const auto& From=P.Sections[S];auto& Into=Sections[S];
            const uint32 Base=static_cast<uint32>(Into.ProcVertexBuffer.Num());
            for(auto Vertex:From.ProcVertexBuffer)
            {
                Vertex.Position-=Center;Into.ProcVertexBuffer.Add(Vertex);
                Into.SectionLocalBox+=Vertex.Position;
                bool Seen=false;for(const FVector& Point:Points)if(Point.Equals(Vertex.Position,WeldCm)){Seen=true;break;}
                if(!Seen)Points.Add(Vertex.Position);
            }
            for(uint32 Index:From.ProcIndexBuffer)Into.ProcIndexBuffer.Add(Base+Index);
        }
        if(Points.Num()<4){Created->Destroy();return nullptr;}
        Hulls.Add(MoveTemp(Points));Volume+=VolumeCm3(P);
    }
    if(Volume<1){Created->Destroy();return nullptr;}
    for(int32 S=0;S<Sections.Num();++S)
    {
        Mesh->SetProcMeshSection(S,Sections[S]);
        Mesh->SetMaterial(S,Materials[S]);
    }
    Mesh->SetCollisionConvexMeshes(Hulls);
    auto* Material=NewObject<UPhysicalMaterial>(Mesh);
    Material->Friction=.65f;Material->Restitution=.04f;
    Material->FrictionCombineMode=EFrictionCombineMode::Average;
    Mesh->SetPhysMaterialOverride(Material);
    Mesh->SetCollisionObjectType(Dynamic?ECC_PhysicsBody:ECC_WorldStatic);
    Mesh->SetCollisionResponseToAllChannels(ECR_Block);
    if(Dynamic)
    {
        // Fragments are cosmetic debris: terrain/static scenery block them;
        // all vehicle bodies and other debris share PhysicsBody and are ignored.
        Mesh->SetCollisionResponseToChannel(ECC_PhysicsBody,ECR_Ignore);
        if(VehicleToIgnore)Mesh->IgnoreActorWhenMoving(VehicleToIgnore,true);
        Mesh->SetCanEverAffectNavigation(false);
    }
    Mesh->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
    MassKg=FMath::Clamp(Volume*FMath::Abs(Instance.GetScale3D().X*Instance.GetScale3D().Y*
        Instance.GetScale3D().Z)*WoodDensityKgM3/1.e6,.12,8.0);
    if(Dynamic)
    {
        Mesh->SetMassOverrideInKg(NAME_None,MassKg,true);
        Mesh->SetLinearDamping(.45f);Mesh->SetAngularDamping(1.1f);
        Mesh->SetUseCCD(true);Mesh->BodyInstance.SetMaxAngularVelocityInRadians(5.0,false,true);
        Mesh->SetSimulatePhysics(true);
        if(!Mesh->IsSimulatingPhysics()){Created->Destroy();return nullptr;}
        Created->SetLifeSpan(8.0f);
    }
    return Created;
}
}

bool WTBreakableGeometry::CopySections(UStaticMesh* Source,TArray<FProcMeshSection>& Sections,
    TArray<UMaterialInterface*>& Materials)
{
    Sections.Reset();Materials.Reset();
    if(!Source||!Source->bAllowCPUAccess||!Source->GetRenderData()||
        !Source->GetRenderData()->LODResources.IsValidIndex(0))return false;
    const FStaticMeshLODResources& LOD=Source->GetRenderData()->LODResources[0];
    const auto& Buffers=LOD.VertexBuffers;
    const uint32 Count=Buffers.PositionVertexBuffer.GetNumVertices();
    if(Count<3||Count>200000||Buffers.PositionVertexBuffer.GetAllocatedSize()==0||
        Buffers.StaticMeshVertexBuffer.GetNumVertices()!=Count)return false;
    const FIndexArrayView Indices=LOD.IndexBuffer.GetArrayView();
    if(Indices.Num()<3||Indices.Num()>600000)return false;
    const uint32 Channels=Buffers.StaticMeshVertexBuffer.GetNumTexCoords();
    if(Channels>4)return false; // Preserve all source channels, never silently truncate.
    for(const FStaticMeshSection& Input:LOD.Sections)
    {
        if(Input.FirstIndex+Input.NumTriangles*3>static_cast<uint32>(Indices.Num()))return false;
        FProcMeshSection Section;
        TMap<uint32,uint32> Remap;
        for(uint32 I=0;I<Input.NumTriangles*3;++I)
        {
            const uint32 Index=Indices[Input.FirstIndex+I];
            if(Index>=Count)return false;
            uint32* Mapped=Remap.Find(Index);
            if(!Mapped)
            {
                FProcMeshVertex V;
                V.Position=FVector(Buffers.PositionVertexBuffer.VertexPosition(Index));
                const FVector4f NZ=Buffers.StaticMeshVertexBuffer.VertexTangentZ(Index);
                const FVector4f TX=Buffers.StaticMeshVertexBuffer.VertexTangentX(Index);
                V.Normal=FVector(NZ.X,NZ.Y,NZ.Z).GetSafeNormal();
                const FVector X(TX.X,TX.Y,TX.Z);
                const FVector Y=FVector(Buffers.StaticMeshVertexBuffer.VertexTangentY(Index));
                V.Tangent=FProcMeshTangent(X.GetSafeNormal(),FVector::DotProduct(FVector::CrossProduct(V.Normal,X),Y)<0);
                if(Buffers.ColorVertexBuffer.GetNumVertices()==Count)V.Color=Buffers.ColorVertexBuffer.VertexColor(Index);
                if(Channels>0)V.UV0=FVector2D(Buffers.StaticMeshVertexBuffer.GetVertexUV(Index,0));
                if(Channels>1)V.UV1=FVector2D(Buffers.StaticMeshVertexBuffer.GetVertexUV(Index,1));
                if(Channels>2)V.UV2=FVector2D(Buffers.StaticMeshVertexBuffer.GetVertexUV(Index,2));
                if(Channels>3)V.UV3=FVector2D(Buffers.StaticMeshVertexBuffer.GetVertexUV(Index,3));
                if(V.Position.ContainsNaN()||V.Normal.ContainsNaN())return false;
                const uint32 NewIndex=static_cast<uint32>(Section.ProcVertexBuffer.Add(V));
                Section.SectionLocalBox+=V.Position;Remap.Add(Index,NewIndex);Mapped=Remap.Find(Index);
            }
            Section.ProcIndexBuffer.Add(*Mapped);
        }
        Sections.Add(MoveTemp(Section));Materials.Add(Source->GetMaterial(Input.MaterialIndex));
    }
    return !Sections.IsEmpty();
}

bool WTBreakableGeometry::CanBreakAt(UStaticMesh* Source,const FTransform& Instance,
    const FVector& HitWorld,bool Fence)
{
    TArray<FPart> Parts;TArray<UMaterialInterface*> Materials;int32 Selected;
    return SelectMember(Source,Instance,HitWorld,Fence,Parts,Materials,Selected);
}

bool WTBreakableGeometry::Create(UWorld* World,UStaticMesh* Source,const FTransform& Instance,
    const FVector& HitWorld,const FVector& IntoObstacleWorld,double ImpulseNs,
    double NormalSpeedMps,bool Fence,AActor* VehicleToIgnore,
    TArray<TWeakObjectPtr<AActor>>& DebrisOut,TArray<TWeakObjectPtr<AActor>>& RemaindersOut)
{
    if(!World||!FMath::IsFinite(ImpulseNs)||ImpulseNs<=0||
        !FMath::IsFinite(NormalSpeedMps)||IntoObstacleWorld.ContainsNaN())return false;
    TArray<FPart> Parts;TArray<UMaterialInterface*> Materials;int32 Selected;
    if(!SelectMember(Source,Instance,HitWorld,Fence,Parts,Materials,Selected))return false;
    const FVector LocalHit=Instance.InverseTransformPosition(HitWorld);
    TArray<FPart> Fixed,Fragments;
    if(Fence)
    {
        for(int32 I=0;I<Parts.Num();++I)if(I!=Selected)Fixed.Add(Parts[I]);
        const FPart& Rail=Parts[Selected];const FVector Size=Rail.Bounds.GetSize();
        const int32 Axis=Size.X>Size.Y?0:1;
        FVector Along=FVector::ZeroVector;Along[Axis]=1.;
        const FVector Side=FVector::CrossProduct(FVector::UpVector,Along);
        const double HalfOpening=32./Instance.GetScale3D()[Axis];
        const double Middle=FMath::Clamp(LocalHit[Axis],
            Rail.Bounds.Min[Axis]+HalfOpening+5,Rail.Bounds.Max[Axis]-HalfOpening-5);
        FVector Left=Rail.Bounds.GetCenter(),Right=Left,SplitPoint=Left;
        Left[Axis]=Middle-HalfOpening;Right[Axis]=Middle+HalfOpening;SplitPoint[Axis]=Middle;
        const FVector LeftNormal=(Along+FVector::UpVector*.19+Side*.07).GetSafeNormal();
        const FVector RightNormal=(Along-FVector::UpVector*.16+Side*.06).GetSafeNormal();
        Fixed.Add(Clip(Rail,Left,-LeftNormal));Fixed.Add(Clip(Rail,Right,RightNormal));
        FPart Center=Clip(Clip(Rail,Left,LeftNormal),Right,-RightNormal);
        const FVector SplitNormal=(Along+FVector::UpVector*.38-Side*.12).GetSafeNormal();
        Fragments.Add(Clip(Center,SplitPoint,SplitNormal));
        Fragments.Add(Clip(Center,SplitPoint,-SplitNormal));
    }
    else
    {
        // Damage only the member whose real surface received the hit. The second
        // thin board remains complete and solid; the selected board retains its stump.
        for(int32 I=0;I<Parts.Num();++I)
        {
            if(I!=Selected){Fixed.Add(Parts[I]);continue;}
            const FPart& Board=Parts[I];
            FVector Cut=Board.Bounds.GetCenter();
            Cut.Z=FMath::Clamp(LocalHit.Z,Board.Bounds.Min.Z+12,
                Board.Bounds.Min.Z+Board.Bounds.GetSize().Z*.65);
            const FVector N=FVector(I==0?.18:-.14,.07,1).GetSafeNormal();
            Fixed.Add(Clip(Board,Cut,-N));Fragments.Add(Clip(Board,Cut,N));
        }
    }
    for(const FPart& P:Fixed)if(!P.Bounds.IsValid||VolumeCm3(P)<1)return false;
    for(const FPart& P:Fragments)if(!P.Bounds.IsValid||VolumeCm3(P)<1)return false;
    if(Fragments.IsEmpty()||Fragments.Num()>6)return false;
    TArray<AActor*> Created;TArray<double> Masses;
    double RemainderMass=0;
    AActor* Remainder=SpawnParts(World,Fixed,Materials,Instance,false,VehicleToIgnore,RemainderMass);
    if(!Remainder)return false;
    Created.Add(Remainder);
    for(const FPart& P:Fragments)
    {
        double Mass=0;TArray<FPart> One;One.Add(P);
        AActor* Debris=SpawnParts(World,One,Materials,Instance,true,VehicleToIgnore,Mass);
        if(!Debris){for(auto* A:Created)A->Destroy();return false;}
        Created.Add(Debris);Masses.Add(Mass);
    }
    double TotalMass=0;for(double Mass:Masses)TotalMass+=Mass;
    // No invented launch energy: only a bounded fraction of this hit's normal impulse.
    // Slow yielding gives smaller departures, and gravity/impact position create the rotation.
    const double MaximumSpeed=FMath::Clamp(NormalSpeedMps*.35+.15,.2,1.5);
    const double Momentum=FMath::Min(ImpulseNs*.25,TotalMass*MaximumSpeed);
    const FVector Direction=IntoObstacleWorld.GetSafeNormal2D();
    if(Direction.IsNearlyZero()){for(auto* A:Created)A->Destroy();return false;}
    RemaindersOut.Add(Remainder);
    for(int32 I=0;I<Masses.Num();++I)
    {
        AActor* Debris=Created[I+1];
        auto* Body=CastChecked<UProceduralMeshComponent>(Debris->GetRootComponent());
        const FVector AppliedImpulse=Direction*(Momentum*Masses[I]/TotalMass*100.0);
        const FVector ApplicationPoint=Body->Bounds.GetBox().GetClosestPointTo(HitWorld);
        Body->AddImpulseAtLocation(AppliedImpulse,ApplicationPoint);
        DebrisOut.Add(Debris);
    }
    UE_LOG(LogTemp,Display,TEXT("WT_WOOD_GEOMETRY mesh=%s source_members=%d fixed_members=%d fragments=%d momentum_Ns=%.4f"),
        *Source->GetName(),Parts.Num(),Fixed.Num(),Fragments.Num(),Momentum);
    return true;
}
