#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace RMSimSensors
{
// Groups actual post-physics samples into phase-locked scan windows. This never
// changes a sample timestamp or generates samples during skipped intervals.
class ScanCadence
{
public:
    void Reset() { Epoch=0.;Period=0.;NextOrdinal=1; }
    void Start(double ActualTime,double FrameHz)
    { Epoch=ActualTime;Period=1./FrameHz;NextOrdinal=1; }

    bool Advance(double ActualTime)
    {
        // Chaos receives a float fixed delta (5ms becomes 0.004999999888...).
        // A 1us grouping tolerance prevents a boundary missing by nanoseconds
        // from adding a whole physical tick. The timestamps remain untouched.
        constexpr double BoundaryToleranceSeconds=1.e-6;
        if(Period<=0.||ActualTime+BoundaryToleranceSeconds<Epoch+double(NextOrdinal)*Period)
            return false;
        // Anchor future boundaries to the epoch, not to the late physical tick.
        // A long gap closes one observed scan; it cannot invent empty scans.
        const auto Reached=std::int64_t(std::floor((ActualTime-Epoch+BoundaryToleranceSeconds)/Period));
        NextOrdinal=std::max(NextOrdinal+1,Reached+1);
        return true;
    }
private:
    double Epoch=0.,Period=0.;
    std::int64_t NextOrdinal=1;
};
}
