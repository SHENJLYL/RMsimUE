#include "CampusGym.h"
#include "MapStage.h"
#include "RosCoordinates.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/RectLightComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/TextRenderComponent.h"
#include "Engine/RectLight.h"
#include "Engine/DirectionalLight.h"
#include "Engine/SkyLight.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/TextureCube.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/PackageName.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
using FObj=TSharedPtr<FJsonObject>;
bool Vec(const FObj& Obj,const TCHAR* Key,FVector& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* A=nullptr;
    if(!Obj.IsValid()||!Obj->TryGetArrayField(Key,A)||A->Num()!=3)return false;
    for(int32 I=0;I<3;++I)if(!(*A)[I]->TryGetNumber(Out[I])||!FMath::IsFinite(Out[I]))return false;
    return true;
}
bool CsvVector(const FString& Text,FVector& Out)
{
    TArray<FString> Parts;
    if(Text.ParseIntoArray(Parts,TEXT(","),false)!=3)return false;
    for(int32 I=0;I<3;++I)
        if(!LexTryParseString(Out[I],*Parts[I])||!FMath::IsFinite(Out[I]))return false;
    return true;
}
FTransform Transform(UStaticMesh* Mesh,const FVector& Centre,const FVector& Size,
                     const FVector& Rotation=FVector::ZeroVector)
{
    const FBox B=Mesh->GetBoundingBox();
    const FVector BoundsSize=B.GetSize();
    FVector Scale=FVector::OneVector;
    for(int32 I=0;I<3;++I)
        if(BoundsSize[I]>=1.e-6)Scale[I]=Size[I]*100.0/BoundsSize[I];
    const FQuat Q=RosCoordinates::Rotation(Rotation*(PI/180.0));
    return FTransform(Q,RosCoordinates::Position(Centre)-Q.RotateVector(B.GetCenter()*Scale),Scale);
}
struct FGroup
{
    UInstancedStaticMeshComponent* Component=nullptr;
    UStaticMesh* Mesh=nullptr;
    void Add(const FVector& Centre,const FVector& Size,const FVector& Rotation=FVector::ZeroVector)
    {Component->AddInstance(Transform(Mesh,Centre,Size,Rotation),true);}
};
FGroup NewGroup(AActor* Host,UStaticMesh* Mesh,UMaterialInterface* Material,
                const FString& Name,bool Shadow=false)
{
    auto* C=NewObject<UInstancedStaticMeshComponent>(Host,MakeUniqueObjectName(Host,UInstancedStaticMeshComponent::StaticClass(),FName(*Name)));
    C->SetupAttachment(Host->GetRootComponent());
    C->SetMobility(EComponentMobility::Static);
    C->SetStaticMesh(Mesh);
    if(Material)for(int32 I=0;I<FMath::Max(1,Mesh->GetStaticMaterials().Num());++I)C->SetMaterial(I,Material);
    C->SetCollisionProfileName(TEXT("NoCollision"));
    C->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    C->SetCanEverAffectNavigation(false);
    C->SetCastShadow(Shadow);
    C->SetReceivesDecals(false);
    Host->AddInstanceComponent(C);
    C->RegisterComponent();
    return {C,Mesh};
}
bool ReadJson(const FString& Path,FObj& Out,int64 MaxBytes)
{
    FString Text;
    return IFileManager::Get().FileSize(*Path)>0&&IFileManager::Get().FileSize(*Path)<=MaxBytes&&
        FFileHelper::LoadFileToString(Text,*Path)&&
        FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Out);
}
UTextRenderComponent* TextLabel(AActor* Host,const FString& Text,const FVector& Position,double Yaw,
               double Size,const FColor& Color)
{
    auto* C=NewObject<UTextRenderComponent>(Host);
    C->SetupAttachment(Host->GetRootComponent());
    C->SetMobility(EComponentMobility::Static);
    C->SetText(FText::FromString(Text));
    C->SetHorizontalAlignment(EHTA_Center);
    C->SetVerticalAlignment(EVRTA_TextCenter);
    C->SetWorldSize(Size*100);
    C->SetTextRenderColor(Color);
    C->SetCastShadow(false);
    C->SetWorldLocationAndRotation(RosCoordinates::Position(Position),FRotator(0,Yaw,0));
    Host->AddInstanceComponent(C);C->RegisterComponent();
    return C;
}
}

bool FCampusGym::IsActive(UWorld* World)
{
    if(!World)return false;
    for(TActorIterator<AActor> I(World);I;++I)
        if(I->ActorHasTag(TEXT("rmsim_campus_gym_indoor_lighting")))return true;
    return false;
}

bool FCampusGym::PreviewCamera(FVector& Position,FVector& Target,float& Fov)
{
    FString PositionText,TargetText,FovText;
    const bool HasPosition=FParse::Value(FCommandLine::Get(),TEXT("GymCameraPosition="),PositionText,false);
    const bool HasTarget=FParse::Value(FCommandLine::Get(),TEXT("GymCameraTarget="),TargetText,false);
    FVector P,T;
    if(HasPosition||HasTarget)
    {
        if(!HasPosition||!HasTarget||!CsvVector(PositionText,P)||!CsvVector(TargetText,T)||P.Equals(T,1.e-6))
        {
            UE_LOG(LogTemp,Error,TEXT("RMSIM_GYM_CAMERA_INVALID require distinct finite GymCameraPosition=x,y,z and GymCameraTarget=x,y,z in ROS metres"));
            return false;
        }
        Fov=72;
    }
    else
    {
        FString View;
        if(!FParse::Value(FCommandLine::Get(),TEXT("GymView="),View))return false;
        if(View==TEXT("hero")){P=FVector(-22,-17,8.4);T=FVector(1,1,2.0);Fov=72;}
        else if(View==TEXT("field")){P=FVector(-6,-5,.85);T=FVector(3,8,3.1);Fov=72;}
        else if(View==TEXT("gate")){P=FVector(-11,-11.3,1.4);T=FVector(-11,-7.9,1.2);Fov=65;}
        else if(View==TEXT("sky")){P=FVector(0,0,40);T=FVector(20,10,40);Fov=80;}
        else if(View==TEXT("reverse")){P=FVector(22,17,7.5);T=FVector(-2,0,1.8);Fov=72;}
        else return false;
    }
    if(FParse::Value(FCommandLine::Get(),TEXT("GymCameraFov="),FovText))
    {
        double CameraFov=0;
        if(!LexTryParseString(CameraFov,*FovText)||!FMath::IsFinite(CameraFov)||CameraFov<5||CameraFov>170)
        {
            UE_LOG(LogTemp,Error,TEXT("RMSIM_GYM_CAMERA_INVALID GymCameraFov must be 5..170 degrees"));
            return false;
        }
        Fov=static_cast<float>(CameraFov);
    }
    Position=RosCoordinates::Position(P);Target=RosCoordinates::Position(T);
    if(Position.ContainsNaN()||Target.ContainsNaN())return false;
    if(HasPosition||HasTarget)
        UE_LOG(LogTemp,Display,TEXT("RMSIM_GYM_CAMERA_CUSTOM position_m=%s target_m=%s fov=%.2f"),*P.ToString(),*T.ToString(),Fov);
    return true;
}

bool FCampusGym::Spawn(UWorld* World,const FMapStage& Stage,const FObj& Manifest,
                      const FString& ManifestFile,TMap<FString,AStaticMeshActor*>& Actors,FString& Error)
{
    auto Fail=[&](const FString& Message){Error=TEXT("Campus gym: ")+Message;return false;};
    if(!World||!Manifest.IsValid()||Stage.Id!=TEXT("rmuc2027"))return Fail(TEXT("invalid map identity"));
    const FObj* Bounds=nullptr;
    FVector Lower,Upper;
    if(Manifest->GetStringField(TEXT("map_id"))!=Stage.Id||
       Manifest->GetStringField(TEXT("units"))!=TEXT("m")||
       Manifest->GetStringField(TEXT("coordinate_system"))!=TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP")||
       !Manifest->TryGetObjectField(TEXT("bounds_m"),Bounds)||
       !Vec(*Bounds,TEXT("lo"),Lower)||!Vec(*Bounds,TEXT("hi"),Upper)||
       !Lower.Equals(Stage.SourceBoundsMetres.Min,1.e-7)||!Upper.Equals(Stage.SourceBoundsMetres.Max,1.e-7))
        return Fail(TEXT("source map envelope mismatch"));
    bool VenueEnabled=true;
    if(Manifest->HasField(TEXT("venue_enabled"))&&!Manifest->TryGetBoolField(TEXT("venue_enabled"),VenueEnabled))
        return Fail(TEXT("venue_enabled must be boolean"));
    FObj Layout;
    if(VenueEnabled)
    {
    FString RelativeLayout;
    if(!Manifest->TryGetStringField(TEXT("layout"),RelativeLayout)||RelativeLayout.Contains(TEXT(".."))||
       FPaths::IsRelative(RelativeLayout)==false)return Fail(TEXT("invalid local layout path"));
    if(!ReadJson(FPaths::Combine(FPaths::GetPath(ManifestFile),RelativeLayout),Layout,4*1024*1024)||
       Layout->GetStringField(TEXT("schema"))!=TEXT("rmsim_campus_gym_layout_v1"))
        return Fail(TEXT("missing/invalid layout; run build_campus_gym_layout.py"));
    }

    TMap<FString,UStaticMesh*> Meshes;
    Meshes.Add(TEXT("cube"),LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube")));
    Meshes.Add(TEXT("cylinder"),LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cylinder.Cylinder")));
    if(VenueEnabled)
    {
    Meshes.Add(TEXT("sphere"),LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Sphere.Sphere")));
    if(!Layout->HasField(TEXT("native_meshes")))
    Meshes.Add(TEXT("seat_row"),LoadObject<UStaticMesh>(nullptr,
        TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Meshes/SM_SeatRow.SM_SeatRow")));
    }
    for(const auto& Pair:Meshes)if(!Pair.Value)return Fail(TEXT("mesh missing: ")+Pair.Key);
    const bool HasNativeMeshes=VenueEnabled&&Layout->HasField(TEXT("native_meshes"));
    auto NativeMeshEvidence=MakeShared<FJsonObject>();
    if(HasNativeMeshes)
    {
        const FObj* NativeMeshes=nullptr;
        if(!Layout->TryGetObjectField(TEXT("native_meshes"),NativeMeshes)||(*NativeMeshes)->Values.Num()>32)
            return Fail(TEXT("native_meshes must be an object containing at most 32 assets"));
        const FString Prefix=TEXT("/Game/RMSim/Maps/rmuc2027/Arena/");
        for(const auto& Pair:(*NativeMeshes)->Values)
        {
            const FString MeshId(Pair.Key.ToView());
            FString Path;
            if(MeshId.IsEmpty()||Meshes.Contains(MeshId)||!Pair.Value.IsValid()||!Pair.Value->TryGetString(Path))
                return Fail(TEXT("native mesh id is empty/reserved or its path is not a string: ")+MeshId);
            if(!Path.StartsWith(Prefix,ESearchCase::CaseSensitive)||Path.Contains(TEXT(":"))||
               !FPackageName::IsValidObjectPath(Path,nullptr))
                return Fail(TEXT("native mesh requires a valid object path under the Arena prefix: ")+Path);
            UStaticMesh* Mesh=LoadObject<UStaticMesh>(nullptr,*Path);
            if(!Mesh)return Fail(TEXT("native static mesh missing: ")+Path);
            const FString LoadedPath=Mesh->GetPathName();
            if(!LoadedPath.StartsWith(Prefix,ESearchCase::CaseSensitive))
                return Fail(TEXT("native static mesh resolved outside the Arena prefix: ")+LoadedPath);
            const FVector Size=Mesh->GetBoundingBox().GetSize();
            if(Size.ContainsNaN()||Size.GetMin()<0.||Size.GetMax()<1.e-6)
                return Fail(TEXT("native mesh requires finite nonnegative bounds and a nonzero extent: ")+LoadedPath);
            Meshes.Add(MeshId,Mesh);
            auto Evidence=MakeShared<FJsonObject>();
            Evidence->SetStringField(TEXT("requested_path"),Path);
            Evidence->SetStringField(TEXT("loaded_object_path"),LoadedPath);
            Evidence->SetNumberField(TEXT("material_slots"),Mesh->GetStaticMaterials().Num());
            Evidence->SetNumberField(TEXT("instances"),0);
            Evidence->SetBoolField(TEXT("all_instances_use_source_materials"),true);
            NativeMeshEvidence->SetObjectField(MeshId,Evidence);
            UE_LOG(LogTemp,Display,TEXT("RMSIM_CAMPUS_NATIVE_MESH id=%s requested=%s loaded=%s material_slots=%d"),
                *MeshId,*Path,*LoadedPath,Mesh->GetStaticMaterials().Num());
        }
    }
    FString GlassMaterialPath=TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Materials/M_glass.M_glass");
    FString EnvironmentMaterialPath=TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Materials/M_environment.M_environment");
    FString EnvironmentCubemapPath=TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Textures/T_CampusCourtyard.T_CampusCourtyard");
    const FObj* Presentation=nullptr;
    if(Manifest->HasField(TEXT("presentation_assets")))
    {
        if(!Manifest->TryGetObjectField(TEXT("presentation_assets"),Presentation))
            return Fail(TEXT("presentation_assets must be an object"));
        auto OverridePath=[&](const TCHAR* Key,FString& Path)
        {
            if(!(*Presentation)->HasField(Key))return true;
            return (*Presentation)->TryGetStringField(Key,Path)&&
                Path.StartsWith(TEXT("/Game/RMSim/Maps/rmuc2027/Arena/"),ESearchCase::CaseSensitive)&&
                !Path.Contains(TEXT(":"))&&FPackageName::IsValidObjectPath(Path,nullptr);
        };
        if(!OverridePath(TEXT("glass_material"),GlassMaterialPath)||
           !OverridePath(TEXT("environment_material"),EnvironmentMaterialPath)||
           !OverridePath(TEXT("environment_cubemap"),EnvironmentCubemapPath))
            return Fail(TEXT("invalid local presentation asset path"));
    }
    TMap<FString,UMaterialInterface*> Materials;
    for(const TCHAR* Id:{TEXT("plaster"),TEXT("concrete"),TEXT("wood_floor"),TEXT("acoustic_wood"),
        TEXT("steel"),TEXT("steel_light"),TEXT("seat_blue"),TEXT("seat_red"),TEXT("seat_gray"),
        TEXT("glass"),TEXT("rubber"),TEXT("emissive"),TEXT("scoreboard"),TEXT("court_line"),TEXT("environment")})
    {
        if(HasNativeMeshes&&FString(Id)!=TEXT("glass")&&FString(Id)!=TEXT("steel_light")&&
           FString(Id)!=TEXT("rubber")&&FString(Id)!=TEXT("court_line")&&FString(Id)!=TEXT("environment"))continue;
        if(!VenueEnabled&&FString(Id)!=TEXT("glass")&&FString(Id)!=TEXT("steel_light")&&
           FString(Id)!=TEXT("rubber")&&FString(Id)!=TEXT("court_line"))continue;
        FString Path=FString::Printf(TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Materials/M_%s.M_%s"),Id,Id);
        if(FString(Id)==TEXT("glass"))Path=GlassMaterialPath;
        else if(FString(Id)==TEXT("environment"))Path=EnvironmentMaterialPath;
        UMaterialInterface* Material=LoadObject<UMaterialInterface>(nullptr,*Path);
        if(!Material)return Fail(TEXT("material missing: ")+Path);
        Materials.Add(Id,Material);
    }
    AActor* Host=World->SpawnActor<AActor>();
    if(!Host)return Fail(TEXT("cannot create gym"));
    Host->Tags.Add(TEXT("rmsim_campus_gym"));
    if(VenueEnabled)Host->Tags.Add(TEXT("rmsim_campus_gym_indoor_lighting"));
    auto* Root=NewObject<USceneComponent>(Host,TEXT("CampusGymRoot"));
    Root->SetMobility(EComponentMobility::Static);
    Host->SetRootComponent(Root);Host->AddInstanceComponent(Root);Root->RegisterComponent();
    int32 LayoutInstances=0,LightCount=0,NativeLayoutInstances=0,ShadowedLightCount=0;
    FString GiMethod=TEXT("none"),ReflectionMethod=TEXT("screen_space");
    bool OverrideReflection=false;
    UMaterialInstanceDynamic* Background=nullptr;
    if(VenueEnabled)
    {
    Background=UMaterialInstanceDynamic::Create(Materials[TEXT("environment")],Host);
    FGroup Environment=NewGroup(Host,Meshes[TEXT("sphere")],Background,TEXT("CampusCourtyardBackdrop"));
    Environment.Add(FVector::ZeroVector,FVector(800,800,800));
    const TArray<TSharedPtr<FJsonValue>>* Groups=nullptr;
    if(!Layout->TryGetArrayField(TEXT("groups"),Groups)||Groups->Num()>100)return Fail(TEXT("invalid layout groups"));
    for(const auto& Entry:*Groups)
    {
        const FObj O=Entry->AsObject();
        FString Id,MeshId,MaterialId;
        const TArray<TSharedPtr<FJsonValue>>* Instances=nullptr;
        if(!O.IsValid()||!O->TryGetStringField(TEXT("id"),Id)||!O->TryGetStringField(TEXT("mesh"),MeshId)||
           !O->TryGetStringField(TEXT("material"),MaterialId)||!O->TryGetArrayField(TEXT("instances"),Instances)||
           !Meshes.Contains(MeshId)||(!Materials.Contains(MaterialId)&&MaterialId!=TEXT("source")))
            return Fail(TEXT("invalid geometry group"));
        bool Shadow=false;O->TryGetBoolField(TEXT("cast_shadow"),Shadow);
        FGroup G=NewGroup(Host,Meshes[MeshId],MaterialId==TEXT("source")?nullptr:Materials[MaterialId],TEXT("Gym_")+Id,Shadow);
        const bool NativeGroup=NativeMeshEvidence->HasField(MeshId);
        for(const auto& Instance:*Instances)
        {
            FVector P,S,R;
            if(!Vec(Instance->AsObject(),TEXT("center"),P)||!Vec(Instance->AsObject(),TEXT("size"),S)||
               !Vec(Instance->AsObject(),TEXT("rotation_deg"),R)||(NativeGroup?S.GetMin()<0:S.GetMin()<=0)||S.GetMax()>100||
               P.GetAbsMax()>100||++LayoutInstances>12000)return Fail(TEXT("geometry outside bounded SI layout"));
            if(NativeGroup)
            {
                const FVector BoundsSizeCm=G.Mesh->GetBoundingBox().GetSize();
                for(int32 I=0;I<3;++I)
                    if(BoundsSizeCm[I]<1.e-6?FMath::Abs(S[I]*100.)>1.e-6:S[I]<=0.)
                        return Fail(TEXT("native size must be zero on flat mesh axes and positive on other axes: ")+MeshId);
            }
            G.Add(P,S,R);
        }
        const FObj* NativeRecord=nullptr;
        if(NativeMeshEvidence->TryGetObjectField(MeshId,NativeRecord))
        {
            const int32 Count=G.Component->GetInstanceCount();
            NativeLayoutInstances+=Count;
            (*NativeRecord)->SetNumberField(TEXT("instances"),(*NativeRecord)->GetNumberField(TEXT("instances"))+Count);
            (*NativeRecord)->SetBoolField(TEXT("all_instances_use_source_materials"),
                (*NativeRecord)->GetBoolField(TEXT("all_instances_use_source_materials"))&&MaterialId==TEXT("source"));
            UE_LOG(LogTemp,Display,TEXT("RMSIM_CAMPUS_NATIVE_GROUP group=%s mesh=%s loaded=%s instances=%d material_mode=%s"),
                *Id,*MeshId,*G.Mesh->GetPathName(),Count,*MaterialId);
        }
    }

    if(!HasNativeMeshes)
    {
    // The floor outside the field is visual only. The original CAD contact
    // terrain and all its wheel-height variation stay intact.
    FGroup Floor=NewGroup(Host,Meshes[TEXT("cube")],Materials[TEXT("wood_floor")],TEXT("GymParquet"),false);
    Floor.Add(FVector(0,-14.00078,-.26),FVector(56,11.99844,.1));
    Floor.Add(FVector(0,14.00078,-.26),FVector(56,11.99844,.1));
    Floor.Add(FVector(-21.,0,-.26),FVector(14.,16.003132,.1));
    Floor.Add(FVector(21.,0,-.26),FVector(14.,16.003132,.1));
    }
    }

    const double CX=(Lower.X+Upper.X)*.5,CY=(Lower.Y+Upper.Y)*.5;
    const double HX=(Upper.X-Lower.X)*.5,HY=(Upper.Y-Lower.Y)*.5;
    const FObj* Perimeter=nullptr;
    if(!Manifest->TryGetObjectField(TEXT("perimeter"),Perimeter))return Fail(TEXT("missing closed perimeter"));
    FString PhysicsContract;
    if(!(*Perimeter)->TryGetStringField(TEXT("physics_contract"),PhysicsContract)||
       (PhysicsContract!=TEXT("outside_source_bounds_v1")&&
        PhysicsContract!=TEXT("preserve_validated_arena_shell_v1")))
        return Fail(TEXT("unsupported closed perimeter contract"));
    const bool OutsidePerimeter=PhysicsContract==TEXT("outside_source_bounds_v1");
    double Clearance=0.,Inset=0.,Thickness,Top,Bottom,CollisionTop,PanelWidth;
    bool Interaction=true;
    if((OutsidePerimeter?
        (!(*Perimeter)->TryGetNumberField(TEXT("outside_clearance_m"),Clearance)||!FMath::IsFinite(Clearance)||Clearance<0||Clearance>.02):
        (!(*Perimeter)->TryGetNumberField(TEXT("inset_m"),Inset)||!FMath::IsFinite(Inset)||Inset<.08||Inset>.4))||
       !(*Perimeter)->TryGetNumberField(TEXT("collision_thickness_m"),Thickness)||!FMath::IsFinite(Thickness)||Thickness<.10||Thickness>.75||
       !(*Perimeter)->TryGetNumberField(TEXT("glass_top_m"),Top)||!FMath::IsFinite(Top)||FMath::Abs(Top-2.6)>.001||
       !(*Perimeter)->TryGetNumberField(TEXT("collision_top_m"),CollisionTop)||!FMath::IsFinite(CollisionTop)||CollisionTop<Upper.Z+.6||CollisionTop>10||
       !(*Perimeter)->TryGetNumberField(TEXT("max_panel_width_m"),PanelWidth)||!FMath::IsFinite(PanelWidth)||PanelWidth<.5||PanelWidth>2||
       !(*Perimeter)->TryGetNumberField(TEXT("collision_bottom_m"),Bottom)||!FMath::IsFinite(Bottom)||Bottom>-.5||Bottom< -3||
       !(*Perimeter)->TryGetBoolField(TEXT("interaction"),Interaction)||Interaction||
       (!OutsidePerimeter&&Inset<=Thickness*.5)||
       (*Perimeter)->GetStringField(TEXT("doors"))!=TEXT("fixed_closed_decorative"))
        return Fail(TEXT("invalid permanently closed glass perimeter"));
    // Each explicit contract retains its own physical/LiDAR envelope. Existing
    // campus packages stay usable while the outside-boundary update is staged.
    const double GuardX=OutsidePerimeter?HX+Clearance+Thickness*.5:HX-Inset;
    const double GuardY=OutsidePerimeter?HY+Clearance+Thickness*.5:HY-Inset;
    const double X=OutsidePerimeter?HX+Clearance:GuardX-Thickness*.5+.006;
    const double Y=OutsidePerimeter?HY+Clearance:GuardY-Thickness*.5+.006;
    const double WallHalfX=OutsidePerimeter?X+Thickness:HX;
    const double WallHalfY=OutsidePerimeter?Y+Thickness:HY;
    struct FWall{const TCHAR* Name;FVector P,S;};
    const FWall Walls[]={
        {TEXT("west"),FVector(CX-GuardX,CY,(CollisionTop+Bottom)*.5),FVector(Thickness,2*WallHalfY,CollisionTop-Bottom)},
        {TEXT("east"),FVector(CX+GuardX,CY,(CollisionTop+Bottom)*.5),FVector(Thickness,2*WallHalfY,CollisionTop-Bottom)},
        {TEXT("south"),FVector(CX,CY-GuardY,(CollisionTop+Bottom)*.5),FVector(2*WallHalfX,Thickness,CollisionTop-Bottom)},
        {TEXT("north"),FVector(CX,CY+GuardY,(CollisionTop+Bottom)*.5),FVector(2*WallHalfX,Thickness,CollisionTop-Bottom)}};
    for(const FWall& Wall:Walls)
    {
        const FString Name=FString(TEXT("arena_guard_"))+Wall.Name;
        auto* A=World->SpawnActor<AStaticMeshActor>(RosCoordinates::Position(Wall.P),FRotator::ZeroRotator);
        auto* C=A->GetStaticMeshComponent();C->SetStaticMesh(Meshes[TEXT("cube")]);
        A->SetActorScale3D(Wall.S);
        C->SetCollisionProfileName(TEXT("BlockAll"));C->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        C->SetCastShadow(false);C->SetCanEverAffectNavigation(false);
        C->SetCollisionResponseToChannel(ECC_Camera,ECR_Ignore);
        A->SetActorHiddenInGame(true);A->Tags.Add(FName(*Name));Actors.Add(Name,A);
    }
    FGroup Glass=NewGroup(Host,Meshes[TEXT("cube")],Materials[TEXT("glass")],TEXT("GlassFence"));
    FGroup Frame=NewGroup(Host,Meshes[TEXT("cube")],Materials[TEXT("steel_light")],TEXT("FenceMullions"),true);
    FGroup Gaskets=NewGroup(Host,Meshes[TEXT("cube")],Materials[TEXT("rubber")],TEXT("FenceGaskets"));
    FGroup Hardware=NewGroup(Host,Meshes[TEXT("cylinder")],Materials[TEXT("steel_light")],TEXT("DoorHardware"),true);
    FGroup Stripe=NewGroup(Host,Meshes[TEXT("cube")],Materials[TEXT("court_line")],TEXT("GlassSafetyMarks"));
    int32 PanelCount=0,GateLeaves=0;
    TArray<UTextRenderComponent*> GateLabels;
    auto FencePoint=[&](bool AlongX,double Fixed,double Along,double Z)
        {return AlongX?FVector(CX+Along,CY+Fixed,Z):FVector(CX+Fixed,CY+Along,Z);};
    auto Part=[&](FGroup& G,bool AlongX,double Fixed,double Along,double Z,double W,double D,double H,double Outset=0.)
        {
            const double Outside=OutsidePerimeter?Fixed+FMath::Sign(Fixed)*(D*.5+Outset):Fixed;
            G.Add(FencePoint(AlongX,Outside,Along,Z),AlongX?FVector(W,D,H):FVector(D,W,H));
        };
    auto Span=[&](bool AlongX,double Fixed,double A,double B)
    {
        const int32 Count=FMath::CeilToInt((B-A)/PanelWidth);
        for(int32 I=0;I<Count;++I)
        {
            const double L=A+(B-A)*I/Count,R=A+(B-A)*(I+1)/Count;
            Part(Glass,AlongX,Fixed,(L+R)*.5,1.18,R-L-.072,.012,2.78);
            Part(Gaskets,AlongX,Fixed,L,1.19,.043,.025,2.65);
            Part(Frame,AlongX,Fixed,L,1.19,.06,.075,2.82);
            // Discrete frosting patches make clear glass legible at eye level.
            Part(Stripe,AlongX,OutsidePerimeter?Fixed:Fixed-.009,(L+R)*.5,.94,.26,.003,.035,.0125);
            ++PanelCount;
        }
        Part(Frame,AlongX,Fixed,B,1.19,.06,.075,2.82);
        Part(Frame,AlongX,Fixed,(A+B)*.5,2.60,B-A,.075,.055);
        Part(Frame,AlongX,Fixed,(A+B)*.5,-.155,B-A,.08,.11);
    };
    // Closed service entrances on opposite long sides. They are dressings on
    // the continuous collider, with no interaction, movable bodies or gap.
    for(const double Sign:{-1.,1.})
    {
        const double GateCentre=Sign*11.0,GateHalf=.92;
        Span(true,Sign*Y,-X,GateCentre-GateHalf);
        Span(true,Sign*Y,GateCentre+GateHalf,X);
        Span(false,Sign*X,-Y,Y);
        Part(Frame,true,Sign*Y,GateCentre,2.60,2*GateHalf,.09,.10);
        for(const double LeafSign:{-1.,1.})
        {
            const double LeafCentre=GateCentre+LeafSign*GateHalf*.5;
            Part(Glass,true,Sign*Y,LeafCentre,1.18,GateHalf-.065,.012,2.78);
            for(const double E:{-1.,1.})Part(Frame,true,Sign*Y,LeafCentre+E*(GateHalf*.5-.018),1.19,.035,.055,2.76);
            Part(Frame,true,Sign*Y,LeafCentre,-.15,GateHalf-.01,.055,.12);
            Part(Frame,true,Sign*Y,LeafCentre,2.55,GateHalf-.01,.055,.06);
            const double HandleX=GateCentre+LeafSign*.14;
            Hardware.Add(FVector(CX+HandleX,CY+Sign*(Y+(OutsidePerimeter?.11:-.11)),1.02),FVector(.026,.026,.38));
            for(const double Z:{.83,1.21})Part(Frame,true,Sign*(Y-(OutsidePerimeter?0.:.055)),HandleX,Z,.025,.12,.025);
            for(const double Z:{.22,1.13,2.12})
                Hardware.Add(FVector(CX+GateCentre+LeafSign*(GateHalf-.025),CY+Sign*(Y+(OutsidePerimeter?.025:-.025)),Z),FVector(.045,.045,.095));
            Part(Stripe,true,Sign*(Y-(OutsidePerimeter?0.:.009)),LeafCentre,.94,.24,.003,.035,.0125);
            ++GateLeaves;
        }
        GateLabels.Add(TextLabel(Host,TEXT("SERVICE ACCESS  /  CLOSED"),FVector(CX+GateCentre,CY+Sign*(Y+(OutsidePerimeter?.020:-.065)),2.43),Sign<0?-90:90,.085,FColor(225,231,232)));
    }
    if(VenueEnabled)
    {
    if(!HasNativeMeshes)
    TextLabel(Host,TEXT("ROBOMASTER  |  RMUC 2027"),FVector(0,-19.68,7.7),-90,.42,FColor(35,46,53));

    // Only this map receives the indoor lighting rig. It never changes the
    // project's global renderer settings or the other map's lighting.
    const FObj* Lighting=nullptr;
    if(!Manifest->TryGetObjectField(TEXT("lighting"),Lighting))return Fail(TEXT("missing indoor lighting"));
    if(((*Lighting)->HasField(TEXT("gi_method"))&&!(*Lighting)->TryGetStringField(TEXT("gi_method"),GiMethod))||
       (!GiMethod.Equals(TEXT("none"),ESearchCase::CaseSensitive)&&
        !GiMethod.Equals(TEXT("screen_space"),ESearchCase::CaseSensitive)&&
        !GiMethod.Equals(TEXT("lumen"),ESearchCase::CaseSensitive)))
        return Fail(TEXT("gi_method must be none, screen_space or lumen"));
    const bool HasReflectionMethod=(*Lighting)->HasField(TEXT("reflection_method"));
    if((HasReflectionMethod&&!(*Lighting)->TryGetStringField(TEXT("reflection_method"),ReflectionMethod))||
       (!ReflectionMethod.Equals(TEXT("screen_space"),ESearchCase::CaseSensitive)&&
        !ReflectionMethod.Equals(TEXT("lumen"),ESearchCase::CaseSensitive)))
        return Fail(TEXT("reflection_method must be screen_space or lumen"));
    // New native layouts default to SSR; old layouts without this field retain
    // their existing inherited reflection setting. All overrides stay in this world.
    OverrideReflection=HasNativeMeshes||HasReflectionMethod;
    double Lumens,SunIntensity,SkyIntensity;
    double ExposureEV=8.,AmbientIntensity=.4,BackgroundBrightness=1024.;
    (*Lighting)->TryGetNumberField(TEXT("exposure_ev100"),ExposureEV);
    (*Lighting)->TryGetNumberField(TEXT("ambient_cubemap_intensity"),AmbientIntensity);
    (*Lighting)->TryGetNumberField(TEXT("background_brightness"),BackgroundBrightness);
    if(!FMath::IsFinite(ExposureEV)||ExposureEV<5||ExposureEV>14||
       !FMath::IsFinite(AmbientIntensity)||AmbientIntensity<0||AmbientIntensity>4||
       !FMath::IsFinite(BackgroundBrightness)||BackgroundBrightness<1||BackgroundBrightness>8192)
        return Fail(TEXT("invalid exposure or environment brightness"));
    Background->SetScalarParameterValue(TEXT("EnvironmentBrightness"),BackgroundBrightness);
    if(!(*Lighting)->TryGetNumberField(TEXT("rect_light_lumens"),Lumens)||!FMath::IsFinite(Lumens)||Lumens<1000||Lumens>150000||
       !(*Lighting)->TryGetNumberField(TEXT("sun_intensity"),SunIntensity)||!FMath::IsFinite(SunIntensity)||SunIntensity<0||SunIntensity>100000||
       !(*Lighting)->TryGetNumberField(TEXT("sky_intensity"),SkyIntensity)||!FMath::IsFinite(SkyIntensity)||SkyIntensity<0||SkyIntensity>1000)
        return Fail(TEXT("invalid indoor photometric configuration"));
    ASkyLight* Sky=World->SpawnActor<ASkyLight>();
    auto* Skylight=Sky->GetLightComponent();Skylight->SetMobility(EComponentMobility::Movable);
    Skylight->SourceType=ESkyLightSourceType::SLS_SpecifiedCubemap;
    Skylight->bLowerHemisphereIsBlack=false;
    UTextureCube* Courtyard=LoadObject<UTextureCube>(nullptr,*EnvironmentCubemapPath);
    if(!Courtyard)return Fail(TEXT("missing CC0 campus HDR environment: ")+EnvironmentCubemapPath);
    UE_LOG(LogTemp,Display,TEXT("RMSIM_CAMPUS_PRESENTATION glass=%s backdrop=%s cubemap=%s background_brightness=%.3f exposure_ev100=%.3f"),
        *GlassMaterialPath,*EnvironmentMaterialPath,*EnvironmentCubemapPath,BackgroundBrightness,ExposureEV);
    Skylight->SetCubemap(Courtyard);
    Skylight->SetIntensity(SkyIntensity);Skylight->SetLowerHemisphereColor(FLinearColor(.11,.10,.085));
    Skylight->SetCastShadows(HasNativeMeshes);Skylight->RecaptureSky();
    FVector SunRotation(-27.,-38.,0.);
    if((*Lighting)->HasField(TEXT("sun_rotation_ue_deg"))&&
       (!Vec(*Lighting,TEXT("sun_rotation_ue_deg"),SunRotation)||
        SunRotation.X>=0.||SunRotation.X<-90.||FMath::Abs(SunRotation.Y)>360.||FMath::Abs(SunRotation.Z)>1.e-6))
        return Fail(TEXT("invalid UE sun rotation"));
    ADirectionalLight* Sun=World->SpawnActor<ADirectionalLight>(FVector(0,0,1800),FRotator(SunRotation.X,SunRotation.Y,SunRotation.Z));
    Sun->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Sun->GetLightComponent()->SetIntensity(SunIntensity);
    Sun->GetLightComponent()->SetLightColor(FLinearColor(.90,.95,1.0));
    auto* SunC=CastChecked<UDirectionalLightComponent>(Sun->GetLightComponent());
    SunC->SetDynamicShadowDistanceMovableLight(8000);
    const TArray<TSharedPtr<FJsonValue>>* Lights=nullptr;
    if(!Layout->TryGetArrayField(TEXT("lights"),Lights)||Lights->Num()>16)return Fail(TEXT("invalid light rig"));
    for(const auto& Entry:*Lights)
    {
        const FObj O=Entry->AsObject();FVector P,R;
        if(!Vec(O,TEXT("center"),P)||!Vec(O,TEXT("rotation_deg"),R))return Fail(TEXT("invalid light pose"));
        R.Z+=90.;
        ARectLight* A=World->SpawnActor<ARectLight>(RosCoordinates::Position(P),RosCoordinates::Rotation(R*(PI/180.)).Rotator());
        auto* C=CastChecked<URectLightComponent>(A->GetLightComponent());
        C->SetMobility(EComponentMobility::Movable);C->SetIntensityUnits(ELightUnits::Lumens);
        C->SetIntensity(Lumens);C->SetSourceWidth(O->GetNumberField(TEXT("width_m"))*100);
        C->SetSourceHeight(O->GetNumberField(TEXT("height_m"))*100);
        C->SetAttenuationRadius(2500);C->SetUseTemperature(true);C->SetTemperature(5000);
        bool Shadowed=FMath::Abs(P.X)<1.&&FMath::Abs(P.Y)<6.;
        if(HasNativeMeshes&&O->HasField(TEXT("cast_shadows"))&&!O->TryGetBoolField(TEXT("cast_shadows"),Shadowed))
            return Fail(TEXT("native light cast_shadows must be boolean"));
        C->SetCastShadows(Shadowed);ShadowedLightCount+=Shadowed;++LightCount;
    }
    APostProcessVolume* Post=World->SpawnActor<APostProcessVolume>();
    Post->bUnbound=true;Post->Priority=2;
    auto& S=Post->Settings;
    S.bOverride_AutoExposureMethod=true;S.AutoExposureMethod=AEM_Manual;
    S.bOverride_AutoExposureApplyPhysicalCameraExposure=true;S.AutoExposureApplyPhysicalCameraExposure=true;
    S.bOverride_CameraISO=true;S.CameraISO=100;
    S.bOverride_CameraShutterSpeed=true;S.CameraShutterSpeed=FMath::Pow(2.,ExposureEV)/16.;
    S.bOverride_DepthOfFieldFstop=true;S.DepthOfFieldFstop=4;
    S.bOverride_AutoExposureBias=true;S.AutoExposureBias=0;
    S.bOverride_DynamicGlobalIlluminationMethod=true;
    S.DynamicGlobalIlluminationMethod=GiMethod==TEXT("lumen")?EDynamicGlobalIlluminationMethod::Lumen:
        (GiMethod==TEXT("screen_space")?EDynamicGlobalIlluminationMethod::ScreenSpace:EDynamicGlobalIlluminationMethod::None);
    if(OverrideReflection)
    {
        S.bOverride_ReflectionMethod=true;
        S.ReflectionMethod=ReflectionMethod==TEXT("lumen")?EReflectionMethod::Lumen:EReflectionMethod::ScreenSpace;
    }
    if(GiMethod==TEXT("lumen"))
    {
        S.bOverride_LumenFinalGatherQuality=true;S.LumenFinalGatherQuality=2.0f;
        S.bOverride_LumenSceneLightingQuality=true;S.LumenSceneLightingQuality=2.0f;
    }
    if(OverrideReflection&&ReflectionMethod==TEXT("lumen"))
    {
        S.bOverride_LumenReflectionQuality=true;S.LumenReflectionQuality=2.0f;
    }
    UE_LOG(LogTemp,Display,TEXT("RMSIM_CAMPUS_LIGHTING_PP gi_requested=%s reflection_requested=%s reflection_override=%d shadowed_rect_lights=%d rect_lights=%d"),
        *GiMethod,OverrideReflection?*ReflectionMethod:TEXT("project_default"),OverrideReflection,ShadowedLightCount,LightCount);
    S.AmbientCubemap=Courtyard;
    S.bOverride_AmbientCubemapIntensity=true;S.AmbientCubemapIntensity=AmbientIntensity;
    S.bOverride_AmbientCubemapTint=true;S.AmbientCubemapTint=FLinearColor(.96,.97,1.);
    S.bOverride_AmbientOcclusionIntensity=true;S.AmbientOcclusionIntensity=.65f;
    S.bOverride_AmbientOcclusionRadius=true;S.AmbientOcclusionRadius=120;
    S.bOverride_BloomIntensity=true;S.BloomIntensity=.12f;
    S.bOverride_MotionBlurAmount=true;S.MotionBlurAmount=0;
    S.bOverride_FilmGrainIntensity=true;S.FilmGrainIntensity=0;
    }

    FString ProbeFile;
    if(FParse::Value(FCommandLine::Get(),TEXT("GymProbeOutput="),ProbeFile))
    {
        TArray<TSharedPtr<FJsonValue>> Samples;
        int32 Passed=0,Tested=0;
        FCollisionQueryParams Query(SCENE_QUERY_STAT(CampusGymBoundary),false);
        for(const auto& Pair:Actors)if(Pair.Key.StartsWith(TEXT("collision_")))Query.AddIgnoredActor(Pair.Value);
        auto Sweep=[&](FVector A,FVector B,const FString& Label)
        {
            FHitResult Hit;
            const bool Blocked=World->SweepSingleByChannel(Hit,RosCoordinates::Position(A),RosCoordinates::Position(B),
                FQuat::Identity,ECC_Visibility,FCollisionShape::MakeSphere(12),Query);
            bool Good=false;for(const FWall& Wall:Walls)Good|=Blocked&&Hit.GetActor()==Actors.FindRef(FString(TEXT("arena_guard_"))+Wall.Name);
            ++Tested;Passed+=Good;
            auto O=MakeShared<FJsonObject>();O->SetStringField(TEXT("name"),Label);O->SetBoolField(TEXT("passed"),Good);
            O->SetNumberField(TEXT("impact_time"),Blocked?Hit.Time:-1);Samples.Add(MakeShared<FJsonValueObject>(O));
        };
        for(const double F:{-.92,-.60,-.25,0.,.25,.60,.92})for(const double Z:{.05,2.35,CollisionTop-.25})
        {
            Sweep(FVector(CX+X-1,CY+F*Y,Z),FVector(CX+X+1,CY+F*Y,Z),TEXT("east"));
            Sweep(FVector(CX-X+1,CY+F*Y,Z),FVector(CX-X-1,CY+F*Y,Z),TEXT("west"));
            Sweep(FVector(CX+F*X,CY+Y-1,Z),FVector(CX+F*X,CY+Y+1,Z),TEXT("north"));
            Sweep(FVector(CX+F*X,CY-Y+1,Z),FVector(CX+F*X,CY-Y-1,Z),TEXT("south"));
        }
        for(const double SX:{-1.,1.})for(const double SY:{-1.,1.})
            Sweep(FVector(CX+SX*(X-.7),CY+SY*(Y-.7),.6),FVector(CX+SX*(X+.7),CY+SY*(Y+.7),.6),TEXT("corner"));
        for(const double Sign:{-1.,1.})for(const double DX:{-.8,-.1,.1,.8})
            Sweep(FVector(CX+Sign*11+DX,CY+Sign*(Y-1),.5),FVector(CX+Sign*11+DX,CY+Sign*(Y+1),.5),TEXT("closed_decorative_gate"));
        bool CosmeticNoCollision=true;int32 AllInstances=0;
        TInlineComponentArray<UInstancedStaticMeshComponent*> Components;Host->GetComponents(Components);
        for(const auto* C:Components){CosmeticNoCollision&=C->GetCollisionEnabled()==ECollisionEnabled::NoCollision;AllInstances+=C->GetInstanceCount();}
        // Inspect each rendered instance, rather than the whole ring's bounds.
        // The latter encloses the field even when every individual part is outside.
        bool BoundaryOutside=true;int32 BoundsChecked=0;double MinimumClearance=1.e30;
        TArray<TSharedPtr<FJsonValue>> Intrusions;
        auto CheckBounds=[&](const FBox& Box,const FString& Name)
        {
            const double MinX=Box.Min.X*.01,MaxX=Box.Max.X*.01;
            const double MinY=-Box.Max.Y*.01,MaxY=-Box.Min.Y*.01;
            const double Gap=FMath::Max(FMath::Max(Lower.X-MaxX,MinX-Upper.X),
                                      FMath::Max(Lower.Y-MaxY,MinY-Upper.Y));
            MinimumClearance=FMath::Min(MinimumClearance,Gap);++BoundsChecked;
            if(Gap < -1.e-6){BoundaryOutside=false;Intrusions.Add(MakeShared<FJsonValueString>(Name));}
        };
        for(const FWall& Wall:Walls)
        {
            auto* A=CastChecked<AStaticMeshActor>(Actors.FindRef(FString(TEXT("arena_guard_"))+Wall.Name));
            CheckBounds(A->GetStaticMeshComponent()->GetStaticMesh()->GetBoundingBox().TransformBy(A->GetActorTransform()),Wall.Name);
        }
        for(const FGroup* G:{&Glass,&Frame,&Gaskets,&Hardware,&Stripe})
            for(int32 I=0;I<G->Component->GetInstanceCount();++I)
            {
                FTransform T;G->Component->GetInstanceTransform(I,T,true);
                CheckBounds(G->Mesh->GetBoundingBox().TransformBy(T),FString::Printf(TEXT("%s[%d]"),*G->Component->GetName(),I));
            }
        for(auto* C:GateLabels){C->UpdateBounds();CheckBounds(C->Bounds.GetBox(),C->GetName());}
        bool CornersOverlap=true;double MinimumCornerOverlap=1.e30;
        for(int32 XI=0;XI<2;++XI)for(int32 YI=2;YI<4;++YI)
        {
            const FWall& A=Walls[XI];const FWall& B=Walls[YI];
            const double DX=FMath::Min(A.P.X+A.S.X*.5,B.P.X+B.S.X*.5)-FMath::Max(A.P.X-A.S.X*.5,B.P.X-B.S.X*.5);
            const double DY=FMath::Min(A.P.Y+A.S.Y*.5,B.P.Y+B.S.Y*.5)-FMath::Max(A.P.Y-A.S.Y*.5,B.P.Y-B.S.Y*.5);
            CornersOverlap&=DX>0&&DY>0;MinimumCornerOverlap=FMath::Min(MinimumCornerOverlap,FMath::Min(DX,DY));
        }
        const bool PerimeterMatchesContract=!OutsidePerimeter||BoundaryOutside;
        const bool LightRigMatches=VenueEnabled?LightCount==12:LightCount==0;
        auto Report=MakeShared<FJsonObject>();
        Report->SetStringField(TEXT("status"),Passed==Tested&&Tested==96&&CosmeticNoCollision&&GateLeaves==4&&PerimeterMatchesContract&&CornersOverlap&&LightRigMatches&&Materials[TEXT("glass")]->GetBlendMode()==BLEND_Translucent?TEXT("passed"):TEXT("failed"));
        Report->SetStringField(TEXT("scope"),TEXT("native collision sweeps; cosmetic glass and shut doors; no gate interaction; source CAD ignored for boundary-isolation queries only"));
        Report->SetNumberField(TEXT("sweep_samples"),Tested);Report->SetNumberField(TEXT("sweep_passed"),Passed);
        if(OutsidePerimeter)Report->SetNumberField(TEXT("outside_clearance_m"),Clearance);
        else Report->SetNumberField(TEXT("collision_inset_m"),Inset);
        Report->SetStringField(TEXT("physics_contract"),PhysicsContract);
        Report->SetBoolField(TEXT("outside_bounds_required"),OutsidePerimeter);
        Report->SetBoolField(TEXT("perimeter_contract_satisfied"),PerimeterMatchesContract);
        Report->SetBoolField(TEXT("all_boundary_parts_outside_source_bounds"),BoundaryOutside);
        Report->SetNumberField(TEXT("boundary_bounds_checked"),BoundsChecked);
        Report->SetNumberField(TEXT("minimum_boundary_clearance_m"),MinimumClearance);
        Report->SetNumberField(TEXT("bounds_numerical_tolerance_m"),1.e-6);
        Report->SetBoolField(TEXT("corner_slabs_overlap"),CornersOverlap);
        Report->SetNumberField(TEXT("minimum_corner_overlap_m"),MinimumCornerOverlap);
        Report->SetArrayField(TEXT("intruding_boundary_parts"),Intrusions);
        Report->SetNumberField(TEXT("collision_thickness_m"),Thickness);
        Report->SetNumberField(TEXT("collision_top_m"),CollisionTop);
        Report->SetNumberField(TEXT("glass_top_m"),Top);
        Report->SetNumberField(TEXT("continuous_collision_walls"),4);Report->SetNumberField(TEXT("glass_panels"),PanelCount);
        Report->SetNumberField(TEXT("fixed_gate_leaves"),GateLeaves);Report->SetBoolField(TEXT("gate_interaction_enabled"),false);
        Report->SetBoolField(TEXT("cosmetic_no_collision"),CosmeticNoCollision);
        Report->SetNumberField(TEXT("visual_instances"),AllInstances);Report->SetNumberField(TEXT("visual_groups"),Components.Num());
        Report->SetNumberField(TEXT("indoor_rect_lights"),LightCount);
        Report->SetBoolField(TEXT("venue_enabled"),VenueEnabled);
        Report->SetBoolField(TEXT("native_layout"),HasNativeMeshes);
        Report->SetObjectField(TEXT("native_meshes"),NativeMeshEvidence);
        Report->SetNumberField(TEXT("native_mesh_assets_loaded"),NativeMeshEvidence->Values.Num());
        Report->SetNumberField(TEXT("native_mesh_instances"),NativeLayoutInstances);
        Report->SetBoolField(TEXT("procedural_parquet_enabled"),VenueEnabled&&!HasNativeMeshes);
        Report->SetBoolField(TEXT("fixed_wall_branding_enabled"),VenueEnabled&&!HasNativeMeshes);
        Report->SetStringField(TEXT("gi_method_requested"),GiMethod);
        Report->SetStringField(TEXT("reflection_method_requested"),OverrideReflection?ReflectionMethod:FString(TEXT("project_default")));
        Report->SetBoolField(TEXT("reflection_method_overridden"),OverrideReflection);
        Report->SetNumberField(TEXT("shadowed_rect_lights"),ShadowedLightCount);
        Report->SetStringField(TEXT("glass_material_blend"),Materials[TEXT("glass")]->GetBlendMode()==BLEND_Translucent?TEXT("translucent"):TEXT("unexpected"));
        Report->SetArrayField(TEXT("checks"),Samples);
        FString Text;FJsonSerializer::Serialize(Report,TJsonWriterFactory<>::Create(&Text));
        if(!FFileHelper::SaveStringToFile(Text,*ProbeFile,FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))return Fail(TEXT("cannot write probe"));
        if(Report->GetStringField(TEXT("status"))!=TEXT("passed"))return Fail(TEXT("boundary probe failed"));
    }
    UE_LOG(LogTemp,Display,TEXT("RMSIM_CAMPUS_GYM_READY layout=%d glass_panels=%d fixed_gate_leaves=%d rect_lights=%d continuous_barriers=4 native_assets=%d native_instances=%d"),
        LayoutInstances,PanelCount,GateLeaves,LightCount,NativeMeshEvidence->Values.Num(),NativeLayoutInstances);
    return true;
}
