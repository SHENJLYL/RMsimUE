#include "RMSimGameEngine.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Widgets/SViewport.h"
#include "VehicleRig.h"
#include "ImportLabGameMode.h"
#include "Dom/JsonObject.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "HAL/PlatformTime.h"
#include "Misc/App.h"
#include "HAL/IConsoleManager.h"

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

void URMSimGameEngine::Tick(float DeltaSeconds,bool bIdleMode)
{
    if(!bNativeRenderInitialized)
    {
        bNativeRenderInitialized=true;
        double RequestedHz=0.;
        FParse::Value(FCommandLine::Get(),TEXT("RMSimViewportHz="),RequestedHz);
        NativeRenderScheduler.SetTargetHz(RequestedHz);
        bNativeRenderConfigured=FParse::Param(FCommandLine::Get(),TEXT("SimRealTimePace"))&&
            NativeRenderScheduler.GetTargetHz()>0.;
        UE_LOG(LogRMSim,Display,TEXT("RMSIM_NATIVE_RENDER_GATE configured=%s hz=%.2f max_render_gap_s=%.3f ")
            TEXT("latch=post_world engine_frame_policy=one_complete_tick display_debt_policy=discard_expired"),
            bNativeRenderConfigured?TEXT("true"):TEXT("false"),NativeRenderScheduler.GetTargetHz(),
            RMSimRendering::NativeRenderScheduler::MaxRenderGapSeconds);
    }
    ++EngineTickCalls;
    bRenderDecisionLatched=false;
    bRenderGateEligibleThisFrame=bNativeRenderConfigured&&!bIdleMode&&
        FApp::UseFixedTimeStep()&&FApp::CanEverRender();
    // Keep the complete EngineLoop lifecycle and its unique GFrameCounter.
    // UGameEngine calls IsRenderingSuspended after this frame's world tick.
    Super::Tick(DeltaSeconds,bIdleMode);
}

bool URMSimGameEngine::IsRenderingSuspended() const
{
    if(!bRenderDecisionLatched)
    {
        bRenderDecisionLatched=true;
        RenderDecisionFrame=GFrameCounter;
        UWorld* World=GameViewport?GameViewport->GetWorld():nullptr;
        AImportLabGameMode* Mode=World?Cast<AImportLabGameMode>(World->GetAuthGameMode()):nullptr;
        if(NativePacingOwner.Get()!=Mode)
        {
            NativePacingOwner=Mode;
            NativeRenderScheduler.Deactivate();
        }
        double Deadline=0.,SolverSeconds=0.,StepSeconds=0.;uint64 Generation=0;
        const bool Eligible=bRenderGateEligibleThisFrame&&IsValid(Mode)&&
            Mode->GetMujocoRenderDeadline(Deadline,SolverSeconds,StepSeconds,Generation);
        if(Eligible)ConfigureNativeRhiOverlap(StepSeconds);
        NativeRenderScheduler.Decide(FPlatformTime::Seconds(),Deadline,StepSeconds,SolverSeconds,Generation,Eligible);
    }
    // LaunchEngineLoop asks again before Slate painting. It receives this same
    // latched decision, while its separate platform/input tick still executes.
    const auto& Decision=NativeRenderScheduler.GetDecision();
    return Super::IsRenderingSuspended()||(Decision.Active&&!Decision.Render);
}

bool URMSimGameEngine::IsNativeRenderGateActive() const
{
    return bRenderDecisionLatched&&NativeRenderScheduler.GetDecision().Active;
}


void URMSimGameEngine::ConfigureNativeRhiOverlap(double StepSeconds) const
{
    if(bNativeRhiOverlapConfigured)return;
    bNativeRhiOverlapConfigured=true;
    NativeRhiWorldStepSeconds=StepSeconds;
    // UE5.8's RHI fence overlap is counted in complete EngineLoop frames.
    // A 60 Hz display needs four 5 ms world frames of queue space. The default
    // two-frame RHI fence otherwise waits after just 10 ms. The N-1 render
    // thread fence and every complete engine-frame lifecycle remain unchanged.
    const double DisplayInterval=1./NativeRenderScheduler.GetTargetHz();
    NativeRhiRequestedOverlap=FMath::Clamp(FMath::CeilToInt(DisplayInterval/StepSeconds-1.e-9),2,8);
    auto* Sync=IConsoleManager::Get().FindConsoleVariable(TEXT("r.GTSyncType"));
    const auto* Lag=IConsoleManager::Get().FindConsoleVariable(TEXT("r.OneFrameThreadLag"));
    if(!Sync)return;
    NativeRhiOriginalSyncType=Sync->GetInt();
    const uint32 SetBy=Sync->GetFlags()&ECVF_SetByMask;
    const int32 RequestedType=2-NativeRhiRequestedOverlap;
    // Preserve explicit project/user/command-line/console choices. Only the
    // engine's untouched constructor default opts in to this native policy.
    if(SetBy==ECVF_SetByConstructor&&NativeRhiOriginalSyncType==0&&RequestedType<0&&Lag&&Lag->GetInt()>0)
    {
        Sync->Set(RequestedType,ECVF_SetByCode);
        bNativeRhiOverlapApplied=Sync->GetInt()==RequestedType;
    }
    UE_LOG(LogRMSim,Display,TEXT("RMSIM_NATIVE_RHI_OVERLAP applied=%s original_type=%d current_type=%d requested_world_frames=%d step_s=%.6f original_set_by=0x%08x policy=constructor_default_only"),
        bNativeRhiOverlapApplied?TEXT("true"):TEXT("false"),NativeRhiOriginalSyncType,Sync->GetInt(),
        NativeRhiRequestedOverlap,NativeRhiWorldStepSeconds,SetBy);
}

void URMSimGameEngine::AppendNativeRenderState(FJsonObject& State) const
{
    const auto& Decision=NativeRenderScheduler.GetDecision();
    State.SetStringField(TEXT("render_gate_policy"),TEXT("native_absolute_deadline; one_complete_engine_tick; no_display_catchup"));
    State.SetBoolField(TEXT("render_gate_configured"),bNativeRenderConfigured);
    State.SetBoolField(TEXT("render_gate_active"),Decision.Active);
    State.SetBoolField(TEXT("render_gate_decision_latched_this_frame"),bRenderDecisionLatched);
    State.SetBoolField(TEXT("render_gate_last_decision_render"),Decision.Render);
    State.SetNumberField(TEXT("render_gate_decision_frame"),double(RenderDecisionFrame));
    State.SetNumberField(TEXT("engine_tick_calls"),double(EngineTickCalls));
    State.SetNumberField(TEXT("native_gate_decisions"),double(NativeRenderScheduler.GetDecisions()));
    State.SetNumberField(TEXT("native_gate_render_frames"),double(NativeRenderScheduler.GetRenderFrames()));
    State.SetNumberField(TEXT("physics_priority_skipped_frames"),double(NativeRenderScheduler.GetPhysicsPrioritySkips()));
    State.SetNumberField(TEXT("render_cadence_skipped_frames"),double(NativeRenderScheduler.GetCadenceSkips()));
    State.SetNumberField(TEXT("render_starvation_frames"),double(NativeRenderScheduler.GetStarvationFrames()));
    State.SetNumberField(TEXT("render_gate_dropped_slots"),double(NativeRenderScheduler.GetDroppedSlots()));
    State.SetNumberField(TEXT("render_gate_fallback_decisions"),double(NativeRenderScheduler.GetFallbackDecisions()));
    State.SetNumberField(TEXT("render_gate_epoch_resets"),double(NativeRenderScheduler.GetEpochResets()));
    State.SetNumberField(TEXT("render_gate_generation"),double(NativeRenderScheduler.GetGeneration()));
    State.SetNumberField(TEXT("render_gate_lag_s"),Decision.LagSeconds);
    State.SetNumberField(TEXT("render_gate_max_lag_s"),NativeRenderScheduler.GetMaxLagSeconds());
    State.SetNumberField(TEXT("render_gate_max_observed_gap_s"),NativeRenderScheduler.GetMaxObservedRenderGapSeconds());
    State.SetNumberField(TEXT("render_gate_starvation_interval_s"),RMSimRendering::NativeRenderScheduler::MaxRenderGapSeconds);
    State.SetStringField(TEXT("render_gate_measurement"),TEXT("last post-world engine-frame decision; scene Draw and display scanout are separate"));
    auto SyncState=MakeShared<FJsonObject>();
    SyncState->SetBoolField(TEXT("configured"),bNativeRhiOverlapConfigured);
    SyncState->SetBoolField(TEXT("automatic_applied"),bNativeRhiOverlapApplied);
    SyncState->SetNumberField(TEXT("original_sync_type"),NativeRhiOriginalSyncType);
    SyncState->SetNumberField(TEXT("requested_world_frame_overlap"),NativeRhiRequestedOverlap);
    SyncState->SetNumberField(TEXT("world_step_s"),NativeRhiWorldStepSeconds);
    const auto* Sync=IConsoleManager::Get().FindConsoleVariable(TEXT("r.GTSyncType"));
    const auto* Lag=IConsoleManager::Get().FindConsoleVariable(TEXT("r.OneFrameThreadLag"));
    if(Sync&&Lag)
    {
        const int32 Type=Sync->GetInt();
        const int32 Overlap=Lag->GetInt()<=0?0:(Type<=0?2-Type:1);
        SyncState->SetNumberField(TEXT("current_sync_type"),Type);
        SyncState->SetNumberField(TEXT("one_frame_thread_lag"),Lag->GetInt());
        SyncState->SetNumberField(TEXT("current_rhi_frame_overlap"),Overlap);
        SyncState->SetNumberField(TEXT("nominal_rhi_overlap_s"),Overlap*NativeRhiWorldStepSeconds);
        SyncState->SetStringField(TEXT("current_set_by"),FString::Printf(TEXT("0x%08x"),Sync->GetFlags()&ECVF_SetByMask));
    }
    SyncState->SetStringField(TEXT("policy"),TEXT("complete engine-frame RHI overlap; N-1 render-thread fence retained; explicit settings preserved; no simulation time changes"));
    State.SetObjectField(TEXT("render_thread_sync"),SyncState);
}
