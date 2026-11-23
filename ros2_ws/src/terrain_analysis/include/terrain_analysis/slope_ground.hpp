#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <optional>
#include <vector>

namespace terrain_analysis
{
// Use the actual XY of the lowest return in each cell. A low quantile over
// three cells alone mixes different ground heights on an inclined surface.
class SlopeGround
{
public:
  explicit SlopeGround(int width)
  : width_(width), low_(width * width), ramps_(width * width), platforms_(width * width) {}

  void reset(double tangent)
  {
    max_gradient_ = tangent;
    if (enabled()) {
      std::fill(low_.begin(), low_.end(), Sample{});
      std::fill(ramps_.begin(), ramps_.end(), Plane{});
      std::fill(platforms_.begin(), platforms_.end(), Plane{});
    }
  }

  void observe(int ix, int iy, double x, double y, double z)
  {
    if (!enabled() || !inside(ix, iy)) {return;}
    auto & sample = low_[index(ix, iy)];
    if (!sample.valid || z < sample.z) {sample = {x, y, z, true};}
  }

  void fit()
  {
    if (!enabled()) {return;}
    for (int ix = 0; ix < width_; ++ix) {
      for (int iy = 0; iy < width_; ++iy) {
        if (!low_[index(ix, iy)].valid) {continue;}
        std::array<Sample, 9> neighbors{};
        for (int dx = -1; dx <= 1; ++dx) {
          for (int dy = -1; dy <= 1; ++dy) {
            if (inside(ix + dx, iy + dy)) {
              neighbors[3 * (dx + 1) + dy + 1] = low_[index(ix + dx, iy + dy)];
            }
          }
        }
        const int cell = index(ix, iy);
        auto & ramp = ramps_[cell];
        auto & platform = platforms_[cell];
        // Shrink only where the whole surface is curved or ends at a transition.
        for (const auto & region : regions_) {
          const bool corner = region[1] - region[0] == 1 && region[3] - region[2] == 1;
          const auto plane = fitRegion(neighbors, region, corner);
          if (!plane.valid) {continue;}
          auto & surface = std::hypot(plane.gx, plane.gy) >= min_slope_ ? ramp : platform;
          if (!surface.valid) {surface = plane;}
          if (ramp.valid && platform.valid) {break;}
        }
      }
    }
    // Accept a level landing only where an already supported ramp approaches
    // its height at this cell. An isolated flat top remains an obstacle.
    for (int ix = 0; ix < width_; ++ix) {
      for (int iy = 0; iy < width_; ++iy) {
        const int cell = index(ix, iy);
        auto & platform = platforms_[cell];
        if (platform.valid && !connectedToRamp(ix, iy)) {platform.valid = false;}
      }
    }
  }

  std::optional<double> elevationAboveSupport(int ix, int iy, double x, double y, double z) const
  {
    if (!enabled() || !inside(ix, iy)) {return std::nullopt;}
    const int cell = index(ix, iy);
    const auto & ramp = ramps_[cell];
    const auto & platform = platforms_[cell];
    if (!ramp.valid && !platform.valid) {return std::nullopt;}
    const auto matches = [&](const Plane & plane) {
      return plane.valid && std::abs(z - plane.height(x, y)) <= residual_;
    };
    if (matches(ramp) || matches(platform)) {return 0.0;}
    const double height = ramp.valid && platform.valid ?
      std::min(ramp.height(x, y), platform.height(x, y)) :
      (ramp.valid ? ramp : platform).height(x, y);
    const double elevation = z - height;
    // Retain the original drop/obstacle policy for points below the support.
    if (elevation < -residual_) {return std::nullopt;}
    return std::max(0.0, elevation);
  }

private:
  struct Sample { double x{}, y{}, z{}; bool valid{}; };
  struct Plane {
    double x{}, y{}, z{}, gx{}, gy{};
    bool valid{};
    double height(double px, double py) const {return z + gx * (px - x) + gy * (py - y);}
  };
  static constexpr double residual_ = 0.035;
  static constexpr double min_slope_ = 0.08;
  // Inclusive crop in a 3x3 patch, always retaining the point's own cell.
  static constexpr std::array<std::array<int, 4>, 9> regions_{{
    {0, 2, 0, 2}, {0, 1, 0, 2}, {1, 2, 0, 2}, {0, 2, 0, 1}, {0, 2, 1, 2},
    {0, 1, 0, 1}, {0, 1, 1, 2}, {1, 2, 0, 1}, {1, 2, 1, 2}}};

  Plane fitRegion(
    const std::array<Sample, 9> & points, const std::array<int, 4> & region, bool corner) const
  {
    Plane plane;
    int count = 0;
    const auto selected = [&](int i) {
      return i / 3 >= region[0] && i / 3 <= region[1] &&
             i % 3 >= region[2] && i % 3 <= region[3] && points[i].valid;
    };
    for (int i = 0; i < 9; ++i) {
      if (!selected(i)) {continue;}
      ++count;
      plane.x += points[i].x;
      plane.y += points[i].y;
      plane.z += points[i].z;
    }
    if (count < (corner ? 4 : 5)) {return {};}
    plane.x /= count;
    plane.y /= count;
    plane.z /= count;
    double xx = 0.0, xy = 0.0, yy = 0.0, xz = 0.0, yz = 0.0;
    for (int i = 0; i < 9; ++i) {
      if (!selected(i)) {continue;}
      const double x = points[i].x - plane.x;
      const double y = points[i].y - plane.y;
      const double z = points[i].z - plane.z;
      xx += x * x; xy += x * y; yy += y * y; xz += x * z; yz += y * z;
    }
    const double det = xx * yy - xy * xy;
    if (det <= 0.0002) {return {};}
    plane.gx = (yy * xz - xy * yz) / det;
    plane.gy = (xx * yz - xy * xz) / det;
    const double gradient = std::hypot(plane.gx, plane.gy);
    if (gradient > max_gradient_) {return {};}
    int supporting_cells = 0;
    for (int i = 0; i < 9; ++i) {
      const auto & sample = points[i];
      if (!sample.valid) {continue;}
      const double dz = sample.z - plane.height(sample.x, sample.y);
      if (std::abs(dz) <= residual_) {
        ++supporting_cells;
        continue;
      }
      if (selected(i)) {return {};}
      if (dz >= 0.0) {continue;}
      // Avoid extrapolating a plane down through the other side of a crest.
      // A lower sample outside the fit must still have a physically gradual
      // approach from at least one adjacent sample that supports the plane.
      bool continuous = false;
      for (int j = 0; j < 9; ++j) {
        if (!selected(j) || std::abs(i / 3 - j / 3) + std::abs(i % 3 - j % 3) != 1) {
          continue;
        }
        const auto & neighbor = points[j];
        const double spacing = std::hypot(sample.x - neighbor.x, sample.y - neighbor.y);
        continuous |= neighbor.z - sample.z <= max_gradient_ * spacing + 0.01;
      }
      if (!continuous) {return {};}
      ++supporting_cells;
    }
    if (supporting_cells < 5) {return {};}
    plane.valid = true;
    return plane;
  }

  bool connectedToRamp(int ix, int iy) const
  {
    const auto & landing = low_[index(ix, iy)];
    static constexpr std::array<std::array<int, 2>, 4> adjacent{{
      {-1, 0}, {1, 0}, {0, -1}, {0, 1}}};
    for (const auto & direction : adjacent) {
      const int x = ix + direction[0], y = iy + direction[1];
      if (!inside(x, y)) {continue;}
      const int cell = index(x, y);
      const auto & ramp = ramps_[cell];
      if (!ramp.valid) {continue;}
      const auto & neighbor = low_[cell];
      const double height_change = std::abs(landing.z - neighbor.z);
      const double spacing = std::hypot(landing.x - neighbor.x, landing.y - neighbor.y);
      const double predicted = ramp.height(landing.x, landing.y);
      if (height_change >= 0.015 && height_change <= max_gradient_ * spacing + 0.01 &&
        std::abs(landing.z - predicted) <= residual_)
      {
        return true;
      }
    }
    return false;
  }

  bool enabled() const {return max_gradient_ > 0.0;}
  bool inside(int x, int y) const {return x >= 0 && y >= 0 && x < width_ && y < width_;}
  int index(int x, int y) const {return x * width_ + y;}
  int width_;
  double max_gradient_{};
  std::vector<Sample> low_;
  std::vector<Plane> ramps_, platforms_;
};
}  // namespace terrain_analysis
