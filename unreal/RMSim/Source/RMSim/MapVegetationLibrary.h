#pragma once
#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MapVegetationLibrary.generated.h"

class ALandscapeProxy;

// Offline authoring and readback of native landscape grass; no vehicle/control input.
UCLASS()
class RMSIM_API UMapVegetationLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static FString BuildLandscapeGrass(ALandscapeProxy* Landscape, const TArray<FVector>& PreviewCameras);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static FString DescribeLandscapeGrass(ALandscapeProxy* Landscape);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static FString DescribeLandscapeGrassSamples(ALandscapeProxy* Landscape, const TArray<FVector>& WorldPointsCm);
};
