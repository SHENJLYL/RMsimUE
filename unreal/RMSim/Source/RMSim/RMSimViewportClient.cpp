#include "RMSimViewportClient.h"
#include "HAL/PlatformTime.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "VehicleRig.h"
#include "Dom/JsonObject.h"
#include "Misc/App.h"
#include "RMSimGameEngine.h"
#include "ImportLabGameMode.h"
#include "Engine/World.h"
#include "DynamicRHI.h"
#include "RenderTimer.h"
#include "Dom/JsonValue.h"

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
    const auto* Engine=Cast<URMSimGameEngine>(GEngine);
    const bool NativeGate=Engine&&Engine->IsNativeRenderGateActive();
    const auto* Mode=GetWorld()?Cast<AImportLabGameMode>(GetWorld()->GetAuthGameMode()):nullptr;
    const bool Dedicated=Mode&&Mode->UsesDedicatedMujoco();
    if(!NativeGate&&bNativeCadenceWasActive)PaintCadence.SetInterval(DisplayIntervalSeconds);
    bNativeCadenceWasActive=NativeGate;
    if(!Dedicated&&!NativeGate&&!PaintCadence.IsDue(Now)){++SkippedFrames;return;}
    if (LastDrawWall > 0.0)
    {
        const double GapMs = 1000.0 * (Now - LastDrawWall);
        LongestDrawGapMs = FMath::Max(LongestDrawGapMs, GapMs);
        if (GapMs > 50.0) {
            ++DrawGapsOver50Ms;
            UE_LOG(LogRMSim, Display, TEXT("SCENE_DRAW_HITCH gap_ms=%.3f previous_draw=%lld"), GapMs, PresentedFrames);
        }
    }
    LastDrawWall = Now;
    Super::Draw(InViewport,SceneCanvas);
    ++PresentedFrames;
    if(!Dedicated&&!NativeGate)PaintCadence.DidDraw(Now); // Native scene + Slate share the engine's one decision.
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
    State->SetNumberField(TEXT("scene_draw_gaps_over_50_ms"),double(DrawGapsOver50Ms));
    State->SetNumberField(TEXT("scene_longest_draw_gap_ms"),LongestDrawGapMs);
    State->SetNumberField(TEXT("scene_skipped_frames"),double(SkippedFrames));
    State->SetNumberField(TEXT("scene_dropped_deadlines"),double(PaintCadence.GetDroppedDeadlines()));
    State->SetNumberField(TEXT("scene_catchup_draws"),double(PaintCadence.GetCatchupDraws()));
    State->SetNumberField(TEXT("scene_cadence_resyncs"),double(PaintCadence.GetResyncs()));
    State->SetNumberField(TEXT("scene_max_deadline_lag_s"),PaintCadence.GetMaxLateness());
    State->SetNumberField(TEXT("max_catchup_frames"),RMSimRendering::SceneDrawCadence::MaxCatchupFrames);
    State->SetNumberField(TEXT("max_catchup_seconds"),RMSimRendering::SceneDrawCadence::MaxCatchupSeconds);
    State->SetStringField(TEXT("measurement"),TEXT("scene Draw calls per monotonic wall second; display scanout unmeasured"));
    if(const auto* Engine=Cast<URMSimGameEngine>(GEngine))Engine->AppendNativeRenderState(*State);
    // Read the same counters as UE's Stat Unit, without enabling a profiler,
    // issuing GPU queries, or synchronizing either rendering thread.
    auto Timing=MakeShared<FJsonObject>();
    const auto AddTiming=[&](const TCHAR* Name,uint32 Cycles)
    {
        const bool Valid=PresentedFrames>0&&Cycles>0;
        const FString Prefix(Name);
        Timing->SetBoolField(Prefix+TEXT("_valid"),Valid);
        if(Valid)Timing->SetNumberField(Prefix+TEXT("_ms"),FPlatformTime::ToMilliseconds(Cycles));
        else Timing->SetField(Prefix+TEXT("_ms"),MakeShared<FJsonValueNull>());
    };
    AddTiming(TEXT("game_thread"),GGameThreadTime);
    AddTiming(TEXT("render_thread"),GRenderThreadTime);
    AddTiming(TEXT("rhi_thread"),GRHIThreadTime);
    AddTiming(TEXT("gpu"),FApp::CanEverRender()&&GDynamicRHI?RHIGetGPUFrameCycles(0):0);
    AddTiming(TEXT("swap_buffer"),GSwapBufferTime);
    Timing->SetNumberField(TEXT("scene_draw_counter_at_read"),double(PresentedFrames));
    Timing->SetNumberField(TEXT("engine_frame_at_read"),double(GFrameCounter));
    Timing->SetBoolField(TEXT("per_draw_sample_freshness_known"),false);
    Timing->SetStringField(TEXT("measurement"),TEXT("latest asynchronous UE cycle counters; zero/unavailable is null; not display scanout"));
    Timing->SetStringField(TEXT("game_thread_scope"),TEXT("excludes instrumented idle; can include uninstrumented SimRealTimePace waiting"));
    State->SetObjectField(TEXT("engine_timing"),Timing);
    return State;
}
