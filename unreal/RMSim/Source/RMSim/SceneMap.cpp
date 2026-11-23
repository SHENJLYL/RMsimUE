#include "MapStage.h"
#include "WTGeneratedCollisionComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/WorldSettings.h"
#include "GrassInstancedStaticMeshComponent.h"
#include "LandscapeProxy.h"
#include "LandscapeHeightfieldCollisionComponent.h"
#include "PhysicsEngine/BodySetup.h"
#include "UObject/Package.h"

namespace
{
bool SceneId(const FString& Value)
{
    if(Value.IsEmpty() || Value.Len()>128 || Value[0]<'a' || Value[0]>'z')return false;
    for(TCHAR C:Value)if(!((C>='a'&&C<='z')||(C>='0'&&C<='9')||C=='_'))return false;
    return true;
}
bool SceneVector(const TSharedPtr<FJsonObject>& Object,const TCHAR* Key,FVector& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* Values=nullptr;
    if(!Object.IsValid() || !Object->TryGetArrayField(Key,Values) || Values->Num()!=3)return false;
    for(int32 I=0;I<3;++I)
        if(!(*Values)[I]->TryGetNumber(Out[I]) || !FMath::IsFinite(Out[I]) || FMath::Abs(Out[I])>100000.0)return false;
    return true;
}
struct FSceneState
{
    int32 Landscapes=0,HeightfieldComponents=0,VisualComponents=0,CollisionComponents=0;
    int64 VisualInstances=0,CollisionInstances=0,GrassInstances=0;
    int32 GrassComponents=0,GeneratedComponents=0;
    int64 GeneratedInstances=0;
    bool GeneratedReady=true;
    bool PhysicsReady=true,MeshesValid=true,GrassCollisionDisabled=true;
};
FSceneState InspectScene(UWorld* World,const FString& AssetPrefix)
{
    FSceneState State;
    if(!World){State.PhysicsReady=false;State.MeshesValid=false;return State;}
    for(TActorIterator<AActor> It(World);It;++It)
    {
        AActor* Actor=*It;
        if(Cast<ALandscapeProxy>(Actor))
        {
            ++State.Landscapes;
            TInlineComponentArray<ULandscapeHeightfieldCollisionComponent*> Components(Actor);
            for(auto* Component:Components)
            {
                ++State.HeightfieldComponents;
                State.PhysicsReady &= Component->IsRegistered() && Component->IsPhysicsStateCreated();
            }
        }
        TInlineComponentArray<UHierarchicalInstancedStaticMeshComponent*> Components(Actor);
        for(auto* Component:Components)
        {
            // Landscape grass clusters stream asynchronously and may be empty
            // while building. Track them apart from immutable source placements.
            if(const auto* GrassComponent=Cast<UGrassInstancedStaticMeshComponent>(Component))
            {
                ++State.GrassComponents;State.GrassInstances+=GrassComponent->GetNumRenderInstances();
                State.GrassCollisionDisabled &= Component->GetCollisionEnabled()==ECollisionEnabled::NoCollision;
                continue;
            }
            UStaticMesh* Mesh=Component->GetStaticMesh();
            if(Component->ComponentHasTag(TEXT("RMSimGeneratedCollision")))
            {
                FString GeneratedError;
                const bool Valid=UWTGeneratedCollisionComponent::ValidateMesh(Mesh,GeneratedError);
                State.MeshesValid &= Valid;
                State.GeneratedReady &= Valid && Component->IsRegistered() && Component->IsPhysicsStateCreated() &&
                    Component->GetCollisionEnabled()==ECollisionEnabled::QueryAndPhysics && Component->GetInstanceCount()>0;
                State.PhysicsReady &= State.GeneratedReady;
                ++State.GeneratedComponents;State.GeneratedInstances+=Component->GetInstanceCount();
                continue;
            }
            State.MeshesValid &= Mesh && Mesh->GetPathName().StartsWith(AssetPrefix) && Component->GetInstanceCount()>0;
            if(Component->GetCollisionEnabled()==ECollisionEnabled::NoCollision)
            {
                ++State.VisualComponents;State.VisualInstances+=Component->GetInstanceCount();
            }
            else
            {
                ++State.CollisionComponents;State.CollisionInstances+=Component->GetInstanceCount();
                State.MeshesValid &= Mesh && Mesh->GetBodySetup() &&
                    Mesh->GetBodySetup()->CollisionTraceFlag==CTF_UseComplexAsSimple;
                State.PhysicsReady &= Component->IsRegistered() && Component->IsPhysicsStateCreated();
            }
        }
    }
    return State;
}
}

bool FMapStage::LoadSceneMap(const TSharedPtr<FJsonObject>& Root,FString& Error)
{
    auto Fail=[&Error](const TCHAR* Reason){Error=Reason;return false;};
    const TArray<FString> Keys={TEXT("schema_version"),TEXT("kind"),TEXT("id"),TEXT("display_name"),TEXT("units"),
        TEXT("coordinate_system"),TEXT("source_sha256"),TEXT("level_asset"),TEXT("bounds_m"),TEXT("spawns"),TEXT("regions")};
    if(!Root.IsValid() || Root->Values.Num()!=Keys.Num())return Fail(TEXT("scene-map fields missing or unknown"));
    for(const FString& Key:Keys)if(!Root->HasField(Key))return Fail(TEXT("scene-map field missing"));
    double Version=0;FString Kind,Units,Coordinates;
    if(!Root->TryGetNumberField(TEXT("schema_version"),Version)||Version!=1 ||
        !Root->TryGetStringField(TEXT("kind"),Kind)||Kind!=TEXT("rm_scene_map") ||
        !Root->TryGetStringField(TEXT("id"),Id)||!SceneId(Id) ||
        !Root->TryGetStringField(TEXT("display_name"),DisplayName)||DisplayName.TrimStartAndEnd().IsEmpty() ||
        !Root->TryGetStringField(TEXT("units"),Units)||Units!=TEXT("m") ||
        !Root->TryGetStringField(TEXT("coordinate_system"),Coordinates)||Coordinates!=TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP") ||
        !Root->TryGetStringField(TEXT("source_sha256"),SceneSourceHash)||SceneSourceHash.Len()!=64 ||
        !Root->TryGetStringField(TEXT("level_asset"),LevelAsset)||LevelAsset!=TEXT("/Game/RMSim/Maps/")+Id+TEXT("/World"))
        return Fail(TEXT("invalid scene-map identity, units or level path"));
    for(TCHAR C:SceneSourceHash)if(!((C>='0'&&C<='9')||(C>='a'&&C<='f')))
        return Fail(TEXT("scene source hash must be lowercase SHA256"));
    const TSharedPtr<FJsonObject>* Bounds=nullptr;FVector Lo,Hi;
    if(!Root->TryGetObjectField(TEXT("bounds_m"),Bounds)||(*Bounds)->Values.Num()!=2 ||
        !SceneVector(*Bounds,TEXT("lo"),Lo)||!SceneVector(*Bounds,TEXT("hi"),Hi) ||
        Hi.X<=Lo.X||Hi.Y<=Lo.Y||Hi.Z<=Lo.Z)return Fail(TEXT("invalid source scene bounds"));
    SourceBoundsMetres=FBox(Lo,Hi);FloorSizeMetres=FVector2D(Hi.X-Lo.X,Hi.Y-Lo.Y);
    Meshes.Reset();Spawns.Reset();Regions.Reset();Obstacles.Reset();InspectionObstacleId.Reset();
    const TArray<TSharedPtr<FJsonValue>>* Entries=nullptr;TSet<FString> Ids;
    if(!Root->TryGetArrayField(TEXT("spawns"),Entries)||Entries->IsEmpty()||Entries->Num()>128)
        return Fail(TEXT("scene map needs 1..128 spawns"));
    for(const auto& Value:*Entries)
    {
        const auto Object=Value->AsObject();FMapSpawn Spawn;
        if(!Object.IsValid()||Object->Values.Num()!=4 ||
            !Object->TryGetStringField(TEXT("id"),Spawn.Id)||!SceneId(Spawn.Id)||Ids.Contains(Spawn.Id) ||
            !Object->TryGetStringField(TEXT("team"),Spawn.Team) ||
            (Spawn.Team!=TEXT("neutral")&&Spawn.Team!=TEXT("red")&&Spawn.Team!=TEXT("blue")) ||
            !SceneVector(Object,TEXT("position_m"),Spawn.GroundMetres) ||
            !Object->TryGetNumberField(TEXT("yaw_rad"),Spawn.YawRadians)||!FMath::IsFinite(Spawn.YawRadians) ||
            FMath::Abs(Spawn.YawRadians)>PI || !SourceBoundsMetres.IsInsideOrOn(Spawn.GroundMetres))
            return Fail(TEXT("invalid, duplicate or out-of-bounds scene spawn"));
        Ids.Add(Spawn.Id);Spawns.Add(Spawn);
    }
    if(!Root->TryGetArrayField(TEXT("regions"),Entries)||!Entries->IsEmpty())
        return Fail(TEXT("saved-scene region bindings require an empty array"));
    FloorZ=Spawns[0].GroundMetres.Z;
    bSavedScene=true;bMeshTerrain=true;
    return true;
}

bool FMapStage::ValidateLoadedScene(UWorld* World,FString& Error) const
{
    if(!World || World->GetOutermost()->GetName()!=LevelAsset)
    {Error=TEXT("loaded world differs from the scene-map level_asset");return false;}
    const AWorldSettings* Settings=World->GetWorldSettings();
    if(!Settings || !Settings->Tags.Contains(FName(*(TEXT("RMSimMapId=")+Id))) ||
        !Settings->Tags.Contains(FName(*(TEXT("RMSimSourceSha256=")+SceneSourceHash))))
    {Error=TEXT("saved scene provenance tags do not match the map package");return false;}
    const FSceneState State=InspectScene(World,TEXT("/Game/RMSim/Maps/")+Id+TEXT("/"));
    if(!State.MeshesValid || State.Landscapes<1 || State.HeightfieldComponents<1 ||
        State.VisualComponents<1 || State.CollisionComponents<1)
    {Error=TEXT("saved scene is missing source Landscape, visual instances or prepared collision meshes");return false;}
    return true;
}

bool FMapStage::IsSceneCollisionReady(UWorld* World) const
{
    if(!bSavedScene)return true;
    const FSceneState State=InspectScene(World,TEXT("/Game/RMSim/Maps/")+Id+TEXT("/"));
    return State.PhysicsReady && State.HeightfieldComponents>0 && State.CollisionComponents>0;
}

void FMapStage::AddSceneReport(UWorld* World,const TSharedRef<FJsonObject>& Report) const
{
    if(!bSavedScene || !World)return;
    const FSceneState State=InspectScene(World,TEXT("/Game/RMSim/Maps/")+Id+TEXT("/"));
    Report->SetStringField(TEXT("level_asset"),LevelAsset);
    Report->SetStringField(TEXT("loaded_world"),World->GetOutermost()->GetName());
    Report->SetStringField(TEXT("source_sha256"),SceneSourceHash);
    Report->SetNumberField(TEXT("landscape_actors"),State.Landscapes);
    Report->SetNumberField(TEXT("heightfield_collision_components"),State.HeightfieldComponents);
    Report->SetNumberField(TEXT("visual_instance_components"),State.VisualComponents);
    Report->SetNumberField(TEXT("collision_instance_components"),State.CollisionComponents);
    Report->SetNumberField(TEXT("visual_instances"),static_cast<double>(State.VisualInstances));
    Report->SetNumberField(TEXT("collision_instances"),static_cast<double>(State.CollisionInstances));
    Report->SetBoolField(TEXT("scene_collision_ready"),State.PhysicsReady);
    Report->SetNumberField(TEXT("generated_collision_components"),State.GeneratedComponents);
    Report->SetNumberField(TEXT("generated_collision_instances"),static_cast<double>(State.GeneratedInstances));
    Report->SetBoolField(TEXT("generated_collision_ready"),State.GeneratedReady);
    Report->SetNumberField(TEXT("streamed_grass_components"),State.GrassComponents);
    Report->SetNumberField(TEXT("streamed_grass_instances"),static_cast<double>(State.GrassInstances));
    Report->SetBoolField(TEXT("streamed_grass_collision_disabled"),State.GrassCollisionDisabled);
}
