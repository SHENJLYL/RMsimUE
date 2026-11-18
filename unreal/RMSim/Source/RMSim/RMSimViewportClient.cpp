#include "RMSimViewportClient.h"
#include "HAL/PlatformTime.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "VehicleRig.h"
#include "Dom/JsonObject.h"
#include "Misc/App.h"

void URMSimViewportClient::Draw(FViewport* InViewport,FCanvas* SceneCanvas)
{
    if(!bCadenceInitialized)
    {
        bCadenceInitialized=true;
        double RequestedHz=0.;
        FParse::Value(FCommandLine::Get(),TEXT("RMSimViewportHz="),RequestedHz);
        if(FMath::IsFinite(RequestedHz)&&RequestedHz>=10.&&RequestedHz<=144.)
            DisplayIntervalSeconds=1./RequestedHz;
        PaintCadence.SetInterval(DisplayIntervalSeconds);
        UE_LOG(LogRMSim,Display,TEXT("RMSIM_VIEWPORT_CADENCE hz=%.2f independent_Chaos_fixed_step=true"),
            DisplayIntervalSeconds>0?1./DisplayIntervalSeconds:0.);
    }
    const double Now=FPlatformTime::Seconds();
    if(!PaintCadence.IsDue(Now)){++SkippedFrames;return;}
    Super::Draw(InViewport,SceneCanvas);
    ++PresentedFrames;
    PaintCadence.DidDraw(Now); // Consume one deadline only after a real scene Draw.
    if(RenderWindowWall<=0.){RenderWindowWall=Now;RenderWindowFrames=PresentedFrames;}
    else if(Now-RenderWindowWall>=1.)
    {
        MeasuredSceneHz=double(PresentedFrames-RenderWindowFrames)/(Now-RenderWindowWall);
        RenderWindowWall=Now;RenderWindowFrames=PresentedFrames;
    }
}

TSharedRef<FJsonObject> URMSimViewportClient::RenderState() const
{
    auto State=MakeShared<FJsonObject>();
    State->SetBoolField(TEXT("renderer_enabled"),FApp::CanEverRender());
    State->SetNumberField(TEXT("target_scene_hz"),DisplayIntervalSeconds>0.?1./DisplayIntervalSeconds:0.);
    State->SetNumberField(TEXT("scene_render_hz"),MeasuredSceneHz);
    State->SetNumberField(TEXT("scene_rendered_frames"),double(PresentedFrames));
    State->SetNumberField(TEXT("scene_skipped_frames"),double(SkippedFrames));
    State->SetNumberField(TEXT("scene_dropped_deadlines"),double(PaintCadence.GetDroppedDeadlines()));
    State->SetNumberField(TEXT("scene_catchup_draws"),double(PaintCadence.GetCatchupDraws()));
    State->SetNumberField(TEXT("scene_cadence_resyncs"),double(PaintCadence.GetResyncs()));
    State->SetNumberField(TEXT("scene_max_deadline_lag_s"),PaintCadence.GetMaxLateness());
    State->SetNumberField(TEXT("max_catchup_frames"),RMSimRendering::SceneDrawCadence::MaxCatchupFrames);
    State->SetNumberField(TEXT("max_catchup_seconds"),RMSimRendering::SceneDrawCadence::MaxCatchupSeconds);
    State->SetStringField(TEXT("measurement"),TEXT("scene Draw calls per monotonic wall second; display scanout unmeasured"));
    return State;
}
