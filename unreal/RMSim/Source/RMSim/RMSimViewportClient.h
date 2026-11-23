#pragma once
#include "CoreMinimal.h"
#include "Engine/GameViewportClient.h"
#include "Rendering/SceneDrawCadence.h"
#include "RMSimViewportClient.generated.h"
class FJsonObject;

/**
 * Native paced worlds use the engine gate for scene and Slate presentation.
 * Other worlds retain the opt-in viewport-only cadence. Manual game windows
 * delegate every Draw() unless explicitly configured.
 */
UCLASS()
class RMSIM_API URMSimViewportClient : public UGameViewportClient
{
    GENERATED_BODY()
public:
    virtual void Draw(FViewport* InViewport,FCanvas* SceneCanvas) override;
    TSharedRef<FJsonObject> RenderState() const;
    double GetMeasuredSceneHz() const { return MeasuredSceneHz; }
private:
    bool bCadenceInitialized=false;
    bool bNativeCadenceWasActive=false;
    double DisplayIntervalSeconds=0.;
    RMSimRendering::SceneDrawCadence PaintCadence;
    double RenderWindowWall=0.,MeasuredSceneHz=0.;
    double LastDrawWall=0.,LongestDrawGapMs=0.;
    int64 DrawGapsOver50Ms=0;
    int64 PresentedFrames=0,SkippedFrames=0,RenderWindowFrames=0;
};
