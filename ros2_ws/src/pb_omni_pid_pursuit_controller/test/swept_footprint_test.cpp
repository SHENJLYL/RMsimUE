#include "pb_omni_pid_pursuit_controller/swept_footprint.hpp"

#include <array>
#include <cstdlib>
#include <iostream>

namespace sweep = pb_omni_pid_pursuit_controller::sweep;
struct Grid {
  std::array<unsigned char, 100 * 100> cells{};
  double getResolution() const {return 0.05;}
  bool worldToMap(double x, double y, unsigned int & mx, unsigned int & my) const {
    if (x < -2.5 || y < -2.5 || x >= 2.5 || y >= 2.5) {return false;}
    mx = static_cast<unsigned int>((x + 2.5) / getResolution());
    my = static_cast<unsigned int>((y + 2.5) / getResolution());
    return true;
  }
  void mapToWorld(unsigned int mx, unsigned int my, double & x, double & y) const {
    x = -2.5 + (mx + 0.5) * getResolution();
    y = -2.5 + (my + 0.5) * getResolution();
  }
  unsigned char getCost(unsigned int x, unsigned int y) const {return cells[100*y+x];}
  void mark(double x, double y, unsigned char cost) {
    unsigned int mx,my;
    if (!worldToMap(x,y,mx,my)) {std::abort();}
    cells[100*my+mx] = cost;
  }
};
void require(bool condition, const char * name) {
  if (!condition) {std::cerr << name << " failed\n"; std::exit(1);}
  std::cout << "PASS " << name << '\n';
}
int main() {
  Grid map;
  std::vector<sweep::Vector> body{{-.27,-.27},{-.27,.27},{.27,.27},{.27,-.27}};
  auto safe = [&](sweep::Vector travel, sweep::Vector drift = {}) {
    return sweep::clear(map,body,travel,drift,254);
  };
  require(safe({1,std::sqrt(3.)}),"open shortcut");
  map.mark(.475,.825,254);
  require(!safe({1,std::sqrt(3.)}),"corner cutting blocked");
  require(safe({1,0}),"shorter tracking point reachable");
  require(!safe({1,0},{0,.8}),"lateral momentum swept");
  require(!safe({2.3,0}),"footprint outside map rejected");
  map = Grid{}; map.mark(.625,.225,255);
  require(!safe({1,0}),"unknown under chassis corner");
  map = Grid{}; map.mark(.025,.025,254);
  require(!safe({0,0}),"obstacle inside stationary footprint");
  map = Grid{}; map.mark(-.725,.025,254);
  require(!safe({-1,0}),"reverse sweep blocked");
  map = Grid{}; map.mark(.025,.325,254);
  require(safe({1,0}),"aligned chassis clears narrow passage");
  for (auto & v:body) {
    const double scale = std::sqrt(0.5);
    v = {(v.x-v.y)*scale,(v.x+v.y)*scale};
  }
  require(!safe({1,0}),"rotated corner hits same wall");
  map = Grid{};
  require(!safe({sweep::stoppingDistance(5,.45,4),0}),"full speed cannot stop inside map");
  require(safe({sweep::stoppingDistance(1,.45,4),0}),"reduced speed can stop");
  body.front().y = std::numeric_limits<double>::quiet_NaN();
  require(!safe({0,0}),"invalid footprint cannot authorize motion");
}
