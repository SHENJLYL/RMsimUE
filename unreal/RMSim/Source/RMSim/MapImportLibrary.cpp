#include "MapImportLibrary.h"
#include "WTGeneratedCollisionComponent.h"
#include "Engine/Texture2DArray.h"
#include "Engine/Texture2D.h"
#if WITH_EDITOR
#include "AssetCompilingManager.h"
#endif
#include "RosCoordinates.h"
#include "VehicleRig.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "Engine/StaticMesh.h"
#include "StaticMeshResources.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "Landscape.h"
#include "LandscapeLayerInfoObject.h"
#include "Materials/MaterialInterface.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "PhysicsEngine/BodySetup.h"
#include "PhysicsEngine/ConvexElem.h"

namespace
{
TSharedPtr<FJsonObject> ReadDescription(const FString& Filename,FString& Error)
{
    FString Text;TSharedPtr<FJsonObject> Object;
    const int64 Size=IFileManager::Get().FileSize(*Filename);
    if(Size<0 || Size>16*1024*1024 || !FFileHelper::LoadFileToString(Text,*Filename) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Object) || !Object.IsValid())
    { Error=TEXT("invalid map import description");return nullptr; }
    return Object;
}
bool LocalFile(const FString& Description,const FString& Relative,FString& Result)
{
    if(!FPaths::IsRelative(Relative) || Relative.Contains(TEXT("..")))return false;
    const FString Root=FPaths::ConvertRelativePathToFull(FPaths::GetPath(Description));
    Result=FPaths::ConvertRelativePathToFull(Root,Relative);
    return FPaths::IsUnderDirectory(Result,Root);
}
bool AssetPath(const FString& Path) { return Path.StartsWith(TEXT("/Game/RMSim/Maps/")) && !Path.Contains(TEXT("..")); }
}

bool UMapImportLibrary::SetConvexCollision(UStaticMesh* Mesh,const TArray<FVector>& Vertices,FString& Error)
{
#if WITH_EDITOR
    if(!Mesh||!Mesh->GetPathName().StartsWith(TEXT("/Game/RMSim/Collision/"))||Vertices.Num()<4||Vertices.Num()>1024)
    {Error=TEXT("convex collision needs a project collision mesh and4..1024 vertices");return false;}
    for(const FVector& V:Vertices)if(V.ContainsNaN()||V.GetAbsMax()>10000)
    {Error=TEXT("invalid convex vertex");return false;}
    Mesh->Modify();Mesh->CreateBodySetup();auto* Body=Mesh->GetBodySetup();Body->Modify();
    Body->RemoveSimpleCollision();FKConvexElem Hull;Hull.VertexData=Vertices;Hull.UpdateElemBox();
    Body->AggGeom.ConvexElems.Add(MoveTemp(Hull));Body->CollisionTraceFlag=CTF_UseSimpleAsComplex;
    Body->InvalidatePhysicsData();Body->CreatePhysicsMeshes();Mesh->PostEditChange();Mesh->MarkPackageDirty();
    return true;
#else
    Error=TEXT("collision preparation requires the editor");return false;
#endif
}

FString UMapImportLibrary::DescribeConvexCollision(UStaticMesh* Mesh)
{
    if(!Mesh||!Mesh->GetBodySetup())return FString();
    auto Report=MakeShared<FJsonObject>();auto* Body=Mesh->GetBodySetup();
    Report->SetNumberField(TEXT("hull_count"),Body->AggGeom.ConvexElems.Num());
    Report->SetNumberField(TEXT("trace_flag"),static_cast<int32>(Body->CollisionTraceFlag));
    Report->SetNumberField(TEXT("primitive_count"),Body->AggGeom.GetElementCount());
    auto Vec=[](const FVector& V){return TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)};};
    TArray<TSharedPtr<FJsonValue>> Hulls;
    for(const FKConvexElem& Hull:Body->AggGeom.ConvexElems)
    {
        auto Item=MakeShared<FJsonObject>();TArray<TSharedPtr<FJsonValue>> Points;FBox Bounds(ForceInit);
        for(const FVector& V:Hull.VertexData)
        {const FVector P=Hull.GetTransform().TransformPosition(V);Bounds+=P;Points.Add(MakeShared<FJsonValueArray>(Vec(P)));}
        Item->SetArrayField(TEXT("vertices_cm"),Points);auto Box=MakeShared<FJsonObject>();
        Box->SetArrayField(TEXT("lo"),Vec(Bounds.Min));Box->SetArrayField(TEXT("hi"),Vec(Bounds.Max));Item->SetObjectField(TEXT("bounds_cm"),Box);
        Hulls.Add(MakeShared<FJsonValueObject>(Item));
    }
    Report->SetArrayField(TEXT("hulls"),Hulls);FString Result;
    FJsonSerializer::Serialize(Report,TJsonWriterFactory<>::Create(&Result));return Result;
}

ALandscape* UMapImportLibrary::ImportHeightfield(UObject* WorldContextObject,const FString& DescriptionFile,FString& Error)
{
#if WITH_EDITOR
    UWorld* World=GEngine->GetWorldFromContextObject(WorldContextObject,EGetWorldErrorMode::ReturnNull);
    const auto Spec=ReadDescription(DescriptionFile,Error);
    if(!World || World->IsGameWorld() || !Spec)return nullptr;
    double WidthValue=0,HeightValue=0,Cell=0,Low=0,Range=0;FString Relative,Filename,MaterialPath;
    const TArray<TSharedPtr<FJsonValue>>* OriginValues=nullptr;
    if(!Spec->TryGetNumberField(TEXT("width"),WidthValue)||!Spec->TryGetNumberField(TEXT("height"),HeightValue)||
        !FMath::IsFinite(WidthValue)||!FMath::IsFinite(HeightValue)||WidthValue<2||HeightValue<2||WidthValue>8192||HeightValue>8192||
        WidthValue!=FMath::Floor(WidthValue)||HeightValue!=FMath::Floor(HeightValue)||
        !Spec->TryGetNumberField(TEXT("cell_m"),Cell)||!FMath::IsFinite(Cell)||Cell<=0||Cell>100||
        !Spec->TryGetNumberField(TEXT("minimum_m"),Low)||!FMath::IsFinite(Low)||
        !Spec->TryGetNumberField(TEXT("range_m"),Range)||!FMath::IsFinite(Range)||Range<=0||Range>10000||
        !Spec->TryGetStringField(TEXT("height_file"),Relative)||!LocalFile(DescriptionFile,Relative,Filename)||
        !Spec->TryGetStringField(TEXT("material"),MaterialPath)||!AssetPath(MaterialPath)||
        !Spec->TryGetArrayField(TEXT("origin_ros_m"),OriginValues)||OriginValues->Num()!=3)
    { Error=TEXT("invalid heightfield metadata");return nullptr; }
    FVector Origin;
    for(int32 I=0;I<3;++I)if(!(*OriginValues)[I]->TryGetNumber(Origin[I])||!FMath::IsFinite(Origin[I]))
    { Error=TEXT("invalid heightfield origin");return nullptr; }
    UMaterialInterface* Material=LoadObject<UMaterialInterface>(nullptr,*MaterialPath);
    TArray<uint8> Raw;const int32 Width=static_cast<int32>(WidthValue),Height=static_cast<int32>(HeightValue);
    if(!Material||!FFileHelper::LoadFileToArray(Raw,*Filename)||Raw.Num()!=static_cast<int64>(Width)*Height*2)
    { Error=TEXT("missing heightfield material or invalid uint16 sample file");return nullptr; }
    constexpr int32 SectionQuads=127;
    const int32 PaddedW=FMath::DivideAndRoundUp(Width,SectionQuads)*SectionQuads+1;
    const int32 PaddedH=FMath::DivideAndRoundUp(Height,SectionQuads)*SectionQuads+1;
    TArray<uint16> Heights;TArray<uint8> Visibility;
    Heights.SetNumUninitialized(PaddedW*PaddedH);Visibility.SetNumUninitialized(PaddedW*PaddedH);
    for(int32 Y=0;Y<PaddedH;++Y)for(int32 X=0;X<PaddedW;++X)
    {
        const int32 Source=(FMath::Min(Y,Height-1)*Width+FMath::Min(X,Width-1))*2;
        Heights[Y*PaddedW+X]=static_cast<uint16>(Raw[Source]|(Raw[Source+1]<<8));
        // Preserve every original sample. Extra UE section padding is hidden and has no fine-terrain collision.
        Visibility[Y*PaddedW+X]=(X>Width || Y>Height)?255:0;
    }
    ALandscape* Landscape=World->SpawnActor<ALandscape>();
    if(!Landscape || !ALandscapeProxy::VisibilityLayer)
    { Error=TEXT("landscape editor resources unavailable");if(Landscape)Landscape->Destroy();return nullptr; }
    FVector Location=RosCoordinates::Position(Origin);
    Location.Z+=(Low+32768.0*Range/65535.0)*100.0;
    // Source rows follow increasing UE Y; the source-to-ROS origin was already authored by the converter.
    Landscape->SetActorLocation(Location);
    Landscape->SetActorScale3D(FVector(Cell*100,Cell*100,Range*100.0*128.0/65535.0));
    Landscape->LandscapeMaterial=Material;Landscape->LandscapeHoleMaterial=Material;
    Landscape->CollisionMipLevel=0;Landscape->SimpleCollisionMipLevel=0;Landscape->StaticLightingLOD=2;
    TMap<FGuid,TArray<uint16>> HeightLayers;HeightLayers.Add(FGuid(),MoveTemp(Heights));
    FLandscapeImportLayerInfo Mask(FName(TEXT("Visibility")));Mask.LayerInfo=ALandscapeProxy::VisibilityLayer;
    Mask.LayerData=MoveTemp(Visibility);
    TArray<FLandscapeImportLayerInfo> Masks;Masks.Add(MoveTemp(Mask));
    TMap<FGuid,TArray<FLandscapeImportLayerInfo>> WeightLayers;WeightLayers.Add(FGuid(),MoveTemp(Masks));
    TArray<FLandscapeLayer> EditLayers;
    Landscape->Import(FGuid::NewGuid(),0,0,PaddedW-1,PaddedH-1,1,SectionQuads,HeightLayers,*Filename,
        WeightLayers,ELandscapeImportAlphamapType::Additive,EditLayers);
    Landscape->SetActorLabel(TEXT("RMSim_SourceHeightfield"));Landscape->PostEditChange();Landscape->MarkPackageDirty();
    UE_LOG(LogRMSim,Display,TEXT("MAP_HEIGHTFIELD_IMPORTED samples=%dx%d sections=%dx%d cell_m=%.6f"),Width,Height,
        (PaddedW-1)/SectionQuads,(PaddedH-1)/SectionQuads,Cell);
    return Landscape;
#else
    Error=TEXT("heightfield authoring requires the editor; runtime loads the saved map");return nullptr;
#endif
}

int32 UMapImportLibrary::ImportInstances(UObject* WorldContextObject,const FString& DescriptionFile,FString& Error)
{
#if WITH_EDITOR
    UWorld* World=GEngine->GetWorldFromContextObject(WorldContextObject,EGetWorldErrorMode::ReturnNull);
    const auto Spec=ReadDescription(DescriptionFile,Error);const TArray<TSharedPtr<FJsonValue>>* Parts=nullptr;
    if(!World || World->IsGameWorld() || !Spec || !Spec->TryGetArrayField(TEXT("parts"),Parts) || Parts->Num()>8192)
    { Error=TEXT("invalid instance import world/parts");return -1; }
    int32 Total=0;
    for(const auto& Value:*Parts)
    {
        const auto Part=Value->AsObject();FString Name,VisualPath,CollisionPath,Relative,File;
        if(!Part || !Part->TryGetStringField(TEXT("id"),Name)||!Part->TryGetStringField(TEXT("visual_asset"),VisualPath)||
            !Part->TryGetStringField(TEXT("collision_asset"),CollisionPath)||!AssetPath(VisualPath)||
            (!CollisionPath.IsEmpty()&&!AssetPath(CollisionPath))||!Part->TryGetStringField(TEXT("instances_file"),Relative)||
            !LocalFile(DescriptionFile,Relative,File))
        { Error=TEXT("invalid instanced part");return -1; }
        UStaticMesh* Visual=LoadObject<UStaticMesh>(nullptr,*VisualPath);
        UStaticMesh* Collision=CollisionPath.IsEmpty()?nullptr:LoadObject<UStaticMesh>(nullptr,*CollisionPath);
        TArray<uint8> Raw;
        if(!Visual||(!CollisionPath.IsEmpty()&&!Collision)||!FFileHelper::LoadFileToArray(Raw,*File)||Raw.Num()<8||
            FMemory::Memcmp(Raw.GetData(),"RMI1",4)!=0)
        { Error=TEXT("missing map mesh or invalid instance stream");return -1; }
        uint32 Count=0;FMemory::Memcpy(&Count,Raw.GetData()+4,4);
        if(Count>1000000 || static_cast<int64>(Count)*40+8!=Raw.Num() || Total+Count>2000000)
        { Error=TEXT("invalid/beyond-limit instance count");return -1; }
        TArray<FTransform> Transforms;Transforms.Reserve(Count);
        for(uint32 I=0;I<Count;++I)
        {
            float F[10];FMemory::Memcpy(F,Raw.GetData()+8+I*40,40);
            for(float V:F)if(!FMath::IsFinite(V)){Error=TEXT("nonfinite instance transform");return -1;}
            const FQuat Q(-F[3],F[4],-F[5],F[6]);
            if(FMath::Abs(Q.SizeSquared()-1)>0.001 || F[7]<=0 || F[8]<=0 || F[9]<=0)
            { Error=TEXT("invalid instance rotation/scale");return -1; }
            Transforms.Emplace(Q.GetNormalized(),RosCoordinates::Position(FVector(F[0],F[1],F[2])),FVector(F[7],F[8],F[9]));
        }
        AActor* Actor=World->SpawnActor<AActor>();
        USceneComponent* Root=NewObject<USceneComponent>(Actor);Root->SetMobility(EComponentMobility::Static);
        Actor->SetRootComponent(Root);Actor->AddInstanceComponent(Root);Root->RegisterComponent();Actor->SetActorLabel(Name);
        for(bool Blocking:{false,true})
        {
            if(Blocking&&!Collision)continue;
            auto* Component=NewObject<UHierarchicalInstancedStaticMeshComponent>(Actor);
            Component->SetupAttachment(Root);Component->SetMobility(EComponentMobility::Static);
            Component->SetStaticMesh(Blocking?Collision:Visual);Component->SetCollisionProfileName(Blocking?TEXT("BlockAll"):TEXT("NoCollision"));
            Component->SetCollisionEnabled(Blocking?ECollisionEnabled::QueryAndPhysics:ECollisionEnabled::NoCollision);
            Component->SetHiddenInGame(Blocking);Component->SetVisibility(!Blocking);Component->SetCastShadow(!Blocking);
            Component->SetCanEverAffectNavigation(false);Component->bAutoRebuildTreeOnInstanceChanges=false;
            Actor->AddInstanceComponent(Component);Component->RegisterComponent();
            Component->AddInstances(Transforms,false,false,false);Component->BuildTreeIfOutdated(false,true);
        }
        Actor->MarkPackageDirty();Total+=Count;
    }
    UE_LOG(LogRMSim,Display,TEXT("MAP_INSTANCES_IMPORTED parts=%d instances=%d"),Parts->Num(),Total);
    return Total;
#else
    Error=TEXT("instance authoring requires the editor; runtime loads the saved map");return -1;
#endif
}

// Source payloads use full-precision UVs: leaf offsets in UV3/UV4 and
// landmesh height-decal opacity in UV1.x. Edit derived project assets only.
bool UMapImportLibrary::PrepareSourceLeafMesh(UStaticMesh* Mesh,FString& Error)
{
    return PrepareSourcePrecisionMesh(Mesh,5,Error);
}

bool UMapImportLibrary::PrepareSourcePrecisionMesh(UStaticMesh* Mesh,int32 ExpectedUVChannels,FString& Error)
{
    Error.Reset();
#if WITH_EDITOR
    if(ExpectedUVChannels<1 || ExpectedUVChannels>8 || !Mesh || !AssetPath(Mesh->GetPathName()) ||
        Mesh->GetNumSourceModels()!=1 || Mesh->GetNumUVChannels(0)!=ExpectedUVChannels)
    {Error=TEXT("source payload mesh requires one LOD and its declared UV channel count");return false;}
    auto& Settings=Mesh->GetSourceModel(0).BuildSettings;
    if(!Settings.bUseFullPrecisionUVs || Settings.bRemoveDegenerates)
    {
        Mesh->Modify();Settings.bUseFullPrecisionUVs=true;Settings.bRemoveDegenerates=false;
        TArray<FText> Errors;Mesh->Build(false,&Errors);
        if(!Errors.IsEmpty())
        {
            Error=TEXT("source payload mesh build reported: ");
            for(const FText& Message:Errors)Error+=Message.ToString()+TEXT("; ");
            return false;
        }
        Mesh->MarkPackageDirty();
    }
    if(!Mesh->GetRenderData() || Mesh->GetRenderData()->LODResources.IsEmpty() ||
        Mesh->GetNumUVChannels(0)!=ExpectedUVChannels || Mesh->GetNumTriangles(0)<=0 ||
        !Mesh->GetRenderData()->LODResources[0].VertexBuffers.StaticMeshVertexBuffer.GetUseFullPrecisionUVs())
    {Error=TEXT("native source payload mesh does not retain its full-precision UV contract");return false;}
    return true;
#else
    Error=TEXT("source payload authoring requires the editor");return false;
#endif
}

FString UMapImportLibrary::DescribeMapMesh(UStaticMesh* Mesh)
{
    if(!Mesh || !AssetPath(Mesh->GetPathName()) || !Mesh->GetRenderData() || Mesh->GetRenderData()->LODResources.IsEmpty())return FString();
    const auto& Lod=Mesh->GetRenderData()->LODResources[0];
    auto Report=MakeShared<FJsonObject>();
    Report->SetNumberField(TEXT("triangles"),Mesh->GetNumTriangles(0));
    Report->SetNumberField(TEXT("uv_channels"),Lod.VertexBuffers.StaticMeshVertexBuffer.GetNumTexCoords());
    Report->SetBoolField(TEXT("full_precision_uvs"),Lod.VertexBuffers.StaticMeshVertexBuffer.GetUseFullPrecisionUVs());
    TArray<TSharedPtr<FJsonValue>> Sections;
    for(const auto& Section:Lod.Sections)
    {
        auto Entry=MakeShared<FJsonObject>();
        Entry->SetNumberField(TEXT("triangles"),Section.NumTriangles);
        Entry->SetNumberField(TEXT("material_index"),Section.MaterialIndex);
        Entry->SetStringField(TEXT("material_slot"),Mesh->GetStaticMaterials().IsValidIndex(Section.MaterialIndex)?
            Mesh->GetStaticMaterials()[Section.MaterialIndex].MaterialSlotName.ToString():TEXT("INVALID"));
        Sections.Add(MakeShared<FJsonValueObject>(Entry));
    }
    Report->SetArrayField(TEXT("sections"),Sections);
    FString Result;FJsonSerializer::Serialize(Report,TJsonWriterFactory<>::Create(&Result));return Result;
}

bool UMapImportLibrary::PrepareGeneratedCollision(UStaticMesh* Mesh,const FString& Mode,
    int32 ExpectedTriangles,const FString& SourceFbxSha256,const FString& RecipeSha256,
    const TArray<FVector>& ConvexVerticesCm,FString& Error)
{
    Error.Reset();
#if WITH_EDITOR
    auto IsHash=[](const FString& Value)
    {
        if(Value.Len()!=64)return false;
        for(TCHAR C:Value)if(!((C>='0'&&C<='9')||(C>='a'&&C<='f')))return false;
        return true;
    };
    if(!Mesh||!Mesh->GetPathName().StartsWith(TEXT("/Game/RMSim/Maps/wt_vlaanderen/GeneratedCollision/"))||
       !IsHash(SourceFbxSha256)||!IsHash(RecipeSha256)||ExpectedTriangles<=0||ExpectedTriangles>500000||
       (Mode!=TEXT("triangle_bvh")&&Mode!=TEXT("convex")))
    {Error=TEXT("invalid generated collision preparation request");return false;}
    TArray<UObject*> Pending;Pending.Add(Mesh);
    FAssetCompilingManager::Get().FinishCompilationForObjects(Pending);
    if(Mesh->GetNumSourceModels()!=1||Mesh->GetNumTriangles(0)!=ExpectedTriangles)
    {Error=TEXT("generated source mesh LOD/triangle count differs");return false;}
    if((Mode==TEXT("convex")&&ConvexVerticesCm.Num()!=8)||
       (Mode==TEXT("triangle_bvh")&&!ConvexVerticesCm.IsEmpty()))
    {Error=TEXT("generated convex must use the eight source corners");return false;}
    for(const FVector& V:ConvexVerticesCm)
        if(V.ContainsNaN()||V.GetAbsMax()>100000.)
        {Error=TEXT("invalid generated convex point");return false;}
    Mesh->Modify();Mesh->CreateBodySetup();
    UBodySetup* Body=Mesh->GetBodySetup();Body->Modify();
    Body->RemoveSimpleCollision();
    Body->bMeshCollideAll=true;Body->bDoubleSidedGeometry=true;
    Body->CollisionTraceFlag=Mode==TEXT("convex")?CTF_UseSimpleAsComplex:CTF_UseComplexAsSimple;
    if(Mode==TEXT("convex"))
    {
        FKConvexElem Hull;Hull.VertexData=ConvexVerticesCm;Hull.UpdateElemBox();
        Body->AggGeom.ConvexElems.Add(MoveTemp(Hull));
    }
    Body->InvalidatePhysicsData();
    Mesh->RemoveUserDataOfClass(UWTGeneratedCollisionData::StaticClass());
    auto* Data=NewObject<UWTGeneratedCollisionData>(Mesh);
    Data->SourceFbxSha256=SourceFbxSha256;Data->RecipeSha256=RecipeSha256;
    Data->CollisionMode=Mode;Data->SourceTriangles=ExpectedTriangles;
    Mesh->AddAssetUserData(Data);
    Mesh->PostEditChange();
    FAssetCompilingManager::Get().FinishCompilationForObjects(Pending);
    Body=Mesh->GetBodySetup();Body->CreatePhysicsMeshes();
    if(!UWTGeneratedCollisionComponent::ValidateMesh(Mesh,Error))return false;
    Mesh->MarkPackageDirty();return true;
#else
    Error=TEXT("generated collision authoring requires the editor");return false;
#endif
}

FString UMapImportLibrary::DescribeGeneratedCollision(UStaticMesh* Mesh)
{
    FString Error;auto J=MakeShared<FJsonObject>();
    const bool Valid=UWTGeneratedCollisionComponent::ValidateMesh(Mesh,Error);
    J->SetBoolField(TEXT("valid"),Valid);J->SetStringField(TEXT("error"),Error);
    if(Mesh)
    {
        J->SetStringField(TEXT("asset"),Mesh->GetPathName());
        J->SetNumberField(TEXT("triangles"),Mesh->GetNumTriangles(0));
        if(const auto* Data=Cast<UWTGeneratedCollisionData>(
            Mesh->GetAssetUserDataOfClass(UWTGeneratedCollisionData::StaticClass())))
        {
            J->SetStringField(TEXT("source_fbx_sha256"),Data->SourceFbxSha256);
            J->SetStringField(TEXT("recipe_sha256"),Data->RecipeSha256);
            J->SetStringField(TEXT("mode"),Data->CollisionMode);
        }
        if(const UBodySetup* Body=Mesh->GetBodySetup())
        {
            J->SetNumberField(TEXT("triangle_bvhs"),Body->TriMeshGeometries.Num());
            J->SetNumberField(TEXT("convex_hulls"),Body->AggGeom.ConvexElems.Num());
            J->SetNumberField(TEXT("simple_shapes"),Body->AggGeom.GetElementCount());
            J->SetNumberField(TEXT("trace_flag"),static_cast<int32>(Body->GetCollisionTraceFlag()));
            J->SetBoolField(TEXT("cooked"),Body->bCreatedPhysicsMeshes&&!Body->bFailedToCreatePhysicsMeshes);
        }
    }
    FString Text;FJsonSerializer::Serialize(J,TJsonWriterFactory<>::Create(&Text));return Text;
}

UWTGeneratedCollisionComponent* UMapImportLibrary::CreateGeneratedCollisionAudit(
    UObject* WorldContextObject,const FString& ManifestFile,const FVector& AnchorWorldCm,
    double QueryRangeMetres,FString& Error)
{
    UWorld* World=GEngine->GetWorldFromContextObject(WorldContextObject,EGetWorldErrorMode::ReturnNull);
    if(!World){Error=TEXT("generated collision audit needs a world");return nullptr;}
    FActorSpawnParameters Params;Params.ObjectFlags|=RF_Transient;
    AActor* Actor=World->SpawnActor<AActor>(FVector::ZeroVector,FRotator::ZeroRotator,Params);
    if(!Actor){Error=TEXT("cannot create generated collision audit actor");return nullptr;}
    auto* Component=NewObject<UWTGeneratedCollisionComponent>(Actor);
    Actor->AddInstanceComponent(Component);Component->RegisterComponent();
    if(!Component->InitializeAudit(ManifestFile,AnchorWorldCm,QueryRangeMetres,Error))
    {Actor->Destroy();return nullptr;}
    return Component;
}

FString UMapImportLibrary::DescribeMapTextureArray(UTexture2DArray* Texture)
{
    if(!Texture)return FString();
#if WITH_EDITOR
    TArray<UObject*> Pending;Pending.Add(Texture);
    FAssetCompilingManager::Get().FinishCompilationForObjects(Pending);
#endif
    auto J=MakeShared<FJsonObject>();
    J->SetNumberField(TEXT("width"),Texture->GetSizeX());
    J->SetNumberField(TEXT("height"),Texture->GetSizeY());
    J->SetNumberField(TEXT("slices"),Texture->GetArraySize());
    J->SetNumberField(TEXT("mips"),Texture->GetNumMips());
    TArray<TSharedPtr<FJsonValue>> Sources;
#if WITH_EDITORONLY_DATA
    J->SetNumberField(TEXT("source_width"),Texture->Source.GetSizeX());
    J->SetNumberField(TEXT("source_height"),Texture->Source.GetSizeY());
    J->SetNumberField(TEXT("source_slices"),Texture->Source.GetNumSlices());
    for(const UTexture2D* Source:Texture->SourceTextures)
        Sources.Add(MakeShared<FJsonValueString>(Source?Source->GetPathName():FString()));
#else
    J->SetNumberField(TEXT("source_width"),0);
    J->SetNumberField(TEXT("source_height"),0);
    J->SetNumberField(TEXT("source_slices"),0);
#endif
    J->SetArrayField(TEXT("source_textures"),Sources);
    FString Text;FJsonSerializer::Serialize(J,TJsonWriterFactory<>::Create(&Text));return Text;
}
