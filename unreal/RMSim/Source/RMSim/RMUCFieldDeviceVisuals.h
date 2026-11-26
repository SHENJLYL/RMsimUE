#pragma once

#include "CoreMinimal.h"

class AActor;
class AStaticMeshActor;
class UBoxComponent;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class UPointLightComponent;
class UPrimitiveComponent;
class UProceduralMeshComponent;
class UStaticMesh;
class UStaticMeshComponent;
struct FHitResult;

struct FRMUCRuneFaceVisualState
{
    int32 Lit = 0, Targets = 0, Confirmed = 0, Groups = 0;
    bool bBig = false, bBlanked = false, bActivated = false;
};

#if WITH_DEV_AUTOMATION_TESTS
struct FRMUCRuneLampEvidence
{
    double Plate = 0., Flow = 0., Progress = 0., FlowMoment = 0., FullArm = 0.;
    int32 ProgressSegments = 0;
    int32 ActivePointLights = 0;
};
#endif

class FRMUCFieldDeviceVisuals
{
public:
    bool Bind(AActor& Owner, AStaticMeshActor* CentrePlatform, AStaticMeshActor* CentreCollision = nullptr);
    bool BindDartGates(AActor& Owner, AStaticMeshActor* RedDartStation, AStaticMeshActor* BlueDartStation,
        AStaticMeshActor* RedDartCollision, AStaticMeshActor* BlueDartCollision);
    bool BindBaseArmor(AActor& Owner, AStaticMeshActor* VisualBase, AStaticMeshActor* CollisionBase);
    void UpdateRune(double AngleRad, double MatchSeconds, const FRMUCRuneFaceVisualState& Red, const FRMUCRuneFaceVisualState& Blue);
    void UpdateDartGates(double RedOpenFraction, double BlueOpenFraction);
    void UpdateDartTrays(double RedExtension, double BlueExtension);
    const UStaticMeshComponent* GetDartTray(bool bRed) const { return DartTrays[bRed ? 0 : 1].Get(); }
    const UStaticMeshComponent* GetDartGate(bool bRed) const { return DartGates[bRed ? 0 : 1].Get(); }
    const UStaticMeshComponent* GetDartLauncher(bool bRed) const { return DartLaunchers[bRed ? 0 : 1].Get(); }
    bool TraceDartExit(bool bRed, const FVector& Exit, const FVector& NextPoint) const;
    bool GetDartSceneAnchors(bool bRedShooter, bool bOutpost, FVector& Exit, FVector& Detector) const;
    bool TraceDartLanding(bool bRedShooter, bool bOutpost, const FVector& Detector, FHitResult& Contact) const;
    UStaticMeshComponent* GetBaseDartHead(bool bRed) const;
    void SetBaseDartOffset(bool bRed, const FVector& Offset);
    void UpdateOutposts(double RedAngleRad, double BlueAngleRad);
    void UpdateOutpostAffiliation(bool bRedSiteOwnerRed, bool bBlueSiteOwnerRed, bool bRedSiteAlive, bool bBlueSiteAlive);
    void UpdateBaseArmor(double RedOpenFraction, double BlueOpenFraction);
    void GetBaseJointPositions(bool bRed, TArray<double>& OutPositions) const;
    bool GetBaseTarget(const FHitResult& Hit, bool& bRed, bool* bLarge = nullptr) const;
    bool GetRuneTarget(const FHitResult& Hit, bool& bRed, int32& PlateIndex, double* PlateOffsetCm = nullptr) const;
    bool GetOutpostTarget(const FHitResult& Hit, bool& bRed) const;
    bool UndoOutpostRotation(bool bRed, const FVector& ImpactPoint, const FVector& ImpactNormal,
        FVector& RestPoint, FVector& RestNormal) const;
    bool IsRuneReady() const { return Rune.IsValid(); }
    bool AreDartGatesReady() const { return DartGates[0].IsValid() && DartGates[1].IsValid(); }
    bool IsBaseArmorReady() const { return BaseVisibility.IsValid(); }
    bool IsBaseLargeArmorExposed(bool bRed) const
    {
        return IsBaseArmorReady() && BaseArmorProgress[bRed ? 0 : 1] >= BaseLargeThreshold;
    }
    bool OutpostsAreReady() const { return Outposts[0].IsValid() && Outposts[1].IsValid(); }
    bool IsCentreVisibilitySplitReady() const { return CentreVisibility.IsValid(); }
    const UPrimitiveComponent* GetCentreVisibilityComponent() const;
    bool VerifyTargetQueries(AActor& Owner);
    bool VerifyBaseTargetQueries(AActor& Owner);
    double GetRuneAngleRad() const { return RuneAngleRad; }
#if WITH_DEV_AUTOMATION_TESTS
    bool GetRuneFixtureFace(bool bRed, int32 Index, FTransform& Transform) const;
    FRMUCRuneLampEvidence GetRuneLampEvidence(bool bRed) const;
    bool VerifyBaseDartOffset(bool bRed, const FVector& Offset) const;
#endif

private:
    static constexpr int32 NumPlates = 5;
    static constexpr int32 OutpostPlatesPerTeam = 3;
    static constexpr int32 BasePanelsPerTeam = 3;
    static constexpr int32 RuneFlowCount = 5;
    static constexpr int32 RuneProgressCount = 5;
    static constexpr int32 RuneTargetSection = 0;
    static constexpr int32 RuneFullSection = 1;
    static constexpr int32 RuneFlowSection = 2;
    static constexpr int32 RuneProgressSection = RuneFlowSection + RuneFlowCount;
    static constexpr int32 RuneSectionCount = RuneProgressSection + RuneProgressCount;
    struct FRuneArmLights
    {
        TWeakObjectPtr<UProceduralMeshComponent> Mesh;
        TWeakObjectPtr<UPointLightComponent> NearbyLights[3];
        TWeakObjectPtr<UMaterialInstanceDynamic> Materials[RuneSectionCount];
        float Emission[RuneSectionCount]{};
        float CadEmission = 0.f;
    };
    struct FBasePanel
    {
        TWeakObjectPtr<UStaticMeshComponent> Visual, Query;
        FVector PivotCm = FVector::ZeroVector;
        FVector SlideAxis = FVector::ZeroVector;
        FVector HingeAxis = FVector::ZeroVector;
        double SlideOpenM = 0.;
        double HingeOpenRad = 0.;
    };
    UStaticMesh* BindOutposts(AActor& DeviceOwner, UStaticMeshComponent& CentreMesh);
    int32 BindRuneLights(AActor& DeviceOwner, UStaticMeshComponent& Rotor, UMaterialInterface& Material);
    void BindCentreVisibility(AStaticMeshActor* CentreCollision, bool bIncludesOutposts);
    TWeakObjectPtr<UStaticMeshComponent> Rune;
    TWeakObjectPtr<AStaticMeshActor> Platform;
    TWeakObjectPtr<UStaticMeshComponent> CentreVisibility;
    TWeakObjectPtr<UBoxComponent> Plates[2][NumPlates];
    TArray<TWeakObjectPtr<UMaterialInstanceDynamic>> Lamps[2][NumPlates];
    FRuneArmLights RuneLights[2][NumPlates];
    TWeakObjectPtr<UStaticMeshComponent> DartGates[2], DartLaunchers[2], DartTrays[2];
    FVector DartLauncherExitCm = FVector::ZeroVector;
    FVector DartRedGateClosedCm = FVector::ZeroVector;
    double DartTrayTravelCm = 0.;
    TWeakObjectPtr<UStaticMeshComponent> Outposts[2];
    TArray<TWeakObjectPtr<UMaterialInstanceDynamic>> OutpostMarkers[2];
    TWeakObjectPtr<UMaterialInstanceDynamic> OutpostArmorLamps[2];
    TArray<TWeakObjectPtr<UPointLightComponent>> OutpostFaceLights[2];
    TWeakObjectPtr<UPointLightComponent> OutpostGuideLights[2];
    bool OutpostOwnerRed[2] = {true, false};
    bool OutpostAlive[2] = {true, true};
    bool bOutpostAffiliationInitialized = false;
    TWeakObjectPtr<UBoxComponent> OutpostPlates[2][OutpostPlatesPerTeam];
    TWeakObjectPtr<UStaticMeshComponent> BaseVisibility;
    TWeakObjectPtr<UStaticMeshComponent> BaseDartHeads[2], BaseDartHeadQueries[2];
    TWeakObjectPtr<UBoxComponent> BaseLargePlates[2][BasePanelsPerTeam];
    FBasePanel BasePanels[2][BasePanelsPerTeam];
    double BaseArmorProgress[2] = {-1., -1.};
    double BaseLargeThreshold = 1.;
    double RuneAngleRad = 0.;
    TWeakObjectPtr<UMaterialInstanceDynamic> RuneHubLamps[2];
    TWeakObjectPtr<UPointLightComponent> RuneHubSceneLights[2];
    float RuneHubIntensity[2] = {};
    double RuneLightSeconds = -1.;
};
