#include "Sensors/ScanCadence.h"
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
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
struct Sample {double Time;std::int64_t OffsetNs;};
struct Scan {double Start,End,Publication;std::vector<Sample> Samples;};
struct Result {std::string Name;std::size_t Scans;double MeasuredHz;};
Result Exercise(const char* Name,double Hz,const std::vector<double>& Steps,int Count,double Epoch)
{
    RMSimSensors::ScanCadence Cadence;Cadence.Start(Epoch,Hz);
    double Time=Epoch,Elapsed=0.,Start=Epoch,End=Epoch,MaxStep=0.;
    std::vector<Sample> Pending;
    std::vector<Scan> Scans;
    for(int I=0;I<Count;++I)
    {
        const double Dt=Steps[std::size_t(I)%Steps.size()];
        MaxStep=std::max(MaxStep,Dt);Elapsed+=Dt;Time=Epoch+Elapsed;
        if(Cadence.Advance(Time))
        {
            Scans.push_back({Start,End,Time,std::move(Pending)});
            Pending.clear();Start=Time;End=Time;
        }
        Pending.push_back({Time,std::llround((Time-Start)*1.e9)});End=Time;
    }
    const auto Expected=std::size_t(std::floor((Time-Epoch+1.e-6)*Hz));
    Require(Scans.size()==Expected,"scan count follows configured frequency and elapsed physics time");
    Require(Scans.size()>100,"observation spans enough complete scans");
    const double Observed=double(Scans.size()-1)/(Scans.back().Publication-Scans.front().Publication);
    Require(std::abs(Observed-Hz)<.01,"float/fractional physical steps retain source scan frequency");
    bool ActualTimes=true,Offsets=true,Intervals=true;
    for(std::size_t I=0;I<Scans.size();++I)
    {
        const auto& S=Scans[I];
        ActualTimes&=S.Start<=S.End&&S.End<=S.Publication;
        const auto StartNs=std::llround(S.Start*1.e9),EndNs=std::llround(S.End*1.e9);
        std::int64_t PreviousOffset=-1;
        for(const auto& P:S.Samples)
        {
            ActualTimes&=P.Time>=S.Start&&P.Time<=S.End;
            Offsets&=P.OffsetNs>=0&&P.OffsetNs>=PreviousOffset&&
                     StartNs+P.OffsetNs<=EndNs+2&&
                     std::abs(double(StartNs+P.OffsetNs)-P.Time*1.e9)<=1.1;
            PreviousOffset=P.OffsetNs;
        }
        if(I)
        {
            const double Interval=S.Publication-Scans[I-1].Publication;
            Intervals&=Interval>=1./Hz-MaxStep-2.e-6&&Interval<=1./Hz+MaxStep+2.e-6;
        }
    }
    Require(ActualTimes,"scan start/end enclose actual samples without invented subframes");
    Require(Offsets,"Livox nanosecond offsets remain monotonic and within actual end time");
    Require(Intervals,"individual scan intervals remain within physical sampling quantization");
    return {Name,Scans.size(),Observed};
}
}
int main()
{
    const double Chaos5ms=double(float(.005));
    std::vector<Result> Results;
    Results.push_back(Exercise("chaos_float_5ms_mid360_10hz",10.,{Chaos5ms},20000,0.));
    Results.push_back(Exercise("non_divisor_30hz",30.,{Chaos5ms},20000,0.));
    Results.push_back(Exercise("non_divisor_7hz",7.,{Chaos5ms},20000,0.));
    Results.push_back(Exercise("jittered_physics",10.,{.003,.007,.004,.006},20000,12.3456789));
    Results.push_back(Exercise("nonzero_clock_epoch",10.,{Chaos5ms},20000,1000000.));

    RMSimSensors::ScanCadence Gap;Gap.Start(7.,10.);
    Require(!Gap.Advance(7.05),"no premature scan before first deadline");
    Require(Gap.Advance(7.8),"a long physical gap closes one actual observation window");
    Require(!Gap.Advance(7.8),"a gap does not emit fabricated catch-up scans");
    Require(Gap.Advance(7.9),"phase remains anchored after gap");
    Gap.Reset();
    Require(!Gap.Advance(100.),"discarded scan history remains inactive");
    Gap.Start(100.,10.);
    Require(!Gap.Advance(100.05)&&Gap.Advance(100.1),"reconnect starts a fresh scan phase");

    std::cout<<std::setprecision(12)<<"{\"status\":\"passed\",\"checks\":"<<Checks<<",\"cases\":[";
    for(std::size_t I=0;I<Results.size();++I)
    {
        if(I)std::cout<<",";
        const auto& R=Results[I];
        std::cout<<"{\"name\":\""<<R.Name<<"\",\"scans\":"<<R.Scans<<",\"measured_hz\":"<<R.MeasuredHz<<"}";
    }
    std::cout<<"],\"scope\":\"native standalone tests of production ScanCadence; live UE/ROS frequency remains separate\"}\n";
}
