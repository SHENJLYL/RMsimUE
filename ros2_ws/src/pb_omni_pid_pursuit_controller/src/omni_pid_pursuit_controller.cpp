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

#include "pb_omni_pid_pursuit_controller/omni_pid_pursuit_controller.hpp"

#include <limits>

#include "nav2_core/exceptions.hpp"
#include "pb_omni_pid_pursuit_controller/swept_footprint.hpp"
#include "nav2_util/geometry_utils.hpp"
#include "nav2_util/node_utils.hpp"

using nav2_util::declare_parameter_if_not_declared;
using nav2_util::geometry_utils::euclidean_distance;
using std::abs;
using std::hypot;
using std::max;
using std::min;
using namespace nav2_costmap_2d;  // NOLINT
using rcl_interfaces::msg::ParameterType;

namespace pb_omni_pid_pursuit_controller
{

void OmniPidPursuitController::configure(
  const rclcpp_lifecycle::LifecycleNode::WeakPtr & parent, std::string name,
  std::shared_ptr<tf2_ros::Buffer> tf, std::shared_ptr<nav2_costmap_2d::Costmap2DROS> costmap_ros)
{
  auto node = parent.lock();
  node_ = parent;
  if (!node) {
    throw nav2_core::PlannerException("Unable to lock node!");
  }

  costmap_ros_ = costmap_ros;
  costmap_ = costmap_ros_->getCostmap();
  tf_ = tf;
  plugin_name_ = name;
  logger_ = node->get_logger();
  clock_ = node->get_clock();

  double transform_tolerance = 1.0;
  double control_frequency = 20.0;
  max_robot_pose_search_dist_ = getCostmapMaxExtent();

  declare_parameter_if_not_declared(
    node, plugin_name_ + ".translation_kp", rclcpp::ParameterValue(3.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".translation_ki", rclcpp::ParameterValue(0.1));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".translation_kd", rclcpp::ParameterValue(0.3));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".enable_rotation", rclcpp::ParameterValue(true));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".rotation_kp", rclcpp::ParameterValue(3.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".rotation_ki", rclcpp::ParameterValue(0.1));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".rotation_kd", rclcpp::ParameterValue(0.3));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".transform_tolerance", rclcpp::ParameterValue(0.1));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".min_max_sum_error", rclcpp::ParameterValue(1.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".lookahead_dist", rclcpp::ParameterValue(0.3));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".use_velocity_scaled_lookahead_dist", rclcpp::ParameterValue(true));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".min_lookahead_dist", rclcpp::ParameterValue(0.2));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".max_lookahead_dist", rclcpp::ParameterValue(1.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".lookahead_time", rclcpp::ParameterValue(1.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".use_interpolation", rclcpp::ParameterValue(true));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".use_rotate_to_heading", rclcpp::ParameterValue(true));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".use_rotate_to_heading_treshold", rclcpp::ParameterValue(0.1));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".min_approach_linear_velocity", rclcpp::ParameterValue(0.05));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".approach_velocity_scaling_dist", rclcpp::ParameterValue(0.6));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".v_linear_min", rclcpp::ParameterValue(-3.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".v_linear_max", rclcpp::ParameterValue(3.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".v_angular_min", rclcpp::ParameterValue(-3.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".v_angular_max", rclcpp::ParameterValue(3.0));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".max_robot_pose_search_dist",
    rclcpp::ParameterValue(getCostmapMaxExtent()));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".curvature_min", rclcpp::ParameterValue(0.4));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".curvature_max", rclcpp::ParameterValue(0.7));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".reduction_ratio_at_high_curvature", rclcpp::ParameterValue(0.5));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".curvature_forward_dist", rclcpp::ParameterValue(0.7));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".curvature_backward_dist", rclcpp::ParameterValue(0.3));
  declare_parameter_if_not_declared(
    node, plugin_name_ + ".max_velocity_scaling_factor_rate", rclcpp::ParameterValue(0.9));

  external_rotation_possible_ = node->declare_parameter<bool>(
    plugin_name_ + ".external_rotation_possible", false);
  path_lateral_acceleration_ = node->declare_parameter<double>(
    plugin_name_ + ".path_lateral_acceleration", 0.0);
  clearance_departure_ = node->declare_parameter<bool>(
    plugin_name_ + ".clearance_departure", false);
  path_stop_preview_ = node->declare_parameter<bool>(
    plugin_name_ + ".path_stop_preview", false);
  emergency_brake_jerk_ = node->declare_parameter<double>(
    plugin_name_ + ".emergency_brake_jerk", 0.0);
  emergency_brake_latency_ = node->declare_parameter<double>(
    plugin_name_ + ".emergency_brake_latency", 0.15);
  if (!std::isfinite(emergency_brake_jerk_) || emergency_brake_jerk_ < 0.0 ||
    !std::isfinite(emergency_brake_latency_) || emergency_brake_latency_ < 0.01)
  {
    throw nav2_core::PlannerException("Invalid hard brake model");
  }
  if (!std::isfinite(path_lateral_acceleration_) || path_lateral_acceleration_ < 0.0) {
    throw nav2_core::PlannerException("Invalid path lateral acceleration");
  }

  node->get_parameter(plugin_name_ + ".translation_kp", translation_kp_);
  node->get_parameter(plugin_name_ + ".translation_ki", translation_ki_);
  node->get_parameter(plugin_name_ + ".translation_kd", translation_kd_);
  node->get_parameter(plugin_name_ + ".enable_rotation", enable_rotation_);
  node->get_parameter(plugin_name_ + ".rotation_kp", rotation_kp_);
  node->get_parameter(plugin_name_ + ".rotation_ki", rotation_ki_);
  node->get_parameter(plugin_name_ + ".rotation_kd", rotation_kd_);
  node->get_parameter(plugin_name_ + ".transform_tolerance", transform_tolerance);
  node->get_parameter(plugin_name_ + ".min_max_sum_error", min_max_sum_error_);
  node->get_parameter(plugin_name_ + ".lookahead_dist", lookahead_dist_);
  node->get_parameter(
    plugin_name_ + ".use_velocity_scaled_lookahead_dist", use_velocity_scaled_lookahead_dist_);
  node->get_parameter(plugin_name_ + ".min_lookahead_dist", min_lookahead_dist_);
  node->get_parameter(plugin_name_ + ".max_lookahead_dist", max_lookahead_dist_);
  node->get_parameter(plugin_name_ + ".lookahead_time", lookahead_time_);
  node->get_parameter(plugin_name_ + ".use_interpolation", use_interpolation_);
  node->get_parameter(plugin_name_ + ".use_rotate_to_heading", use_rotate_to_heading_);
  node->get_parameter(
    plugin_name_ + ".use_rotate_to_heading_treshold", use_rotate_to_heading_treshold_);
  node->get_parameter(
    plugin_name_ + ".min_approach_linear_velocity", min_approach_linear_velocity_);
  node->get_parameter(
    plugin_name_ + ".approach_velocity_scaling_dist", approach_velocity_scaling_dist_);
  if (approach_velocity_scaling_dist_ > costmap_->getSizeInMetersX() / 2.0) {
    RCLCPP_WARN(
      logger_,
      "approach_velocity_scaling_dist is larger than forward costmap extent, "
      "leading to permanent slowdown");
  }
  node->get_parameter(plugin_name_ + ".v_linear_max", v_linear_max_);
  node->get_parameter(plugin_name_ + ".v_linear_min", v_linear_min_);
  node->get_parameter(plugin_name_ + ".v_angular_max", v_angular_max_);
  node->get_parameter(plugin_name_ + ".v_angular_min", v_angular_min_);
  node->get_parameter(plugin_name_ + ".max_robot_pose_search_dist", max_robot_pose_search_dist_);
  node->get_parameter(plugin_name_ + ".curvature_min", curvature_min_);
  node->get_parameter(plugin_name_ + ".curvature_max", curvature_max_);
  node->get_parameter(
    plugin_name_ + ".reduction_ratio_at_high_curvature", reduction_ratio_at_high_curvature_);
  node->get_parameter(plugin_name_ + ".curvature_forward_dist", curvature_forward_dist_);
  node->get_parameter(plugin_name_ + ".curvature_backward_dist", curvature_backward_dist_);
  node->get_parameter(
    plugin_name_ + ".max_velocity_scaling_factor_rate", max_velocity_scaling_factor_rate_);

  node->get_parameter("controller_frequency", control_frequency);
  declare_parameter_if_not_declared(node, plugin_name_ + ".collision_body_frame",
    rclcpp::ParameterValue(costmap_ros_->getBaseFrameID()));
  declare_parameter_if_not_declared(node, plugin_name_ + ".collision_deceleration",
    rclcpp::ParameterValue(2.0));
  declare_parameter_if_not_declared(node, plugin_name_ + ".collision_reaction_time",
    rclcpp::ParameterValue(0.2));
  node->get_parameter(plugin_name_ + ".collision_body_frame", collision_body_frame_);
  node->get_parameter(plugin_name_ + ".collision_deceleration", collision_deceleration_);
  node->get_parameter(plugin_name_ + ".collision_reaction_time", collision_reaction_time_);
  declare_parameter_if_not_declared(node, plugin_name_ + ".collision_observation_range",
    rclcpp::ParameterValue(0.0));
  node->get_parameter(plugin_name_ + ".collision_observation_range", collision_observation_range_);
  declare_parameter_if_not_declared(node, plugin_name_ + ".tracking_deceleration",
    rclcpp::ParameterValue(collision_deceleration_));
  node->get_parameter(plugin_name_ + ".tracking_deceleration", tracking_deceleration_);
  if (collision_body_frame_.empty() || !std::isfinite(collision_deceleration_) ||
    collision_deceleration_ <= 0 || !std::isfinite(collision_reaction_time_) ||
    collision_reaction_time_ < 0 || !std::isfinite(collision_observation_range_) ||
    collision_observation_range_ < 0 || !std::isfinite(tracking_deceleration_) ||
    tracking_deceleration_ <= 0 || tracking_deceleration_ > collision_deceleration_) {
    throw nav2_core::PlannerException("Invalid chassis collision envelope configuration");
  }

  transform_tolerance_ = tf2::durationFromSec(transform_tolerance);
  control_duration_ = 1.0 / control_frequency;
  track_planned_curve_ = node->declare_parameter<bool>(plugin_name_ + ".track_planned_curve", false);
  swept_stop_only_ = node->declare_parameter<bool>(plugin_name_ + ".swept_stop_only", false);
  curved_stop_sweep_ = node->declare_parameter<bool>(plugin_name_ + ".curved_stop_sweep", false);
  apply_body_speed_limit_ = node->declare_parameter<bool>(plugin_name_ + ".apply_body_speed_limit", true);
  topp_profile_ = node->declare_parameter<bool>(plugin_name_ + ".topp_profile", false);
  fast_race_mode_ = node->declare_parameter<bool>(plugin_name_ + ".fast_race_mode", false);
  mpc_race_mode_ = node->declare_parameter<bool>(plugin_name_ + ".mpc_race_mode", false);
  emergency_brake_deceleration_ = node->declare_parameter<double>(plugin_name_ + ".emergency_brake_deceleration", 0.0);

  local_path_pub_ = node->create_publisher<nav_msgs::msg::Path>("local_plan", 1);
  carrot_pub_ = node->create_publisher<geometry_msgs::msg::PointStamped>("lookahead_point", 1);
  curvature_points_pub_ =
    node_.lock()
      ->create_publisher<visualization_msgs::msg::MarkerArray>(  // 初始化 MarkerArray Publisher
        "curvature_points_marker_array", rclcpp::QoS(10));

  move_pid_ = std::make_shared<PID>(
    control_duration_, v_linear_max_, v_linear_min_, translation_kp_, translation_kd_,
    translation_ki_);
  heading_pid_ = std::make_shared<PID>(
    control_duration_, v_angular_max_, v_angular_min_, rotation_kp_, rotation_kd_, rotation_ki_);
}

void OmniPidPursuitController::cleanup()
{
  RCLCPP_INFO(
    logger_,
    "Cleaning up controller: %s of type"
    " pb_omni_pid_pursuit_controller::OmniPidPursuitController",
    plugin_name_.c_str());
  local_path_pub_.reset();
  carrot_pub_.reset();
  curvature_points_pub_.reset();
}

void OmniPidPursuitController::activate()
{
  RCLCPP_INFO(
    logger_,
    "Activating controller: %s of type "
    "regulated_pure_pursuit_controller::OmniPidPursuitController",
    plugin_name_.c_str());
  local_path_pub_->on_activate();
  carrot_pub_->on_activate();
  curvature_points_pub_->on_activate();
  // Add callback for dynamic parameters
  auto node = node_.lock();
  dyn_params_handler_ = node->add_on_set_parameters_callback(
    std::bind(&OmniPidPursuitController::dynamicParametersCallback, this, std::placeholders::_1));
}

void OmniPidPursuitController::deactivate()
{
  RCLCPP_INFO(
    logger_,
    "Deactivating controller: %s of type "
    "regulated_pure_pursuit_controller::OmniPidPursuitController",
    plugin_name_.c_str());
  local_path_pub_->on_deactivate();
  carrot_pub_->on_deactivate();
  curvature_points_pub_->on_deactivate();
  dyn_params_handler_.reset();
}

geometry_msgs::msg::TwistStamped OmniPidPursuitController::computeVelocityCommands(
  const geometry_msgs::msg::PoseStamped & pose, const geometry_msgs::msg::Twist & velocity,
  nav2_core::GoalChecker * /*goal_checker*/)
{
  std::lock_guard<std::mutex> lock_reinit(mutex_);

  nav2_costmap_2d::Costmap2D * costmap = costmap_ros_->getCostmap();
  std::unique_lock<nav2_costmap_2d::Costmap2D::mutex_t> lock(*(costmap->getMutex()));

  // Transform path to robot base frame
  bool contains_goal = false;
  auto transformed_plan = transformGlobalPlan(pose, contains_goal);

  const bool profile_speed = path_lateral_acceleration_ > 0.0;
  // The costmap's virtual tracking axes and the footprint's physical yaw are independent.
  const auto & map_frame = costmap_ros_->getGlobalFrameID();
  geometry_msgs::msg::PoseStamped nav_pose, body_pose, body_origin;
  body_origin.header = pose.header;
  body_origin.header.frame_id = collision_body_frame_;
  body_origin.pose.orientation.w = 1.0;
  if (!transformPose(map_frame, pose, nav_pose) || !transformPose(map_frame, body_origin, body_pose)) {
    throw nav2_core::PlannerException("Missing transform for chassis collision prediction");
  }
  const double nav_yaw = tf2::getYaw(nav_pose.pose.orientation);
  const double body_yaw = tf2::getYaw(body_pose.pose.orientation);
  const auto & origin = body_pose.pose.position;
  auto rotate = [](double x, double y, double yaw) -> sweep::Vector {
    return {x * std::cos(yaw) - y * std::sin(yaw), x * std::sin(yaw) + y * std::cos(yaw)};
  };
  const auto measured = rotate(velocity.linear.x, velocity.linear.y, body_yaw);
  const double speed = measured.length();
  if (!std::isfinite(speed) || !std::isfinite(velocity.angular.z) || !std::isfinite(nav_yaw) ||
    !std::isfinite(origin.x) || !std::isfinite(origin.y)) {
    throw nav2_core::PlannerException("Invalid chassis motion for collision prediction");
  }
  double pose_age = 0.0;
  if (profile_speed) {
    const double age = (clock_->now() -
      rclcpp::Time(pose.header.stamp, clock_->get_clock_type())).seconds();
    if (age > 0.25) {
      geometry_msgs::msg::TwistStamped stop;
      stop.header = pose.header;
      return stop;
    }
    // Anticipate the displacement already covered since the tracking pose.
    // The swept-footprint guard below still uses the stamped pose and its full
    // stopping horizon.
    pose_age = std::max(0.0, age);
    const auto offset = rotate(measured.x, measured.y, -nav_yaw) * std::min(pose_age, 0.20);
    for (auto & point : transformed_plan.poses) {
      point.pose.position.x -= offset.x;
      point.pose.position.y -= offset.y;
    }
    const auto limit = nav2_util::geometry_utils::first_after_integrated_distance(
      transformed_plan.poses.begin(), transformed_plan.poses.end(), offset.length() + 0.25);
    auto nearest = std::min_element(transformed_plan.poses.begin(), limit, [](const auto & a, const auto & b) {
        return std::hypot(a.pose.position.x, a.pose.position.y) <
               std::hypot(b.pose.position.x, b.pose.position.y);
      });
    transformed_plan.poses.erase(transformed_plan.poses.begin(), nearest);
  }
  double lookahead_dist = getLookAheadDistance(velocity);
  auto carrot_pose = getLookAheadPoint(lookahead_dist, transformed_plan);
  const auto footprint = costmap_ros_->getRobotFootprint();
  if (footprint.size() < 3) {throw nav2_core::PlannerException("Missing chassis footprint");}
  double footprint_radius = 0.0;
  for (const auto & v : footprint) {
    footprint_radius = std::max(footprint_radius, std::hypot(v.x, v.y));
  }
  std::vector<sweep::Vector> body, rotating_body;
  for (const auto & vertex : footprint) {
    const auto offset = rotate(vertex.x, vertex.y, body_yaw);
    body.push_back({origin.x + offset.x, origin.y + offset.y});
  }
  constexpr int sides = 20;
  const double radius = footprint_radius / std::cos(M_PI / sides);
  for (int i = 0; i < sides; ++i) {
    const double angle = 2.0 * M_PI * i / sides;
    rotating_body.push_back({origin.x + radius * std::cos(angle), origin.y + radius * std::sin(angle)});
  }
  // In translation-only navigation, bound the excursion of every corner at the
  // measured yaw rate over a full reaction and braking interval. A complete
  // rotation remains the envelope for commands that can actively turn the base.
  const double horizon = collision_reaction_time_ + std::max(speed, v_linear_max_) / collision_deceleration_;
  const double yaw_excursion = std::min(M_PI, std::abs(velocity.angular.z) * horizon);
  const double yaw_margin = 2.0 * footprint_radius * std::sin(0.5 * yaw_excursion);
  const double stop_decel = (emergency_brake_deceleration_ > 0.0) ? emergency_brake_deceleration_ : collision_deceleration_;
  const auto stop_distance = [&](double v, double advance = 0.0) {
    if (profile_speed && emergency_brake_jerk_ > 0.0) {
      return sweep::jerkLimitedStoppingDistance(
        v, pose_age + emergency_brake_latency_ + advance,
        stop_decel, emergency_brake_jerk_);
    }
    return sweep::stoppingDistance(v, collision_reaction_time_ + advance, stop_decel);
  };
  const auto coasting = measured * (speed > 1e-6 ? stop_distance(speed) / speed : 0.0);
  const auto plan_distances = calculateCumulativeDistances(transformed_plan);
  auto curved_path_clear = [&](double req_speed, double /*stop_dist*/) {
    if (!curved_stop_sweep_ || transformed_plan.poses.size() < 2 || plan_distances.back() < 0.05) {
      return false;
    }
    // Physically simulate an inertial braking + turning rollout from current measured velocity
    // using honest tracking deceleration (4.0 m/s^2), conservative lateral accel (5.5 m/s^2),
    // and full pipeline latency (pose_age + 0.14s) with 8cm margin.
    const double lat_max = std::min(5.5, std::max(3.5, path_lateral_acceleration_));
    const double dec_max = std::min(tracking_deceleration_, 4.0);
    const double sim_dt = 0.05;
    const double margin = 0.08;

    // 1. Reaction latency coast at current measured velocity
    const double latency = pose_age + 0.14;
    sweep::Vector pos{0.0, 0.0};
    sweep::Vector vel = measured;
    const double init_v = std::max(speed, req_speed);
    if (speed > 1e-4 && req_speed > speed) {
      vel = measured * (req_speed / speed);
    } else if (speed <= 1e-4 && req_speed > 1e-4) {
      const auto p1 = findPoseAtDistance(transformed_plan, plan_distances, std::min(0.5, plan_distances.back())).pose.position;
      const auto d1 = rotate(p1.x, p1.y, nav_yaw);
      const double l1 = d1.length();
      if (l1 > 1e-4) {vel = d1 * (req_speed / l1);}
    }
    const sweep::Vector coast_step = vel * latency;
    if (coast_step.length() > 1e-4) {
      if (!sweep::clear(*costmap, rotating_body, coast_step, {}, nav2_costmap_2d::LETHAL_OBSTACLE, margin)) {
        return false;
      }
      pos.x += coast_step.x;
      pos.y += coast_step.y;
    }

    // 2. Roll out inertial turning + braking until stopped (max 25 steps = 1.25s)
    double arc_s = latency * init_v;
    for (int step = 0; step < 25; ++step) {
      const double v_mag = vel.length();
      if (v_mag < 0.12) {break;}
      // Desired path tangent at current rollout arc_s
      const double s_a = std::min(arc_s, plan_distances.back());
      const double s_b = std::min(arc_s + 0.45, plan_distances.back());
      const auto pa = findPoseAtDistance(transformed_plan, plan_distances, s_a).pose.position;
      const auto pb = findPoseAtDistance(transformed_plan, plan_distances, s_b).pose.position;
      const auto dir_w = rotate(pb.x - pa.x, pb.y - pa.y, nav_yaw);
      const double dir_len = dir_w.length();
      const sweep::Vector tangent = (dir_len > 1e-4) ? (dir_w * (1.0 / dir_len)) : (vel * (1.0 / v_mag));

      // Decelerate speed along tangent and steer velocity direction toward tangent within lat_max
      const double next_v = std::max(0.0, v_mag - dec_max * sim_dt);
      const sweep::Vector cur_dir = vel * (1.0 / v_mag);
      const double cross = cur_dir.x * tangent.y - cur_dir.y * tangent.x;
      const double dot = std::clamp(cur_dir.x * tangent.x + cur_dir.y * tangent.y, -1.0, 1.0);
      const double ang_err = std::atan2(cross, dot);
      const double max_dtheta = (lat_max / std::max(0.5, v_mag)) * sim_dt;
      const double dtheta = std::clamp(ang_err, -max_dtheta, max_dtheta);
      const double c = std::cos(dtheta), s = std::sin(dtheta);
      const sweep::Vector next_dir{c * cur_dir.x - s * cur_dir.y, s * cur_dir.x + c * cur_dir.y};
      const sweep::Vector next_vel = next_dir * next_v;
      const sweep::Vector delta{(vel.x + next_vel.x) * 0.5 * sim_dt, (vel.y + next_vel.y) * 0.5 * sim_dt};

      if (collision_observation_range_ > 0.0 &&
        std::hypot(pos.x + delta.x, pos.y + delta.y) + 2.0 * footprint_radius + margin > collision_observation_range_) {
        return false;
      }
      std::vector<sweep::Vector> shifted = rotating_body;
      for (auto & pt : shifted) {
        pt.x += pos.x;
        pt.y += pos.y;
      }
      if (!sweep::clear(*costmap, shifted, delta, {}, nav2_costmap_2d::LETHAL_OBSTACLE, margin)) {
        return false;
      }
      pos.x += delta.x;
      pos.y += delta.y;
      arc_s += delta.length();
      vel = next_vel;
    }
    return true;
  };
  sweep::Block last_block;
  auto clear = [&](sweep::Vector travel, sweep::Vector drift, double margin = 0.0,
      const std::vector<sweep::Vector> * sector = nullptr,
      const sweep::ClearanceDeparture * departure = nullptr) {
    last_block = {};
    // The rectangular window can extend past the terrain marking radius at its corners.
    // Reserve room for the AIM sensor offset inside the chassis and the swept footprint.
    double range = travel.length() + drift.length();
    if (sector) {
      for (const auto & point : *sector) {range = std::max(range, point.length());}
    }
    if (collision_observation_range_ > 0.0 &&
      range + 2.0 * footprint_radius + margin > collision_observation_range_) {
      return false;
    }
    if (!enable_rotation_ && !external_rotation_possible_ && yaw_margin < footprint_radius &&
      sweep::clear(*costmap, body, travel, drift, nav2_costmap_2d::LETHAL_OBSTACLE, yaw_margin + margin, nullptr, sector)) {
      return true;
    }
    // This alternative circumscribes the chassis for all orientations.
    return sweep::clear(*costmap, rotating_body, travel, drift,
      nav2_costmap_2d::LETHAL_OBSTACLE, margin, &last_block, sector, departure);
  };
  if (mpc_race_mode_ && profile_speed && transformed_plan.poses.size() >= 2 && plan_distances.back() > 0.05) {
    const double total_len = plan_distances.back();
    const auto local_vel = rotate(measured.x, measured.y, -nav_yaw);

    // 1. Project robot onto nearest point on transformed_plan
    double best_d2 = std::numeric_limits<double>::infinity();
    double s_near = 0.0;
    for (size_t i = 1; i < plan_distances.size() && plan_distances[i - 1] < 3.5; ++i) {
      const auto & a = transformed_plan.poses[i - 1].pose.position;
      const auto & b = transformed_plan.poses[i].pose.position;
      const double dx = b.x - a.x, dy = b.y - a.y;
      const double seg2 = dx * dx + dy * dy;
      if (seg2 < 1e-10) {continue;}
      const double u = std::clamp((-a.x * dx - a.y * dy) / seg2, 0.0, 1.0);
      const double qx = a.x + u * dx, qy = a.y + u * dy;
      const double d2 = qx * qx + qy * qy;
      if (d2 < best_d2) {
        best_d2 = d2;
        s_near = plan_distances[i - 1] + u * (plan_distances[i] - plan_distances[i - 1]);
      }
    }
    const double rem_len = std::max(0.0, total_len - s_near);

    // 2. Final goal precision approach (< 1.25m to goal)
    if (contains_goal && rem_len < 1.25) {
      const auto & g = transformed_plan.poses.back().pose.position;
      const double gdist = std::hypot(g.x, g.y);
      geometry_msgs::msg::TwistStamped cmd;
      cmd.header = pose.header;
      if (gdist > 1e-4) {
        const double v_brake = std::sqrt(2.0 * 2.2 * gdist);
        const double v_app = std::clamp(
          std::min(v_brake, 2.6 * gdist),
          min_approach_linear_velocity_, std::min(2.8, v_linear_max_));
        cmd.twist.linear.x = v_app * (g.x / gdist);
        cmd.twist.linear.y = v_app * (g.y / gdist);
      }
      last_mpc_vx_ = cmd.twist.linear.x;
      last_mpc_vy_ = cmd.twist.linear.y;
      return cmd;
    }

    // 3. Precompute world-relative path samples (position + unit tangent) every 0.20m up to 8.0m ahead
    struct PathSample { double s; double x; double y; double tx; double ty; };
    std::vector<PathSample> samples;
    const double sample_end = std::min(total_len, s_near + 8.0);
    for (double s = s_near; s <= sample_end + 1e-6; s += 0.20) {
      const double sc = std::min(s, total_len);
      const auto p = findPoseAtDistance(transformed_plan, plan_distances, sc).pose.position;
      const auto p_nxt = findPoseAtDistance(transformed_plan, plan_distances, std::min(total_len, sc + 0.30)).pose.position;
      const auto pw = rotate(p.x, p.y, nav_yaw);
      const auto dw = rotate(p_nxt.x - p.x, p_nxt.y - p.y, nav_yaw);
      const double dl = dw.length();
      const double tx = (dl > 1e-4) ? (dw.x / dl) : 1.0;
      const double ty = (dl > 1e-4) ? (dw.y / dl) : 0.0;
      samples.push_back({sc, pw.x, pw.y, tx, ty});
    }
    auto project_idx = [&](double x, double y, double & out_d2) -> size_t {
      out_d2 = std::numeric_limits<double>::infinity();
      size_t best_i = 0;
      for (size_t i = 0; i < samples.size(); ++i) {
        const double d2 = (x - samples[i].x) * (x - samples[i].x) + (y - samples[i].y) * (y - samples[i].y);
        if (d2 < out_d2) {
          out_d2 = d2;
          best_i = i;
        }
      }
      return best_i;
    };

    // 4. Fast exact rotating-disk costmap collision checker (mu=1.05: 15.5cm Tier-1 / 9.5cm Tier-2)
    const double hard_r = radius + 0.095;
    const double safe_r = radius + 0.155;
    static const std::array<std::pair<double, double>, 19> kDiskOffsets = []() {
      std::array<std::pair<double, double>, 19> arr{};
      arr[0] = {0.0, 0.0};
      for (int i = 0; i < 12; ++i) {
        const double a = 2.0 * M_PI * i / 12.0;
        arr[1 + i] = {std::cos(a), std::sin(a)};
      }
      for (int i = 0; i < 6; ++i) {
        const double a = 2.0 * M_PI * i / 6.0;
        arr[13 + i] = {0.55 * std::cos(a), 0.55 * std::sin(a)};
      }
      return arr;
    }();
    auto disk_cost_r = [&](double rx, double ry, double r_use) -> int {
      int max_c = 0;
      for (const auto & off : kDiskOffsets) {
        unsigned int mx = 0, my = 0;
        const double wx = origin.x + rx + off.first * r_use;
        const double wy = origin.y + ry + off.second * r_use;
        if (!costmap->worldToMap(wx, wy, mx, my)) {
          // Only reject if within observed local costmap radius; don't dead-stop on distant map edge
          if (std::hypot(rx, ry) < 6.0) {return -1;}
          continue;
        }
        const unsigned char c = costmap->getCost(mx, my);
        if (c >= nav2_costmap_2d::LETHAL_OBSTACLE || c == nav2_costmap_2d::NO_INFORMATION) {
          return -1;
        }
        if (c > max_c) {max_c = c;}
      }
      return max_c;
    };
    const double active_r = (disk_cost_r(0.0, 0.0, safe_r) >= 0) ? safe_r : hard_r;
    auto disk_cost = [&](double rx, double ry) -> int {
      return disk_cost_r(rx, ry, active_r);
    };

    // 5. Path tangent near robot
    const double ang1 = std::atan2(samples.front().ty, samples.front().tx);
    const sweep::Vector t1{samples.front().tx, samples.front().ty};
    const size_t i_mid = std::min(samples.size() - 1, static_cast<size_t>(7));
    const sweep::Vector t2{samples[i_mid].tx, samples[i_mid].ty};

    // 6. Closed-loop Stage-2 Rollout Evaluator (general lookahead curvature profiler + 3D tilt + goal braking)
    const auto & bq = body_pose.pose.orientation;
    const double uz = std::clamp(1.0 - 2.0 * (bq.x * bq.x + bq.y * bq.y), -1.0, 1.0);
    const double tilt_deg = std::acos(uz) * (180.0 / M_PI);
    double v_eff = (tilt_deg > 5.0)
      ? std::clamp(v_linear_max_ - 0.24 * (tilt_deg - 5.0), 2.3, v_linear_max_)
      : v_linear_max_;
    // Lookahead curvature speed envelope: full speed on straights, smooth anticipatory braking before any corner
    for (size_t i = 2; i + 2 < samples.size(); ++i) {
      const double d_ahead = std::max(0.0, samples[i].s - s_near);
      if (d_ahead > 4.2) {break;}
      const double ds = std::max(0.10, samples[i + 2].s - samples[i - 2].s);
      const double dot = std::clamp(
        samples[i - 2].tx * samples[i + 2].tx + samples[i - 2].ty * samples[i + 2].ty, -1.0, 1.0);
      const double kappa = std::acos(dot) / ds;
      if (kappa > 0.18) {
        const double v_turn = std::max(1.45, std::sqrt(3.4 / kappa));
        const double v_allow = std::sqrt(v_turn * v_turn + 2.0 * 3.2 * d_ahead);
        if (v_allow < v_eff) {v_eff = v_allow;}
      }
    }
    if (contains_goal) {
      const double rem_goal = std::max(0.0, plan_distances.back() - s_near);
      if (rem_goal < 4.5) {
        v_eff = std::min(v_eff, std::max(0.35, std::sqrt(2.0 * 2.0 * rem_goal)));
      }
    }
    const double tau = std::min(pose_age, 0.06) + 0.02;
    const double dt = 0.06;
    const double a_acc = 4.2;
    const double a_dec = 3.8;
    const double a_brake = 4.0;
    const auto last_w = rotate(last_mpc_vx_, last_mpc_vy_, nav_yaw);

    auto eval_rollout_r = [&](sweep::Vector u1, double stage2_v, double r_use, double & out_score) -> bool {
      // Latency coast at current measured velocity (checked against hard_r so curving candidates aren't falsely killed)
      double px = measured.x * tau;
      double py = measured.y * tau;
      double vx = measured.x;
      double vy = measured.y;
      const double coast_r = std::min(r_use, hard_r);
      if (disk_cost_r(0.5 * px, 0.5 * py, coast_r) < 0 || disk_cost_r(px, py, coast_r) < 0) {
        return false;
      }
      int max_infl = 0;
      double mid_x = px, mid_y = py;
      // Stage 1 (4 steps = 0.24s open-loop u1) + Stage 2 (5 steps = 0.30s closed-loop curve following!)
      for (int step = 0; step < 9; ++step) {
        sweep::Vector u = u1;
        if (step >= 4) {
          double d2_tmp = 0.0;
          const size_t idx = project_idx(px, py, d2_tmp);
          const auto & sp = samples[idx];
          u.x = stage2_v * sp.tx + 2.2 * (sp.x - px);
          u.y = stage2_v * sp.ty + 2.2 * (sp.y - py);
          const double um = std::hypot(u.x, u.y);
          if (um > v_eff && um > 1e-6) {
            u.x *= (v_eff / um);
            u.y *= (v_eff / um);
          }
        }
        const double dvx = u.x - vx;
        const double dvy = u.y - vy;
        const double dv = std::hypot(dvx, dvy);
        if (dv > 1e-5) {
          const bool braking = (dvx * vx + dvy * vy) < 0.0;
          const double max_dv = (braking ? a_dec : a_acc) * dt;
          const double scale = std::min(1.0, max_dv / dv);
          vx += dvx * scale;
          vy += dvy * scale;
        }
        const double prev_x = px, prev_y = py;
        px += vx * dt;
        py += vy * dt;
        const int cm = disk_cost_r(0.5 * (prev_x + px), 0.5 * (prev_y + py), r_use);
        if (cm < 0) {return false;}
        const int c = disk_cost_r(px, py, r_use);
        if (c < 0) {return false;}
        if (cm > max_infl) {max_infl = cm;}
        if (c > max_infl) {max_infl = c;}
        if (step == 3) {mid_x = px; mid_y = py;}
      }
      const double end_x = px, end_y = py;

      // Terminal braking safety guarantee: must be able to brake to < 0.25 m/s without collision!
      double bx = px, by = py, bvx = vx, bvy = vy;
      for (int bstep = 0; bstep < 20; ++bstep) {
        const double bv = std::hypot(bvx, bvy);
        if (bv < 0.25) {break;}
        const double next_bv = std::max(0.0, bv - a_brake * dt);
        const double s = next_bv / bv;
        bvx *= s;
        bvy *= s;
        const double pbx = bx, pby = by;
        bx += bvx * dt;
        by += bvy * dt;
        if (disk_cost_r(0.5 * (pbx + bx), 0.5 * (pby + by), r_use) < 0 || disk_cost_r(bx, by, r_use) < 0) {return false;}
      }

      double d2_end = 0.0, d2_mid = 0.0;
      const size_t i_end = project_idx(end_x, end_y, d2_end);
      const size_t i_m = project_idx(mid_x, mid_y, d2_mid);
      const double progress = (samples[i_end].s - s_near) + 0.35 * (samples[i_m].s - s_near);
      const double smooth_d2 = (u1.x - last_w.x) * (u1.x - last_w.x) + (u1.y - last_w.y) * (u1.y - last_w.y);
      const double infl_pen = (max_infl > 90) ? 0.032 * (max_infl - 90) : 0.0;
      out_score = 5.0 * progress - 2.0 * d2_mid - 3.0 * d2_end - infl_pen - 0.030 * smooth_d2;
      return true;
    };

    // 7. Deterministic 2-stage closed-loop racing lattice (~140 candidates, <0.4ms)
    bool found = false;
    double best_score = -1e18;
    sweep::Vector best_u1{0.0, 0.0};

    constexpr std::array<double, 9> kAngles = {-1.50, -1.05, -0.65, -0.30, 0.0, 0.30, 0.65, 1.05, 1.50};
    const std::array<double, 5> speeds = {
      std::min(1.0, v_eff), std::min(1.8, v_eff), std::min(2.7, v_eff),
      std::min(3.6, v_eff), v_eff};
    const std::array<double, 4> stage2_vs = {
      std::min(1.4, v_eff), std::min(2.3, v_eff), std::min(3.3, v_eff), v_eff};

    for (double da : kAngles) {
      const double a = ang1 + da;
      const sweep::Vector dir{std::cos(a), std::sin(a)};
      for (double sp : speeds) {
        const sweep::Vector u1 = dir * sp;
        for (double s2v : stage2_vs) {
          double sc = 0.0;
          if (eval_rollout_r(u1, s2v, active_r, sc) && sc > best_score) {
            best_score = sc;
            best_u1 = u1;
            found = true;
          }
        }
      }
    }
    // Tier 2: if 16cm buffer rejected all candidates, try evasive curving at hard_r (+10cm clear!)
    if (!found && active_r > hard_r + 1e-4) {
      for (double da : kAngles) {
        const double a = ang1 + da;
        const sweep::Vector dir{std::cos(a), std::sin(a)};
        for (double sp : {0.9, 1.6, 2.2}) {
          for (double s2v : {1.3, 2.0}) {
            double sc = 0.0;
            if (eval_rollout_r(dir * sp, s2v, hard_r, sc) && sc > best_score) {
              best_score = sc;
              best_u1 = dir * sp;
              found = true;
            }
          }
        }
      }
    }
    // Also test pure braking & slow centering along t1/t2 at hard_r
    if (!found) {
      for (const auto & u1 : {sweep::Vector{0.0, 0.0}, t1 * 0.9, t2 * 1.4, t2 * 2.4}) {
        double sc = 0.0;
        if (eval_rollout_r(u1, 1.4, hard_r, sc) && sc > best_score) {
          best_score = sc;
          best_u1 = u1;
          found = true;
        }
      }
    }

    if (!found && speed < 0.45) {
      // Safe departure if standing in tight clearance: never freeze at 0.0 m/s
      const double esc_r = radius + 0.02;
      for (double da : {0.0, 0.35, -0.35, 0.75, -0.75, 1.25, -1.25, 1.85, -1.85, 2.5, -2.5, 3.14}) {
        const double a = ang1 + da;
        const sweep::Vector dir{std::cos(a), std::sin(a)};
        if (disk_cost_r(dir.x * 0.15, dir.y * 0.15, esc_r) >= 0 &&
            disk_cost_r(dir.x * 0.35, dir.y * 0.35, esc_r) >= 0) {
          best_u1 = dir * 1.35;
          found = true;
          break;
        }
      }
    }

    geometry_msgs::msg::TwistStamped cmd;
    cmd.header = pose.header;
    if (found) {
      const auto u_nav = rotate(best_u1.x, best_u1.y, -nav_yaw);
      cmd.twist.linear.x = u_nav.x;
      cmd.twist.linear.y = u_nav.y;
    } else {
      RCLCPP_INFO(logger_, "turntrace coast origin=%.3f,%.3f obstacle=0.000,0.000 cost=254 measured=%.3f,%.3f age=%.3f yawm=%.3f",
        origin.x, origin.y, measured.x, measured.y, pose_age, yaw_margin);
    }
    last_mpc_vx_ = cmd.twist.linear.x;
    last_mpc_vy_ = cmd.twist.linear.y;
    return cmd;
  }
  if (fast_race_mode_ && profile_speed && transformed_plan.poses.size() >= 2 && plan_distances.back() > 0.05) {
    // 1. Immediate reaction-time straight-line coast MUST be clear (hard safety shield).
    const auto react_coast = measured * (pose_age + 0.10);
    if (!clear(react_coast, {}, 0.06)) {
      RCLCPP_INFO(logger_, "turntrace coast origin=%.3f,%.3f obstacle=%.3f,%.3f cost=%d measured=%.3f,%.3f age=%.3f yawm=%.3f",
        origin.x, origin.y, last_block.center.x, last_block.center.y, last_block.cost,
        measured.x, measured.y, pose_age, yaw_margin);
      geometry_msgs::msg::TwistStamped stop;
      stop.header = pose.header;
      return stop;
    }

    const double total_len = plan_distances.back();
    const auto local_vel = rotate(measured.x, measured.y, -nav_yaw);
    const double pred_x = local_vel.x * 0.06;
    const double pred_y = local_vel.y * 0.06;

    // 2. Project predicted robot position onto the nearest point on transformed_plan.
    double best_d2 = std::numeric_limits<double>::infinity();
    double s_near = 0.0;
    sweep::Vector p_near{transformed_plan.poses.front().pose.position.x,
                         transformed_plan.poses.front().pose.position.y};
    for (size_t i = 1; i < plan_distances.size() && plan_distances[i - 1] < 3.0; ++i) {
      const auto & a = transformed_plan.poses[i - 1].pose.position;
      const auto & b = transformed_plan.poses[i].pose.position;
      const double dx = b.x - a.x, dy = b.y - a.y;
      const double seg2 = dx * dx + dy * dy;
      if (seg2 < 1e-10) {continue;}
      const double u = std::clamp(((pred_x - a.x) * dx + (pred_y - a.y) * dy) / seg2, 0.0, 1.0);
      const double qx = a.x + u * dx, qy = a.y + u * dy;
      const double d2 = (qx - pred_x) * (qx - pred_x) + (qy - pred_y) * (qy - pred_y);
      if (d2 < best_d2) {
        best_d2 = d2;
        s_near = plan_distances[i - 1] + u * (plan_distances[i] - plan_distances[i - 1]);
        p_near = {qx, qy};
      }
    }

    const double rem_len = std::max(0.0, total_len - s_near);
    // 3. Final goal precision approach (< 1.1m to goal, smooth exponential settling)
    if (contains_goal && rem_len < 1.10) {
      const auto & g = transformed_plan.poses.back().pose.position;
      const double gdist = std::hypot(g.x, g.y);
      geometry_msgs::msg::TwistStamped cmd;
      cmd.header = pose.header;
      if (gdist > 1e-4) {
        const double v_brake = std::sqrt(2.0 * 2.2 * gdist);
        const double v_app = std::clamp(
          std::min(v_brake, 2.8 * gdist),
          min_approach_linear_velocity_, std::min(2.5, v_linear_max_));
        cmd.twist.linear.x = v_app * (g.x / gdist);
        cmd.twist.linear.y = v_app * (g.y / gdist);
      }
      return cmd;
    }

    // 4. Tangent & normal at s_near + 0.30m
    const auto pa = findPoseAtDistance(transformed_plan, plan_distances, std::min(total_len, s_near + 0.05)).pose.position;
    const auto pb = findPoseAtDistance(transformed_plan, plan_distances, std::min(total_len, s_near + 0.55)).pose.position;
    double tx = pb.x - pa.x, ty = pb.y - pa.y;
    const double tlen = std::hypot(tx, ty);
    if (tlen > 1e-4) {
      tx /= tlen;
      ty /= tlen;
    } else {
      const auto & g = transformed_plan.poses.back().pose.position;
      const double gl = std::max(1e-4, std::hypot(g.x, g.y));
      tx = g.x / gl;
      ty = g.y / gl;
    }
    const double nx = -ty, ny = tx;
    const double e_perp = (p_near.x - pred_x) * nx + (p_near.y - pred_y) * ny;
    const double v_perp = local_vel.x * nx + local_vel.y * ny;

    // 5. Multi-scale curvature TOPP speed profile (no side-wall costmap stall!)
    constexpr double ds = 0.15;
    const double horizon = std::min(rem_len, 8.0);
    const int n = std::max(2, static_cast<int>(std::ceil(horizon / ds)) + 1);
    std::vector<double> vmax(n, v_linear_max_);
    const double decel = std::max(1.0, tracking_deceleration_);
    const double lat_a = std::max(1.0, path_lateral_acceleration_);
    for (int i = 0; i < n; ++i) {
      const double s = std::min(s_near + i * ds, total_len);
      const auto pm = findPoseAtDistance(transformed_plan, plan_distances, s).pose.position;
      double r_min = 1e6;
      if (s >= 0.25 && s + 0.25 <= total_len) {
        const auto p1 = findPoseAtDistance(transformed_plan, plan_distances, std::max(0.0, s - 0.45)).pose.position;
        const auto p2 = findPoseAtDistance(transformed_plan, plan_distances, std::min(total_len, s + 0.45)).pose.position;
        r_min = std::min(r_min, calculateCurvatureRadius(p1, pm, p2));
      }
      if (s >= 0.50 && s + 0.50 <= total_len) {
        const auto p1 = findPoseAtDistance(transformed_plan, plan_distances, std::max(0.0, s - 0.90)).pose.position;
        const auto p2 = findPoseAtDistance(transformed_plan, plan_distances, std::min(total_len, s + 0.90)).pose.position;
        r_min = std::min(r_min, calculateCurvatureRadius(p1, pm, p2));
      }
      const double bend_v = std::max(1.8, std::sqrt(lat_a * r_min));
      vmax[i] = std::min(vmax[i], bend_v);
    }
    if (contains_goal && rem_len <= horizon + 0.05) {
      vmax[n - 1] = 0.0;
    }
    for (int i = n - 2; i >= 0; --i) {
      const double step_len = std::min(s_near + (i + 1) * ds, total_len) - std::min(s_near + i * ds, total_len);
      if (step_len > 1e-6) {
        vmax[i] = std::min(vmax[i], std::sqrt(vmax[i + 1] * vmax[i + 1] + 2.0 * decel * step_len));
      }
    }
    double v_ref = (n > 3) ? std::min(vmax[0], vmax[3]) : vmax[0];
    v_ref = std::clamp(v_ref, 0.0, v_linear_max_);

    // 6. Path-swept stopping check starting from the robot's ACTUAL position (0, 0)!
    auto path_stop_clear = [&](double test_v) {
      const double stop_len = stop_distance(test_v, control_duration_);
      if (collision_observation_range_ > 0.0 &&
        stop_len + 2.0 * footprint_radius + 0.06 > collision_observation_range_)
      {
        return false;
      }
      if (std::abs(e_perp) > 0.22) {
        const auto dir_w = rotate(tx, ty, nav_yaw);
        return clear(dir_w * stop_len, {});
      }
      const int steps = std::max(1, static_cast<int>(std::ceil(stop_len / 0.18)));
      sweep::Vector prev_w{0.0, 0.0};
      for (int k = 1; k <= steps; ++k) {
        const double sk = std::min(total_len, s_near + (stop_len * k) / steps);
        const auto pk = findPoseAtDistance(transformed_plan, plan_distances, sk).pose.position;
        const auto cur_w = rotate(pk.x, pk.y, nav_yaw);
        const sweep::Vector delta{cur_w.x - prev_w.x, cur_w.y - prev_w.y};
        std::vector<sweep::Vector> shifted = rotating_body;
        for (auto & pt : shifted) {
          pt.x += prev_w.x;
          pt.y += prev_w.y;
        }
        if (!sweep::clear(*costmap, shifted, delta, {}, nav2_costmap_2d::LETHAL_OBSTACLE, 0.06, &last_block)) {
          return false;
        }
        prev_w = cur_w;
      }
      return true;
    };

    if (!path_stop_clear(v_ref)) {
      if (v_ref > 1.0) {
        RCLCPP_INFO(logger_, "turntrace cap origin=%.3f,%.3f obstacle=%.3f,%.3f cost=%d measured=%.3f,%.3f desired=%.3f",
          origin.x, origin.y, last_block.center.x, last_block.center.y, last_block.cost, measured.x, measured.y, v_ref);
      }
      double low = 0.0, high = v_ref;
      for (int i = 0; i < 10; ++i) {
        const double mid = 0.5 * (low + high);
        if (path_stop_clear(mid)) {low = mid;} else {high = mid;}
      }
      v_ref = low;
    }

    // 7. Holonomic vector tracking: tangential v_ref + cross-track PD damping
    const double v_lat = std::clamp(3.6 * e_perp - 0.55 * v_perp, -2.8, 2.8);
    double vx_nav = v_ref * tx + v_lat * nx;
    double vy_nav = v_ref * ty + v_lat * ny;
    const double v_mag = std::hypot(vx_nav, vy_nav);
    if (v_mag > v_linear_max_ && v_mag > 1e-6) {
      vx_nav *= (v_linear_max_ / v_mag);
      vy_nav *= (v_linear_max_ / v_mag);
    }
    geometry_msgs::msg::TwistStamped cmd;
    cmd.header = pose.header;
    cmd.twist.linear.x = vx_nav;
    cmd.twist.linear.y = vy_nav;
    return cmd;
  }
  if (!clear(coasting, {})) {
    if (profile_speed) {
      RCLCPP_INFO(logger_, "turntrace coast origin=%.3f,%.3f obstacle=%.3f,%.3f cost=%d measured=%.3f,%.3f age=%.3f yawm=%.3f",
        origin.x, origin.y, last_block.center.x, last_block.center.y, last_block.cost,
        measured.x, measured.y, pose_age, yaw_margin);
    }
    // Braking is a valid control action. Throwing on every brake cycle makes
    // controller_server clear a populated costmap when failure_tolerance expires.
    RCLCPP_WARN_THROTTLE(logger_, *clock_, 1000, "Braking for blocked or unobserved chassis stopping footprint");
    geometry_msgs::msg::TwistStamped stop;
    stop.header = pose.header;
    return stop;
  }
  auto reachable = [&](const geometry_msgs::msg::PoseStamped & candidate) {
    const auto & p = candidate.pose.position;
    const double angle = std::atan2(p.y, p.x) + nav_yaw;
    const sweep::Vector heading{std::cos(angle), std::sin(angle)};
    const auto drift = sweep::transverseDrift(coasting, heading);
    return clear(heading * std::hypot(p.x, p.y), drift);
  };
  double rejected_distance = lookahead_dist;
  while (!reachable(carrot_pose)) {
    if (lookahead_dist <= costmap->getResolution()) {
      throw nav2_core::PlannerException("No clear chassis sweep to a local tracking point");
    }
    rejected_distance = lookahead_dist;
    lookahead_dist = std::max(costmap->getResolution(), 0.5 * lookahead_dist);
    carrot_pose = getLookAheadPoint(lookahead_dist, transformed_plan);
  }
  // Keep a tested clear target while refining the coarse half-distance bracket.
  while (rejected_distance - lookahead_dist > costmap->getResolution()) {
    const double trial = 0.5 * (lookahead_dist + rejected_distance);
    const auto candidate = getLookAheadPoint(trial, transformed_plan);
    if (reachable(candidate)) {
      carrot_pose = candidate;
      lookahead_dist = trial;
    } else {
      rejected_distance = trial;
    }
  }
  carrot_pub_->publish(createCarrotMsg(carrot_pose));

  double lin_dist = hypot(carrot_pose.pose.position.x, carrot_pose.pose.position.y);
  double theta_dist = atan2(carrot_pose.pose.position.y, carrot_pose.pose.position.x);
  if ((track_planned_curve_ || profile_speed) &&
    (!contains_goal || std::hypot(
      transformed_plan.poses.back().pose.position.x, transformed_plan.poses.back().pose.position.y) > 0.85))
  {
    const auto distances = calculateCumulativeDistances(transformed_plan);
    // Select the tangent at the position the steering reference will reach.
    const auto local_velocity = rotate(measured.x, measured.y, -nav_yaw);
    const double response = 0.15;
    double projected_arc = 0.0;
    if (track_planned_curve_) {
      const auto ahead = local_velocity * response;
      double best_error = std::numeric_limits<double>::infinity();
      for (size_t i=1;i<distances.size() && distances[i-1]<ahead.length()+0.80;++i) {
        const auto & p = transformed_plan.poses[i-1].pose.position;
        const auto & q = transformed_plan.poses[i].pose.position;
        const sweep::Vector chord{q.x-p.x,q.y-p.y};
        const double squared = chord.dot(chord);
        if (squared < 1.e-10) {continue;}
        const sweep::Vector offset{ahead.x-p.x,ahead.y-p.y};
        const double u=std::clamp(offset.dot(chord)/squared,0.0,1.0);
        const double error=std::hypot(offset.x-u*chord.x,offset.y-u*chord.y);
        if (error<best_error) {
          best_error=error;
          projected_arc=distances[i-1]+u*(distances[i]-distances[i-1]);
        }
      }
    }
    const double preview = track_planned_curve_ ? projected_arc : 0.20 + 0.18 * speed;
    const double tangent_span=track_planned_curve_ ? 0.15 : 0.20;
    const auto a = findPoseAtDistance(transformed_plan, distances, std::max(0.0,preview-tangent_span)).pose.position;
    const auto b = findPoseAtDistance(transformed_plan, distances, preview+tangent_span).pose.position;
    const double length = std::hypot(b.x - a.x, b.y - a.y);
    if (length > 1e-4) {
      const sweep::Vector tangent{(b.x - a.x) / length, (b.y - a.y) / length};
      auto point = transformed_plan.poses.front().pose.position;
      if (track_planned_curve_) {
        point=findPoseAtDistance(transformed_plan,distances,projected_arc).pose.position;
        point.x-=local_velocity.x*response;
        point.y-=local_velocity.y*response;
      }
      const double cross_track = tangent.x * point.y - tangent.y * point.x;
      const double correction = std::clamp(2.6 * cross_track / std::max(0.9, speed), -0.68, 0.68);
      const double steering = std::atan2(tangent.y, tangent.x) + std::atan(correction);
      const sweep::Vector candidate{std::cos(steering + nav_yaw), std::sin(steering + nav_yaw)};
      if (clear(candidate * 0.40, sweep::transverseDrift(coasting, candidate), 0.06)) {
        theta_dist = steering;
      }
    }
  }
  const sweep::Vector heading{std::cos(theta_dist + nav_yaw), std::sin(theta_dist + nav_yaw)};
  const auto drift = sweep::transverseDrift(coasting, heading);
  double angle_to_goal = tf2::getYaw(carrot_pose.pose.orientation);

  if (use_rotate_to_heading_) {
    angle_to_goal = tf2::getYaw(transformed_plan.poses.back().pose.orientation);
    if (fabs(angle_to_goal) > use_rotate_to_heading_treshold_) {
      lin_dist = 0;
    }
  }

  // The carrot moves on every replan. Damping uses measured closing speed, so
  // changing the reference cannot inject a one-cycle stop or acceleration kick.
  auto lin_vel = profile_speed ? (lin_dist > 1e-4 ? pathCruiseSpeed(transformed_plan, contains_goal, nav_yaw, origin.x, origin.y) : 0.0) :
    move_pid_->calculate(lin_dist, 0, -measured.dot(heading));
  auto angular_vel = enable_rotation_ ? heading_pid_->calculate(angle_to_goal, 0) : 0.0;

  const auto limit_body_speed = [&](double command, sweep::Vector direction) {
    const auto travel_axis = rotate(direction.x, direction.y, -body_yaw);
    const double longitudinal = travel_axis.x >= 0.0 ? v_linear_max_ : -v_linear_min_;
    return std::min(command, longitudinal / std::max(1e-6, std::abs(travel_axis.x)));
  };
  if (!profile_speed) {applyCurvatureLimitation(transformed_plan, carrot_pose, lin_vel);}
  if (contains_goal) {applyApproachVelocityScaling(transformed_plan, lin_vel);}
  if (profile_speed && apply_body_speed_limit_) {
    lin_vel = limit_body_speed(lin_vel, heading);
  } else if (!profile_speed) {
    // lin_dist is a magnitude; theta_dist already carries reverse and strafe directions.
    lin_vel = std::max(0.0, lin_vel);
    // A bend consumes steering and braking distance. Find where the remaining
    // route departs from the current tracking corridor. Do not stop solely
    // because an otherwise straight path is cropped at the local map boundary.
    const double hx = std::cos(theta_dist), hy = std::sin(theta_dist);
    const double corridor = std::max(2.0 * costmap->getResolution(), 0.5 * footprint_radius);
    double ahead = lin_dist;
    bool beyond_carrot = false;
    bool upcoming_turn = false;
    double turn_speed = 0.0;
    auto last = carrot_pose.pose.position;
    for (const auto & waypoint : transformed_plan.poses) {
      const auto & point = waypoint.pose.position;
      beyond_carrot |= std::hypot(point.x, point.y) >= lin_dist - 1e-6;
      if (!beyond_carrot) {continue;}
      const double forward = point.x * hx + point.y * hy;
      const double lateral = std::abs(point.x * hy - point.y * hx);
      if (lateral > corridor || forward + costmap->getResolution() < ahead) {
        upcoming_turn = true;
        const double dx = point.x - last.x;
        const double dy = point.y - last.y;
        const double length = std::hypot(dx, dy);
        if (length > 1e-6) {
          // A shallow bend can be entered at a crawl without making a full stop.
          constexpr double sharp_turn_cosine = 0.7071067811865476;
          const double straightness = std::clamp((dx * hx + dy * hy) / length, -1.0, 1.0);
          turn_speed = std::clamp((straightness - sharp_turn_cosine) /
            (1.0 - sharp_turn_cosine), 0.0, 1.0);
        }
        break;
      }
      ahead = std::max(ahead, forward);
      last = point;
    }
    if (upcoming_turn) {
      const double delay = tracking_deceleration_ * collision_reaction_time_;
      const double entry = std::sqrt(delay * delay + turn_speed * turn_speed +
        2.0 * tracking_deceleration_ * ahead) - delay;
      lin_vel = std::min(lin_vel, std::max(turn_speed, entry));
    } else if (contains_goal) {
      lin_vel = std::min(lin_vel,
        sweep::approachSpeed(ahead, collision_reaction_time_, tracking_deceleration_));
    }
  }
  // Allow for the larger registration displacement while the chassis spins.
  const double position_margin = profile_speed ?
    0.085 + (external_rotation_possible_ ?
      std::min(0.12, 0.04*std::max(0.0,std::abs(velocity.angular.z)-2.0)) : 0.0) : 0.0;
  const auto limit_upcoming_stop = [&](double lin_vel, sweep::Vector direction) {
    if (!profile_speed || !path_stop_preview_ || lin_vel <= 1.25) {return lin_vel;}
    const double before_preview = lin_vel;
    const auto distances = calculateCumulativeDistances(transformed_plan);
    const auto current_shift = measured * std::min(pose_age, 0.20);
    for (double distance : {0.5, 0.95, 1.4}) {
      if (distance + 0.20 >= distances.back()) {break;}
      const auto before = findPoseAtDistance(transformed_plan, distances, distance - 0.20).pose.position;
      const auto point = findPoseAtDistance(transformed_plan, distances, distance).pose.position;
      const auto after = findPoseAtDistance(transformed_plan, distances, distance + 0.20).pose.position;
      const auto tangent = rotate(after.x - before.x, after.y - before.y, nav_yaw);
      const double length = tangent.length();
      if (length < 1e-6 || tangent.dot(direction) >= 0.974 * length) {continue;}
      const auto direction = tangent * (1.0 / length);
      const auto next = rotate(point.x, point.y, nav_yaw);
      const sweep::Vector shift{next.x + current_shift.x, next.y + current_shift.y};
      auto footprint_at_point = rotating_body;
      for (auto & vertex : footprint_at_point) {
        vertex.x += shift.x;
        vertex.y += shift.y;
      }
      const auto has_stopping_room = [&](double prospective_speed) {
        const double length_to_stop = stop_distance(prospective_speed, 2.0 * control_duration_);
        const double margin = position_margin + std::min(0.13, 0.045 * prospective_speed);
        if (collision_observation_range_ > 0.0 &&
          shift.length() + length_to_stop + 2.0 * footprint_radius + margin > collision_observation_range_)
        {
          return false;
        }
        return sweep::clear(*costmap, footprint_at_point, direction * length_to_stop, {},
          nav2_costmap_2d::LETHAL_OBSTACLE, margin);
      };
      if (has_stopping_room(lin_vel)) {continue;}
      double low = 0.0, high = lin_vel;
      for (int i = 0; i < 7; ++i) {
        const double mid = 0.5 * (low + high);
        if (has_stopping_room(mid)) {low = mid;} else {high = mid;}
      }
      // Back-propagate the certified speed at the bend. The existing checks
      // still certify the current measured and commanded stopping footprints.
      const double braking_distance = std::max(0.0, distance - 0.18);
      const double delay = tracking_deceleration_ * collision_reaction_time_;
      const double approach = std::sqrt(delay*delay + low*low +
        2.0 * tracking_deceleration_ * braking_distance) - delay;
      lin_vel = std::min(lin_vel, std::max(low, approach));
    }
    if (before_preview - lin_vel > 0.15) {
      RCLCPP_INFO_THROTTLE(logger_, *clock_, 800,
        "Path stop preview limits approach from %.2f to %.2f m/s", before_preview, lin_vel);
    }
    return lin_vel;
  };
  lin_vel = limit_upcoming_stop(lin_vel, heading);
  auto can_stop = [&](double requested_speed, sweep::Vector direction, double travel_yaw) {
    const auto lateral_drift = sweep::transverseDrift(coasting, direction);
    // Start easing off two controller cycles before the independent measured-speed stop.
    // Reserve space for the estimated pose and the swerve transition near
    // walls. The allowance vanishes at crawl speed so tight, clear routes
    // remain possible. The independent measured-speed stop uses the real footprint.
    const double transition_margin = std::min(0.24, 0.08 * requested_speed);
    // The speed-dependent delay allowance extends the travel direction.
    // Use a separate lateral allowance in tight corridors.
    const double lateral_margin = profile_speed ? std::min(0.13, 0.045 * requested_speed) : transition_margin;
    const double travel = stop_distance(
      requested_speed,
      (emergency_brake_deceleration_ > 0.0) ? control_duration_ : 2.0 * control_duration_);
    if (swept_stop_only_ && profile_speed) {
      // The measured stop was clear. Check the requested stop with the
      // rotating footprint, residual lateral motion and observed range,
      // or along the certified planned curve when turning around corners.
      return clear(direction * travel, lateral_drift) || curved_path_clear(requested_speed, travel);
    }
    const bool straight_safe = clear(direction * (travel + transition_margin - lateral_margin), lateral_drift,
      lateral_margin + position_margin +
      std::min(requested_speed, std::abs(measured.x * direction.y - measured.y * direction.x)) * control_duration_);
    if (straight_safe) {return true;}
    if (profile_speed && speed > 0.12 && measured.dot(direction) >= 0.0) {
      const auto sector = sweep::brakingSector(
        std::atan2(measured.y, measured.x), travel_yaw,
        stop_distance(std::max(speed, requested_speed), 2.0 * control_duration_));
      if (clear({}, {}, position_margin + 0.025 * requested_speed, &sector)) {return true;}
    }
    return false;
  };
  const double desired_speed = lin_vel;
  if (!can_stop(lin_vel, heading, theta_dist + nav_yaw)) {
    if (profile_speed && desired_speed > 1.0) {
      RCLCPP_INFO(logger_, "turntrace cap origin=%.3f,%.3f obstacle=%.3f,%.3f cost=%d measured=%.3f,%.3f desired=%.3f",
        origin.x, origin.y, last_block.center.x, last_block.center.y, last_block.cost, measured.x, measured.y, desired_speed);
    }
    double low = 0.0, high = lin_vel;
    for (int i = 0; i < 12; ++i) {
      const double trial = 0.5 * (low + high);
      if (can_stop(trial, heading, theta_dist + nav_yaw)) {low = trial;} else {high = trial;}
    }
    lin_vel = low;
  }
  if (clearance_departure_ && profile_speed && external_rotation_possible_ &&
    lin_vel < 0.10 && speed < 0.18 && desired_speed >= 0.35 &&
    !clear({}, {}, position_margin))
  {
    const double crawl = std::min(0.55, desired_speed);
    const double range = stop_distance(crawl, 2.0 * control_duration_) + 0.08 * crawl;
    const auto travel = heading * range;
    const sweep::ClearanceDeparture departure{{origin.x, origin.y}, radius};
    if (clear(travel, drift) &&
      clear(travel, drift, position_margin + 0.045*crawl, nullptr, &departure))
    {
      lin_vel = crawl;
      RCLCPP_INFO_THROTTLE(logger_, *clock_, 750,
        "Leaving an occupied positioning margin at %.2f m/s with a clear hard footprint", crawl);
    }
  }
  geometry_msgs::msg::TwistStamped cmd_vel;
  cmd_vel.header = pose.header;
  cmd_vel.twist.linear.x = lin_vel * std::cos(theta_dist);
  cmd_vel.twist.linear.y = lin_vel * std::sin(theta_dist);
  cmd_vel.twist.angular.z = angular_vel;

  return cmd_vel;
}

double OmniPidPursuitController::pathCruiseSpeed(
  const nav_msgs::msg::Path & path, bool contains_goal, double nav_yaw,
  double origin_x, double origin_y) const
{
  const auto distances = calculateCumulativeDistances(path);
  const double length = distances.back();
  if (topp_profile_ && length > 0.1) {
    // Forward-backward Time-Optimal Path Velocity Profile (TOPP) with curvature +
    // local costmap inflation clearance shaping so the robot arrives at corners
    // at exact optimal speed without triggering bang-bang coasting dead-stops.
    constexpr double ds = 0.15;
    const double horizon = std::min(length, 6.0);
    const int n = std::max(2, static_cast<int>(std::ceil(horizon / ds)) + 1);
    std::vector<double> vmax(n, v_linear_max_);
    const double c_nav = std::cos(nav_yaw), s_nav = std::sin(nav_yaw);
    const double decel = std::max(1.0, tracking_deceleration_);
    for (int i = 0; i < n; ++i) {
      const double s = std::min(i * ds, length);
      const auto p_mid = findPoseAtDistance(path, distances, s).pose.position;
      // 1. Curvature limit (chord ±0.45m)
      if (s >= 0.25 && s + 0.25 <= length) {
        const auto p_a = findPoseAtDistance(path, distances, std::max(0.0, s - 0.45)).pose.position;
        const auto p_b = findPoseAtDistance(path, distances, std::min(length, s + 0.45)).pose.position;
        const double radius = calculateCurvatureRadius(p_a, p_mid, p_b);
        const double bend_v = std::sqrt(std::max(1.0, path_lateral_acceleration_) * radius);
        vmax[i] = std::min(vmax[i], bend_v);
      }
      // 2. Costmap inflation clearance shaping (only slow down when very close to a wall)
      const double wx = origin_x + p_mid.x * c_nav - p_mid.y * s_nav;
      const double wy = origin_y + p_mid.x * s_nav + p_mid.y * c_nav;
      unsigned int mx = 0, my = 0;
      if (costmap_->worldToMap(wx, wy, mx, my)) {
        const unsigned char cost = costmap_->getCost(mx, my);
        if (cost >= nav2_costmap_2d::INSCRIBED_INFLATED_OBSTACLE) {
          vmax[i] = std::min(vmax[i], 1.5);
        } else if (cost > 160) {
          const double frac = static_cast<double>(cost - 160) /
            static_cast<double>(nav2_costmap_2d::INSCRIBED_INFLATED_OBSTACLE - 160);
          const double wall_v = v_linear_max_ - frac * (v_linear_max_ - 2.4);
          vmax[i] = std::min(vmax[i], std::max(2.4, wall_v));
        }
      }
    }
    if (contains_goal) {
      vmax[n - 1] = 0.0;
    }
    // Backward kinematic pass: v[i] = min(v[i], sqrt(v[i+1]^2 + 2 * decel * ds))
    for (int i = n - 2; i >= 0; --i) {
      const double step_len = std::min((i + 1) * ds, length) - std::min(i * ds, length);
      if (step_len > 1e-6) {
        vmax[i] = std::min(vmax[i], std::sqrt(vmax[i + 1] * vmax[i + 1] + 2.0 * decel * step_len));
      }
    }
    // Account for ~0.10s pipeline latency by taking min over the first 0.30m (2 steps)
    double v_out = vmax[0];
    if (n > 2) {
      v_out = std::min(v_out, vmax[2]);
    }
    return std::clamp(v_out, 0.0, v_linear_max_);
  }
  // At each bend, project the admissible corner speed backward under braking.
  // Steering uses its own lookahead to keep following the corridor at low speed.
  constexpr double step = 0.55;
  const double track_react = (emergency_brake_deceleration_ > 0.0) ? std::min(collision_reaction_time_, 0.15) : collision_reaction_time_;
  const double horizon = std::min(length, sweep::stoppingDistance(
      v_linear_max_, track_react, tracking_deceleration_) + step);
  const double delay = tracking_deceleration_ * track_react;
  double limit = v_linear_max_;
  for (double s = step; s + 0.1 < horizon; s += step) {
    const auto before = findPoseAtDistance(path, distances, s - step).pose.position;
    const auto middle = findPoseAtDistance(path, distances, s).pose.position;
    const auto after = findPoseAtDistance(path, distances, s + step).pose.position;
    const double radius = calculateCurvatureRadius(before, middle, after);
    const double bend_speed = std::sqrt(path_lateral_acceleration_ * radius);
    const double approach = std::max(0.0, s - step);
    const double entry = std::sqrt(delay * delay + bend_speed * bend_speed +
        2.0 * tracking_deceleration_ * approach) - delay;
    limit = std::min(limit, std::max(bend_speed, entry));
  }
  if (contains_goal) {
    const auto & goal = path.poses.back().pose.position;
    limit = std::min(limit, sweep::approachSpeed(std::max(length, std::hypot(goal.x, goal.y)),
        collision_reaction_time_, tracking_deceleration_));
  }
  return limit;
}

void OmniPidPursuitController::setPlan(const nav_msgs::msg::Path & path)
{
  global_plan_ = path;
}

void OmniPidPursuitController::setSpeedLimit(
  const double & /*speed_limit*/, const bool & /*percentage*/)
{
  RCLCPP_WARN(logger_, "Speed limit is not implemented in this controller.");
}

nav_msgs::msg::Path OmniPidPursuitController::transformGlobalPlan(
  const geometry_msgs::msg::PoseStamped & pose, bool & contains_goal)
{
  if (global_plan_.poses.empty()) {
    throw nav2_core::PlannerException("Received plan with zero length");
  }

  // let's get the pose of the robot in the frame of the plan
  geometry_msgs::msg::PoseStamped robot_pose;
  if (!transformPose(global_plan_.header.frame_id, pose, robot_pose)) {
    throw nav2_core::PlannerException("Unable to transform robot pose into global plan's frame");
  }

  // We'll discard points on the plan that are outside the local costmap
  double max_costmap_extent = getCostmapMaxExtent();

  auto closest_pose_upper_bound = nav2_util::geometry_utils::first_after_integrated_distance(
    global_plan_.poses.begin(), global_plan_.poses.end(), max_robot_pose_search_dist_);

  // First find the closest pose on the path to the robot
  // bounded by when the path turns around (if it does) so we don't get a pose from a later
  // portion of the path
  auto transformation_begin = nav2_util::geometry_utils::min_by(
    global_plan_.poses.begin(), closest_pose_upper_bound,
    [&robot_pose](const geometry_msgs::msg::PoseStamped & ps) {
      return euclidean_distance(robot_pose, ps);
    });

  // Find points up to max_transform_dist so we only transform them.
  auto transformation_end = std::find_if(
    transformation_begin, global_plan_.poses.end(),
    [&](const auto & pose) { return euclidean_distance(pose, robot_pose) > max_costmap_extent; });

  contains_goal = transformation_end == global_plan_.poses.end();

  // Lambda to transform a PoseStamped from global frame to local
  auto transform_global_pose_to_local = [&](const auto & global_plan_pose) {
    geometry_msgs::msg::PoseStamped stamped_pose, transformed_pose;
    stamped_pose.header.frame_id = global_plan_.header.frame_id;
    stamped_pose.header.stamp = robot_pose.header.stamp;
    stamped_pose.pose = global_plan_pose.pose;
    if (!transformPose(costmap_ros_->getBaseFrameID(), stamped_pose, transformed_pose)) {
      throw nav2_core::PlannerException("Unable to transform a local tracking point");
    }
    transformed_pose.pose.position.z = 0.0;
    return transformed_pose;
  };

  // Transform the near part of the global plan into the robot's frame of reference.
  nav_msgs::msg::Path transformed_plan;
  std::transform(
    transformation_begin, transformation_end, std::back_inserter(transformed_plan.poses),
    transform_global_pose_to_local);
  transformed_plan.header.frame_id = costmap_ros_->getBaseFrameID();
  transformed_plan.header.stamp = robot_pose.header.stamp;

  // Remove the portion of the global plan that we've already passed so we don't
  // process it on the next iteration (this is called path pruning)
  global_plan_.poses.erase(begin(global_plan_.poses), transformation_begin);
  local_path_pub_->publish(transformed_plan);

  if (transformed_plan.poses.empty()) {
    throw nav2_core::PlannerException("Resulting plan has 0 poses in it.");
  }

  return transformed_plan;
}

std::unique_ptr<geometry_msgs::msg::PointStamped> OmniPidPursuitController::createCarrotMsg(
  const geometry_msgs::msg::PoseStamped & carrot_pose)
{
  auto carrot_msg = std::make_unique<geometry_msgs::msg::PointStamped>();
  carrot_msg->header = carrot_pose.header;
  carrot_msg->point.x = carrot_pose.pose.position.x;
  carrot_msg->point.y = carrot_pose.pose.position.y;
  carrot_msg->point.z = 0.01;  // publish right over map to stand out
  return carrot_msg;
}

geometry_msgs::msg::PoseStamped OmniPidPursuitController::getLookAheadPoint(
  const double & lookahead_dist, const nav_msgs::msg::Path & transformed_plan)
{
  // Find the first pose which is at a distance greater than the lookahead distance
  auto goal_pose_it = std::find_if(
    transformed_plan.poses.begin(), transformed_plan.poses.end(), [&](const auto & ps) {
      return hypot(ps.pose.position.x, ps.pose.position.y) >= lookahead_dist;
    });

  // If the no pose is not far enough, take the last pose
  if (goal_pose_it == transformed_plan.poses.end()) {
    goal_pose_it = std::prev(transformed_plan.poses.end());
  } else if (use_interpolation_ && goal_pose_it != transformed_plan.poses.begin()) {
    // Find the point on the line segment between the two poses
    // that is exactly the lookahead distance away from the robot pose (the origin)
    // This can be found with a closed form for the intersection of a segment and a circle
    // Because of the way we did the std::find_if, prev_pose is guaranteed to be inside the circle,
    // and goal_pose is guaranteed to be outside the circle.
    auto prev_pose_it = std::prev(goal_pose_it);
    auto point = circleSegmentIntersection(
      prev_pose_it->pose.position, goal_pose_it->pose.position, lookahead_dist);
    geometry_msgs::msg::PoseStamped pose;
    pose.header.frame_id = prev_pose_it->header.frame_id;
    pose.header.stamp = goal_pose_it->header.stamp;
    pose.pose.position = point;
    pose.pose.orientation = goal_pose_it->pose.orientation;
    return pose;
  }

  return *goal_pose_it;
}

geometry_msgs::msg::Point OmniPidPursuitController::circleSegmentIntersection(
  const geometry_msgs::msg::Point & p1, const geometry_msgs::msg::Point & p2, double r)
{
  // Formula for intersection of a line with a circle centered at the origin,
  // modified to always return the point that is on the segment between the two points.
  // https://mathworld.wolfram.com/Circle-LineIntersection.html
  // This works because the poses are transformed into the robot frame.
  // This can be derived from solving the system of equations of a line and a circle
  // which results in something that is just a reformulation of the quadratic formula.
  // Interactive illustration in doc/circle-segment-intersection.ipynb as well as at
  // https://www.desmos.com/calculator/td5cwbuocd
  double x1 = p1.x;
  double x2 = p2.x;
  double y1 = p1.y;
  double y2 = p2.y;

  double dx = x2 - x1;
  double dy = y2 - y1;
  double dr2 = dx * dx + dy * dy;
  double d = x1 * y2 - x2 * y1;

  // Augmentation to only return point within segment
  double d1 = x1 * x1 + y1 * y1;
  double d2 = x2 * x2 + y2 * y2;
  double dd = d2 - d1;

  geometry_msgs::msg::Point p;
  double sqrt_term = std::sqrt(r * r * dr2 - d * d);
  p.x = (d * dy + std::copysign(1.0, dd) * dx * sqrt_term) / dr2;
  p.y = (-d * dx + std::copysign(1.0, dd) * dy * sqrt_term) / dr2;
  return p;
}

double OmniPidPursuitController::getCostmapMaxExtent() const
{
  const double max_costmap_dim_meters =
    std::max(costmap_->getSizeInMetersX(), costmap_->getSizeInMetersY());
  return max_costmap_dim_meters / 2.0;
}
bool OmniPidPursuitController::transformPose(
  const std::string frame, const geometry_msgs::msg::PoseStamped & in_pose,
  geometry_msgs::msg::PoseStamped & out_pose) const
{
  if (in_pose.header.frame_id == frame) {
    out_pose = in_pose;
    return true;
  }

  try {
    tf_->transform(in_pose, out_pose, frame, transform_tolerance_);
    return true;
  } catch (tf2::TransformException & ex) {
    RCLCPP_ERROR(logger_, "Exception in transformPose: %s", ex.what());
  }
  return false;
}

double OmniPidPursuitController::getLookAheadDistance(const geometry_msgs::msg::Twist & speed)
{
  // If using velocity-scaled look ahead distances, find and clamp the dist
  // Else, use the static look ahead distance
  double lookahead_dist = lookahead_dist_;

  if (use_velocity_scaled_lookahead_dist_) {
    lookahead_dist = hypot(speed.linear.x, speed.linear.y) * lookahead_time_;
    lookahead_dist = std::clamp(lookahead_dist, min_lookahead_dist_, max_lookahead_dist_);
  }

  return lookahead_dist;
}

double OmniPidPursuitController::approachVelocityScalingFactor(
  const nav_msgs::msg::Path & transformed_path) const
{
  // Waiting to apply the threshold based on integrated distance ensures we don't
  // erroneously apply approach scaling on curvy paths that are contained in a large local costmap.
  double remaining_distance = nav2_util::geometry_utils::calculate_path_length(transformed_path);
  if (remaining_distance < approach_velocity_scaling_dist_) {
    auto & last = transformed_path.poses.back();
    // Here we will use a regular euclidean distance from the robot frame (origin)
    // to get smooth scaling, regardless of path density.
    double distance_to_last_pose = std::hypot(last.pose.position.x, last.pose.position.y);
    return distance_to_last_pose / approach_velocity_scaling_dist_;
  } else {
    return 1.0;
  }
}

void OmniPidPursuitController::applyApproachVelocityScaling(
  const nav_msgs::msg::Path & path, double & linear_vel) const
{
  // Bound the requested speed by a distance-based cap. Multiplying the PID
  // output by the same distance again produces quadratic creep near the goal.
  const double factor = std::clamp(approachVelocityScalingFactor(path), 0.0, 1.0);
  if (factor >= 1.0) {return;}
  const double cap = std::max(min_approach_linear_velocity_, v_linear_max_ * factor);
  linear_vel = std::min(linear_vel, cap);
}

void OmniPidPursuitController::applyCurvatureLimitation(
  const nav_msgs::msg::Path & path, const geometry_msgs::msg::PoseStamped & lookahead_pose,
  double & linear_vel)
{
  double curvature =
    calculateCurvature(path, lookahead_pose, curvature_forward_dist_, curvature_backward_dist_);

  double target_factor = 1.0;
  if (curvature >= curvature_max_) {
    target_factor = reduction_ratio_at_high_curvature_;
  } else if (curvature > curvature_min_) {
    const double blend = (curvature - curvature_min_) / (curvature_max_ - curvature_min_);
    target_factor -= blend * (1.0 - reduction_ratio_at_high_curvature_);
  }
  // Limit the dimensionless scaling factor on both entry and exit. Limiting
  // absolute speed here re-starts the ramp after each short tracking point.
  const double change = max_velocity_scaling_factor_rate_ * control_duration_;
  last_velocity_scaling_factor_ +=
    std::clamp(target_factor - last_velocity_scaling_factor_, -change, change);
  linear_vel *= last_velocity_scaling_factor_;
}

double OmniPidPursuitController::calculateCurvature(
  const nav_msgs::msg::Path & path, const geometry_msgs::msg::PoseStamped & lookahead_pose,
  double forward_dist, double backward_dist) const
{
  geometry_msgs::msg::PoseStamped backward_pose, forward_pose;
  std::vector<double> cumulative_distances = calculateCumulativeDistances(path);

  // Interpolated carrots live on the path. The straight-line distance to the
  // robot picks the wrong curvature samples whenever the path bends.
  const auto & carrot = lookahead_pose.pose.position;
  double lookahead_pose_cumulative_distance = 0.0;
  double best_error = std::numeric_limits<double>::infinity();
  for (size_t i = 1; i < path.poses.size(); ++i) {
    const auto & a = path.poses[i - 1].pose.position;
    const auto & b = path.poses[i].pose.position;
    const double dx = b.x - a.x, dy = b.y - a.y;
    const double length = cumulative_distances[i] - cumulative_distances[i - 1];
    if (length <= 1e-9) {continue;}
    const double fraction = std::clamp(
      ((carrot.x - a.x) * dx + (carrot.y - a.y) * dy) / (length * length), 0.0, 1.0);
    const double error = std::hypot(carrot.x - a.x - fraction * dx, carrot.y - a.y - fraction * dy);
    if (error < best_error) {
      best_error = error;
      lookahead_pose_cumulative_distance = cumulative_distances[i - 1] + fraction * length;
      if (error < 1e-8) {break;}
    }
  }

  backward_pose = findPoseAtDistance(
    path, cumulative_distances, lookahead_pose_cumulative_distance - backward_dist);

  forward_pose = findPoseAtDistance(
    path, cumulative_distances, lookahead_pose_cumulative_distance + forward_dist);

  double curvature_radius = calculateCurvatureRadius(
    backward_pose.pose.position, lookahead_pose.pose.position, forward_pose.pose.position);
  double curvature = 1.0 / curvature_radius;
  visualizeCurvaturePoints(backward_pose, forward_pose);
  return curvature;
}

double OmniPidPursuitController::calculateCurvatureRadius(
  const geometry_msgs::msg::Point & near_point, const geometry_msgs::msg::Point & current_point,
  const geometry_msgs::msg::Point & far_point) const
{
  double x1 = near_point.x, y1 = near_point.y;
  double x2 = current_point.x, y2 = current_point.y;
  double x3 = far_point.x, y3 = far_point.y;

  const double twice_area = std::abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1));
  if (twice_area < 1e-9) {return 1e9;}
  return std::hypot(x2 - x1, y2 - y1) * std::hypot(x3 - x2, y3 - y2) *
         std::hypot(x3 - x1, y3 - y1) / (2.0 * twice_area);
}

void OmniPidPursuitController::visualizeCurvaturePoints(
  const geometry_msgs::msg::PoseStamped & backward_pose,
  const geometry_msgs::msg::PoseStamped & forward_pose) const
{
  visualization_msgs::msg::MarkerArray marker_array;

  visualization_msgs::msg::Marker near_marker;
  near_marker.header = backward_pose.header;
  near_marker.ns = "curvature_points";
  near_marker.id = 0;
  near_marker.type = visualization_msgs::msg::Marker::SPHERE;
  near_marker.action = visualization_msgs::msg::Marker::ADD;
  near_marker.pose = backward_pose.pose;
  near_marker.scale.x = near_marker.scale.y = near_marker.scale.z = 0.1;
  near_marker.color.g = 1.0;
  near_marker.color.a = 1.0;

  visualization_msgs::msg::Marker far_marker;
  far_marker.header = forward_pose.header;
  far_marker.ns = "curvature_points";
  far_marker.id = 1;
  far_marker.type = visualization_msgs::msg::Marker::SPHERE;
  far_marker.action = visualization_msgs::msg::Marker::ADD;
  far_marker.pose = forward_pose.pose;
  far_marker.scale.x = far_marker.scale.y = far_marker.scale.z = 0.1;
  far_marker.color.r = 1.0;
  far_marker.color.a = 1.0;

  marker_array.markers.push_back(near_marker);
  marker_array.markers.push_back(far_marker);

  curvature_points_pub_->publish(marker_array);
}

std::vector<double> OmniPidPursuitController::calculateCumulativeDistances(
  const nav_msgs::msg::Path & path) const
{
  std::vector<double> cumulative_distances;
  cumulative_distances.push_back(0.0);

  for (size_t i = 1; i < path.poses.size(); ++i) {
    const auto & prev_pose = path.poses[i - 1].pose.position;
    const auto & curr_pose = path.poses[i].pose.position;
    double distance = hypot(curr_pose.x - prev_pose.x, curr_pose.y - prev_pose.y);
    cumulative_distances.push_back(cumulative_distances.back() + distance);
  }
  return cumulative_distances;
}

geometry_msgs::msg::PoseStamped OmniPidPursuitController::findPoseAtDistance(
  const nav_msgs::msg::Path & path, const std::vector<double> & cumulative_distances,
  double target_distance) const
{
  if (path.poses.empty() || cumulative_distances.empty()) {
    return geometry_msgs::msg::PoseStamped();
  }
  if (target_distance <= 0.0) {
    return path.poses.front();
  }
  if (target_distance >= cumulative_distances.back()) {
    return path.poses.back();
  }
  auto it =
    std::lower_bound(cumulative_distances.begin(), cumulative_distances.end(), target_distance);
  size_t index = std::distance(cumulative_distances.begin(), it);

  if (index == 0) {
    return path.poses.front();
  }

  double ratio = (target_distance - cumulative_distances[index - 1]) /
                 (cumulative_distances[index] - cumulative_distances[index - 1]);
  geometry_msgs::msg::PoseStamped pose1 = path.poses[index - 1];
  geometry_msgs::msg::PoseStamped pose2 = path.poses[index];

  geometry_msgs::msg::PoseStamped interpolated_pose;
  interpolated_pose.header = pose2.header;
  interpolated_pose.pose.position.x =
    pose1.pose.position.x + ratio * (pose2.pose.position.x - pose1.pose.position.x);
  interpolated_pose.pose.position.y =
    pose1.pose.position.y + ratio * (pose2.pose.position.y - pose1.pose.position.y);
  interpolated_pose.pose.position.z =
    pose1.pose.position.z + ratio * (pose2.pose.position.z - pose1.pose.position.z);
  interpolated_pose.pose.orientation = pose2.pose.orientation;

  return interpolated_pose;
}

rcl_interfaces::msg::SetParametersResult OmniPidPursuitController::dynamicParametersCallback(
  std::vector<rclcpp::Parameter> parameters)
{
  rcl_interfaces::msg::SetParametersResult result;
  std::lock_guard<std::mutex> lock_reinit(mutex_);

  for (const auto & parameter : parameters) {
    const auto & type = parameter.get_type();
    const auto & name = parameter.get_name();

    if (type == ParameterType::PARAMETER_DOUBLE) {
      if (name == plugin_name_ + ".translation_kp") {
        translation_kp_ = parameter.as_double();
      } else if (name == plugin_name_ + ".translation_ki") {
        translation_ki_ = parameter.as_double();
      } else if (name == plugin_name_ + ".translation_kd") {
        translation_kd_ = parameter.as_double();
      } else if (name == plugin_name_ + ".rotation_kp") {
        rotation_kp_ = parameter.as_double();
      } else if (name == plugin_name_ + ".rotation_ki") {
        rotation_ki_ = parameter.as_double();
      } else if (name == plugin_name_ + ".rotation_kd") {
        rotation_kd_ = parameter.as_double();
      } else if (name == plugin_name_ + ".transform_tolerance") {
        double transform_tolerance = parameter.as_double();
        transform_tolerance_ = tf2::durationFromSec(transform_tolerance);
      } else if (name == plugin_name_ + ".min_max_sum_error") {
        min_max_sum_error_ = parameter.as_double();
      } else if (name == plugin_name_ + ".lookahead_dist") {
        lookahead_dist_ = parameter.as_double();
      } else if (name == plugin_name_ + ".min_lookahead_dist") {
        min_lookahead_dist_ = parameter.as_double();
      } else if (name == plugin_name_ + ".max_lookahead_dist") {
        max_lookahead_dist_ = parameter.as_double();
      } else if (name == plugin_name_ + ".lookahead_time") {
        lookahead_time_ = parameter.as_double();
      } else if (name == plugin_name_ + ".use_rotate_to_heading_treshold") {
        use_rotate_to_heading_treshold_ = parameter.as_double();
      } else if (name == plugin_name_ + ".min_approach_linear_velocity") {
        min_approach_linear_velocity_ = parameter.as_double();
      } else if (name == plugin_name_ + ".approach_velocity_scaling_dist") {
        approach_velocity_scaling_dist_ = parameter.as_double();
      } else if (name == plugin_name_ + ".v_linear_max") {
        v_linear_max_ = parameter.as_double();
      } else if (name == plugin_name_ + ".v_linear_min") {
        v_linear_min_ = parameter.as_double();
      } else if (name == plugin_name_ + ".v_angular_max") {
        v_angular_max_ = parameter.as_double();
      } else if (name == plugin_name_ + ".v_angular_min") {
        v_angular_min_ = parameter.as_double();
      } else if (name == plugin_name_ + ".curvature_min") {
        curvature_min_ = parameter.as_double();
      } else if (name == plugin_name_ + ".curvature_max") {
        curvature_max_ = parameter.as_double();
      } else if (name == plugin_name_ + ".reduction_ratio_at_high_curvature") {
        reduction_ratio_at_high_curvature_ = parameter.as_double();
      } else if (name == plugin_name_ + ".curvature_forward_dist") {
        curvature_forward_dist_ = parameter.as_double();
      } else if (name == plugin_name_ + ".curvature_backward_dist") {
        curvature_backward_dist_ = parameter.as_double();
      } else if (name == plugin_name_ + ".max_velocity_scaling_factor_rate") {
        max_velocity_scaling_factor_rate_ = parameter.as_double();
      }
    } else if (type == ParameterType::PARAMETER_BOOL) {
      if (name == plugin_name_ + ".use_velocity_scaled_lookahead_dist") {
        use_velocity_scaled_lookahead_dist_ = parameter.as_bool();
      } else if (name == plugin_name_ + ".use_interpolation") {
        use_interpolation_ = parameter.as_bool();
      } else if (name == plugin_name_ + ".use_rotate_to_heading") {
        use_rotate_to_heading_ = parameter.as_bool();
      }
    }
  }
  result.successful = true;
  return result;
}

};  // namespace pb_omni_pid_pursuit_controller
// Register this controller as a nav2_core plugin
#include "pluginlib/class_list_macros.hpp"
PLUGINLIB_EXPORT_CLASS(
  pb_omni_pid_pursuit_controller::OmniPidPursuitController, nav2_core::Controller)
