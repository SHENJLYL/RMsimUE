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

#include "sensor_scan_generation/sensor_scan_generation.hpp"

#include <chrono>
#include <cmath>
#include <stdexcept>
#include "pcl_ros/transforms.hpp"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"

namespace sensor_scan_generation
{

SensorScanGenerationNode::SensorScanGenerationNode(const rclcpp::NodeOptions & options)
: Node("sensor_scan_generation", options)
{
  this->declare_parameter<std::string>("lidar_frame", "");
  this->declare_parameter<std::string>("base_frame", "");
  this->declare_parameter<std::string>("robot_base_frame", "");
  direct_control_odom_ = this->declare_parameter<bool>("direct_control_odom", false);
  velocity_window_s_ = this->declare_parameter<double>("odometry_velocity_window_s", 0.0);
  if (!std::isfinite(velocity_window_s_) || velocity_window_s_ < 0.0 || velocity_window_s_ > 0.25) {
    throw std::invalid_argument("odometry_velocity_window_s must be in [0, 0.25]");
  }

  this->get_parameter("lidar_frame", lidar_frame_);
  this->get_parameter("base_frame", base_frame_);
  this->get_parameter("robot_base_frame", robot_base_frame_);
  if (direct_control_odom_ && base_frame_ != robot_base_frame_) {
    throw std::invalid_argument("Direct odometry requires the controller base frame");
  }

  tf_buffer_ = std::make_unique<tf2_ros::Buffer>(this->get_clock());
  tf_listener_ = std::make_unique<tf2_ros::TransformListener>(*tf_buffer_);
  br_ = std::make_unique<tf2_ros::TransformBroadcaster>(*this);

  pub_laser_cloud_ = this->create_publisher<sensor_msgs::msg::PointCloud2>("sensor_scan", 2);
  pub_chassis_odometry_ = this->create_publisher<nav_msgs::msg::Odometry>("odometry", 2);

  rmw_qos_profile_t qos_profile = {
    RMW_QOS_POLICY_HISTORY_KEEP_LAST,
    1,
    RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT,
    RMW_QOS_POLICY_DURABILITY_VOLATILE,
    RMW_QOS_DEADLINE_DEFAULT,
    RMW_QOS_LIFESPAN_DEFAULT,
    RMW_QOS_POLICY_LIVELINESS_SYSTEM_DEFAULT,
    RMW_QOS_LIVELINESS_LEASE_DURATION_DEFAULT,
    false};

  odometry_sub_.subscribe(this, "lidar_odometry", qos_profile);
  laser_cloud_sub_.subscribe(this, "registered_scan", qos_profile);

  sync_ = std::make_unique<message_filters::Synchronizer<SyncPolicy>>(
    SyncPolicy(100), odometry_sub_, laser_cloud_sub_);
  sync_->registerCallback(std::bind(
    &SensorScanGenerationNode::laserCloudAndOdometryHandler, this, std::placeholders::_1,
    std::placeholders::_2));
  odometry_sub_.registerCallback([this](const nav_msgs::msg::Odometry::ConstSharedPtr & m) {
    ++received_odometry_;
    latest_odom_stamp_ = rclcpp::Time(m->header.stamp).seconds();
    if (direct_control_odom_) {publishDirectOdometry(*m);}
  });
  laser_cloud_sub_.registerCallback([this](const sensor_msgs::msg::PointCloud2::ConstSharedPtr & m) {
    ++received_clouds_; latest_cloud_stamp_ = rclcpp::Time(m->header.stamp).seconds();
  });
  diagnostic_timer_ = this->create_wall_timer(std::chrono::seconds(3), [this]() {
    RCLCPP_INFO(get_logger(),
      "SCAN_HEALTH odom=%llu cloud=%llu paired=%llu transformed=%llu now=%.9f odom_stamp=%.9f cloud_stamp=%.9f",
      static_cast<unsigned long long>(received_odometry_), static_cast<unsigned long long>(received_clouds_),
      static_cast<unsigned long long>(paired_), static_cast<unsigned long long>(transformed_),
      now().seconds(), latest_odom_stamp_, latest_cloud_stamp_);
  });
}

void SensorScanGenerationNode::laserCloudAndOdometryHandler(
  const nav_msgs::msg::Odometry::ConstSharedPtr & odometry_msg,
  const sensor_msgs::msg::PointCloud2::ConstSharedPtr & pcd_msg)
{
  tf2::Transform tf_lidar_to_chassis;
  tf2::Transform tf_odom_to_chassis;
  tf2::Transform tf_odom_to_robot_base;
  tf2::Transform tf_odom_to_lidar;

  tf2::fromMsg(odometry_msg->pose.pose, tf_odom_to_lidar);
  ++paired_;
  try {
    tf_lidar_to_robot_base_ = getTransform(lidar_frame_, robot_base_frame_, pcd_msg->header.stamp);
    tf_lidar_to_chassis = getTransform(lidar_frame_, base_frame_, pcd_msg->header.stamp);
  } catch (const tf2::TransformException & ex) {
    RCLCPP_WARN_THROTTLE(
      this->get_logger(), *this->get_clock(), 2000,
      "TF lookup failed: %s. Skipping scan and odometry.", ex.what());
    return;
  }

  ++transformed_;
  tf_odom_to_chassis = tf_odom_to_lidar * tf_lidar_to_chassis;
  tf_odom_to_robot_base = tf_odom_to_lidar * tf_lidar_to_robot_base_;

  if (!direct_control_odom_) {
    publishTransform(
      tf_odom_to_chassis, odometry_msg->header.frame_id, base_frame_, pcd_msg->header.stamp);
    publishOdometry(
      tf_odom_to_robot_base, odometry_msg->header.frame_id, robot_base_frame_, pcd_msg->header.stamp);
  }

  sensor_msgs::msg::PointCloud2 out;
  pcl_ros::transformPointCloud(lidar_frame_, tf_odom_to_lidar.inverse(), *pcd_msg, out);
  pub_laser_cloud_->publish(out);
}

void SensorScanGenerationNode::publishDirectOdometry(const nav_msgs::msg::Odometry & input)
{
  const rclcpp::Time stamp(input.header.stamp, get_clock()->get_clock_type());
  if (latest_mount_ && stamp <= latest_mount_->stamp) {return;}
  const double dt = latest_mount_ ? (stamp - latest_mount_->stamp).seconds() : 0.;
  if (latest_mount_ && dt < 0.02) {return;}
  tf2::Transform mount;
  try {
    mount = getTransform(lidar_frame_, robot_base_frame_, stamp);
  } catch (const tf2::TransformException &) {
    return;  // The next stamped estimator update retries.
  }
  const auto previous = latest_mount_;
  latest_mount_ = PoseSample{stamp, mount};
  if (!previous || dt > 0.16) {return;}

  const tf2::Vector3 v(input.twist.twist.linear.x, input.twist.twist.linear.y, input.twist.twist.linear.z);
  const tf2::Vector3 w(input.twist.twist.angular.x, input.twist.twist.angular.y, input.twist.twist.angular.z);
  const auto finite = [](const tf2::Vector3 & a) {
      return std::isfinite(a.x()) && std::isfinite(a.y()) && std::isfinite(a.z());
    };
  if (!finite(v) || !finite(w)) {return;}
  const auto relative_velocity = (mount.getOrigin() - previous->transform.getOrigin()) / dt;
  auto relative_rotation = mount.getRotation() * previous->transform.getRotation().inverse();
  relative_rotation.normalize();
  if (relative_rotation.w() < 0.0) {
    relative_rotation = tf2::Quaternion(-relative_rotation.x(), -relative_rotation.y(),
      -relative_rotation.z(), -relative_rotation.w());
  }
  const auto relative_rate = relative_rotation.getAxis() * (relative_rotation.getAngle() / dt);
  const auto inverse = mount.getRotation().inverse();
  // Account for both lever-arm velocity and articulation of the sensor mount.
  const auto base_v = tf2::quatRotate(inverse, v + w.cross(mount.getOrigin()) + relative_velocity);
  const auto base_w = tf2::quatRotate(inverse, w + relative_rate);
  if (!finite(base_v) || !finite(base_w)) {return;}

  tf2::Transform lidar;
  tf2::fromMsg(input.pose.pose, lidar);
  const auto body = lidar * mount;
  nav_msgs::msg::Odometry out;
  out.header = input.header;
  out.child_frame_id = robot_base_frame_;
  tf2::toMsg(body, out.pose.pose);
  out.twist.twist.linear = tf2::toMsg(base_v);
  out.twist.twist.angular = tf2::toMsg(base_w);
  publishTransform(body, input.header.frame_id, robot_base_frame_, stamp);
  pub_chassis_odometry_->publish(out);
}

tf2::Transform SensorScanGenerationNode::getTransform(
  const std::string & target_frame, const std::string & source_frame, const rclcpp::Time & time)
{
  // Never wait on a simulated-clock timeout inside a subscription callback.
  // Missing mount TF skips this observation; the next real sample retries at its own stamp.
  auto transform_stamped = tf_buffer_->lookupTransform(
    target_frame, source_frame, time, rclcpp::Duration::from_seconds(0.0));
  tf2::Transform transform;
  tf2::fromMsg(transform_stamped.transform, transform);
  return transform;
}

void SensorScanGenerationNode::publishTransform(
  const tf2::Transform & transform, const std::string & parent_frame,
  const std::string & child_frame, const rclcpp::Time & stamp)
{
  geometry_msgs::msg::TransformStamped transform_msg;
  transform_msg.header.stamp = stamp;
  transform_msg.header.frame_id = parent_frame;
  transform_msg.child_frame_id = child_frame;
  transform_msg.transform = tf2::toMsg(transform);
  br_->sendTransform(transform_msg);
}

void SensorScanGenerationNode::publishOdometry(
  const tf2::Transform & transform, std::string parent_frame, const std::string & child_frame,
  const rclcpp::Time & stamp)
{
  nav_msgs::msg::Odometry out;
  out.header.stamp = stamp;
  out.header.frame_id = parent_frame;
  out.child_frame_id = child_frame;

  const auto & origin = transform.getOrigin();
  out.pose.pose.position.x = origin.x();
  out.pose.pose.position.y = origin.y();
  out.pose.pose.position.z = origin.z();
  out.pose.pose.orientation = tf2::toMsg(transform.getRotation());

  if (!velocity_samples_.empty() && stamp <= velocity_samples_.back().stamp) {
    velocity_samples_.clear();
  }
  // Pose and stamp are unchanged. Estimate twist over a short interval to
  // prevent single LIO scan corrections from driving the stopping envelope.
  while (velocity_samples_.size() > 1 &&
    (stamp - velocity_samples_[1].stamp).seconds() >= velocity_window_s_)
  {
    velocity_samples_.pop_front();
  }
  if (!velocity_samples_.empty()) {
    const auto & previous = velocity_samples_.front();
    const auto & previous_transform = previous.transform;
    const double dt = (stamp - previous.stamp).seconds();
    if (dt > 0.0) {
      const auto inverse_rotation = transform.getRotation().inverse();
      const auto linear_velocity = tf2::quatRotate(
        inverse_rotation, (transform.getOrigin() - previous_transform.getOrigin()) / dt);
      auto delta = transform.getRotation() * previous_transform.getRotation().inverse();
      delta.normalize();
      if (delta.w() < 0.0) {
        delta = tf2::Quaternion(-delta.x(), -delta.y(), -delta.z(), -delta.w());
      }
      const auto angular_velocity =
        tf2::quatRotate(inverse_rotation, delta.getAxis() * (delta.getAngle() / dt));
      out.twist.twist.linear.x = linear_velocity.x();
      out.twist.twist.linear.y = linear_velocity.y();
      out.twist.twist.linear.z = linear_velocity.z();
      out.twist.twist.angular.x = angular_velocity.x();
      out.twist.twist.angular.y = angular_velocity.y();
      out.twist.twist.angular.z = angular_velocity.z();
    }
  }
  velocity_samples_.push_back({stamp, transform});

  pub_chassis_odometry_->publish(out);
}

}  // namespace sensor_scan_generation

#include "rclcpp_components/register_node_macro.hpp"

RCLCPP_COMPONENTS_REGISTER_NODE(sensor_scan_generation::SensorScanGenerationNode)
