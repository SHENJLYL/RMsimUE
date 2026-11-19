#include "ArenaShell.h"
#include "CampusGym.h"
#include "MapStage.h"
#include "RosCoordinates.h"

#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/EngineTypes.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
using FObj=TSharedPtr<FJsonObject>;
bool Finite(const FObj& O,const TCHAR* Key,double& Result,double Minimum,double Maximum)
{
    return O.IsValid()&&O->TryGetNumberField(Key,Result)&&FMath::IsFinite(Result)
           &&Result>=Minimum&&Result<=Maximum;
}
bool Vector3(const FObj& O,const TCHAR* Key,FVector& Result)
{
    const TArray<TSharedPtr<FJsonValue>>* Values=nullptr;
    if(!O.IsValid()||!O->TryGetArrayField(Key,Values)||Values->Num()!=3)return false;
    for(int32 Index=0;Index<3;++Index)
        if(!(*Values)[Index]->TryGetNumber(Result[Index])||!FMath::IsFinite(Result[Index]))return false;
    return true;
}
bool JsonObject(const FObj& O,const TCHAR* Key,FObj& Result)
{
    const FObj* Ptr=nullptr;
    if(!O.IsValid()||!O->TryGetObjectField(Key,Ptr))return false;
    Result=*Ptr;
    return Result.IsValid();
}
FTransform Place(const FVector& PositionMetres,const FVector& SizeMetres,
                 const UStaticMesh* Mesh,const double YawDegrees=0)
{
    // Coordinates/geometry are all in metres in source JSON. UE is cm, with
    // ROS Y-left converted into UE Y-right; the imported FBX bounds may have
    // any origin. Preserve its real shape while fitting the desired silhouette.
    const FBox Bounds=Mesh->GetBoundingBox();
    const FVector NativeSize=Bounds.GetSize();
    const FVector Scale(SizeMetres.X*100.0/NativeSize.X,
                        SizeMetres.Y*100.0/NativeSize.Y,
                        SizeMetres.Z*100.0/NativeSize.Z);
    const FQuat Q=FRotator(0,YawDegrees,0).Quaternion();
    const FVector Location=RosCoordinates::Position(PositionMetres)
                           -Q.RotateVector(Bounds.GetCenter()*Scale);
    return FTransform(Q,Location,Scale);
}
struct FInstanceGroup
{
    UInstancedStaticMeshComponent* Component=nullptr;
    UStaticMesh* Mesh=nullptr;
    int32 Instances=0;
};
FInstanceGroup Group(AActor* Host,USceneComponent* Root,UStaticMesh* Mesh,
                     UMaterialInterface* Material,const FLinearColor Color,const FString& Name)
{
    FInstanceGroup Result;
    UInstancedStaticMeshComponent* Component=NewObject<UInstancedStaticMeshComponent>(Host,FName(*Name));
    Component->SetupAttachment(Root);
    Component->SetMobility(EComponentMobility::Static);
    Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Component->SetCollisionProfileName(TEXT("NoCollision"));
    Component->SetCastShadow(false);
    Component->SetReceivesDecals(false);
    Component->SetCanEverAffectNavigation(false);
    Component->SetStaticMesh(Mesh);
    // Use native constant-shader palette materials. The old dynamic Color
    // parameter was not supported by the imported ISM materials and yielded
    // washed-out white audience silhouettes.
    (void)Color;
    for(int32 Index=0;Index<FMath::Max(1,Mesh->GetStaticMaterials().Num());++Index)
        Component->SetMaterial(Index,Material);
    Host->AddInstanceComponent(Component);
    Component->RegisterComponent();
    Result.Component=Component;
    Result.Mesh=Mesh;
    return Result;
}
void Add(FInstanceGroup& Group,const FVector& Centre,const FVector& Size,double Yaw=0)
{
    Group.Component->AddInstance(Place(Centre,Size,Group.Mesh,Yaw),true);
    ++Group.Instances;
}
FLinearColor Color(const FObj& O,const TCHAR* Key,const FLinearColor Fallback)
{
    FVector V;
    if(!Vector3(O,Key,V)||V.X<0||V.Y<0||V.Z<0||V.X>1||V.Y>1||V.Z>1)return Fallback;
    return FLinearColor(V.X,V.Y,V.Z);
}
} // namespace

bool FArenaShell::Spawn(UWorld* World,const FMapStage& Stage,
                       TMap<FString,AStaticMeshActor*>& Actors,FString& Error)
{
    if(!World||!Stage.bMeshTerrain)return false;
    FString Source=FPaths::Combine(FPaths::GetPath(Stage.SourceFile),TEXT("arena-shell.json"));
    if(!IFileManager::Get().FileExists(*Source) && Stage.Id==TEXT("rmuc2027"))
    {
        // The Nav2 console copies the map JSON into a temporary session.
        // Ascend at most 8 parents looking for the matching project assets,
        // independent of the UE project path (including isolated builds).
        // No user-derived path fragments are accepted beyond fixed rmuc2027.
        FString Directory=FPaths::GetPath(Stage.SourceFile);
        for(int32 Depth=0;Depth<8;++Depth)
        {
            const FString Candidate=FPaths::Combine(Directory,
                TEXT("assets/maps/rmuc2027/arena-shell.json"));
            if(IFileManager::Get().FileExists(*Candidate))
            {
                Source=Candidate;
                break;
            }
            const FString Parent=FPaths::GetPath(Directory);
            if(Parent.IsEmpty()||Parent==Directory)break;
            Directory=Parent;
        }
    }
    if(!IFileManager::Get().FileExists(*Source))return true; // Maps without a shell remain unchanged.
    auto Fail=[&Error](const FString& Message){Error=TEXT("Arena shell: ")+Message;return false;};
    FString SourceText;
    FObj Root;
    if(IFileManager::Get().FileSize(*Source)>64*1024||
       !FFileHelper::LoadFileToString(SourceText,*Source)||
       !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(SourceText),Root))
        return Fail(TEXT("missing/oversized/broken manifest"));
    FString CampusSchema;
    if(Root->TryGetStringField(TEXT("schema"),CampusSchema)&&CampusSchema==TEXT("rmsim_campus_gym_v1"))
        return FCampusGym::Spawn(World,Stage,Root,Source,Actors,Error);
    FString Schema,MapId,Units,Coordinates;
    if(!Root->TryGetStringField(TEXT("schema"),Schema)||Schema!=TEXT("rmsim_arena_shell_v1")||
       !Root->TryGetStringField(TEXT("map_id"),MapId)||MapId!=Stage.Id||
       !Root->TryGetStringField(TEXT("units"),Units)||Units!=TEXT("m")||
       !Root->TryGetStringField(TEXT("coordinate_system"),Coordinates)||
       Coordinates!=TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP"))
        return Fail(TEXT("arena map identity/units mismatch"));
    FObj Bounds,Perimeter,Stadium,Models,Colors;
    FVector Lower,Upper;
    if(!JsonObject(Root,TEXT("bounds_m"),Bounds)||
       !Vector3(Bounds,TEXT("lo"),Lower)||!Vector3(Bounds,TEXT("hi"),Upper)||
       !JsonObject(Root,TEXT("perimeter"),Perimeter)||
       !JsonObject(Root,TEXT("stadium"),Stadium)||
       !JsonObject(Root,TEXT("models"),Models)||
       !JsonObject(Root,TEXT("colors"),Colors))
        return Fail(TEXT("missing envelope/style data"));
    if(!Lower.Equals(Stage.SourceBoundsMetres.Min,.01)||
       !Upper.Equals(Stage.SourceBoundsMetres.Max,.01))
        return Fail(TEXT("arena envelope differs from unchanged RMUC geometry"));

    double Inset,Thickness,Bottom,Top,VisualTop,VisualBottom;
    if(!Finite(Perimeter,TEXT("inset_m"),Inset,.02,.8)||
       !Finite(Perimeter,TEXT("wall_thickness_m"),Thickness,.15,.75)||
       !Finite(Perimeter,TEXT("collision_bottom_m"),Bottom,-5,0)||
       !Finite(Perimeter,TEXT("collision_top_m"),Top,3,10)||
       !Finite(Perimeter,TEXT("facade_bottom_m"),VisualBottom,-3,0)||
       !Finite(Perimeter,TEXT("facade_top_m"),VisualTop,.7,3)||
       !(Bottom<VisualBottom&&VisualBottom<VisualTop&&VisualTop<Top)||
       Inset<=Thickness/2||
       Top<Upper.Z+.6)
        return Fail(TEXT("invalid continuous guard-wall profile"));
    double FirstSetback,TierDepth,FirstHeight,TierRise,CanopyHeight,CanopyDepth,TierCount;
    if(!Finite(Stadium,TEXT("tiers"),TierCount,2,6)||FMath::FloorToDouble(TierCount)!=TierCount||
       !Finite(Stadium,TEXT("first_row_setback_m"),FirstSetback,.8,5)||
       !Finite(Stadium,TEXT("tier_depth_m"),TierDepth,.7,2.5)||
       !Finite(Stadium,TEXT("first_row_height_m"),FirstHeight,1.7,6)||
       !Finite(Stadium,TEXT("tier_rise_m"),TierRise,.5,1.5)||
       !Finite(Stadium,TEXT("canopy_height_m"),CanopyHeight,7,20)||
       !Finite(Stadium,TEXT("canopy_depth_m"),CanopyDepth,1,6))
        return Fail(TEXT("invalid spectator tiers"));

    UStaticMesh* Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));
    if(!Cube)return Fail(TEXT("engine primitive mesh unavailable"));
    struct FPaletteFile{const TCHAR* Key;const TCHAR* Asset;};
    const FPaletteFile PaletteFiles[]={
        {TEXT("facade"),TEXT("Mat_Facade")},{TEXT("seats"),TEXT("Mat_Seats")},
        {TEXT("upper_seats"),TEXT("Mat_UpperSeats")},{TEXT("supports"),TEXT("Mat_Supports")},
        {TEXT("roof"),TEXT("Mat_Roof")},{TEXT("accent_red"),TEXT("Mat_AccentRed")},
        {TEXT("accent_blue"),TEXT("Mat_AccentBlue")},{TEXT("accent_light"),TEXT("Mat_AccentLight")}
    };
    TMap<FString,UMaterialInterface*> Palette;
    for(const FPaletteFile& Entry:PaletteFiles)
    {
        const FString AssetPath=FString::Printf(TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Materials/%s.%s"),
                                            Entry.Asset,Entry.Asset);
        UMaterialInterface* NativeMaterial=LoadObject<UMaterialInterface>(nullptr,*AssetPath);
        if(!NativeMaterial)return Fail(TEXT("missing native stadium palette material ")+AssetPath);
        Palette.Add(Entry.Key,NativeMaterial);
    }
    TMap<FString,UStaticMesh*> Meshes;
    for(const TCHAR* ModelName:{TEXT("wall"),TEXT("stairs"),TEXT("column"),
                                TEXT("border-straight"),TEXT("wall-gate")})
    {
        FString Asset;
        if(!Models->TryGetStringField(ModelName,Asset)||
           !Asset.StartsWith(TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Kenney/"))||
           Asset.Contains(TEXT("..")))
            return Fail(FString::Printf(TEXT("invalid CC0 asset path %s"),ModelName));
        UStaticMesh* Mesh=LoadObject<UStaticMesh>(nullptr,*Asset);
        if(!Mesh||!Mesh->GetBoundingBox().IsValid||
           Mesh->GetBoundingBox().GetSize().GetMin()<.01)
            return Fail(FString::Printf(TEXT("CC0 model not yet imported: %s"),ModelName));
        Meshes.Add(ModelName,Mesh);
    }

    const FVector Center=(Lower+Upper)*.5;
    const double HX=(Upper.X-Lower.X)/2,HY=(Upper.Y-Lower.Y)/2;
    const double X=HX-Inset,Y=HY-Inset;
    const double WallHeight=Top-Bottom;
    const double WallCentre=(Top+Bottom)*.5;
    // The four continuous collision slabs overlap at all corners. All widths
    // extend to the field's real edge, with no entry gates or open segments.
    struct FWall {const TCHAR* Name;FVector Centre;FVector Size;};
    const FWall Walls[]={
        {TEXT("west"),FVector(Center.X-X,Center.Y,WallCentre),FVector(Thickness,2*HY,WallHeight)},
        {TEXT("east"),FVector(Center.X+X,Center.Y,WallCentre),FVector(Thickness,2*HY,WallHeight)},
        {TEXT("south"),FVector(Center.X,Center.Y-Y,WallCentre),FVector(2*HX,Thickness,WallHeight)},
        {TEXT("north"),FVector(Center.X,Center.Y+Y,WallCentre),FVector(2*HX,Thickness,WallHeight)}
    };
    for(const FWall& Wall:Walls)
    {
        const FString Name=FString(TEXT("arena_guard_"))+Wall.Name;
        AStaticMeshActor* Actor=World->SpawnActor<AStaticMeshActor>(RosCoordinates::Position(Wall.Centre),
                                                                     FRotator::ZeroRotator);
        if(!Actor)return Fail(TEXT("failed to spawn continuous collision wall"));
        UStaticMeshComponent* Comp=Actor->GetStaticMeshComponent();
        Comp->SetStaticMesh(Cube);
        Actor->SetActorScale3D(Wall.Size); // UE BasicShape Cube = exactly 1 metre.
        Comp->SetCollisionProfileName(TEXT("BlockAll"));
        Comp->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Comp->SetCastShadow(false);
        Actor->SetActorHiddenInGame(true); // Visible lower facade and open safety rails below.
        Actor->Tags.Add(FName(*Name));
        Actors.Add(Name,Actor);
    }

    AActor* Visual=World->SpawnActor<AActor>();
    if(!Visual)return Fail(TEXT("failed to spawn no-collision spectator shell"));
    Visual->Tags.Add(FName(TEXT("arena_spectator_shell")));
    USceneComponent* Origin=NewObject<USceneComponent>(Visual,TEXT("ArenaRoot"));
    Origin->SetMobility(EComponentMobility::Static);
    Visual->SetRootComponent(Origin);
    Visual->AddInstanceComponent(Origin);
    Origin->RegisterComponent();

    const FLinearColor FacadeColor=Color(Colors,TEXT("facade"),FLinearColor(.075,.105,.14));
    FInstanceGroup Facade=Group(Visual,Origin,Cube,Palette[TEXT("facade")],FacadeColor,TEXT("ArenaFacade"));
    FInstanceGroup Bench=Group(Visual,Origin,Cube,Palette[TEXT("seats")],Color(Colors,TEXT("seats"),FLinearColor(.21,.26,.33)),TEXT("ArenaTierBlocks"));
    FInstanceGroup Rear=Group(Visual,Origin,Cube,Palette[TEXT("upper_seats")],Color(Colors,TEXT("upper_seats"),FLinearColor(.17,.19,.22)),TEXT("ArenaUpperTier"));
    FInstanceGroup Steel=Group(Visual,Origin,Cube,Palette[TEXT("supports")],Color(Colors,TEXT("supports"),FLinearColor(.25,.28,.34)),TEXT("ArenaSteel"));
    FInstanceGroup Canopy=Group(Visual,Origin,Cube,Palette[TEXT("roof")],Color(Colors,TEXT("roof"),FLinearColor(.06,.08,.105)),TEXT("ArenaCanopy"));
    FInstanceGroup Red=Group(Visual,Origin,Cube,Palette[TEXT("accent_red")],Color(Colors,TEXT("accent_red"),FLinearColor(.76,.1,.115)),TEXT("ArenaRedLeds"));
    FInstanceGroup Blue=Group(Visual,Origin,Cube,Palette[TEXT("accent_blue")],Color(Colors,TEXT("accent_blue"),FLinearColor(.10,.33,.82)),TEXT("ArenaBlueLeds"));
    FInstanceGroup Light=Group(Visual,Origin,Cube,Palette[TEXT("accent_light")],Color(Colors,TEXT("accent_light"),FLinearColor(.74,.83,.91)),TEXT("ArenaLightBars"));
    FInstanceGroup Stairs=Group(Visual,Origin,Meshes[TEXT("stairs")],Palette[TEXT("upper_seats")],
                               Color(Colors,TEXT("upper_seats"),FLinearColor(.17,.19,.22)),TEXT("KenneyStairs"));
    FInstanceGroup Columns=Group(Visual,Origin,Meshes[TEXT("column")],Palette[TEXT("supports")],
                                Color(Colors,TEXT("supports"),FLinearColor(.25,.28,.34)),TEXT("KenneyColumns"));
    FInstanceGroup Borders=Group(Visual,Origin,Meshes[TEXT("border-straight")],Palette[TEXT("accent_light")],
                                Color(Colors,TEXT("accent_light"),FLinearColor(.74,.83,.91)),TEXT("KenneyBorders"));
    FInstanceGroup ArenaWalls=Group(Visual,Origin,Meshes[TEXT("wall")],Palette[TEXT("facade")],
                                   FacadeColor,TEXT("KenneyWallPanels"));
    FInstanceGroup Gates=Group(Visual,Origin,Meshes[TEXT("wall-gate")],Palette[TEXT("supports")],
                              Color(Colors,TEXT("supports"),FLinearColor(.25,.28,.34)),TEXT("KenneyConcourses"));

    for(const FWall& Wall:Walls)
    {
        FVector FacadeSize=Wall.Size;
        FacadeSize.Z=VisualTop-VisualBottom;
        Add(Facade,FVector(Wall.Centre.X,Wall.Centre.Y,(VisualTop+VisualBottom)*.5),FacadeSize);
        FVector Ribbon=Wall.Size;
        Ribbon.Z=.10;
        if(Wall.Size.X>Wall.Size.Y)Ribbon.Y=.12;else Ribbon.X=.12;
        Add(Wall.Centre.X<Center.X-.1||Wall.Centre.Y<Center.Y-.1?Blue:Red,
            FVector(Wall.Centre.X,Wall.Centre.Y,VisualTop-.07),Ribbon);
        Add(Light,FVector(Wall.Centre.X,Wall.Centre.Y,VisualTop+.12),Ribbon);
    }

    // Front-facing tiers are simple horizontal massing. Back/underside props,
    // hidden back seats and individual chair meshes are intentionally absent.
    for(int32 Tier=0;Tier<static_cast<int32>(TierCount);++Tier)
    {
        const double Distance=FirstSetback+Tier*TierDepth;
        const double Elevation=FirstHeight+Tier*TierRise;
        FInstanceGroup& Seats=Tier>=2?Rear:Bench;
        for(const double Sign:{-1.,1.})
        {
            const double YSeat=Center.Y+Sign*(HY+Distance);
            const double XSeat=Center.X+Sign*(HX+Distance);
            Add(Seats,FVector(Center.X,YSeat,Elevation),FVector(2*HX+3.,TierDepth*.86,.55));
            Add(Seats,FVector(XSeat,Center.Y,Elevation),FVector(TierDepth*.86,2*HY+3.,.55));
            // The source stairs form the visible front of each bank, with
            // aggressive spacing; little geometric detail is seen from the field.
            for(int32 S=-10;S<=10;S+=2)
                Add(Stairs,FVector(Center.X+S*1.22,YSeat-Sign*.38,Elevation+.31),
                    FVector(1.1,.52,.7),Sign>0?180:0);
            for(int32 S=-5;S<=5;S+=2)
                Add(Stairs,FVector(XSeat-Sign*.38,Center.Y+S*1.20,Elevation+.31),
                    FVector(.52,1.1,.7),Sign>0?90:-90);
        }
    }

    // A single open stadium roof ring gives context without shadowed,
    // invisible rooms, stairwells, backstage spaces or animated spectators.
    const double RoofX=HX+FirstSetback+TierCount*TierDepth+CanopyDepth*.35;
    const double RoofY=HY+FirstSetback+TierCount*TierDepth+CanopyDepth*.35;
    for(const double Sign:{-1.,1.})
    {
        Add(Canopy,FVector(Center.X,Center.Y+Sign*RoofY,CanopyHeight),
            FVector(2*RoofX+1,CanopyDepth,.32));
        Add(Canopy,FVector(Center.X+Sign*RoofX,Center.Y,CanopyHeight),
            FVector(CanopyDepth,2*RoofY+1,.32));
        Add(Steel,FVector(Center.X,Center.Y+Sign*(RoofY-CanopyDepth*.45),CanopyHeight-.4),
            FVector(2*RoofX,.14,.36));
        Add(Steel,FVector(Center.X+Sign*(RoofX-CanopyDepth*.45),Center.Y,CanopyHeight-.4),
            FVector(.14,2*RoofY,.36));
        Add(Light,FVector(Center.X,Center.Y+Sign*(RoofY-CanopyDepth*.85),CanopyHeight-.55),
            FVector(2*HX+5,.13,.08));
        Add(Light,FVector(Center.X+Sign*(RoofX-CanopyDepth*.85),Center.Y,CanopyHeight-.55),
            FVector(.13,2*HY+5,.08));
    }
    for(const double SignX:{-1.,0.,1.})
        for(const double SignY:{-1.,1.})
        {
            const double PosX=Center.X+SignX*(HX+FirstSetback+TierCount*TierDepth);
            const double PosY=Center.Y+SignY*(HY+FirstSetback+TierCount*TierDepth);
            Add(Columns,FVector(PosX,PosY,CanopyHeight*.50),FVector(.85,.85,CanopyHeight*.94));
        }
    for(const double SignX:{-1.,1.})
        for(const double SignY:{-1.,0.,1.})
        {
            if(SignY==0)
            {
                Add(Columns,FVector(Center.X+SignX*(HX+FirstSetback+TierCount*TierDepth),Center.Y,
                                    CanopyHeight*.50),FVector(.85,.85,CanopyHeight*.94));
            }
        }
    for(int32 Index=0;Index<8;++Index)
    {
        const double XBorder=Center.X+(Index-3.5)*3.5;
        Add(Borders,FVector(XBorder,Center.Y+HY+FirstSetback+.10,VisualTop+.30),FVector(.9,.26,.43));
    }
    for(const double Sign:{-1.,1.})
    {
        Add(Gates,FVector(Center.X+Sign*(HX+FirstSetback+TierDepth*TierCount-.4),Center.Y,
                          FirstHeight+2*TierRise+.5),FVector(.78,1.7,2.2),Sign>0?90:-90);
        for(int32 I=-3;I<=3;++I)
            Add(ArenaWalls,FVector(Center.X+Sign*(HX+FirstSetback+TierDepth*TierCount-.1),
                                   Center.Y+I*2.0,FirstHeight+2*TierRise+.9),
                FVector(.48,1.3,1.75),Sign>0?90:-90);
    }

    const int32 CosmeticInstances=Facade.Instances+Bench.Instances+Rear.Instances+
        Steel.Instances+Canopy.Instances+Red.Instances+Blue.Instances+Light.Instances+
        Stairs.Instances+Columns.Instances+Borders.Instances+ArenaWalls.Instances+
        Gates.Instances;
    FString ProbePath;
    if(FParse::Value(FCommandLine::Get(),TEXT("ArenaProbeOutput="),ProbePath))
    {
        TSharedRef<FJsonObject> Report=MakeShared<FJsonObject>();
        int32 Tested=0,Passed=0;
        TArray<TSharedPtr<FJsonValue>> Samples;
        FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimArenaSafetySweep),false);
        int32 IgnoredSourceColliders=0;
        for(const auto& Existing:Actors)
            if(Existing.Key.StartsWith(TEXT("collision_")))
            {
                Query.AddIgnoredActor(Existing.Value);
                ++IgnoredSourceColliders;
            }
        // The original map stays physically active. Ignore it only inside
        // this diagnostic sweep so a terrain triangle cannot mask the barrier.
        auto Sweep=[&](const FString& Expected,const FVector& Start,const FVector& End)
        {
            FHitResult Hit;
            const bool bBlocking=World->SweepSingleByChannel(Hit,
                RosCoordinates::Position(Start),RosCoordinates::Position(End),FQuat::Identity,
                ECC_Visibility,FCollisionShape::MakeSphere(12.0f),Query);
            const bool bCorrect=bBlocking&&
                (Expected==TEXT("arena_guard_corner") ?
                    (Hit.GetActor()==Actors.FindRef(TEXT("arena_guard_east"))||
                     Hit.GetActor()==Actors.FindRef(TEXT("arena_guard_west"))||
                     Hit.GetActor()==Actors.FindRef(TEXT("arena_guard_north"))||
                     Hit.GetActor()==Actors.FindRef(TEXT("arena_guard_south"))) :
                    Hit.GetActor()==Actors.FindRef(Expected));
            ++Tested;Passed+=bCorrect?1:0;
            TSharedRef<FJsonObject> Sample=MakeShared<FJsonObject>();
            Sample->SetStringField(TEXT("expected"),Expected);
            Sample->SetStringField(TEXT("hit_actor"),Hit.GetActor()?Hit.GetActor()->GetName():TEXT("none"));
            Sample->SetBoolField(TEXT("passed"),bCorrect);
            Sample->SetNumberField(TEXT("impact_time"),bBlocking?Hit.Time:-1.0);
            Samples.Add(MakeShared<FJsonValueObject>(Sample));
        };
        for(const double Fraction:{-.85,0.,.85})
            for(const double Z:{1.20,3.85})
            {
                const double TestY=Center.Y+Fraction*(HY-.45);
                const double TestX=Center.X+Fraction*(HX-.45);
                Sweep(TEXT("arena_guard_east"),FVector(Center.X+HX-1.15,TestY,Z),
                      FVector(Center.X+HX+.6,TestY,Z));
                Sweep(TEXT("arena_guard_west"),FVector(Center.X-HX+1.15,TestY,Z),
                      FVector(Center.X-HX-.6,TestY,Z));
                Sweep(TEXT("arena_guard_north"),FVector(TestX,Center.Y+HY-1.15,Z),
                      FVector(TestX,Center.Y+HY+.6,Z));
                Sweep(TEXT("arena_guard_south"),FVector(TestX,Center.Y-HY+1.15,Z),
                      FVector(TestX,Center.Y-HY-.6,Z));
            }
        for(const double SX:{-1.,1.})
            for(const double SY:{-1.,1.})
                Sweep(TEXT("arena_guard_corner"),
                    FVector(Center.X+SX*(HX-1.1),Center.Y+SY*(HY-1.1),3.85),
                    FVector(Center.X+SX*(HX+.5),Center.Y+SY*(HY+.5),3.85));
        bool bNoCosmeticCollision=true;
        int32 InstancedGroups=0,Instances=0;
        TInlineComponentArray<UInstancedStaticMeshComponent*> Groups;
        Visual->GetComponents(Groups);
        for(const UInstancedStaticMeshComponent* Component:Groups)
        {
            bNoCosmeticCollision&=Component->GetCollisionEnabled()==ECollisionEnabled::NoCollision;
            bNoCosmeticCollision&=!Component->CastShadow;
            ++InstancedGroups;Instances+=Component->GetInstanceCount();
        }
        Report->SetStringField(TEXT("status"),Passed==Tested&&bNoCosmeticCollision&&Tested==28&&
                               InstancedGroups>=12&&Instances==CosmeticInstances?TEXT("passed"):TEXT("failed"));
        Report->SetStringField(TEXT("scope"),TEXT("native Chaos static wall capsule/sphere sweeps; spectator instancing has no collision or dynamic shadow"));
        Report->SetStringField(TEXT("map_id"),Stage.Id);
        Report->SetNumberField(TEXT("sweep_samples"),Tested);
        Report->SetNumberField(TEXT("sweep_passed"),Passed);
        Report->SetNumberField(TEXT("collision_walls"),4);
        Report->SetNumberField(TEXT("ignored_source_collision_meshes_for_barrier_probe"),IgnoredSourceColliders);
        Report->SetNumberField(TEXT("visual_instances"),Instances);
        Report->SetNumberField(TEXT("visual_instanced_groups"),InstancedGroups);
        Report->SetBoolField(TEXT("cosmetic_collision_disabled"),bNoCosmeticCollision);
        Report->SetArrayField(TEXT("sweeps"),Samples);
        FString Output;
        TSharedRef<TJsonWriter<>> Writer=TJsonWriterFactory<>::Create(&Output);
        FJsonSerializer::Serialize(Report,Writer);
        if(!FFileHelper::SaveStringToFile(Output,*ProbePath))return Fail(TEXT("cannot save native barrier report"));
        if(Report->GetStringField(TEXT("status"))!=TEXT("passed"))
            return Fail(TEXT("native continuous barrier safety test failed; consult ArenaProbeOutput"));
    }
    UE_LOG(LogTemp,Display,TEXT("RMSIM_ARENA_SHELL_READY id=%s guards=4 cosmetic_instances=%d colision_only_4=true source=%s"),
           *Stage.Id,CosmeticInstances,*Source);
    return true;
}
