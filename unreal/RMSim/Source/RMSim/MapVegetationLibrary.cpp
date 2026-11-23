#include "MapVegetationLibrary.h"

#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GrassInstancedStaticMeshComponent.h"
#include "LandscapeComponent.h"
#include "LandscapeGrassType.h"
#include "LandscapeProxy.h"
#include "RenderingThread.h"
#include "RHI.h"
#include "Serialization/JsonSerializer.h"
#if WITH_EDITOR
#include "AssetCompilingManager.h"
#include "ShaderCompiler.h"
#endif

namespace
{
FString JsonText(const TSharedRef<FJsonObject>& Report)
{
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    return Text;
}

TSharedRef<FJsonObject> GrassReport(ALandscapeProxy* Landscape)
{
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("schema"), TEXT("rmsim_landscape_grass_readback_v1"));
    if (!IsValid(Landscape))
    {
        Report->SetStringField(TEXT("status"), TEXT("failed"));
        Report->SetStringField(TEXT("error"), TEXT("missing landscape"));
        return Report;
    }
    Report->SetStringField(TEXT("landscape"), Landscape->GetPathName());
    Report->SetNumberField(TEXT("landscape_components"), Landscape->LandscapeComponents.Num());
    int32 ValidComponents = 0, WeightedComponents = 0;
    int32 GenerationChecked = 0, FreshComponents = 0, GenerationMismatches = 0;
    TArray<TSharedPtr<FJsonValue>> GenerationRows;
    int64 DataBytes = 0;
    TMap<FName, TSharedPtr<FJsonObject>> Layers;
    for (ULandscapeComponent* Component : Landscape->LandscapeComponents)
    {
        if (!Component) continue;
        const auto& Data = Component->GrassData;
        // HasValidData is NumElements >= 0; the non-exported method is not called across modules.
        const bool bValidData = Data->NumElements >= 0;
        ValidComponents += bValidData ? 1 : 0;
#if WITH_EDITOR && WITH_EDITORONLY_DATA
        // Compare the serialized cache with the same exported computation used by
        // ULandscapeComponent::IsGrassMapOutdated. Never assign GenerationHash.
        const uint32 SavedGeneration = Data->GenerationHash;
        const uint32 CurrentGeneration = Component->ComputeGrassMapGenerationHash();
        const bool bGenerationMatches = SavedGeneration == CurrentGeneration;
        const bool bFreshData = bValidData && bGenerationMatches;
        ++GenerationChecked;
        FreshComponents += bFreshData ? 1 : 0;
        GenerationMismatches += bGenerationMatches ? 0 : 1;
        auto GenerationRow = MakeShared<FJsonObject>();
        GenerationRow->SetStringField(TEXT("component"), Component->GetPathName());
        GenerationRow->SetNumberField(TEXT("saved_generation_hash"), static_cast<double>(SavedGeneration));
        GenerationRow->SetNumberField(TEXT("current_generation_hash"), static_cast<double>(CurrentGeneration));
        GenerationRow->SetBoolField(TEXT("valid_data"), bValidData);
        GenerationRow->SetBoolField(TEXT("matches_current_generation"), bGenerationMatches);
        GenerationRow->SetBoolField(TEXT("fresh_data"), bFreshData);
        GenerationRows.Add(MakeShared<FJsonValueObject>(GenerationRow));
#endif
        WeightedComponents += !Data->WeightOffsets.IsEmpty() ? 1 : 0;
        DataBytes += Data->HeightWeightData.Num();
        for (const auto& Pair : Component->GetNamedGrassTypes())
        {
            if (!IsValid(Pair.Value)) continue;
            auto& Row = Layers.FindOrAdd(Pair.Key);
            if (!Row)
            {
                Row = MakeShared<FJsonObject>();
                Row->SetStringField(TEXT("name"), Pair.Key.ToString());
                Row->SetStringField(TEXT("grass_type"), Pair.Value->GetPathName());
                Row->SetNumberField(TEXT("varieties"), Pair.Value->GrassVarieties.Num());
                Row->SetNumberField(TEXT("weighted_components"), 0);
                Row->SetNumberField(TEXT("nonzero_weight_samples"), 0);
                Row->SetNumberField(TEXT("weight_byte_sum"), 0);
                TArray<TSharedPtr<FJsonValue>> Varieties;
                for (const FGrassVariety& Variety : Pair.Value->GrassVarieties)
                {
                    auto V = MakeShared<FJsonObject>();
                    V->SetStringField(TEXT("mesh"), IsValid(Variety.GrassMesh) ? Variety.GrassMesh->GetPathName() : TEXT(""));
                    V->SetNumberField(TEXT("density_per_100m2"), Variety.GetDensity());
                    V->SetNumberField(TEXT("start_cull_cm"), Variety.GetStartCullDistance());
                    V->SetNumberField(TEXT("end_cull_cm"), Variety.GetEndCullDistance());
                    V->SetNumberField(TEXT("scale_x_min"), Variety.ScaleX.Min);
                    V->SetNumberField(TEXT("scale_x_max"), Variety.ScaleX.Max);
                    V->SetNumberField(TEXT("scale_z_min"), Variety.ScaleZ.Min);
                    V->SetNumberField(TEXT("scale_z_max"), Variety.ScaleZ.Max);
                    Varieties.Add(MakeShared<FJsonValueObject>(V));
                }
                Row->SetArrayField(TEXT("variety_settings"), Varieties);
            }
            const int32* Offset = Data->WeightOffsets.Find(Pair.Key);
            if (Offset && Data->NumElements > 0 && *Offset >= 0 &&
                static_cast<int64>(*Offset) + Data->NumElements <= Data->HeightWeightData.Num())
            {
                int64 Nonzero = 0, Sum = 0;
                for (int32 Index = 0; Index < Data->NumElements; ++Index)
                {
                    const uint8 Value = Data->HeightWeightData[*Offset + Index];
                    Nonzero += Value != 0 ? 1 : 0;
                    Sum += Value;
                }
                Row->SetNumberField(TEXT("weighted_components"), Row->GetNumberField(TEXT("weighted_components")) + 1);
                Row->SetNumberField(TEXT("nonzero_weight_samples"), Row->GetNumberField(TEXT("nonzero_weight_samples")) + Nonzero);
                Row->SetNumberField(TEXT("weight_byte_sum"), Row->GetNumberField(TEXT("weight_byte_sum")) + Sum);
            }
        }
    }
    auto Generation = MakeShared<FJsonObject>();
#if WITH_EDITOR && WITH_EDITORONLY_DATA
    Generation->SetBoolField(TEXT("available"), true);
#else
    Generation->SetBoolField(TEXT("available"), false);
#endif
    Generation->SetStringField(TEXT("method"), TEXT("ULandscapeComponent::ComputeGrassMapGenerationHash"));
    Generation->SetStringField(TEXT("scope"), TEXT("Saved GenerationHash compared with current engine state; no grass build or cache mutation"));
    Generation->SetNumberField(TEXT("checked_components"), GenerationChecked);
    Generation->SetNumberField(TEXT("fresh_components"), FreshComponents);
    Generation->SetNumberField(TEXT("mismatch_components"), GenerationMismatches);
    Generation->SetArrayField(TEXT("components"), GenerationRows);
    Report->SetObjectField(TEXT("generation_freshness"), Generation);
    Report->SetNumberField(TEXT("components_with_valid_data"), ValidComponents);
    Report->SetNumberField(TEXT("components_with_weights"), WeightedComponents);
    Report->SetNumberField(TEXT("height_weight_bytes"), static_cast<double>(DataBytes));
    TArray<FName> Names;
    Layers.GenerateKeyArray(Names);
    Names.Sort(FNameLexicalLess());
    TArray<TSharedPtr<FJsonValue>> LayerRows;
    for (const FName& Name : Names) LayerRows.Add(MakeShared<FJsonValueObject>(Layers[Name]));
    Report->SetArrayField(TEXT("layers"), LayerRows);
    TInlineComponentArray<UGrassInstancedStaticMeshComponent*> GrassComponents(Landscape);
    int64 Instances = 0;
    int32 CollisionComponents = 0;
    TMap<FString, int64> MeshCounts;
    for (const auto* Component : GrassComponents)
    {
        if (!Component) continue;
        // Grass uses a prebuilt render buffer and leaves editable PerInstanceSMData empty.
        const int32 Count = Component->GetNumRenderInstances();
        Instances += Count;
        CollisionComponents += Component->IsCollisionEnabled() ? 1 : 0;
        const UStaticMesh* Mesh = Component->GetStaticMesh();
        MeshCounts.FindOrAdd(IsValid(Mesh) ? Mesh->GetPathName() : TEXT("missing")) += Count;
    }
    auto Meshes = MakeShared<FJsonObject>();
    for (const auto& Pair : MeshCounts) Meshes->SetNumberField(Pair.Key, static_cast<double>(Pair.Value));
    Report->SetObjectField(TEXT("instances_by_mesh"), Meshes);
    Report->SetNumberField(TEXT("generated_hism_components"), GrassComponents.Num());
    Report->SetNumberField(TEXT("generated_instances"), static_cast<double>(Instances));
    Report->SetStringField(TEXT("instance_count_method"), TEXT("UGrassInstancedStaticMeshComponent::GetNumRenderInstances"));
    Report->SetNumberField(TEXT("collision_enabled_grass_components"), CollisionComponents);
    Report->SetStringField(TEXT("status"), TEXT("read_back"));
    return Report;
}
}

FString UMapVegetationLibrary::DescribeLandscapeGrass(ALandscapeProxy* Landscape)
{
    return JsonText(GrassReport(Landscape));
}

FString UMapVegetationLibrary::BuildLandscapeGrass(ALandscapeProxy* Landscape, const TArray<FVector>& PreviewCameras)
{
    auto Failure = MakeShared<FJsonObject>();
    Failure->SetStringField(TEXT("status"), TEXT("failed"));
#if WITH_EDITOR
    if (!IsValid(Landscape) || !Landscape->GetWorld() || Landscape->GetWorld()->IsGameWorld() ||
        !Landscape->GetPathName().StartsWith(TEXT("/Game/RMSim/Maps/")) || GUsingNullRHI ||
        PreviewCameras.IsEmpty() || PreviewCameras.Num() > 16)
    {
        Failure->SetStringField(TEXT("error"), TEXT("grass authoring requires a saved project landscape, rendering RHI and 1..16 preview cameras"));
        return JsonText(Failure);
    }
    for (const FVector& Camera : PreviewCameras)
    {
        if (Camera.ContainsNaN())
        {
            Failure->SetStringField(TEXT("error"), TEXT("invalid preview camera"));
            return JsonText(Failure);
        }
    }
    const double Started = FPlatformTime::Seconds();
    UE_LOG(LogTemp, Display, TEXT("WT_GRASS_PHASE finish_assets begin shader_jobs=%d"),
        GShaderCompilingManager ? GShaderCompilingManager->GetNumRemainingJobs() : 0);
    FAssetCompilingManager::Get().FinishAllCompilation();
    FlushRenderingCommands();
    const double AssetsFinished = FPlatformTime::Seconds();
    UE_LOG(LogTemp, Display, TEXT("WT_GRASS_PHASE finish_assets end elapsed_s=%.3f"), AssetsFinished-Started);
    Landscape->Modify();
    UE_LOG(LogTemp, Display, TEXT("WT_GRASS_PHASE build_maps begin components=%d"), Landscape->LandscapeComponents.Num());
    Landscape->BuildGrassMaps();
    const int32 OutdatedMaps = Landscape->GetOutdatedGrassMapCount();
    const double MapsFinished = FPlatformTime::Seconds();
    UE_LOG(LogTemp, Display, TEXT("WT_GRASS_PHASE build_maps end elapsed_s=%.3f outdated=%d"), MapsFinished-AssetsFinished, OutdatedMaps);
    UE_LOG(LogTemp, Display, TEXT("WT_GRASS_PHASE update_instances begin"));
    Landscape->UpdateGrass(PreviewCameras, true);
    FlushRenderingCommands();
    const double InstancesFinished = FPlatformTime::Seconds();
    UE_LOG(LogTemp, Display, TEXT("WT_GRASS_PHASE update_instances end elapsed_s=%.3f"), InstancesFinished-MapsFinished);
    Landscape->MarkPackageDirty();
    auto Report = GrassReport(Landscape);
    Report->SetNumberField(TEXT("finish_assets_seconds"), AssetsFinished-Started);
    Report->SetNumberField(TEXT("build_maps_seconds"), MapsFinished-AssetsFinished);
    Report->SetNumberField(TEXT("update_instances_seconds"), InstancesFinished-MapsFinished);
    Report->SetNumberField(TEXT("outdated_grass_maps_after_build"), OutdatedMaps);
    Report->SetNumberField(TEXT("preview_camera_count"), PreviewCameras.Num());
    Report->SetBoolField(TEXT("synchronous_instance_update_returned"), true);
    const bool Complete = OutdatedMaps == 0 && Report->GetArrayField(TEXT("layers")).Num() > 0 &&
        Report->GetNumberField(TEXT("components_with_valid_data")) == Landscape->LandscapeComponents.Num() &&
        Report->GetNumberField(TEXT("collision_enabled_grass_components")) == 0;
    Report->SetStringField(TEXT("status"), Complete ? TEXT("built") : TEXT("incomplete"));
    return JsonText(Report);
#else
    Failure->SetStringField(TEXT("error"), TEXT("grass authoring requires the editor"));
    return JsonText(Failure);
#endif
}

FString UMapVegetationLibrary::DescribeLandscapeGrassSamples(ALandscapeProxy* Landscape, const TArray<FVector>& WorldPointsCm)
{
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("schema"), TEXT("rmsim_grass_weight_samples_v1"));
    if (!IsValid(Landscape) || WorldPointsCm.IsEmpty() || WorldPointsCm.Num() > 64)
    {
        Report->SetStringField(TEXT("status"), TEXT("failed"));
        return JsonText(Report);
    }
    auto PairJson = [](double X, double Y)
    {
        return MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{
            MakeShared<FJsonValueNumber>(X), MakeShared<FJsonValueNumber>(Y)});
    };
    const FTransform Transform = Landscape->GetActorTransform();
    TArray<TSharedPtr<FJsonValue>> Samples;
    bool AllValid = true;
    for (const FVector& Point : WorldPointsCm)
    {
        auto Sample = MakeShared<FJsonObject>();
        Sample->SetArrayField(TEXT("world_cm"), {MakeShared<FJsonValueNumber>(Point.X),
            MakeShared<FJsonValueNumber>(Point.Y), MakeShared<FJsonValueNumber>(Point.Z)});
        const FVector Local = Transform.InverseTransformPosition(Point);
        Sample->SetField(TEXT("landscape_local_xy"), PairJson(Local.X, Local.Y));
        ULandscapeComponent* Target = nullptr;
        for (ULandscapeComponent* Component : Landscape->LandscapeComponents)
        {
            if (!Component) continue;
            const FIntPoint Base = Component->GetSectionBase();
            if (Local.X >= Base.X && Local.Y >= Base.Y &&
                Local.X <= Base.X + Component->ComponentSizeQuads && Local.Y <= Base.Y + Component->ComponentSizeQuads)
            { Target = Component; break; }
        }
        bool Valid = Target && !Point.ContainsNaN();
        TArray<TSharedPtr<FJsonValue>> LayerSamples;
        if (Valid)
        {
            const int32 Side = Target->ComponentSizeQuads + 1;
            const FIntPoint Base = Target->GetSectionBase();
            const double X = Local.X - Base.X, Y = Local.Y - Base.Y;
            const int32 X0 = FMath::Clamp(FMath::FloorToInt(X), 0, Side-2);
            const int32 Y0 = FMath::Clamp(FMath::FloorToInt(Y), 0, Side-2);
            const double FractionX = X-X0, FractionY = Y-Y0;
            const int32 Indices[] = {Y0*Side+X0, Y0*Side+X0+1, (Y0+1)*Side+X0, (Y0+1)*Side+X0+1};
            const auto& Data = Target->GrassData;
            Valid = Data->NumElements == 0 || Data->NumElements == Side*Side;
            Sample->SetStringField(TEXT("component"), Target->GetPathName());
            Sample->SetNumberField(TEXT("grid_side"), Side);
            TArray<TSharedPtr<FJsonValue>> CornerWorld;
            for (int32 I=0; I<4; ++I)
            {
                const FVector Corner = Transform.TransformPosition(FVector(Base.X+X0+(I%2), Base.Y+Y0+(I/2), 0));
                CornerWorld.Add(PairJson(Corner.X, Corner.Y));
            }
            for (const auto& Named : Target->GetNamedGrassTypes())
            {
                auto Row = MakeShared<FJsonObject>();
                Row->SetStringField(TEXT("name"), Named.Key.ToString());
                double Values[4] = {0,0,0,0};
                if (const int32* Offset = Data->WeightOffsets.Find(Named.Key))
                {
                    if (*Offset < 0 || static_cast<int64>(*Offset)+Data->NumElements > Data->HeightWeightData.Num())
                        Valid = false;
                    else if (Data->NumElements > 0)
                        for (int32 I=0; I<4; ++I) Values[I] = Data->HeightWeightData[*Offset+Indices[I]];
                }
                TArray<TSharedPtr<FJsonValue>> Corners;
                for (double Value : Values) Corners.Add(MakeShared<FJsonValueNumber>(Value));
                Row->SetArrayField(TEXT("corners"), Corners);
                Row->SetArrayField(TEXT("corner_world_xy_cm"), CornerWorld);
                Row->SetNumberField(TEXT("interpolated_byte"),
                    FMath::Lerp(FMath::Lerp(Values[0],Values[1],FractionX), FMath::Lerp(Values[2],Values[3],FractionX),FractionY));
                LayerSamples.Add(MakeShared<FJsonValueObject>(Row));
            }
        }
        Sample->SetBoolField(TEXT("valid_data"), Valid);
        Sample->SetArrayField(TEXT("layers"), LayerSamples);
        AllValid &= Valid;
        Samples.Add(MakeShared<FJsonValueObject>(Sample));
    }
    Report->SetArrayField(TEXT("samples"), Samples);
    Report->SetStringField(TEXT("status"), AllValid ? TEXT("passed") : TEXT("failed"));
    return JsonText(Report);
}
