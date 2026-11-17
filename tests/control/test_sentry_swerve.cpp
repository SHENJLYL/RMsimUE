#include "Control/SentrySwerveKinematics.h"
#include <cassert>
#include <cmath>
#include <iostream>

int main()
{
    using namespace RMSimSentry;
    assert(WrapEncoder(-1)==32767&&WrapEncoder(65535)==32767);
    auto Path=ChooseShortest(32760,10);assert(Path.Error==18&&Path.Direction==1);
    Path=ChooseShortest(0,16384);assert(Path.Error==0&&Path.Direction==-1);
    // Calibrated zero crossing that was unsafe after the source's int16 narrowing.
    Path=ChooseShortest(30507,33000);assert(Path.Error==2493&&Path.Direction==1);
    auto Wheel=SolveWheel(1,0,0,.137,.137,0);assert(std::abs(Wheel.SteeringRosRadians)<1.e-4&&Wheel.SurfaceSpeedMetresPerSecond==1);
    Wheel=SolveWheel(-1,0,0,.137,.137,0);assert(std::abs(Wheel.SteeringRosRadians)<1.e-4&&Wheel.SurfaceSpeedMetresPerSecond==-1);
    Wheel=SolveWheel(0,1,0,.137,.137,0);assert(std::abs(Wheel.SteeringRosRadians-Tau/4)<1.e-4&&Wheel.SurfaceSpeedMetresPerSecond==1);
    Wheel=SolveWheel(0,0,1,.137,.137,0);assert(std::abs(Wheel.SteeringRosRadians+Tau/8)<1.e-4&&Wheel.SurfaceSpeedMetresPerSecond<0);
    for(double Yaw:{-4.,-1.,0.,1.,4.})for(double Forward:{-2.,0.,2.})for(double Left:{-1.,0.,1.})
        for(double X:{-.137,.137})for(double Y:{-.137,.137})for(double Angle:{-3.1,-1.,0.,1.,3.1})
        {
            Wheel=SolveWheel(Forward,Left,Yaw,X,Y,Angle);
            assert(std::abs(Wheel.SteeringRosRadians-Angle)<=Tau/4+1.e-4);
            // Reconstruct the contact velocity: reversed wheel speed must preserve the commanded vector.
            assert(std::abs(Wheel.SurfaceSpeedMetresPerSecond*std::cos(Wheel.SteeringRosRadians)-(Forward-Yaw*Y))<.0015);
            assert(std::abs(Wheel.SurfaceSpeedMetresPerSecond*std::sin(Wheel.SteeringRosRadians)-(Left+Yaw*X))<.0015);
        }
    std::cout<<"PASS source-compatible swerve vectors, signed wheel reversal and encoder wrap\n";
}
