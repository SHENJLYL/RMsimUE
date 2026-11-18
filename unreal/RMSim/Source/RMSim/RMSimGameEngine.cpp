#include "RMSimGameEngine.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Widgets/SViewport.h"
#include "VehicleRig.h"

TSharedRef<SViewport> URMSimGameEngine::CreateGameViewportWidget()
{
    TSharedRef<SViewport> Widget=Super::CreateGameViewportWidget();
    double RequestedHz=0.;
    const bool bPersistentTarget=FParse::Value(FCommandLine::Get(),TEXT("RMSimViewportHz="),RequestedHz)
        &&FMath::IsFinite(RequestedHz)&&RequestedHz>=10.&&RequestedHz<=144.;
    if(bPersistentTarget)
    {
        // UGameEngine calls this before GameViewportClient->CreateViewport().
        // FSceneViewport caches this flag in its constructor. A separate scene
        // target retains its pixels while Slate presents intermediate frames.
        Widget->SetRenderDirectlyToWindow(false);
    }
    UE_LOG(LogRMSim,Display,TEXT("RMSIM_VIEWPORT_TARGET mode=%s scene_hz=%.2f"),
        bPersistentTarget?TEXT("persistent_scene_texture"):TEXT("engine_default"),
        bPersistentTarget?RequestedHz:0.);
    return Widget;
}
