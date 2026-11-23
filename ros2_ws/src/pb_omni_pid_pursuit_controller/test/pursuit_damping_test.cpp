#include "pb_omni_pid_pursuit_controller/pid.hpp"
#include "pb_omni_pid_pursuit_controller/swept_footprint.hpp"
#include <cmath>
#include <iostream>

int main()
{
  PID speed(.05, 5., -2.5, 3., .3, 0.);
  speed.calculate(2., 0., -1.6);
  const double after_shorter_target = speed.calculate(.8, 0., -1.6);
  const double after_longer_target = speed.calculate(1.1, 0., -1.6);
  const double approaching_faster = speed.calculate(1.1, 0., -2.6);
  PID classic(.05, 100., -100., 0., 1., 0.);
  namespace sweep = pb_omni_pid_pursuit_controller::sweep;
  for (double distance : {0.0, 0.05, 0.5, 2.0, 5.0}) {
    const double planned = sweep::approachSpeed(distance, .45, 2.0);
    if (std::abs(sweep::stoppingDistance(planned, .45, 2.0) - distance) > 1e-8) {return 1;}
  }
  if (std::abs(after_shorter_target - 1.92) > 1e-9 ||
    std::abs(after_longer_target - 2.82) > 1e-9 ||
    std::abs(approaching_faster - 2.52) > 1e-9 ||
    std::abs(classic.calculate(1., 0.) - 20.) > 1e-9 ||
    std::abs(classic.calculate(.8, 0.) + 4.) > 1e-9) {
    return 1;
  }
  std::cout << "PASS shortened and lengthened tracking points, measured damping and original heading PID\n";
  struct CellGrid {
    double getResolution() const {return .05;}
    bool worldToMap(double x, double y, unsigned int & mx, unsigned int & my) const {
      if (x < -1 || y < -1 || x >= 1 || y >= 1) {return false;}
      mx = static_cast<unsigned int>((x + 1)/.05); my = static_cast<unsigned int>((y + 1)/.05); return true;
    }
    unsigned char getCost(unsigned int x, unsigned int y) const {return x==27 && y==20 ? 254 : 0;}
    void mapToWorld(unsigned int x,unsigned int y,double & wx,double & wy) const {
      wx=-1+(x+.5)*.05; wy=-1+(y+.5)*.05;
    }
  } grid;
  const std::vector<sweep::Vector> body{{-.27,-.27},{.27,-.27},{.27,.27},{-.27,.27}};
  if (!sweep::clear(grid,body,{0,.2},{},254,.02) || sweep::clear(grid,body,{0,.2},{},254,.12)) {
    return 1;
  }
  return 0;
}
