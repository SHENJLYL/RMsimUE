#pragma once
#include <cmath>
#include <cstdint>

namespace RMSimRendering
{
// A deadline is consumed only after a real scene Draw. Short scheduling jitter
// may be recovered by later real draws; stale pause/overload debt is discarded.
class SceneDrawCadence
{
public:
    static constexpr int MaxCatchupFrames=2;
    static constexpr double MaxCatchupSeconds=.05;

    void SetInterval(double Seconds)
    {
        Interval=std::isfinite(Seconds)&&Seconds>0.?Seconds:0.;
        NextDeadline=-1.;LastDraw=-1.;CatchupSince=-1.;
        DroppedDeadlines=0;CatchupDraws=0;Resyncs=0;MaxLateness=0.;
    }

    bool IsDue(double Now) const
    {
        return std::isfinite(Now)&&(Interval<=0.||NextDeadline<0.||
            Now<LastDraw||Now+Tolerance>=NextDeadline);
    }

    void DidDraw(double Now)
    {
        if(!std::isfinite(Now))return;
        if(Interval<=0.){LastDraw=Now;return;}
        if(NextDeadline<0.||Now<LastDraw)
        {
            if(LastDraw>=0.)++Resyncs;
            NextDeadline=Now+Interval;LastDraw=Now;CatchupSince=-1.;
            return;
        }
        const double Lateness=Now>NextDeadline?Now-NextDeadline:0.;
        if(Lateness>MaxLateness)MaxLateness=Lateness;
        const bool HasBacklog=Lateness+Tolerance>=Interval;
        if(HasBacklog&&CatchupSince<0.)CatchupSince=Now;
        const bool Expired=Lateness>MaxCatchupFrames*Interval+Tolerance||
            Lateness>MaxCatchupSeconds+Tolerance||
            (CatchupSince>=0.&&Now-CatchupSince>MaxCatchupSeconds+Tolerance);

        // This is the one slot consumed by the completed Super::Draw call.
        NextDeadline+=Interval;
        if(Expired)
        {
            if(Now+Tolerance>=NextDeadline)
            {
                const auto Missed=static_cast<std::uint64_t>(
                    std::floor((Now-NextDeadline+Tolerance)/Interval))+1;
                DroppedDeadlines+=Missed;
                NextDeadline+=double(Missed)*Interval;
                ++Resyncs;
            }
            CatchupSince=-1.;
        }
        else if(HasBacklog)++CatchupDraws;
        if(NextDeadline>Now+Tolerance)CatchupSince=-1.;
        LastDraw=Now;
    }

    std::uint64_t GetDroppedDeadlines() const { return DroppedDeadlines; }
    std::uint64_t GetCatchupDraws() const { return CatchupDraws; }
    std::uint64_t GetResyncs() const { return Resyncs; }
    double GetMaxLateness() const { return MaxLateness; }
    double GetNextDeadline() const { return NextDeadline; }

private:
    static constexpr double Tolerance=1.e-9;
    double Interval=0.,NextDeadline=-1.,LastDraw=-1.,CatchupSince=-1.,MaxLateness=0.;
    std::uint64_t DroppedDeadlines=0,CatchupDraws=0,Resyncs=0;
};
}
