#include "WTGeneratedCollisionComponent.h"
#include "VehicleRig.h"
#include "RosCoordinates.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "PhysicsEngine/BodySetup.h"
#include "PhysicsEngine/BodyInstance.h"
#include "Physics/PhysicsInterfaceCore.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "HAL/FileManager.h"

namespace
{
const FName GeneratedTag(TEXT("RMSimGeneratedCollision"));
const FString AssetPrefix(TEXT("/Game/RMSim/Maps/wt_vlaanderen/GeneratedCollision/"));
bool Hex(const FString& Value, int32 Length)
{
    if(Value.Len()!=Length)return false;
    for(TCHAR C:Value)if(!((C>='0'&&C<='9')||(C>='a'&&C<='f')))return false;
    return true;
}
FString Sha1(const TArray<uint8>& Bytes)
{
    FSHAHash Hash;FSHA1::HashBuffer(Bytes.GetData(),Bytes.Num(),Hash.Hash);
    return Hash.ToString().ToLower();
}
bool ReadJson(const FString& File,TSharedPtr<FJsonObject>& Out,TArray<uint8>& Raw,FString& Error)
{
    const int64 Size=IFileManager::Get().FileSize(*File);
    FString Text;
    if(Size<=0||Size>16*1024*1024||!FFileHelper::LoadFileToArray(Raw,*File))
    {Error=TEXT("generated collision JSON missing or beyond size limit");return false;}
    FFileHelper::BufferToString(Text,Raw.GetData(),Raw.Num());
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Out)||!Out)
    {Error=TEXT("invalid generated collision JSON");return false;}
    return true;
}
bool Local(const FString& Root,const FString& Relative,FString& Out)
{
    if(!Relative.StartsWith(TEXT("instances/"))||!Relative.EndsWith(TEXT(".rmi"))||
       Relative.Contains(TEXT(".."))||Relative.Contains(TEXT(":"))||Relative.Contains(TEXT("\\")))return false;
    Out=FPaths::ConvertRelativePathToFull(FPaths::Combine(Root,Relative));
    return FPaths::IsUnderDirectory(Out,FPaths::ConvertRelativePathToFull(Root));
}
bool Number(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,double& Value,double Min,double Max)
{
    return J&&J->TryGetNumberField(Key,Value)&&FMath::IsFinite(Value)&&Value>=Min&&Value<=Max;
}
bool Vector(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,FVector& Value)
{
    const TArray<TSharedPtr<FJsonValue>>* A=nullptr;
    if(!J||!J->TryGetArrayField(Key,A)||A->Num()!=3)return false;
    for(int32 I=0;I<3;++I)
        if(!(*A)[I]->TryGetNumber(Value[I])||!FMath::IsFinite(Value[I])||FMath::Abs(Value[I])>100000)return false;
    return true;
}
double Distance2D(const FBox& B,const FVector& P)
{
    const double X=FMath::Max3(B.Min.X-P.X,0.,P.X-B.Max.X);
    const double Y=FMath::Max3(B.Min.Y-P.Y,0.,P.Y-B.Max.Y);
    return FMath::Sqrt(X*X+Y*Y);
}
}

UWTGeneratedCollisionComponent::UWTGeneratedCollisionComponent()
{
    PrimaryComponentTick.bCanEverTick=false;
}

bool UWTGeneratedCollisionComponent::ValidateMesh(UStaticMesh* Mesh,FString& Error)
{
    if(!Mesh||!Mesh->GetPathName().StartsWith(AssetPrefix))
    {Error=TEXT("generated collision asset outside dedicated folder");return false;}
    const auto* Data=Cast<UWTGeneratedCollisionData>(
        Mesh->GetAssetUserDataOfClass(UWTGeneratedCollisionData::StaticClass()));
    const UBodySetup* Body=Mesh->GetBodySetup();
    if(!Data||!Body||!Hex(Data->SourceFbxSha256,64)||!Hex(Data->RecipeSha256,64)||
       Data->SourceTriangles<=0||Mesh->GetNumTriangles(0)!=Data->SourceTriangles||
       !Body->bCreatedPhysicsMeshes||Body->bFailedToCreatePhysicsMeshes)
    {Error=TEXT("generated collision identity or cooked data invalid");return false;}
    if(Data->CollisionMode==TEXT("triangle_bvh"))
    {
        if(Body->GetCollisionTraceFlag()!=CTF_UseComplexAsSimple||
           Body->TriMeshGeometries.Num()!=1||Body->AggGeom.GetElementCount()!=0||
           !Body->bDoubleSidedGeometry)
        {Error=TEXT("generated triangle proxy must have one shared BVH shape");return false;}
    }
    else if(Data->CollisionMode==TEXT("convex"))
    {
        if(Body->GetCollisionTraceFlag()!=CTF_UseSimpleAsComplex||
           Body->AggGeom.ConvexElems.Num()!=1||Body->AggGeom.GetElementCount()!=1||
           Body->TriMeshGeometries.Num()!=0)
        {Error=TEXT("generated convex proxy must have one source convex shape");return false;}
    }
    else {Error=TEXT("unknown generated collision mode");return false;}
    return true;
}

bool UWTGeneratedCollisionComponent::Load(const FString& ManifestFile,const FString& ExpectedSource,FString& Error)
{
    if(bLoaded||!GetWorld()||!GetOwner())
    {Error=TEXT("generated collision initialized twice or without world");return false;}
    const double Started=FPlatformTime::Seconds();
    TArray<uint8> Raw;TSharedPtr<FJsonObject> Root;
    if(!ReadJson(ManifestFile,Root,Raw,Error))return false;
    FString Schema,Id,Units,Coordinates;double Total=0;
    const TArray<TSharedPtr<FJsonValue>> *MeshValues=nullptr,*CellValues=nullptr;
    if(!Root->TryGetStringField(TEXT("schema"),Schema)||Schema!=TEXT("rm_generated_static_collision_v1")||
       !Root->TryGetStringField(TEXT("map_id"),Id)||Id!=TEXT("wt_vlaanderen")||
       !Root->TryGetStringField(TEXT("source_sha256"),MapSourceSha256)||!Hex(MapSourceSha256,64)||
       (!ExpectedSource.IsEmpty()&&ExpectedSource!=MapSourceSha256)||
       !Root->TryGetStringField(TEXT("recipe_sha256"),RecipeSha256)||!Hex(RecipeSha256,64)||
       !Root->TryGetStringField(TEXT("units"),Units)||Units!=TEXT("m")||
       !Root->TryGetStringField(TEXT("coordinate_system"),Coordinates)||Coordinates!=TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP")||
       !Number(Root,TEXT("source_instances"),Total,1,1000000)||Total!=FMath::FloorToDouble(Total)||
       !Root->TryGetArrayField(TEXT("meshes"),MeshValues)||MeshValues->IsEmpty()||MeshValues->Num()>4096||
       !Root->TryGetArrayField(TEXT("cells"),CellValues)||CellValues->IsEmpty()||CellValues->Num()>16384)
    {Error=TEXT("invalid generated collision contract");return false;}
    ManifestSha1=Sha1(Raw);SourceInstances=static_cast<int64>(Total);
    TMap<FString,int32> Lookup;
    for(const auto& Entry:*MeshValues)
    {
        auto J=Entry->AsObject();FString IdValue,Path,Mode,FbxHash;double Triangles=0;
        if(!J||!J->TryGetStringField(TEXT("id"),IdValue)||Lookup.Contains(IdValue)||
           !J->TryGetStringField(TEXT("asset"),Path)||!Path.StartsWith(AssetPrefix)||Path.Contains(TEXT(".."))||
           !J->TryGetStringField(TEXT("mode"),Mode)||!J->TryGetStringField(TEXT("sha256"),FbxHash)||!Hex(FbxHash,64)||
           !Number(J,TEXT("triangles"),Triangles,1,500000)||Triangles!=FMath::FloorToDouble(Triangles))
        {Error=TEXT("invalid generated collision mesh record");return false;}
        UStaticMesh* Mesh=LoadObject<UStaticMesh>(nullptr,*Path);
        if(!ValidateMesh(Mesh,Error))return false;
        const auto* Data=CastChecked<UWTGeneratedCollisionData>(
            Mesh->GetAssetUserDataOfClass(UWTGeneratedCollisionData::StaticClass()));
        if(Data->RecipeSha256!=RecipeSha256||Data->SourceFbxSha256!=FbxHash||
           Data->CollisionMode!=Mode||Data->SourceTriangles!=static_cast<int32>(Triangles))
        {Error=TEXT("generated collision asset is stale for its recipe/source");return false;}
        Lookup.Add(IdValue,Meshes.Num());Meshes.Add(Mesh);
    }
    int64 SourcesRead=0,PhysicalInstances=0;
    TSet<FString> Ids;
    for(const auto& Entry:*CellValues)
    {
        auto J=Entry->AsObject();FString MeshId,Relative,Hash,File;double Count=0,Sources=0;
        const TSharedPtr<FJsonObject>* Bounds=nullptr;FVector Lo,Hi;FCell Cell;
        if(!J||!J->TryGetStringField(TEXT("id"),Cell.Id)||Ids.Contains(Cell.Id)||
           !J->TryGetStringField(TEXT("mesh_id"),MeshId)||!Lookup.Contains(MeshId)||
           !J->TryGetStringField(TEXT("instances_file"),Relative)||!Local(FPaths::GetPath(ManifestFile),Relative,File)||
           !J->TryGetStringField(TEXT("sha1"),Hash)||!Hex(Hash,40)||
           !Number(J,TEXT("instances"),Count,1,100000)||Count!=FMath::FloorToDouble(Count)||
           !Number(J,TEXT("source_instances"),Sources,1,100000)||Sources!=FMath::FloorToDouble(Sources)||
           !J->TryGetObjectField(TEXT("bounds_m"),Bounds)||!Vector(*Bounds,TEXT("lo"),Lo)||!Vector(*Bounds,TEXT("hi"),Hi)||
           Hi.X<Lo.X||Hi.Y<Lo.Y||Hi.Z<Lo.Z)
        {Error=TEXT("invalid generated collision cell");return false;}
        TArray<uint8> Bytes;
        if(IFileManager::Get().FileSize(*File)!=static_cast<int64>(Count)*40+8||
           !FFileHelper::LoadFileToArray(Bytes,*File)||Sha1(Bytes)!=Hash||
           FMemory::Memcmp(Bytes.GetData(),"RMI1",4)!=0)
        {Error=TEXT("generated collision instance bytes/hash differ");return false;}
        uint32 N=0;FMemory::Memcpy(&N,Bytes.GetData()+4,4);
        if(N!=static_cast<uint32>(Count)){Error=TEXT("generated collision RMI count differs");return false;}
        Cell.MeshIndex=Lookup[MeshId];Cell.SourceInstances=static_cast<int32>(Sources);
        Cell.BoundsWorldCm=FBox(FVector(Lo.X,-Hi.Y,Lo.Z)*100.,FVector(Hi.X,-Lo.Y,Hi.Z)*100.);
        Cell.Transforms.Reserve(N);
        for(uint32 I=0;I<N;++I)
        {
            float V[10];FMemory::Memcpy(V,Bytes.GetData()+8+I*40,40);
            for(float X:V)if(!FMath::IsFinite(X)){Error=TEXT("nonfinite generated transform");return false;}
            FQuat Q(-V[3],V[4],-V[5],V[6]);
            if(FMath::Abs(Q.SizeSquared()-1)>0.001||V[7]<=0||V[8]<=0||V[9]<=0||
               V[7]>1000||V[8]>1000||V[9]>1000)
            {Error=TEXT("invalid generated transform rotation/scale");return false;}
            Cell.Transforms.Emplace(Q.GetNormalized(),RosCoordinates::Position(FVector(V[0],V[1],V[2])),FVector(V[7],V[8],V[9]));
        }
        SourcesRead+=Cell.SourceInstances;PhysicalInstances+=N;
        if(SourcesRead>1000000||PhysicalInstances>1000000)
        {Error=TEXT("generated collision budget exceeded");return false;}
        Ids.Add(Cell.Id);Cells.Add(MoveTemp(Cell));
    }
    if(SourcesRead!=SourceInstances){Error=TEXT("generated source coverage count differs");return false;}
    LoadRadiusCm=(FMath::Max(16.,QueryRangeM)+32.)*100.;
    RetainRadiusCm=LoadRadiusCm+32.*100.;
    bLoaded=true;InitialLoadSeconds=FPlatformTime::Seconds()-Started;
    return true;
}

bool UWTGeneratedCollisionComponent::Initialize(AVehicleRig* Vehicle,const FString& MapFile,
    const FString& SourceSha256,FString& Error)
{
    if(!IsValid(Vehicle)){Error=TEXT("generated collision needs the active vehicle");return false;}
    Rig=Vehicle;SpawnAnchor=Rig->GetActorLocation();LastPosition=SpawnAnchor;
    QueryRangeM=0;
    if(FParse::Param(FCommandLine::Get(),TEXT("RosLidar")))
    {
        FString Profile;
        if(!FParse::Value(FCommandLine::Get(),TEXT("LidarProfile="),Profile))
        {
            FString RigFile;
            if(!FParse::Value(FCommandLine::Get(),TEXT("MeshRigFile="),RigFile))
            {Error=TEXT("generated collision needs the active LiDAR profile");return false;}
            Profile=FPaths::Combine(FPaths::GetPath(RigFile),TEXT("sensors/lidar.json"));
        }
        TSharedPtr<FJsonObject> J;TArray<uint8> Raw;
        if(!ReadJson(Profile,J,Raw,Error)||!Number(J,TEXT("max_range_m"),QueryRangeM,.01,200))return false;
        const TSharedPtr<FJsonObject>* Origin=nullptr;FVector Offset;
        if(!J->TryGetObjectField(TEXT("origin"),Origin)||!Vector(*Origin,TEXT("xyz"),Offset))
        {Error=TEXT("generated collision cannot bound the LiDAR mount");return false;}
        QueryRangeM+=Offset.Size()+Rig->GetLocalVisualBounds().GetExtent().Size()/100.;
    }
    const FString Manifest=FPaths::Combine(FPaths::GetPath(MapFile),TEXT("generated-collision/manifest.json"));
    if(!Load(Manifest,SourceSha256,Error)||!Refresh(SpawnAnchor,true,Error))return false;
    UE_LOG(LogRMSim,Display,TEXT("WT_GENERATED_COLLISION_READY %s"),*Describe());
    return true;
}

bool UWTGeneratedCollisionComponent::InitializeAudit(const FString& ManifestFile,
    const FVector& AnchorWorldCm,double QueryRangeMetres,FString& Error)
{
    if(!FMath::IsFinite(QueryRangeMetres)||QueryRangeMetres<0||QueryRangeMetres>250||
       AnchorWorldCm.ContainsNaN())
    {Error=TEXT("invalid generated collision audit anchor/range");return false;}
    SpawnAnchor=AnchorWorldCm;LastPosition=AnchorWorldCm;QueryRangeM=QueryRangeMetres;
    return Load(ManifestFile,FString(),Error)&&Refresh(AnchorWorldCm,true,Error);
}

bool UWTGeneratedCollisionComponent::Advance(FString& Error)
{
    if(!bLoaded||!IsValid(Rig)){Error=TEXT("generated collision lost its active rig");return false;}
    return Refresh(Rig->GetActorLocation(),false,Error);
}

bool UWTGeneratedCollisionComponent::RefreshAtWorldPoint(const FVector& WorldCm,FString& Error)
{
    if(WorldCm.ContainsNaN()||WorldCm.GetAbsMax()>10000000.)
    {Error=TEXT("invalid generated collision world point");return false;}
    return Refresh(WorldCm,true,Error);
}

bool UWTGeneratedCollisionComponent::Refresh(const FVector& Position,bool Force,FString& Error)
{
    if(!bLoaded){Error=TEXT("generated collision contract not loaded");return false;}
    if(!Force&&FVector::DistSquared2D(Position,LastPosition)<FMath::Square(800.))return true;
    const double Started=FPlatformTime::Seconds();
    // Create the complete incoming footprint before releasing any outgoing body.
    for(FCell& Cell:Cells)
    {
        const bool Wanted=FMath::Min(Distance2D(Cell.BoundsWorldCm,Position),
            Distance2D(Cell.BoundsWorldCm,SpawnAnchor))<=LoadRadiusCm;
        if(!Wanted||Cell.Component.IsValid())continue;
        auto* C=NewObject<UHierarchicalInstancedStaticMeshComponent>(GetOwner());
        C->SetMobility(EComponentMobility::Static);
        C->SetStaticMesh(Meshes[Cell.MeshIndex]);
        C->SetCollisionProfileName(TEXT("BlockAll"));
        C->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        C->SetHiddenInGame(true);C->SetVisibility(false);C->SetCastShadow(false);
        C->SetCanEverAffectNavigation(false);
        C->bAutoRebuildTreeOnInstanceChanges=false;
        C->ComponentTags.Add(GeneratedTag);
        GetOwner()->AddInstanceComponent(C);C->RegisterComponent();
        C->AddInstances(Cell.Transforms,false,true,false);
        C->BuildTreeIfOutdated(false,true);
        Cell.Component=C;
        if(!C->IsPhysicsStateCreated()||C->GetInstanceCount()!=Cell.Transforms.Num())
        {Error=TEXT("generated collision failed synchronous body creation");return false;}
        for(int32 I=0;I<C->GetInstanceCount();++I)
        {
            const FBodyInstance* Body=C->GetBodyInstance(NAME_None,false,I);
            if(!Body||!Body->IsValidBodyInstance())
            {Error=TEXT("generated collision instance has no native physics body");return false;}
            int32 NativeShapeCount=0;
            FPhysicsCommand::ExecuteRead(Body->GetPhysicsActorHandle(),[&](const FPhysicsActorHandle&)
            {
                TArray<FPhysicsShapeHandle> Shapes;
                NativeShapeCount=Body->GetAllShapes_AssumesLocked(Shapes);
            });
            if(NativeShapeCount!=1)
            {Error=TEXT("generated body must contain exactly one native shape");return false;}
            ++Cell.NativeBodies;Cell.NativeShapes+=NativeShapeCount;
        }
        ++CellsCreated;
    }
    for(FCell& Cell:Cells)
    {
        if(!Cell.Component.IsValid())continue;
        const double Distance=FMath::Min(Distance2D(Cell.BoundsWorldCm,Position),
            Distance2D(Cell.BoundsWorldCm,SpawnAnchor));
        if(Distance>RetainRadiusCm)
        {Cell.Component->DestroyComponent();Cell.Component.Reset();Cell.NativeBodies=0;Cell.NativeShapes=0;++CellsReleased;}
    }
    LastPosition=Position;++ActivationPasses;
    int64 Bodies=0;for(const FCell& Cell:Cells)if(Cell.Component.IsValid())Bodies+=Cell.Component->GetInstanceCount();
    PeakBodies=FMath::Max(PeakBodies,Bodies);
    MaxRefreshSeconds=FMath::Max(MaxRefreshSeconds,FPlatformTime::Seconds()-Started);
    if(!IsReady()){Error=TEXT("generated collision active cell is not physics ready");return false;}
    return true;
}

bool UWTGeneratedCollisionComponent::IsReady() const
{
    if(!bLoaded)return false;
    for(const FCell& Cell:Cells)
        if(Cell.Component.IsValid()&&(!Cell.Component->IsRegistered()||!Cell.Component->IsPhysicsStateCreated()))return false;
    return true;
}

FString UWTGeneratedCollisionComponent::Describe() const
{
    auto J=MakeShared<FJsonObject>();
    J->SetStringField(TEXT("schema"),TEXT("rmsim_generated_collision_runtime_v1"));
    J->SetStringField(TEXT("manifest_sha1"),ManifestSha1);
    J->SetStringField(TEXT("recipe_sha256"),RecipeSha256);
    J->SetStringField(TEXT("source_sha256"),MapSourceSha256);
    J->SetNumberField(TEXT("source_instances"),SourceInstances);
    J->SetNumberField(TEXT("mesh_assets"),Meshes.Num());J->SetNumberField(TEXT("total_cells"),Cells.Num());
    int64 Bodies=0,Shapes=0,Sources=0;int32 Active=0;
    for(const FCell& C:Cells)if(C.Component.IsValid())
    {++Active;Bodies+=C.NativeBodies;Shapes+=C.NativeShapes;Sources+=C.SourceInstances;}
    J->SetNumberField(TEXT("active_cells"),Active);J->SetNumberField(TEXT("active_native_bodies"),Bodies);
    J->SetNumberField(TEXT("active_shapes"),Shapes);
    J->SetNumberField(TEXT("active_source_instances"),Sources);J->SetNumberField(TEXT("peak_native_bodies"),PeakBodies);
    J->SetNumberField(TEXT("query_range_with_mount_m"),QueryRangeM);
    J->SetNumberField(TEXT("load_radius_m"),LoadRadiusCm/100.);
    J->SetNumberField(TEXT("retain_radius_m"),RetainRadiusCm/100.);
    J->SetNumberField(TEXT("activation_passes"),ActivationPasses);
    J->SetNumberField(TEXT("cells_created"),CellsCreated);J->SetNumberField(TEXT("cells_released"),CellsReleased);
    J->SetNumberField(TEXT("initial_asset_load_seconds"),InitialLoadSeconds);
    J->SetNumberField(TEXT("max_refresh_seconds"),MaxRefreshSeconds);
    J->SetBoolField(TEXT("spawn_footprint_pinned"),true);J->SetBoolField(TEXT("physics_ready"),IsReady());
    FString Text;FJsonSerializer::Serialize(J,TJsonWriterFactory<>::Create(&Text));return Text;
}

void UWTGeneratedCollisionComponent::EndPlay(const EEndPlayReason::Type Reason)
{
    if(bLoaded)UE_LOG(LogRMSim,Display,TEXT("WT_GENERATED_COLLISION_FINAL %s"),*Describe());
    for(FCell& Cell:Cells)if(Cell.Component.IsValid())Cell.Component->DestroyComponent();
    Cells.Empty();Meshes.Empty();bLoaded=false;Super::EndPlay(Reason);
}
