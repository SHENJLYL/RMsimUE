#include "Rendering/SceneDrawCadence.h"
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace
{
int Checks=0;
void Require(bool Passed,const char* Message)
{
    ++Checks;
    if(!Passed){std::cerr<<"FAIL "<<Message<<"\n";std::exit(1);}
}
struct DrawHarness
{
    RMSimRendering::SceneDrawCadence Cadence;
    int ActualDraws=0;
    explicit DrawHarness(double Hz){Cadence.SetInterval(Hz>0.?1./Hz:0.);}
    bool Opportunity(double Now)
    {
        if(!Cadence.IsDue(Now))return false;
        ++ActualDraws; // Represents a completed real Draw, never a missed slot.
        Cadence.DidDraw(Now);
        return true;
    }
};
struct RateResult {double Target,Epoch;int Draws;double Seconds;};
RateResult FixedOpportunities(double Hz,double Epoch)
{
    DrawHarness H(Hz);
    constexpr int Steps=12000;
    for(int I=0;I<Steps;++I)H.Opportunity(Epoch+double(I)*.005);
    Require(std::abs(double(H.ActualDraws)-Hz*60.)<=1.,
        "200Hz opportunities preserve the requested draw cadence");
    Require(H.Cadence.GetDroppedDeadlines()==0,"steady 200Hz has no dropped deadlines");
    Require(H.Cadence.GetCatchupDraws()==0,"quantized steady ticks do not accumulate a full frame debt");
    return {Hz,Epoch,H.ActualDraws,60.};
}
struct ReplayResult
{
    std::size_t Opportunities=0;
    int OldDraws=0,NewDraws=0;
    double Seconds=0.,OldHz=0.,NewHz=0.;
    std::uint64_t Dropped=0,Catchup=0,Resyncs=0;
};
ReplayResult Replay(const char* Path)
{
    std::ifstream Input(Path);
    Require(bool(Input),"frame-time replay input opens");
    DrawHarness H(60.);
    double OldDeadline=-1.,Time=0.,Last=0.,Milliseconds=0.;
    constexpr double Interval=1./60.;
    ReplayResult R;
    bool AllTimesValid=true;
    while(Input>>Milliseconds)
    {
        AllTimesValid=AllTimesValid&&std::isfinite(Milliseconds)&&Milliseconds>=0.;
        Time+=Milliseconds*.001;
        H.Opportunity(Time);
        if(OldDeadline<0.||Time>=OldDeadline)
        {
            ++R.OldDraws;
            if(OldDeadline<0.)OldDeadline=Time+Interval;
            else OldDeadline+=double(std::max(1,int(std::floor((Time-OldDeadline)/Interval))+1))*Interval;
        }
        Last=Time;++R.Opportunities;
    }
    Require(AllTimesValid,"replay frame times are finite nonnegative measured intervals");
    Require(R.Opportunities>1000,"replay covers a sustained recorded frame sequence");
    R.Seconds=Last;R.NewDraws=H.ActualDraws;
    R.OldHz=double(R.OldDraws)/Last;R.NewHz=double(R.NewDraws)/Last;
    R.Dropped=H.Cadence.GetDroppedDeadlines();R.Catchup=H.Cadence.GetCatchupDraws();
    R.Resyncs=H.Cadence.GetResyncs();
    Require(R.NewDraws>=R.OldDraws,"bounded recovery preserves at least the old real-draw opportunities");
    Require(R.NewHz>=57.&&R.NewHz<=63.,"recorded jitter replay meets mean scene cadence");
    Require(std::uint64_t(R.NewDraws)+R.Dropped<=std::uint64_t(std::ceil(Last*60.))+1,
        "completed draws and discarded slots stay within wall-time deadline budget");
    return R;
}
}

int main(int Argc,char** Argv)
{
    std::vector<RateResult> Rates;
    for(double Hz:{10.,30.,60.,120.,144.})Rates.push_back(FixedOpportunities(Hz,0.));
    Rates.push_back(FixedOpportunities(60.,1000000.));

    DrawHarness Query(60.);
    Require(Query.Cadence.IsDue(0.)&&Query.Cadence.IsDue(0.),
        "checking a deadline alone does not consume a draw slot");
    Query.Opportunity(0.);
    Require(!Query.Cadence.IsDue(.005),"one completed draw creates the next deadline");
    Require(Query.Cadence.IsDue(.020)&&Query.Cadence.IsDue(.021),
        "an overdue slot survives repeated inspection without a completed draw");
    Require(Query.ActualDraws==1,"inspecting cadence cannot fabricate completed draws");

    DrawHarness Unlimited(0.);
    for(int I=0;I<100;++I)Unlimited.Opportunity(double(I)*.001);
    Require(Unlimited.ActualDraws==100&&Unlimited.Cadence.GetDroppedDeadlines()==0,
        "unconfigured manual rendering preserves every real opportunity");

    DrawHarness Jitter(60.);
    for(double T:{0.,.040,.045,.050})Jitter.Opportunity(T);
    Require(Jitter.ActualDraws==4,"short jitter is recovered through subsequent real draw calls");
    Require(Jitter.Cadence.GetDroppedDeadlines()==0&&Jitter.Cadence.GetCatchupDraws()>0,
        "recoverable jitter creates catch-up diagnostics without dropping slots");
    Require(!Jitter.Cadence.IsDue(.055),"recovered cadence returns to its future phase");

    DrawHarness Boundary(60.);Boundary.Opportunity(0.);Boundary.Opportunity(.050);
    Require(Boundary.Cadence.GetDroppedDeadlines()==0,"exact two-frame debt remains recoverable");
    Boundary.Opportunity(.055);Boundary.Opportunity(.060);
    Require(!Boundary.Cadence.IsDue(.061)&&Boundary.ActualDraws==4,
        "bounded debt is cleared only by actual later draws");

    DrawHarness OverBoundary(60.);OverBoundary.Opportunity(0.);OverBoundary.Opportunity(.050002);
    Require(OverBoundary.Cadence.GetDroppedDeadlines()==2&&
        OverBoundary.Cadence.GetNextDeadline()>.050002,
        "debt beyond two frames is explicitly discarded instead of replayed");

    DrawHarness Pause(60.);Pause.Opportunity(0.);Pause.Opportunity(1.);
    Require(Pause.ActualDraws==2&&Pause.Cadence.GetDroppedDeadlines()==59,
        "a one-second pause resumes with one actual draw and counts stale deadlines");
    Require(!Pause.Cadence.IsDue(1.005)&&Pause.Cadence.GetResyncs()==1,
        "paused-window debt cannot create an old-frame burst");
    Require(std::abs(Pause.Cadence.GetMaxLateness()-(1.-1./60.))<1.e-12,
        "lateness telemetry exposes actual pause duration");

    DrawHarness SlowTarget(10.);SlowTarget.Opportunity(0.);SlowTarget.Opportunity(.26);
    Require(SlowTarget.Cadence.GetDroppedDeadlines()==1&&!SlowTarget.Cadence.IsDue(.265),
        "50ms maximum debt duration also applies at low target rates");

    DrawHarness Persistent(60.);Persistent.Opportunity(0.);
    for(int I=0;I<4;++I)Persistent.Opportunity(.04+double(I)/60.);
    Require(Persistent.Cadence.GetDroppedDeadlines()==0,"a backlog episode may recover within 50ms");
    Persistent.Opportunity(.04+4./60.);
    Require(Persistent.Cadence.GetDroppedDeadlines()==1&&
        Persistent.Cadence.GetNextDeadline()>.04+4./60.,
        "persistent backlog expires after 50ms instead of chasing old deadlines forever");

    DrawHarness Overload(60.);
    for(int I=0;I<200;++I)Overload.Opportunity(double(I)*.05);
    Require(Overload.ActualDraws==200,"20Hz opportunities cannot be reported as 60Hz actual draws");
    Require(Overload.Cadence.GetDroppedDeadlines()>300,
        "sustained overload exposes missed deadlines");

    DrawHarness Rollback(60.);Rollback.Opportunity(2.);Rollback.Opportunity(1.);
    Require(Rollback.Cadence.GetResyncs()==1&&Rollback.Cadence.GetDroppedDeadlines()==0&&
        Rollback.Cadence.GetNextDeadline()>1.,"wall-clock regression rebases without unsigned slot inflation");
    Require(!Rollback.Cadence.IsDue(std::numeric_limits<double>::quiet_NaN())&&
        !Rollback.Cadence.IsDue(std::numeric_limits<double>::infinity()),
        "invalid wall times never request draws");
    const double Before=Rollback.Cadence.GetNextDeadline();
    Rollback.Cadence.DidDraw(std::numeric_limits<double>::quiet_NaN());
    Require(Rollback.Cadence.GetNextDeadline()==Before,"invalid completion times cannot mutate phase");
    Rollback.Cadence.SetInterval(1./30.);
    Require(Rollback.Cadence.GetResyncs()==0&&Rollback.Cadence.IsDue(10.),
        "a new session clears cadence debt and diagnostics");

    ReplayResult R;
    if(Argc>1)R=Replay(Argv[1]);
    std::cout<<std::setprecision(12)<<"{\"status\":\"passed\",\"checks\":"<<Checks<<",\"rates\":[";
    for(std::size_t I=0;I<Rates.size();++I)
    {
        if(I)std::cout<<",";
        const auto& V=Rates[I];
        std::cout<<"{\"target_hz\":"<<V.Target<<",\"epoch\":"<<V.Epoch
            <<",\"actual_draws\":"<<V.Draws<<",\"seconds\":"<<V.Seconds<<"}";
    }
    std::cout<<"],\"replay\":{\"opportunities\":"<<R.Opportunities<<",\"seconds\":"<<R.Seconds
        <<",\"old_actual_draws\":"<<R.OldDraws<<",\"new_actual_draws\":"<<R.NewDraws
        <<",\"old_hz\":"<<R.OldHz<<",\"new_hz\":"<<R.NewHz<<",\"dropped_deadlines\":"<<R.Dropped
        <<",\"catchup_draws\":"<<R.Catchup<<",\"resyncs\":"<<R.Resyncs
        <<"},\"scope\":\"standalone production-helper test and fixed frame-time replay; native UE render acceptance remains separate\"}\n";
}
