#include "chassis_controllers/utils/pid.hpp"
#include <cassert>
#include <cmath>
#include <limits>

int main()
{
    aim::algorithm::PID nominal(0,2,3,1000,1000);
    assert(std::abs(nominal.calculate(0,1)-4)<1.e-6);
    assert(std::abs(nominal.calculate(0,3)-11)<1.e-6);
    aim::algorithm::PID sampled(0,2,3,1000,1000);
    assert(std::abs(sampled.calculate(0,1)-4)<1.e-6);
    sampled.set_sample_ratio(2);
    assert(std::abs(sampled.calculate(0,3)-12)<1.e-6);
    sampled.clear();sampled.set_sample_ratio(std::numeric_limits<float>::quiet_NaN());
    assert(std::abs(sampled.calculate(0,1)-4)<1.e-6);
    return 0;
}
