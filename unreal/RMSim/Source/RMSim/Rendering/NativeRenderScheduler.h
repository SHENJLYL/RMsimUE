#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace RMSimRendering
{
// Called once after the real world tick. This class only grants presentation
// slots: it never changes solver time, the absolute pacing deadline, or a step.
class NativeRenderScheduler
{
public:
    enum class Reason { Fallback, Cadence, PhysicsPriority, Render, Starvation };
    struct Decision
    {
        bool Active=false;
        bool Render=true;
        Reason Why=Reason::Fallback;
        double LagSeconds=0.;
    };
    static constexpr double MaxRenderGapSeconds=.250;

    void SetTargetHz(double Hz)
    {
        Interval=std::isfinite(Hz)&&Hz>=10.&&Hz<=144.?1./Hz:0.;
        Deactivate();
    }

    void Deactivate()
    {
        Active=false;
        PhaseStartWall=LastWall=LastRenderWall=LastRenderSim=LastSim=-1.;
        NextSlot=0;
        Current={};
    }

    Decision Decide(double Now,double SolverWallDeadline,double StepSeconds,
        double SolverSeconds,std::uint64_t Generation,bool Eligible)
    {
        if(!Eligible||Interval<=0.||!std::isfinite(Now)||Now<0.||
            !std::isfinite(SolverWallDeadline)||!std::isfinite(StepSeconds)||StepSeconds<=0.||
            !std::isfinite(SolverSeconds)||SolverSeconds<0.)
        {
            Deactivate();
            ++FallbackDecisions;
            return Current;
        }
        const bool EpochChanged=HasEpoch&&Generation!=Epoch;
        const bool ClockReset=Active&&(SolverSeconds+Tolerance<LastSim||Now<LastWall);
        const bool NewEpoch=!Active||EpochChanged||ClockReset;
        if(NewEpoch)
        {
            if(EpochChanged||ClockReset)++EpochResets;
            Active=true;HasEpoch=true;Epoch=Generation;PhaseStartWall=Now;
            LastRenderWall=LastRenderSim=-1.;NextSlot=0;
        }
        LastWall=Now;LastSim=SolverSeconds;++Decisions;
        Current={true,false,Reason::Cadence,std::max(0.,Now-SolverWallDeadline)};
        MaxLagSeconds=std::max(MaxLagSeconds,Current.LagSeconds);
        const double DrawGap=Now-(LastRenderWall>=0.?LastRenderWall:PhaseStartWall);
        const bool Starved=DrawGap+Tolerance>=MaxRenderGapSeconds;
        const bool Late=Current.LagSeconds>StepSeconds+Tolerance;

        // Every delayed world step still runs. A bounded interaction frame is
        // the sole exception while physics catches up, and is counted openly.
        if(Late&&!Starved)
        {
            Current.Why=Reason::PhysicsPriority;
            ++PhysicsPrioritySkips;
            return Current;
        }

        const double NextDeadline=PhaseStartWall+double(NextSlot)*Interval;
        // At 200 Hz / 60 Hz the normal pattern is three, three, four world
        // ticks. A late slot must not replay a burst on consecutive ticks.
        const double MinimumSimGap=std::max(1.,std::floor(Interval/StepSeconds+Tolerance))*StepSeconds;
        const bool EnoughPhysics=LastRenderSim<0.||SolverSeconds-LastRenderSim+Tolerance>=MinimumSimGap;
        if(Now+Tolerance<NextDeadline||(!EnoughPhysics&&!Starved))
        {
            ++CadenceSkips;
            return Current;
        }

        Current.Render=true;
        Current.Why=Starved&&(Late||!EnoughPhysics)?Reason::Starvation:Reason::Render;
        if(Current.Why==Reason::Starvation)++StarvationFrames;
        ++RenderFrames;
        if(LastRenderWall>=0.)MaxObservedRenderGapSeconds=std::max(MaxObservedRenderGapSeconds,DrawGap);
        LastRenderWall=Now;LastRenderSim=SolverSeconds;
        // Consume at most the current wall-time slot. Missed display slots are
        // discarded here, with no catch-up debt and no change to physics debt.
        const auto WallSlot=static_cast<std::uint64_t>(std::max(0.,std::floor((Now-PhaseStartWall+Tolerance)/Interval)));
        const auto ConsumedSlot=std::max(NextSlot,WallSlot);
        DroppedSlots+=ConsumedSlot-NextSlot;
        NextSlot=ConsumedSlot+1;
        return Current;
    }

    const Decision& GetDecision() const { return Current; }
    double GetTargetHz() const { return Interval>0.?1./Interval:0.; }
    double GetMaxLagSeconds() const { return MaxLagSeconds; }
    double GetMaxObservedRenderGapSeconds() const { return MaxObservedRenderGapSeconds; }
    std::uint64_t GetDecisions() const { return Decisions; }
    std::uint64_t GetRenderFrames() const { return RenderFrames; }
    std::uint64_t GetPhysicsPrioritySkips() const { return PhysicsPrioritySkips; }
    std::uint64_t GetCadenceSkips() const { return CadenceSkips; }
    std::uint64_t GetStarvationFrames() const { return StarvationFrames; }
    std::uint64_t GetDroppedSlots() const { return DroppedSlots; }
    std::uint64_t GetFallbackDecisions() const { return FallbackDecisions; }
    std::uint64_t GetEpochResets() const { return EpochResets; }
    std::uint64_t GetGeneration() const { return Epoch; }

private:
    static constexpr double Tolerance=1.e-9;
    double Interval=0.,PhaseStartWall=-1.,LastWall=-1.,LastRenderWall=-1.,LastRenderSim=-1.,LastSim=-1.;
    double MaxLagSeconds=0.,MaxObservedRenderGapSeconds=0.;
    std::uint64_t Epoch=0,NextSlot=0,Decisions=0,RenderFrames=0,PhysicsPrioritySkips=0,CadenceSkips=0;
    std::uint64_t StarvationFrames=0,DroppedSlots=0,FallbackDecisions=0,EpochResets=0;
    bool Active=false,HasEpoch=false;
    Decision Current;
};
}
