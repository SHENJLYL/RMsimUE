#pragma once
#include "CoreMinimal.h"
#include "Engine/GameEngine.h"
#include "Rendering/NativeRenderScheduler.h"
#include "RMSimGameEngine.generated.h"
class AImportLabGameMode;
class FJsonObject;

/** Select a persistent scene target before the game viewport is constructed. */
UCLASS()
class RMSIM_API URMSimGameEngine : public UGameEngine
{
    GENERATED_BODY()
public:
    virtual TSharedRef<SViewport> CreateGameViewportWidget() override;
    virtual void Tick(float DeltaSeconds,bool bIdleMode) override;
    virtual bool IsRenderingSuspended() const override;
    bool IsNativeRenderGateActive() const;
    void AppendNativeRenderState(FJsonObject& State) const;
private:
    bool bNativeRenderConfigured=false,bNativeRenderInitialized=false,bRenderGateEligibleThisFrame=false;
    uint64 EngineTickCalls=0;
    mutable bool bRenderDecisionLatched=false;
    mutable uint64 RenderDecisionFrame=0;
    mutable TWeakObjectPtr<AImportLabGameMode> NativePacingOwner;
    mutable RMSimRendering::NativeRenderScheduler NativeRenderScheduler;
    void ConfigureNativeRhiOverlap(double StepSeconds) const;
    mutable bool bNativeRhiOverlapConfigured=false,bNativeRhiOverlapApplied=false;
    mutable int32 NativeRhiRequestedOverlap=2,NativeRhiOriginalSyncType=0;
    mutable double NativeRhiWorldStepSeconds=0.;
};
