#include "MapStage.h"
#include "RosCoordinates.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "Engine/World.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Components/StaticMeshComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "PhysicsEngine/BodySetup.h"

namespace
{
using FObj = TSharedPtr<FJsonObject>;
bool Fields(const FObj& O, std::initializer_list<const TCHAR*> Names, std::initializer_list<const TCHAR*> Optional = {})
{
    if (!O.IsValid()) return false;
    int32 Expected = static_cast<int32>(Names.size());
    for (const TCHAR* Name : Names) if (!O->HasField(Name)) return false;
    for (const TCHAR* Name : Optional) if (O->HasField(Name)) ++Expected;
    return O->Values.Num() == Expected;
}
FObj Object(const FObj& O, const TCHAR* Key)
{
    const FObj* Value = nullptr;
    return O.IsValid() && O->TryGetObjectField(Key, Value) ? *Value : nullptr;
}
bool Num(const FObj& O, const TCHAR* Key, double& Value, double Min, double Max)
{
    return O.IsValid() && O->TryGetNumberField(Key, Value) && FMath::IsFinite(Value) && Value >= Min && Value <= Max;
}
bool Vec(const FObj& O, const TCHAR* Key, FVector& V, int32 Count, double Min, double Max)
{
    const TArray<TSharedPtr<FJsonValue>>* A = nullptr;
    if (!O.IsValid() || !O->TryGetArrayField(Key, A) || A->Num() != Count) return false;
    V = FVector::ZeroVector;
    for (int32 I = 0; I < Count; ++I)
        if (!(*A)[I]->TryGetNumber(V[I]) || !FMath::IsFinite(V[I]) || V[I] < Min || V[I] > Max) return false;
    return true;
}
bool Identifier(const FObj& O, const TCHAR* Key, FString& Value)
{
    if (!O.IsValid() || !O->TryGetStringField(Key, Value) || Value.IsEmpty() || Value[0] < 'a' || Value[0] > 'z') return false;
    for (TCHAR C : Value) if (!((C >= 'a' && C <= 'z') || (C >= '0' && C <= '9') || C == '_')) return false;
    return true;
}
bool Team(const FObj& O, FString& V)
{
    return O->TryGetStringField(TEXT("team"), V) && (V == TEXT("red") || V == TEXT("blue") || V == TEXT("neutral"));
}
TArray<TSharedPtr<FJsonValue>> JsonVector(const FVector& V)
{
    return {MakeShared<FJsonValueNumber>(V.X), MakeShared<FJsonValueNumber>(V.Y), MakeShared<FJsonValueNumber>(V.Z)};
}
}

bool FMapStage::Load(const FString& Filename, FString& Error)
{
    auto Fail = [&Error](const TCHAR* Reason) { Error = Reason; return false; };
    SourceFile = FPaths::ConvertRelativePathToFull(Filename);
    const int64 Size = IFileManager::Get().FileSize(*SourceFile);
    FString Text;
    FObj Root;
    if (Size < 0 || Size > 1024 * 1024 || !FFileHelper::LoadFileToString(Text, *SourceFile) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root)) return Fail(TEXT("map JSON unavailable, invalid or larger than 1MiB"));
    bSavedScene=false;LevelAsset.Reset();SceneSourceHash.Reset();
    FString RequestedKind;
    if (Root.IsValid() && Root->TryGetStringField(TEXT("kind"),RequestedKind) && RequestedKind==TEXT("rm_scene_map"))
        return LoadSceneMap(Root,Error);
    if (Root.IsValid() && Root->TryGetStringField(TEXT("kind"),RequestedKind) && RequestedKind==TEXT("rm_mesh_map"))
        return LoadMeshMap(Root,Error);
    bMeshTerrain=false;
    if (!Fields(Root, {TEXT("schema_version"),TEXT("kind"),TEXT("id"),TEXT("display_name"),TEXT("units"),TEXT("coordinate_system"),
        TEXT("floor"),TEXT("boundary"),TEXT("obstacles"),TEXT("spawns"),TEXT("regions")},{TEXT("inspection_obstacle_id")})) return Fail(TEXT("map has missing/unknown fields"));
    double Version;
    FString Kind, Units, Coordinates;
    if (!Num(Root,TEXT("schema_version"),Version,1,1) || !Identifier(Root,TEXT("id"),Id) ||
        !Root->TryGetStringField(TEXT("display_name"),DisplayName) || DisplayName.TrimStartAndEnd().IsEmpty() ||
        !Root->TryGetStringField(TEXT("kind"),Kind) || Kind != TEXT("rm_flat_map") ||
        !Root->TryGetStringField(TEXT("units"),Units) || Units != TEXT("m") ||
        !Root->TryGetStringField(TEXT("coordinate_system"),Coordinates) || Coordinates != TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP")) return Fail(TEXT("unsupported map identity/version/units/coordinates"));
    const FObj Floor = Object(Root,TEXT("floor")), Wall = Object(Root,TEXT("boundary"));
    FVector FloorXY;
    if (!Fields(Floor,{TEXT("size_m"),TEXT("surface_z_m"),TEXT("thickness_m"),TEXT("color_rgb")}) ||
        !Vec(Floor,TEXT("size_m"),FloorXY,2,2,100) || !Num(Floor,TEXT("surface_z_m"),FloorZ,-100,100) ||
        !Num(Floor,TEXT("thickness_m"),FloorThickness,0.01,5) || !Vec(Floor,TEXT("color_rgb"),FloorColor,3,0,1) ||
        !Fields(Wall,{TEXT("height_m"),TEXT("thickness_m")}) || !Num(Wall,TEXT("height_m"),WallHeight,0.1,10) ||
        !Num(Wall,TEXT("thickness_m"),WallThickness,0.05,FMath::Min(FloorXY.X,FloorXY.Y)/4)) return Fail(TEXT("invalid floor/boundary"));
    FloorSizeMetres = FVector2D(FloorXY.X,FloorXY.Y);
    Obstacles.Reset(); Spawns.Reset(); Regions.Reset();
    for (const TCHAR* Category : {TEXT("obstacles"),TEXT("spawns"),TEXT("regions")})
    {
        const TArray<TSharedPtr<FJsonValue>>* Entries = nullptr;
        if (!Root->TryGetArrayField(Category,Entries) || Entries->Num() > 256) return Fail(TEXT("invalid map entry array"));
        TSet<FString> Ids;
        for (const auto& Value : *Entries)
        {
            const FObj O = Value->AsObject();
            FString EntryId;
            if (!Identifier(O,TEXT("id"),EntryId) || Ids.Contains(EntryId)) return Fail(TEXT("invalid/duplicate map entry id"));
            Ids.Add(EntryId);
            if (FCString::Strcmp(Category,TEXT("obstacles")) == 0)
            {
                FMapBox B; B.Id = EntryId;
                if (!Fields(O,{TEXT("id"),TEXT("center_m"),TEXT("size_m"),TEXT("yaw_rad"),TEXT("color_rgb")}) ||
                    !Vec(O,TEXT("center_m"),B.CenterMetres,3,-100,100) || !Vec(O,TEXT("size_m"),B.SizeMetres,3,0.02,100) ||
                    !Num(O,TEXT("yaw_rad"),B.YawRadians,-PI,PI) || !Vec(O,TEXT("color_rgb"),B.Color,3,0,1) ||
                    !FMath::IsNearlyEqual(B.CenterMetres.Z-B.SizeMetres.Z/2,FloorZ,1.e-6)) return Fail(TEXT("invalid flat obstacle"));
                const double C=FMath::Abs(FMath::Cos(B.YawRadians)), S=FMath::Abs(FMath::Sin(B.YawRadians));
                const FVector2D Ext((C*B.SizeMetres.X+S*B.SizeMetres.Y)/2,(S*B.SizeMetres.X+C*B.SizeMetres.Y)/2);
                if (FMath::Abs(B.CenterMetres.X)+Ext.X > FloorXY.X/2-WallThickness ||
                    FMath::Abs(B.CenterMetres.Y)+Ext.Y > FloorXY.Y/2-WallThickness) return Fail(TEXT("obstacle extends outside inner walls"));
                Obstacles.Add(B);
            }
            else if (FCString::Strcmp(Category,TEXT("spawns")) == 0)
            {
                FMapSpawn Spawn; Spawn.Id=EntryId;
                if (!Fields(O,{TEXT("id"),TEXT("team"),TEXT("position_m"),TEXT("yaw_rad")}) || !Team(O,Spawn.Team) ||
                    !Vec(O,TEXT("position_m"),Spawn.GroundMetres,3,-100,100) || !Num(O,TEXT("yaw_rad"),Spawn.YawRadians,-PI,PI) ||
                    !FMath::IsNearlyEqual(Spawn.GroundMetres.Z,FloorZ,1.e-6) ||
                    FMath::Abs(Spawn.GroundMetres.X)>=FloorXY.X/2-WallThickness || FMath::Abs(Spawn.GroundMetres.Y)>=FloorXY.Y/2-WallThickness) return Fail(TEXT("invalid spawn ground pose"));
                for (const FMapBox& B : Obstacles)
                {
                    const FVector D=Spawn.GroundMetres-B.CenterMetres;
                    const double C=FMath::Cos(B.YawRadians), S=FMath::Sin(B.YawRadians);
                    if (FMath::Abs(C*D.X+S*D.Y)<=B.SizeMetres.X/2 && FMath::Abs(-S*D.X+C*D.Y)<=B.SizeMetres.Y/2) return Fail(TEXT("spawn point inside obstacle"));
                }
                Spawns.Add(Spawn);
            }
            else
            {
                FMapRegion R; R.Id=EntryId; FVector Center, RegionSize;
                if (!Fields(O,{TEXT("id"),TEXT("kind"),TEXT("team"),TEXT("center_m"),TEXT("size_m")}) || !Team(O,R.Team) ||
                    !O->TryGetStringField(TEXT("kind"),R.Kind) || (R.Kind!=TEXT("spawn") && R.Kind!=TEXT("supply") && R.Kind!=TEXT("capture")) ||
                    !Vec(O,TEXT("center_m"),Center,2,-100,100) || !Vec(O,TEXT("size_m"),RegionSize,2,0.05,100) ||
                    FMath::Abs(Center.X)+RegionSize.X/2 > FloorXY.X/2-WallThickness ||
                    FMath::Abs(Center.Y)+RegionSize.Y/2 > FloorXY.Y/2-WallThickness) return Fail(TEXT("invalid region"));
                R.CenterMetres=FVector2D(Center.X,Center.Y); R.SizeMetres=FVector2D(RegionSize.X,RegionSize.Y); Regions.Add(R);
            }
        }
    }
    if (Spawns.IsEmpty()) return Fail(TEXT("missing spawn"));
    InspectionObstacleId.Reset();
    if (Root->HasField(TEXT("inspection_obstacle_id")))
    {
        if (!Root->TryGetStringField(TEXT("inspection_obstacle_id"),InspectionObstacleId)) return Fail(TEXT("invalid inspection obstacle id"));
        if (!InspectionObstacleId.IsEmpty() && (!Identifier(Root,TEXT("inspection_obstacle_id"),InspectionObstacleId) ||
            !Obstacles.ContainsByPredicate([this](const FMapBox& B){ return B.Id==InspectionObstacleId; }))) return Fail(TEXT("unknown inspection obstacle"));
    }
    return true;
}

bool FMapStage::SpawnGeometry(UWorld* World, TMap<FString,AStaticMeshActor*>& Boxes, FString& Error) const
{
    if (bSavedScene)return ValidateLoadedScene(World,Error);
    if (bMeshTerrain)
    {
        for (const FMapMesh& Part : Meshes)
        {
            for (bool Collision : {false,true})
            {
                UStaticMesh* Mesh=LoadObject<UStaticMesh>(nullptr,*(Collision ? Part.CollisionAsset : Part.VisualAsset));
                if (!Mesh || (Collision && (!Mesh->GetBodySetup() || Mesh->GetBodySetup()->CollisionTraceFlag!=CTF_UseComplexAsSimple)))
                { Error=TEXT("map mesh missing or triangle collision not prepared"); return false; }
                AStaticMeshActor* A=World->SpawnActor<AStaticMeshActor>();
                UStaticMeshComponent* Component=A->GetStaticMeshComponent();
                Component->SetStaticMesh(Mesh);
                Component->SetCollisionProfileName(Collision ? TEXT("BlockAll") : TEXT("NoCollision"));
                Component->SetCollisionEnabled(Collision ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
                if (Collision) { A->SetActorHiddenInGame(true); Component->SetCastShadow(false); }
                const FString GeometryId=(Collision ? TEXT("collision_") : TEXT("visual_"))+Part.Id;
                A->Tags.Add(FName(*GeometryId)); Boxes.Add(GeometryId,A);
            }
        }
        return true;
    }
    UStaticMesh* Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));
    UMaterialInterface* Material=LoadObject<UMaterialInterface>(nullptr,TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    if (!World || !Cube || !Material) { Error=TEXT("map primitive assets unavailable"); return false; }
    auto Box=[&](const FString& Key,const FVector& Centre,const FVector& Size,double Yaw,const FVector& Color,bool Blocking)
    {
        AStaticMeshActor* A=World->SpawnActor<AStaticMeshActor>(RosCoordinates::Position(Centre),RosCoordinates::Rotation(FVector(0,0,Yaw)).Rotator());
        UStaticMeshComponent* Mesh=A->GetStaticMeshComponent();
        Mesh->SetStaticMesh(Cube); A->SetActorScale3D(Size); // Engine cube is exactly 1m.
        Mesh->SetCollisionProfileName(Blocking ? TEXT("BlockAll") : TEXT("NoCollision"));
        Mesh->SetCollisionEnabled(Blocking ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
        UMaterialInstanceDynamic* MID=UMaterialInstanceDynamic::Create(Material,A);
        MID->SetVectorParameterValue(TEXT("Color"),FLinearColor(Color.X,Color.Y,Color.Z)); Mesh->SetMaterial(0,MID);
        A->Tags.Add(FName(*Key)); Boxes.Add(Key,A);
    };
    Box(TEXT("floor"),FVector(0,0,FloorZ-FloorThickness/2),FVector(FloorSizeMetres.X,FloorSizeMetres.Y,FloorThickness),0,FloorColor,true);
    for (double Sign : {-1.0,1.0})
    {
        Box(FString::Printf(TEXT("wall_x_%s"),Sign<0?TEXT("neg"):TEXT("pos")),FVector(Sign*(FloorSizeMetres.X-WallThickness)/2,0,FloorZ+WallHeight/2),FVector(WallThickness,FloorSizeMetres.Y,WallHeight),0,FloorColor*0.65,true);
        Box(FString::Printf(TEXT("wall_y_%s"),Sign<0?TEXT("neg"):TEXT("pos")),FVector(0,Sign*(FloorSizeMetres.Y-WallThickness)/2,FloorZ+WallHeight/2),FVector(FloorSizeMetres.X,WallThickness,WallHeight),0,FloorColor*0.65,true);
    }
    for (const FMapBox& B : Obstacles) Box(TEXT("obstacle_")+B.Id,B.CenterMetres,B.SizeMetres,B.YawRadians,B.Color,true);
    for (const FMapRegion& R : Regions)
    {
        const FVector Color=R.Team==TEXT("red") ? FVector(0.7,0.12,0.12) : R.Team==TEXT("blue") ? FVector(0.1,0.25,0.75) : FVector(0.75,0.58,0.1);
        Box(TEXT("region_")+R.Id,FVector(R.CenterMetres.X,R.CenterMetres.Y,FloorZ+0.006),FVector(R.SizeMetres.X,R.SizeMetres.Y,0.01),0,Color,false);
    }
    return true;
}

TArray<FString> FMapStage::RegionsAt(const FVector& World) const
{
    TArray<FString> Result;
    const FVector2D Metres(World.X/100.0,-World.Y/100.0);
    for (const FMapRegion& R : Regions)
        if (FMath::Abs(Metres.X-R.CenterMetres.X)<=R.SizeMetres.X/2 && FMath::Abs(Metres.Y-R.CenterMetres.Y)<=R.SizeMetres.Y/2) Result.Add(R.Id);
    return Result;
}

bool FMapStage::WriteReport(const FString& Filename,const FMapSpawn& Spawn,const TMap<FString,AStaticMeshActor*>& Boxes,const FVector& Location,bool Overlapping,UWorld* World) const
{
    // Evidence is written to a fresh path, never over an input map or a previous run.
    if (IFileManager::Get().FileExists(*Filename)) return false;
    TSharedRef<FJsonObject> Report=MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("map_id"),Id); Report->SetStringField(TEXT("source_file"),SourceFile);
    Report->SetStringField(TEXT("spawn_id"),Spawn.Id); Report->SetStringField(TEXT("scope"),TEXT("actual static map geometry, not referee rules"));
    Report->SetArrayField(TEXT("vehicle_position_cm"),JsonVector(Location)); Report->SetBoolField(TEXT("initial_overlap"),Overlapping);
    Report->SetNumberField(TEXT("obstacle_count"),Obstacles.Num()); Report->SetNumberField(TEXT("region_count"),Regions.Num());
    Report->SetNumberField(TEXT("mesh_pairs"),Meshes.Num());
    Report->SetStringField(TEXT("map_kind"),bSavedScene ? TEXT("rm_scene_map") : bMeshTerrain ? TEXT("rm_mesh_map") : TEXT("rm_flat_map"));
    AddSceneReport(World,Report);
    TArray<TSharedPtr<FJsonValue>> Entries, CurrentRegions;
    for (const auto& Pair : Boxes)
    {
        const FBox Bounds=Pair.Value->GetComponentsBoundingBox(true,false);
        TSharedRef<FJsonObject> Entry=MakeShared<FJsonObject>();
        Entry->SetStringField(TEXT("id"),Pair.Key); Entry->SetArrayField(TEXT("location_cm"),JsonVector(Pair.Value->GetActorLocation()));
        Entry->SetNumberField(TEXT("yaw_degrees"),Pair.Value->GetActorRotation().Yaw);
        Entry->SetArrayField(TEXT("bounds_min_cm"),JsonVector(Bounds.Min)); Entry->SetArrayField(TEXT("bounds_max_cm"),JsonVector(Bounds.Max));
        Entries.Add(MakeShared<FJsonValueObject>(Entry));
    }
    for (const FString& R : RegionsAt(Location)) CurrentRegions.Add(MakeShared<FJsonValueString>(R));
    Report->SetArrayField(TEXT("geometry"),Entries); Report->SetArrayField(TEXT("initial_regions"),CurrentRegions);
    FString Text;
    IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename),true);
    return FJsonSerializer::Serialize(Report,TJsonWriterFactory<>::Create(&Text)) && FFileHelper::SaveStringToFile(Text,*Filename,FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
}
