#include "MapStage.h"
#include "Dom/JsonObject.h"

bool FMapStage::LoadMeshMap(const TSharedPtr<FJsonObject>& Root,FString& Error)
{
    auto Fail=[&Error](const TCHAR* Reason){Error=Reason;return false;};
    auto String=[&](const TCHAR* Key,FString& Out){return Root->TryGetStringField(Key,Out)&&!Out.IsEmpty();};
    auto Vec=[](const TSharedPtr<FJsonObject>& O,const TCHAR* Key,FVector& Out)
    {
        const TArray<TSharedPtr<FJsonValue>>* Values=nullptr;
        if (!O.IsValid()||!O->TryGetArrayField(Key,Values)||Values->Num()!=3)return false;
        for(int32 I=0;I<3;++I)if(!(*Values)[I]->TryGetNumber(Out[I])||!FMath::IsFinite(Out[I])||FMath::Abs(Out[I])>10000)return false;
        return true;
    };
    double Version=0;FString Kind,Units,Coordinates,SourceHash;
    if(!Root->TryGetNumberField(TEXT("schema_version"),Version)||Version!=1||!String(TEXT("id"),Id)||
        !String(TEXT("display_name"),DisplayName)||!String(TEXT("units"),Units)||Units!=TEXT("m")||
        !String(TEXT("coordinate_system"),Coordinates)||Coordinates!=TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP")||
        !String(TEXT("source_sha256"),SourceHash)||SourceHash.Len()!=64)return Fail(TEXT("invalid mesh-map identity/units/source"));
    const TSharedPtr<FJsonObject>* Bounds=nullptr;FVector Lo,Hi;
    if(!Root->TryGetObjectField(TEXT("bounds_m"),Bounds)||!Vec(*Bounds,TEXT("lo"),Lo)||!Vec(*Bounds,TEXT("hi"),Hi)||
        Hi.X<=Lo.X||Hi.Y<=Lo.Y||Hi.Z<=Lo.Z)return Fail(TEXT("invalid mesh map bounds"));
    SourceBoundsMetres=FBox(Lo,Hi);FloorSizeMetres=FVector2D(Hi.X-Lo.X,Hi.Y-Lo.Y);FloorZ=0;
    const TArray<TSharedPtr<FJsonValue>>* Parts=nullptr;
    if(!Root->TryGetArrayField(TEXT("meshes"),Parts)||Parts->IsEmpty()||Parts->Num()>128)return Fail(TEXT("invalid mesh array"));
    TSet<FString> Ids;Meshes.Reset();Spawns.Reset();Regions.Reset();Obstacles.Reset();InspectionObstacleId.Reset();
    for(const auto& Value:*Parts)
    {
        const TSharedPtr<FJsonObject> O=Value->AsObject();FMapMesh Part;
        if(!O.IsValid()||!O->TryGetStringField(TEXT("id"),Part.Id)||Part.Id.IsEmpty()||Ids.Contains(Part.Id)||
            !O->TryGetStringField(TEXT("visual_asset"),Part.VisualAsset)||!O->TryGetStringField(TEXT("collision_asset"),Part.CollisionAsset)||
            !Part.VisualAsset.StartsWith(TEXT("/Game/RMSim/Maps/"))||!Part.CollisionAsset.StartsWith(TEXT("/Game/RMSim/Maps/"))||
            Part.VisualAsset.Contains(TEXT(".."))||Part.CollisionAsset.Contains(TEXT("..")))return Fail(TEXT("invalid map mesh reference"));
        Ids.Add(Part.Id);Meshes.Add(Part);
    }
    const TArray<TSharedPtr<FJsonValue>>* SpawnValues=nullptr;
    if(!Root->TryGetArrayField(TEXT("spawns"),SpawnValues)||SpawnValues->IsEmpty()||SpawnValues->Num()>128)return Fail(TEXT("missing mesh-map spawns"));
    Ids.Reset();
    for(const auto& Value:*SpawnValues)
    {
        const TSharedPtr<FJsonObject> O=Value->AsObject();FMapSpawn Spawn;
        if(!O.IsValid()||!O->TryGetStringField(TEXT("id"),Spawn.Id)||Spawn.Id.IsEmpty()||Ids.Contains(Spawn.Id)||
            !O->TryGetStringField(TEXT("team"),Spawn.Team)||!Vec(O,TEXT("position_m"),Spawn.GroundMetres)||
            !O->TryGetNumberField(TEXT("yaw_rad"),Spawn.YawRadians)||!FMath::IsFinite(Spawn.YawRadians)||
            Spawn.GroundMetres.X<Lo.X||Spawn.GroundMetres.X>Hi.X||Spawn.GroundMetres.Y<Lo.Y||Spawn.GroundMetres.Y>Hi.Y)
            return Fail(TEXT("invalid mesh-map spawn"));
        Ids.Add(Spawn.Id);Spawns.Add(Spawn);
    }
    const TArray<TSharedPtr<FJsonValue>>* RegionValues=nullptr;
    if(!Root->TryGetArrayField(TEXT("regions"),RegionValues)||!RegionValues->IsEmpty())
        return Fail(TEXT("mesh-map region bindings are not authored yet; use an empty array"));
    bMeshTerrain=true;
    return true;
}
