#include "../../unreal/RMSim/Source/RMSim/Sensors/Mid360Model.h"
#include <cassert>
#include <cmath>
#include <iostream>

using namespace RMSimSensors;
int main()
{
    Mid360PulseClock Clock;
    std::int64_t Count=0,Last=-1;
    for(std::int64_t T=5000000;T<=1000000000;T+=5000000)
    {
        const auto B=Clock.Advance(T,4096);assert(B.Count==1000&&B.Missed==0);
        for(auto I=B.First;I<B.End;++I)
        {assert(I==Last+1);assert(I*5000<T);Last=I;++Count;}
    }
    assert(Count==200000&&Last==199999);
    for(double Hz:{5.,10.,20.,50.,100.})
    {
        assert(Mid360Model::SupportedFrameRate(Hz));
        const auto N=static_cast<std::int64_t>(200000/Hz);
        assert(Mid360Model::ScanOrdinal(N-1,Hz)==0);
        assert(Mid360Model::ScanOrdinal(N,Hz)==1);
        assert((N-1)*5000<Mid360Model::ScanPeriodNs(Hz));
    }
    assert(!Mid360Model::SupportedFrameRate(30.));
    auto Off=Clock.Advance(1234500000,0);assert(Off.Count==0&&Off.Missed==46900);
    auto On=Clock.Advance(1239500000,4096);assert(On.First==246900&&On.Count==1000);
    assert(On.First%4==0); // Phase survives connection changes.
    auto Stall=Clock.Advance(1300000000,4096);assert(Stall.Count==4096&&Stall.Missed==8004);
    assert(Stall.First==255904&&Stall.End==260000);
    auto Backward=Clock.Advance(1200000000,4096);assert(Backward.Count==0);
    Clock.Reset();assert(Clock.Advance(5000000,4096).First==0);
    assert(std::abs(Mid360Model::DetectableRange(.1,.1,40.,.8,70.,70.)-40.)<1.e-12);
    assert(std::abs(Mid360Model::DetectableRange(.8,.1,40.,.8,70.,70.)-70.)<1.e-12);
    assert(Mid360Model::DetectableRange(0.,.1,40.,.8,70.,70.)==0.);
    assert(Mid360Model::DetectableRange(.05,.1,40.,.8,70.,70.)<40.);
    assert(Mid360Model::RangeSigma(.2,.03,.02,.2,10.)==.03);
    assert(std::abs(Mid360Model::RangeSigma(10.,.03,.02,.2,10.)-.02)<1.e-12);
    bool Saturated=false;
    assert(Mid360Model::QuantizeAndClamp(3.,2.,.3,Saturated)==2.&&Saturated);
    assert(Mid360Model::QuantizeAndClamp(-3.,2.,.3,Saturated)==-2.&&Saturated);
    assert(std::abs(Mid360Model::QuantizeAndClamp(1.24,0.,.1,Saturated)-1.2)<1.e-12&&!Saturated);
    assert(Mid360Model::QuantizeAndClamp(1.24,0.,0.,Saturated)==1.24&&!Saturated);
    const double Repeated=Mid360Model::Gaussian(2027,12345,0);
    double Sum=0,SumSquares=0;
    for(std::uint64_t I=0;I<100000;++I)
    {const double X=Mid360Model::Gaussian(2027,I,0);assert(std::isfinite(X));Sum+=X;SumSquares+=X*X;}
    assert(std::abs(Sum/100000)<.02&&std::abs(SumSquares/100000-1.)<.05);
    assert(Mid360Model::Gaussian(2027,12345,0)==Repeated);
    assert(Mid360Model::Gaussian(2027,12345,1)!=Repeated);
    assert(Mid360Model::Gaussian(2028,12345,0)!=Repeated);
    for(const std::int64_t Rate:{200,600,997})
    {
        // Check exact rational timestamps near the epoch and after an entire day.
        for(const std::int64_t I:{0LL,1LL,2LL,599LL,600LL,86400017LL})
        {
            const auto Expected=(I*1000000000LL+Rate/2)/Rate;
            assert(EpochSampleGrid::StampNs(I,double(Rate))==Expected);
            const double T=EpochSampleGrid::Time(I,double(Rate));
            assert(EpochSampleGrid::LastSupported(T,double(Rate))==I);
            assert(EpochSampleGrid::LastSupported(std::nextafter(T,-INFINITY),double(Rate))<I);
        }
        std::int64_t Next=0;
        for(int Step=1;Step<=20000;++Step)
        {
            const double Supported=double(Step)*.005-.001;
            const auto End=EpochSampleGrid::LastSupported(Supported,double(Rate))+1;
            while(Next<End)
            {
                assert(EpochSampleGrid::Time(Next,double(Rate))<=Supported);
                assert(EpochSampleGrid::StampNs(Next,double(Rate))==(Next*1000000000LL+Rate/2)/Rate);
                ++Next;
            }
        }
        assert(Next==EpochSampleGrid::LastSupported(99.999,double(Rate))+1);
        const auto Reset=EpochSampleGrid::FirstAtOrAfter(2.346,double(Rate));
        assert(EpochSampleGrid::Time(Reset,double(Rate))>=2.346);
        assert(EpochSampleGrid::Time(Reset-1,double(Rate))<2.346);
    }
    std::cout<<"MID360_MODEL_TEST_PASSED emission phase frame boundaries reflectance noise saturation integer_IMU_200_600_997_Hz\n";
}
