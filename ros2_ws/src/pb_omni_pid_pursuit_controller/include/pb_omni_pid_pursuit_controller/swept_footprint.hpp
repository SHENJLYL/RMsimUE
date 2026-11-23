#pragma once

#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>

namespace pb_omni_pid_pursuit_controller::sweep
{
struct Vector
{
  double x, y;
  Vector operator*(double scale) const { return {x * scale, y * scale}; }
  double dot(Vector other) const { return x * other.x + y * other.y; }
  double length() const { return std::hypot(x, y); }
};

struct Block {
  Vector center{};
  int cost{-1};
};

struct ClearanceDeparture {
  Vector origin;
  double rotation_radius;
};

// Project the swept convex footprint and a square map cell on each separating axis.
// The two displacements bound motion along the command and residual lateral coasting.
template<class Grid>
bool clear(const Grid & grid, const std::vector<Vector> & body, Vector travel, Vector drift,
  unsigned char blocked_cost, double margin = 0.0, Block * first_block = nullptr,
  const std::vector<Vector> * displacements = nullptr,
  const ClearanceDeparture * departure = nullptr)
{
  if (!std::isfinite(margin) || margin < 0.0 || body.size() < 3 || !std::isfinite(travel.length()) || !std::isfinite(drift.length())) {
    return false;
  }
  for (const auto & vertex : body) {
    if (!std::isfinite(vertex.x) || !std::isfinite(vertex.y)) {return false;}
  }
  struct Interval {Vector axis; double low, high;};
  const double half_cell = 0.5 * grid.getResolution();
  auto bounds = [&](Vector axis) {
    double lo = std::numeric_limits<double>::infinity();
    double hi = -lo;
    for (const auto & vertex : body) {
      double projection = axis.dot(vertex);
      lo = std::min(lo, projection);
      hi = std::max(hi, projection);
    }
    if (displacements) {
      double lower = 0.0;
      double upper = 0.0;
      for (const auto shift : *displacements) {
        lower = std::min(lower, axis.dot(shift));
        upper = std::max(upper, axis.dot(shift));
      }
      lo += lower;
      hi += upper;
    } else {
      for (const auto shift : {travel, drift}) {
        const double offset = axis.dot(shift);
        lo += std::min(0.0, offset);
        hi += std::max(0.0, offset);
      }
    }
    // Minkowski sum with a metric disk; projections of non-unit axes scale by their length.
    const double extra = margin * axis.length();
    return Interval{axis, lo - extra, hi + extra};
  };

  const auto x = bounds({1, 0});
  const auto y = bounds({0, 1});
  unsigned int left, bottom, right, top;
  if (!std::isfinite(x.low) || !std::isfinite(y.low) || !std::isfinite(x.high) || !std::isfinite(y.high) ||
    !grid.worldToMap(x.low - 1e-9, y.low - 1e-9, left, bottom) ||
    !grid.worldToMap(x.high + 1e-9, y.high + 1e-9, right, top)) {
    return false;
  }

  std::vector<Interval> axes;
  axes.reserve(body.size() + 2);
  auto add_edge = [&](Vector edge) {
    if (edge.x == 0 && edge.y == 0) {return;}
    auto interval = bounds({-edge.y, edge.x});
    const double cell_projection = half_cell * (std::abs(edge.x) + std::abs(edge.y));
    interval.low -= cell_projection;
    interval.high += cell_projection;
    axes.push_back(interval);
  };
  for (size_t i = 0; i < body.size(); ++i) {
    const auto & next = body[(i + 1) % body.size()];
    add_edge({next.x - body[i].x, next.y - body[i].y});
  }
  if (displacements) {
    for (size_t i = 0; i < displacements->size(); ++i) {
      const auto & a = (*displacements)[i];
      const auto & b = (*displacements)[(i + 1) % displacements->size()];
      add_edge({b.x - a.x, b.y - a.y});
    }
  } else {
    add_edge(travel);
    add_edge(drift);
  }
  for (unsigned int my = bottom; my <= top; ++my) {
    for (unsigned int mx = left; mx <= right; ++mx) {
      const auto cost = grid.getCost(mx, my);
      if (cost < blocked_cost) {continue;}
      Vector cell;
      grid.mapToWorld(mx, my, cell.x, cell.y);
      const bool overlaps = std::all_of(axes.begin(), axes.end(), [&](const Interval & axis) {
        const double p = axis.axis.dot(cell);
        return p >= axis.low && p <= axis.high;
      });
      if (!overlaps) {continue;}
      if (departure && !displacements && cost == blocked_cost && travel.length() > 1e-6) {
        const Vector relative{cell.x - departure->origin.x, cell.y - departure->origin.y};
        const double gap = std::hypot(
          std::max(0.0, std::abs(relative.x) - half_cell),
          std::max(0.0, std::abs(relative.y) - half_cell));
        const auto non_approaching = [&](Vector shift) {
          // Every point of the cell satisfies this projection bound. The
          // distance to it cannot shrink anywhere in the swept parallelogram.
          return relative.dot(shift) + half_cell*(std::abs(shift.x)+std::abs(shift.y)) <= 0.0;
        };
        if (gap > departure->rotation_radius &&
          gap <= departure->rotation_radius + margin &&
          non_approaching(travel) && non_approaching(drift))
        {
          continue;
        }
      }
      if (first_block) {*first_block = {cell, cost};}
      return false;
    }
  }
  return true;
}

// Circumscribe the sector of attainable stop displacements. The existing and
// requested headings share a single distance budget during their transition.
inline std::vector<Vector> brakingSector(double actual, double target, double distance)
{
  constexpr double angular_margin = 0.07;
  const double turn = std::remainder(target - actual, 2.0 * std::acos(-1.0));
  const double start = actual + std::min(0.0, turn) - angular_margin;
  const double span = std::abs(turn) + 2.0 * angular_margin;
  const int count = std::max(1, static_cast<int>(std::ceil(span / 0.17)));
  const double interval = span / count;
  const double radius = distance / std::cos(0.5 * interval);
  std::vector<Vector> polygon{{0.0, 0.0}};
  polygon.reserve(static_cast<size_t>(count) + 2);
  for (int i = 0; i <= count; ++i) {
    const double heading = start + i * interval;
    polygon.push_back({radius * std::cos(heading), radius * std::sin(heading)});
  }
  return polygon;
}

inline double stoppingDistance(double speed, double delay, double deceleration)
{
  return speed * delay + speed * speed / (2.0 * deceleration);
}

// Integrate the ramp from zero longitudinal acceleration. The hard brake
// clears positive acceleration before its jerk-limited ramp starts.
inline double jerkLimitedStoppingDistance(
  double speed, double delay, double deceleration, double jerk)
{
  const double ramp = std::min(deceleration / jerk, std::sqrt(2.0 * speed / jerk));
  const double remaining = std::max(0.0, speed - 0.5 * jerk * ramp * ramp);
  return speed * (delay + ramp) - jerk * ramp * ramp * ramp / 6.0 +
         remaining * remaining / (2.0 * deceleration);
}

inline Vector transverseDrift(Vector stopping_displacement, Vector heading)
{
  const double forward = std::max(0.0, stopping_displacement.dot(heading));
  return {stopping_displacement.x - forward * heading.x, stopping_displacement.y - forward * heading.y};
}

inline double approachSpeed(double distance, double delay, double deceleration)
{
  const double delayed = deceleration * delay;
  return std::sqrt(delayed * delayed + 2.0 * deceleration * std::max(0.0, distance)) - delayed;
}
}  // namespace pb_omni_pid_pursuit_controller::sweep
