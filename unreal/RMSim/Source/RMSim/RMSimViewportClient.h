#pragma once
#include "CoreMinimal.h"
#include "Engine/GameViewportClient.h"
#include "Rendering/SceneDrawCadence.h"
#include "RMSimViewportClient.generated.h"
class FJsonObject;

/**
 * Optional rendering cadence independent of the synchronous Chaos fixed step.
 * At 200Hz physics, a 60Hz display may safely retain the last rendered frame.
 * Manual game windows delegate every Draw() unless explicitly configured.
 */
UCLASS()
class RMSIM_API URMSimViewportClient : public UGameViewportClient
{
    GENERATED_BODY()
public:
    virtual void Draw(FViewport* InViewport,FCanvas* SceneCanvas) override;
    TSharedRef<FJsonObject> RenderState() const;
private:
    bool bCadenceInitialized=false;
    double DisplayIntervalSeconds=0.;
    RMSimRendering::SceneDrawCadence PaintCadence;
    double RenderWindowWall=0.,MeasuredSceneHz=0.;
    int64 PresentedFrames=0,SkippedFrames=0,RenderWindowFrames=0;
};
