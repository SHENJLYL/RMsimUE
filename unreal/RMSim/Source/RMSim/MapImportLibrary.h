#pragma once
#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MapImportLibrary.generated.h"

class ALandscape;
class UStaticMesh;
class UTexture2DArray;
class UWTGeneratedCollisionComponent;

// Offline editor authoring only; gameplay consumes the saved level and standard UE actors.
UCLASS()
class RMSIM_API UMapImportLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static bool SetConvexCollision(UStaticMesh* Mesh, const TArray<FVector>& Vertices, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static FString DescribeConvexCollision(UStaticMesh* Mesh);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static bool PrepareSourceLeafMesh(UStaticMesh* Mesh, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static bool PrepareSourcePrecisionMesh(UStaticMesh* Mesh, int32 ExpectedUVChannels, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static FString DescribeMapMesh(UStaticMesh* Mesh);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport", meta=(WorldContext="WorldContextObject"))
    static ALandscape* ImportHeightfield(UObject* WorldContextObject, const FString& DescriptionFile, FString& Error);

    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport", meta=(WorldContext="WorldContextObject"))
    static int32 ImportInstances(UObject* WorldContextObject, const FString& DescriptionFile, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static bool PrepareGeneratedCollision(UStaticMesh* Mesh, const FString& Mode,
        int32 ExpectedTriangles, const FString& SourceFbxSha256, const FString& RecipeSha256,
        const TArray<FVector>& ConvexVerticesCm, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static FString DescribeGeneratedCollision(UStaticMesh* Mesh);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport", meta=(WorldContext="WorldContextObject"))
    static UWTGeneratedCollisionComponent* CreateGeneratedCollisionAudit(UObject* WorldContextObject,
        const FString& ManifestFile, const FVector& AnchorWorldCm, double QueryRangeMetres, FString& Error);
    UFUNCTION(BlueprintCallable, Category="RMSim|MapImport")
    static FString DescribeMapTextureArray(UTexture2DArray* Texture);

};
