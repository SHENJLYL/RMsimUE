#include "terrain_analysis/slope_ground.hpp"
#include <cmath>
#include <iostream>

int main()
{
  terrain_analysis::SlopeGround ground(5);
  const auto surface = [&](int ix, int iy, double x, double y, double z) {
    const auto elevation = ground.elevationAboveSupport(ix, iy, x, y, z);
    return elevation && *elevation < 0.035;
  };
  const double max_gradient = std::tan(35.0 * std::acos(-1.0) / 180.0);
  for (double gradient : {.65, -.65}) {
    ground.reset(max_gradient);
    for (int x = 0; x < 5; ++x) {
      for (int y = 0; y < 5; ++y) {
        double xx = .2*x-.075, yy = .2*y+.04;
        double z = gradient*xx + .1*yy;
        ground.observe(x,y,xx,yy,z+.16);
        ground.observe(x,y,xx,yy,z);
      }
    }
    ground.fit();
    if (!surface(2,2,.46,.47,gradient*.46+.1*.47) ||
         surface(2,2,.46,.47,gradient*.46+.1*.47+.15)) {return 1;}
  }
  std::cout << "PASS uphill downhill and obstacle over ramp\n";
  for (int kind = 0; kind < 3; ++kind) {
    ground.reset(max_gradient);
    for (int x = 0; x < 5; ++x) {
      for (int y = 0; y < 5; ++y) {
        double xx = .2*x, yy = .2*y;
        double z = kind==0 ? xx : kind==1 && x>=2 ? .16 : 0.;
        if (kind != 2 || y==2) {ground.observe(x,y,xx,yy,z);}
      }
    }
    ground.fit();
    const double z = kind==0 ? .4 : kind==1 ? .16 : 0.;
    if (surface(2,2,.4,.4,z)) {return 1;}
  }
  std::cout << "PASS steep face, step and collinear returns remain unclassified\n";

  ground.reset(max_gradient);
  for (int x = 0; x < 5; ++x) {
    const double px = .2 * x;
    const double z = px <= .4 ? .6 * px : .24 - .45 * (px - .4);
    for (int y = 0; y < 5; ++y) {
      const double py = .2 * y;
      ground.observe(x, y, px, py, z);
      ground.observe(x, y, px, py, z + .12);
    }
  }
  ground.fit();
  if (!surface(2, 2, .4, .4, .24) ||
    surface(2, 2, .4, .4, .36))
  {
    return 1;
  }
  std::cout << "PASS traversable crest and obstacle above the crest\n";


  for (double step : {0.0, 0.12}) {
    ground.reset(max_gradient);
    for (int x = 0; x < 5; ++x) {
      const double px = .2 * x;
      const double z = x < 2 ? .38 * px : .15 + step;
      for (int y = 0; y < 5; ++y) {
        ground.observe(x, y, px, .2 * y, z);
      }
    }
    ground.fit();
    if (surface(2, 2, .41, .41, .15 + step) != (step == 0.0) ||
      surface(2, 2, .41, .41, .27 + step))
    {
      return 1;
    }
  }
  std::cout << "PASS ramp landing, raised step and protruding obstacle\n";

  for (double step : {0.0, 0.14}) {
    ground.reset(max_gradient);
    for (int x = 0; x < 5; ++x) {
      const double px = .2 * x;
      const double z = x <= 2 ? 0.0 : .6 * (px - .4) + step;
      for (int y = 0; y < 5; ++y) {
        ground.observe(x, y, px, .2 * y, z);
      }
    }
    ground.fit();
    const auto landing = ground.elevationAboveSupport(2, 2, .305, .4, 0.0);
    const auto raised = ground.elevationAboveSupport(2, 2, .305, .4, .14);
    if (step == 0.0 && (!landing || *landing != 0.0 || !raised || *raised <= .10)) {
      std::cerr << "FAIL level ground below a connected ramp\n";
      return 1;
    }
    if (step > 0.0 && surface(3, 2, .6, .4, .12 + step)) {
      std::cerr << "FAIL discontinuous raised ramp was cleared\n";
      return 1;
    }
  }
  std::cout << "PASS lower ramp landing and discontinuous boundary\n";

  ground.reset(max_gradient);
  for (int x = 0; x < 5; ++x) {
    for (int y = 0; y < 5; ++y) {
      ground.observe(x, y, .2 * x, .2 * y, .6 * .2 * x);
    }
  }
  ground.fit();
  const auto near_surface = ground.elevationAboveSupport(2, 2, .4, .4, .29);
  const auto obstacle = ground.elevationAboveSupport(2, 2, .4, .4, .36);
  if (!near_surface || *near_surface >= .10 || !obstacle || *obstacle <= .10 ||
    ground.elevationAboveSupport(2, 2, .4, .4, .14))
  {
    return 1;
  }
  std::cout << "PASS height above support, protrusion and drop fallback\n";
}
