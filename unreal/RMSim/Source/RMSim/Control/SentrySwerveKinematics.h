#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

// Portable adaptation of the user-supplied LK chassis controller's wheel-vector
// and encoder shortest-path logic. Source hashes/coordinate mapping are recorded
// in artifacts/sources/sentry_chassis_local and docs/research/sentry-controller-adapter.md.
// No ROS, EtherCAT, hardware-current PID or navigation truth is required here.
namespace RMSimSentry
{
constexpr double Tau=6.283185307179586476925286766559;
constexpr std::int32_t EncoderPeriod=32768;
inline std::int32_t WrapEncoder(std::int32_t Value)
{return (Value%EncoderPeriod+EncoderPeriod)%EncoderPeriod;}

struct ShortestPath {std::int32_t Error;int Direction;};
inline ShortestPath ChooseShortest(std::int32_t Current,std::int32_t Target)
{
    // Keep arithmetic int32 and normalize negatives: the original int16 target
    // cast can wrap before modulo when a calibrated encoder zero is near 32768.
    Current=WrapEncoder(Current);Target=WrapEncoder(Target);
    const auto ForwardA=WrapEncoder(Target-Current),BackwardA=WrapEncoder(Current-Target);
    const auto ForwardB=WrapEncoder(Target-Current+EncoderPeriod/2),BackwardB=WrapEncoder(Current-Target+EncoderPeriod/2);
    if(std::min(ForwardA,BackwardA)<=std::min(ForwardB,BackwardB))
        return {ForwardA<=BackwardA?ForwardA:-BackwardA,1};
    return {ForwardB<=BackwardB?ForwardB:-BackwardB,-1};
}

struct WheelTarget {double SteeringRosRadians,SurfaceSpeedMetresPerSecond;};
inline WheelTarget SolveWheel(double ForwardMps,double LeftMps,double YawRadS,
                             double WheelForwardMetres,double WheelLeftMetres,double CurrentSteeringRosRadians)
{
    const double Vx=ForwardMps-YawRadS*WheelLeftMetres;
    const double VyClockwise=-LeftMps-YawRadS*WheelForwardMetres;
    const double Magnitude=std::hypot(Vx,VyClockwise);
    if(Magnitude<1.e-9)return {CurrentSteeringRosRadians,0};
    double Clockwise=std::atan2(VyClockwise,Vx);if(Clockwise<0)Clockwise+=Tau;
    const auto Current=WrapEncoder(static_cast<std::int32_t>(std::llround(-CurrentSteeringRosRadians/Tau*EncoderPeriod)));
    const auto Target=WrapEncoder(static_cast<std::int32_t>(std::llround(Clockwise/Tau*EncoderPeriod)));
    const auto Choice=ChooseShortest(Current,Target);
    return {CurrentSteeringRosRadians-double(Choice.Error)/EncoderPeriod*Tau,Magnitude*Choice.Direction};
}
}
