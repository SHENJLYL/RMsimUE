// Copyright 2025 Lihan Chen
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "pb_nav2_plugins/layers/intensity_voxel_layer.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <stdexcept>
#include <vector>

#include "tf2/LinearMath/Transform.h"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"

#include "sensor_msgs/point_cloud2_iterator.hpp"

#define VOXEL_BITS 16

using nav2_costmap_2d::FREE_SPACE;
using nav2_costmap_2d::LETHAL_OBSTACLE;
using nav2_costmap_2d::NO_INFORMATION;

using nav2_costmap_2d::Observation;
using nav2_costmap_2d::ObservationBuffer;

namespace pb_nav2_costmap_2d
{

namespace
{
bool nearTurn(const nav_msgs::msg::Path & path, double x, double y)
{
  const auto & poses = path.poses;
  if (poses.size() < 5) {return false;}
  auto nearest = poses.begin();
  double best_distance = 0.60 * 0.60;
  for (auto it = poses.begin(); it != poses.end(); ++it) {
    const auto & p = it->pose.position;
    const double d = (p.x - x) * (p.x - x) + (p.y - y) * (p.y - y);
    if (d < best_distance) {
      nearest = it;
      best_distance = d;
    }
  }
  if (best_distance >= 0.60 * 0.60) {return false;}
  const auto & a = nearest->pose.position;
  auto middle = nearest, end = nearest;
  double arc = 0.0;
  for (auto it = nearest + 1; it != poses.end(); ++it) {
    const auto & u = (it - 1)->pose.position;
    const auto & v = it->pose.position;
    arc += std::hypot(v.x - u.x, v.y - u.y);
    if (middle == nearest && arc >= 0.65) {middle = it;}
    if (arc >= 1.30) {
      end = it;
      break;
    }
  }
  if (middle == nearest || end == nearest) {return false;}
  const auto & b = middle->pose.position;
  const auto & c = end->pose.position;
  const double ux = b.x - a.x, uy = b.y - a.y;
  const double vx = c.x - b.x, vy = c.y - b.y;
  const double product = std::hypot(ux, uy) * std::hypot(vx, vy);
  // A heading change of roughly 20 degrees over the two measured chords.
  return product > 0.03 && ux * vx + uy * vy < 0.94 * product;
}
}  // namespace

std::optional<double> IntensityVoxelLayer::GroundSupport::heightAt(
  double wx, double wy, bool require_patch) const
{
  const double px = (wx - x) / resolution;
  const double py = (wy - y) / resolution;
  if (!std::isfinite(px) || !std::isfinite(py) || px < 1.0 || py < 1.0 ||
    px >= width - 2 || py >= height - 2)
  {
    return std::nullopt;
  }
  const int col = static_cast<int>(std::floor(px)), row = static_cast<int>(std::floor(py));
  const auto value = [&](int cx, int cy) {return static_cast<double>(values[cy * width + cx]);};
  double lo = 1e10, hi = -1e10;
  const int pad = require_patch ? 1 : 0;
  for (int i = -pad; i <= 1 + pad; ++i) {
    for (int j = -pad; j <= 1 + pad; ++j) {
      const double sample = value(col + i, row + j);
      if (!std::isfinite(sample)) {return std::nullopt;}
      lo = std::min(lo, sample);
      hi = std::max(hi, sample);
    }
  }
  // A return near a solid edge lacks a continuous reference patch.
  if (require_patch && hi - lo > 0.045) {return std::nullopt;}
  const double u = px - col, v = py - row;
  return (1.0-v) * ((1.0-u) * value(col, row) + u * value(col+1, row)) +
         v * ((1.0-u) * value(col, row+1) + u * value(col+1, row+1));
}

void IntensityVoxelLayer::onInitialize()
{
  auto node = node_.lock();
  clock_ = node->get_clock();
  ObstacleLayer::onInitialize();
  footprint_clearing_enabled_ =
    node->get_parameter(name_ + ".footprint_clearing_enabled").as_bool();
  enabled_ = node->get_parameter(name_ + ".enabled").as_bool();
  max_obstacle_height_ = node->get_parameter(name_ + ".max_obstacle_height").as_double();
  combination_method_ = node->get_parameter(name_ + ".combination_method").as_int();

  size_z_ = node->declare_parameter(name_ + ".z_voxels", 16);
  origin_z_ = node->declare_parameter(name_ + ".origin_z", 16.0);
  min_obstacle_intensity_ = node->declare_parameter(name_ + ".min_obstacle_intensity", 0.1);
  max_obstacle_intensity_ = node->declare_parameter(name_ + ".max_obstacle_intensity", 2.0);
  z_resolution_ = node->declare_parameter(name_ + ".z_resolution", 0.05);
  unknown_threshold_ =
    node->declare_parameter(name_ + ".unknown_threshold", 15) + (VOXEL_BITS - size_z_);
  mark_threshold_ = node->declare_parameter(name_ + ".mark_threshold", 0);
  publish_voxel_ = node->declare_parameter(name_ + ".publish_voxel_map", false);

  confirmation_age_s_ = node->declare_parameter<double>(name_ + ".confirmation_age_s", 0.0);
  confirmation_radius_m_ = node->declare_parameter<double>(name_ + ".confirmation_radius_m", 0.10);
  if (!std::isfinite(confirmation_age_s_) || confirmation_age_s_ < 0.0 ||
    confirmation_age_s_ > 0.8 || !std::isfinite(confirmation_radius_m_) ||
    confirmation_radius_m_ < 0.025 || confirmation_radius_m_ > 0.25 ||
    (confirmation_age_s_ > 0.0 && rolling_window_))
  {
    throw std::invalid_argument("Invalid confirmation settings for fixed planning overlay");
  }

  confirmation_turn_hold_s_ = node->declare_parameter<double>(name_ + ".confirmation_turn_hold_s", 0.0);
  if (!std::isfinite(confirmation_turn_hold_s_) || confirmation_turn_hold_s_ < 0.0 ||
    confirmation_turn_hold_s_ > 8.0)
  {
    throw std::invalid_argument("Invalid confirmation turn hold");
  }
  if (confirmation_age_s_ > 0.0 && confirmation_turn_hold_s_ > 0.0) {
    near_plan_sub_ = node->create_subscription<nav_msgs::msg::Path>(
      "/plan", rclcpp::SensorDataQoS(),
      [this](nav_msgs::msg::Path::ConstSharedPtr p) {
        std::atomic_store(&near_plan_, std::move(p));
      });
  }

  const auto support_file = node->declare_parameter<std::string>(name_ + ".ground_support_file", "");
  support_frame_ = node->declare_parameter<std::string>(name_ + ".ground_support_frame", "");
  if (!support_file.empty()) {
    std::ifstream stream(support_file, std::ios::binary);
    std::string magic;
    stream >> magic >> support_.width >> support_.height >> support_.x >> support_.y >> support_.resolution;
    if (!stream || magic != "RMSIM_SUPPORT_V1" || support_.width < 5 || support_.width > 2048 ||
      support_.height < 5 || support_.height > 2048 || support_frame_.empty() ||
      !std::isfinite(support_.x) || !std::isfinite(support_.y) ||
      !std::isfinite(support_.resolution) || support_.resolution < 0.02 || support_.resolution > 0.2 ||
      stream.get() != '\n')
    {
      throw std::invalid_argument("Invalid ground support grid");
    }
    support_.values.resize(support_.width * support_.height);
    if (!stream.read(reinterpret_cast<char *>(support_.values.data()),
        support_.values.size() * sizeof(float)))
    {
      throw std::invalid_argument("Truncated ground support grid");
    }
  }

  if (publish_voxel_) {
    voxel_pub_ = node->create_publisher<nav2_msgs::msg::VoxelGrid>("voxel_grid", 1);
  }

  matchSize();
}

IntensityVoxelLayer::~IntensityVoxelLayer() {}

void IntensityVoxelLayer::updateFootprint(
  double robot_x, double robot_y, double robot_yaw, double * min_x, double * min_y, double * max_x,
  double * max_y)
{
  if (!footprint_clearing_enabled_) {
    return;
  }

  nav2_costmap_2d::transformFootprint(
    robot_x, robot_y, robot_yaw, getFootprint(), transformed_footprint_);

  for (auto & i : transformed_footprint_) {
    touch(i.x, i.y, min_x, min_y, max_x, max_y);
  }

  setConvexPolygonCost(transformed_footprint_, nav2_costmap_2d::FREE_SPACE);
}

void IntensityVoxelLayer::matchSize()
{
  ObstacleLayer::matchSize();
  voxel_grid_.resize(size_x_, size_y_, size_z_);
  confirmation_.clear();
  last_bend_ns_ = 0;
}

void IntensityVoxelLayer::reset()
{
  ObstacleLayer::reset();
  resetMaps();
  confirmation_.clear();
  last_bend_ns_ = 0;
}

void IntensityVoxelLayer::resetMaps()
{
  ObstacleLayer::resetMaps();
  voxel_grid_.reset();
}

void IntensityVoxelLayer::updateBounds(
  double robot_x, double robot_y, double robot_yaw, double * min_x, double * min_y, double * max_x,
  double * max_y)
{
  // update origin information for rolling costmap publication
  if (rolling_window_) {
    updateOrigin(robot_x - getSizeInMetersX() / 2, robot_y - getSizeInMetersY() / 2);
  }

  // reset maps each iteration
  resetMaps();

  // if not enabled, stop here
  // resetMaps clears this layer, but the master only recomposes dirty cells.
  // Include the previous marks, even when this scan finds nothing there.
  if (previous_bounds_.valid()) {
    touch(previous_bounds_.min_x, previous_bounds_.min_y, min_x, min_y, max_x, max_y);
    touch(previous_bounds_.max_x, previous_bounds_.max_y, min_x, min_y, max_x, max_y);
  }
  Bounds current_bounds{1e30, 1e30, -1e30, -1e30};
  if (!enabled_) {
    previous_bounds_ = current_bounds;
    return;
  }

  // get the maximum sized window required to operate
  useExtraBounds(min_x, min_y, max_x, max_y);

  // get the marking observations
  bool current = true;
  std::vector<Observation> observations;
  current = getMarkingObservations(observations) && current;

  // update the global current status
  current_ = current;
  const bool confirm = confirmation_age_s_ > 0.0;
  std::int64_t stamp = 0;
  const auto max_gap = static_cast<std::int64_t>(confirmation_age_s_ * 1.e9);
  if (confirm) {
    if (observations.size() == 1 && observations.front().cloud_) {
      const auto & header = observations.front().cloud_->header;
      const auto now = clock_->now();
      stamp = rclcpp::Time(header.stamp, now.get_clock_type()).nanoseconds();
      const auto age = now.nanoseconds() - stamp;
      if (age < -50000000 || age > max_gap) {stamp = 0;}
    }
    if (stamp <= 0) {confirmation_.clear();}
    if (stamp <= 0 || stamp == confirmation_.stamp()) {observations.clear();}
  }
  std::vector<unsigned int> candidates;
  std::optional<tf2::Transform> to_support;
  if (!support_.values.empty()) {
    try {
      tf2::Transform transform;
      transform.setIdentity();
      if (support_frame_ != global_frame_) {
        tf2::fromMsg(tf_->lookupTransform(support_frame_, global_frame_, tf2::TimePointZero).transform, transform);
      }
      to_support = transform;
    } catch (const tf2::TransformException &) {
      // Fall back to direct obstacle marking until the reference is available.
    }
  }
  int ground_returns = 0;
  // place the new obstacles into a priority queue... each with a priority of zero to begin with
  for (const auto & obs : observations) {
    double sq_obstacle_max_range = obs.obstacle_max_range_ * obs.obstacle_max_range_;
    double sq_obstacle_min_range = obs.obstacle_min_range_ * obs.obstacle_min_range_;

    std::optional<double> ground_offset;
    if (to_support) {
      std::vector<double> offsets;
      sensor_msgs::PointCloud2ConstIterator<float> x(*obs.cloud_, "x"), y(*obs.cloud_, "y"),
        z(*obs.cloud_, "z"), value(*obs.cloud_, "intensity");
      int stride = 0;
      for (; x != x.end(); ++x, ++y, ++z, ++value) {
        if (*value >= 0.035f || ++stride % 7 != 0 ||
          std::hypot(*x - obs.origin_.x, *y - obs.origin_.y) > 3.0)
        {
          continue;
        }
        const auto world = *to_support * tf2::Vector3(*x, *y, *z);
        if (const auto height = support_.heightAt(world.x(), world.y(), true)) {
          const double offset = world.z() - *height;
          if (std::abs(offset) < 0.45) {offsets.push_back(offset);}
        }
      }
      if (offsets.size() >= 25) {
        const auto median = offsets.begin() + offsets.size() / 2;
        std::nth_element(offsets.begin(), median, offsets.end());
        const double estimate = *median;
        const auto inliers = std::count_if(offsets.begin(), offsets.end(), [&](double value) {
            return std::abs(value - estimate) < 0.032;
          });
        if (static_cast<size_t>(inliers) * 3 >= offsets.size() * 2) {ground_offset = estimate;}
      }
    }
    sensor_msgs::PointCloud2ConstIterator<float> it_x(*obs.cloud_, "x");
    sensor_msgs::PointCloud2ConstIterator<float> it_y(*obs.cloud_, "y");
    sensor_msgs::PointCloud2ConstIterator<float> it_z(*obs.cloud_, "z");
    sensor_msgs::PointCloud2ConstIterator<float> it_i(*obs.cloud_, "intensity");
    for (; it_x != it_x.end(); ++it_x, ++it_y, ++it_z, ++it_i) {
      double px = *it_x, py = *it_y, pz = *it_z;

      // if the obstacle is too low/high, we won't add it
      if (pz < min_obstacle_height_ || pz > max_obstacle_height_) {
        continue;
      }

      // if the intensity is not in the range we want, we won't add it
      if (*it_i < min_obstacle_intensity_ || *it_i > max_obstacle_intensity_) {
        continue;
      }

      // compute the squared distance from the hitpoint to the pointcloud's origin
      double sq_dist = (px - obs.origin_.x) * (px - obs.origin_.x) +
                       (py - obs.origin_.y) * (py - obs.origin_.y) +
                       (pz - obs.origin_.z) * (pz - obs.origin_.z);

      // if the point is far/close enough away... we won't consider it
      if (sq_dist <= sq_obstacle_min_range || sq_dist >= sq_obstacle_max_range) {
        continue;
      }

      if (ground_offset) {
        const auto world = *to_support * tf2::Vector3(px, py, pz);
        if (const auto height = support_.heightAt(world.x(), world.y(), true);
          height && std::abs(world.z() - *ground_offset - *height) <= 0.036)
        {
          ++ground_returns;
          continue;
        }
      }

      // now we need to compute the map coordinates for the observation
      unsigned int mx, my, mz;
      if (pz < origin_z_) {
        if (!worldToMap3D(px, py, origin_z_, mx, my, mz)) {
          continue;
        }
      } else if (!worldToMap3D(px, py, pz, mx, my, mz)) {
        continue;
      }

      // mark the cell in the voxel grid and check if we should also mark it in the costmap
      if (voxel_grid_.markVoxelInMap(mx, my, mz, mark_threshold_)) {
        const auto index = getIndex(mx, my);
        if (confirm) {
          candidates.push_back(index);
          continue;
        }
        costmap_[index] = LETHAL_OBSTACLE;
        double cell_x, cell_y;
        mapToWorld(mx, my, cell_x, cell_y);
        touch(cell_x, cell_y, &current_bounds.min_x, &current_bounds.min_y,
          &current_bounds.max_x, &current_bounds.max_y);
      }
    }
  }

  bool use_near_marks = true;
  if (confirm && confirmation_turn_hold_s_ > 0.0) {
    const auto now = clock_->now();
    const auto plan = std::atomic_load(&near_plan_);
    if (last_bend_ns_ > now.nanoseconds()) {last_bend_ns_ = 0;}
    if (plan && plan->header.frame_id == global_frame_) {
      const auto plan_age = now - rclcpp::Time(plan->header.stamp, now.get_clock_type());
      if (plan_age.seconds() >= -0.05 && plan_age.seconds() < 0.8 && nearTurn(*plan, robot_x, robot_y)) {
        last_bend_ns_ = now.nanoseconds();
      }
    }
    use_near_marks = last_bend_ns_ > 0 &&
      now.nanoseconds() - last_bend_ns_ <= static_cast<std::int64_t>(confirmation_turn_hold_s_ * 1.e9);
  }
  if (confirm && stamp > 0) {
    if (stamp != confirmation_.stamp()) {
      const auto count = candidates.size();
      const int radius = static_cast<int>(std::floor(confirmation_radius_m_ / resolution_ + 1.e-9));
      confirmation_.update(stamp, max_gap, size_x_, size_y_, radius, candidates);
      RCLCPP_INFO_THROTTLE(node_.lock()->get_logger(), *clock_, 1000,
        "Planning overlay confirmed %zu marks from %zu candidates, active=%d",
        confirmation_.marks().size(), count, int(use_near_marks));
    }
    if (use_near_marks) {
      for (const auto index : confirmation_.marks()) {
        costmap_[index] = LETHAL_OBSTACLE;
        unsigned int mx, my;
        indexToCells(index, mx, my);
        double x, y;
        mapToWorld(mx, my, x, y);
        touch(x, y, &current_bounds.min_x, &current_bounds.min_y,
          &current_bounds.max_x, &current_bounds.max_y);
      }
    }
  }

  if (publish_voxel_) {
    nav2_msgs::msg::VoxelGrid grid_msg;
    unsigned int size = voxel_grid_.sizeX() * voxel_grid_.sizeY();
    grid_msg.size_x = voxel_grid_.sizeX();
    grid_msg.size_y = voxel_grid_.sizeY();
    grid_msg.size_z = voxel_grid_.sizeZ();
    grid_msg.data.resize(size);
    memcpy(&grid_msg.data[0], voxel_grid_.getData(), size * sizeof(unsigned int));

    grid_msg.origin.x = origin_x_;
    grid_msg.origin.y = origin_y_;
    grid_msg.origin.z = origin_z_;

    grid_msg.resolutions.x = resolution_;
    grid_msg.resolutions.y = resolution_;
    grid_msg.resolutions.z = z_resolution_;
    grid_msg.header.frame_id = global_frame_;
    grid_msg.header.stamp = clock_->now();
    voxel_pub_->publish(grid_msg);
  }

  updateFootprint(robot_x, robot_y, robot_yaw, &current_bounds.min_x, &current_bounds.min_y,
    &current_bounds.max_x, &current_bounds.max_y);
  if (current_bounds.valid()) {
    touch(current_bounds.min_x, current_bounds.min_y, min_x, min_y, max_x, max_y);
    touch(current_bounds.max_x, current_bounds.max_y, min_x, min_y, max_x, max_y);
  }
  previous_bounds_ = current_bounds;
  if (ground_returns > 0) {
    RCLCPP_INFO_THROTTLE(node_.lock()->get_logger(), *clock_, 1500, "Observed ground matched support grid: %d", ground_returns);
  }
}

void IntensityVoxelLayer::updateOrigin(double new_origin_x, double new_origin_y)
{
  // project the new origin into the grid
  int cell_ox, cell_oy;
  cell_ox = static_cast<int>((new_origin_x - origin_x_) / resolution_);
  cell_oy = static_cast<int>((new_origin_y - origin_y_) / resolution_);

  // update the origin with the appropriate world coordinates
  origin_x_ = origin_x_ + cell_ox * resolution_;
  origin_y_ = origin_y_ + cell_oy * resolution_;
}

}  // namespace pb_nav2_costmap_2d

#include "pluginlib/class_list_macros.hpp"
PLUGINLIB_EXPORT_CLASS(pb_nav2_costmap_2d::IntensityVoxelLayer, nav2_costmap_2d::Layer)
