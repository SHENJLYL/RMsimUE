#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace RMSimSensors
{
// Public nominal output contract. This does not encode the private optical trajectory.
struct Mid360Model
{
    static constexpr std::int64_t PointIntervalNs=5000;
    static constexpr int ChannelCount=4;
    static bool SupportedFrameRate(double Hz)
    {return Hz==5.||Hz==10.||Hz==20.||Hz==50.||Hz==100.;}
    static std::int64_t ScanPeriodNs(double Hz)
    {return static_cast<std::int64_t>(std::llround(1.e9/Hz));}
    static std::int64_t ScanOrdinal(std::int64_t PointIndex,double Hz)
    {return PointIndex/(ScanPeriodNs(Hz)/PointIntervalNs);}
    static std::uint64_t Mix(std::uint64_t Value)
    {
        Value+=0x9e3779b97f4a7c15ULL;Value=(Value^(Value>>30))*0xbf58476d1ce4e5b9ULL;
        Value=(Value^(Value>>27))*0x94d049bb133111ebULL;return Value^(Value>>31);
    }
    static double Gaussian(std::uint64_t Seed,std::uint64_t PointIndex,std::uint64_t Dimension)
    {
        // Counter-based noise is stable across task scheduling, missed rays and reconnects.
        const auto Key=Mix(Seed)^Mix(PointIndex)^Mix(Dimension+0x517cc1b727220a95ULL);
        const double U1=(double(Mix(Key)>>11)+.5)/9007199254740992.;
        const double U2=(double(Mix(Key^0xd6e8feb86659fd93ULL)>>11)+.5)/9007199254740992.;
        return std::sqrt(-2.*std::log(U1))*std::cos(6.2831853071795864769*U2);
    }
    static double DetectableRange(double Reflectance,double LowReflectance,double LowRange,
                                  double HighReflectance,double HighRange,double MaximumRange)
    {
        if(Reflectance<=0.)return 0.;
        // Uncalibrated log/log interpolation through two specified normal-incidence anchors.
        const double Exponent=std::log(HighRange/LowRange)/std::log(HighReflectance/LowReflectance);
        return std::min(MaximumRange,LowRange*std::pow(Reflectance/LowReflectance,Exponent));
    }
    static double RangeSigma(double Range,double NearSigma,double ReferenceSigma,double NearDistance,double ReferenceDistance)
    {
        const double Alpha=std::clamp((Range-NearDistance)/(ReferenceDistance-NearDistance),0.,1.);
        return NearSigma+(ReferenceSigma-NearSigma)*Alpha;
    }
    static double QuantizeAndClamp(double Value,double Range,double Quantum,bool& Saturated)
    {
        Saturated=Range>0.&&std::abs(Value)>Range;
        if(Range>0.)Value=std::clamp(Value,-Range,Range);
        if(Quantum>0.)Value=std::round(Value/Quantum)*Quantum;
        return Range>0.?std::clamp(Value,-Range,Range):Value;
    }
};

// An integer sample index keeps non-integral periods (for example600Hz) on the
// original epoch grid for long runs. Decisions compare real support times; no future sample is invented.
struct EpochSampleGrid
{
    static double Time(std::int64_t Index,double Rate){return double(Index)/Rate;}
    static std::int64_t StampNs(std::int64_t Index,double Rate)
    {return static_cast<std::int64_t>(std::llround(static_cast<long double>(Index)*1000000000.L/Rate));}
    static std::int64_t FirstAtOrAfter(double Seconds,double Rate)
    {
        if(Seconds<=0.)return 0;
        auto Index=static_cast<std::int64_t>(std::ceil(Seconds*Rate));
        while(Index>0&&Time(Index-1,Rate)>=Seconds)--Index;
        while(Time(Index,Rate)<Seconds)++Index;
        return Index;
    }
    static std::int64_t LastSupported(double Seconds,double Rate)
    {
        if(Seconds<0.)return -1;
        auto Index=static_cast<std::int64_t>(std::floor(Seconds*Rate));
        while(Index>=0&&Time(Index,Rate)>Seconds)--Index;
        while(Time(Index+1,Rate)<=Seconds)++Index;
        return Index;
    }
};

// Samples occupy [epoch, now). Advancing while disconnected preserves phase without
// doing scene work. A bounded tick retains the newest supported pulses, with loss explicit.
class Mid360PulseClock
{
public:
    struct Batch {std::int64_t First=0,Count=0,Missed=0,End=0;};
    void Reset(){Next=0;}
    Batch Advance(std::int64_t NowNs,std::int64_t Budget)
    {
        Batch B;B.First=Next;B.End=Next;
        if(NowNs<0||Budget<0)return B;
        const auto End=NowNs/Mid360Model::PointIntervalNs+(NowNs%Mid360Model::PointIntervalNs?1:0);
        if(End<=Next)return B;
        B.End=End;B.Count=std::min(Budget,End-Next);B.First=End-B.Count;
        B.Missed=B.First-Next;Next=End;return B;
    }
    std::int64_t NextIndex() const{return Next;}
private:
    std::int64_t Next=0;
};
}
