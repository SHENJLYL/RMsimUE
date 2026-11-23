#pragma once

#include "terrain_analysis/surveyed_ground.hpp"
#include "rclcpp/rclcpp.hpp"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"
#include "tf2_ros/transform_listener.h"
#include "tf2_ros/buffer.h"

namespace terrain_analysis
{
class SurveyedGroundFilter
{
public:
  explicit SurveyedGroundFilter(const rclcpp::Node::SharedPtr & node)
  : node_(node)
  {
    const auto file=node->declare_parameter<std::string>("surveyed_ground_file","");
    frame_=node->declare_parameter<std::string>("surveyed_ground_frame","");
    const auto align_xy=node->declare_parameter<bool>("surveyed_ground_align_xy",false);
    observed_plane_=node->declare_parameter<bool>("surveyed_ground_observed_plane",false);
    if (file.empty()) {return;}
    if (frame_.empty()) {throw std::invalid_argument("Missing surveyed ground frame");}
    ground_.emplace(file,align_xy);
    buffer_=std::make_shared<tf2_ros::Buffer>(node->get_clock());
    listener_=std::make_shared<tf2_ros::TransformListener>(*buffer_);
    RCLCPP_INFO(node_->get_logger(),"Loaded surveyed support in %s",frame_.c_str());
  }
  template<class Points,class Scan=Points>
  void apply(Points & points,const std::string & odom,const builtin_interfaces::msg::Time & stamp,
    double x,double y,double grade,const Scan * same_stamp_scan=nullptr)
  {
    if (!ground_) {return;}
    tf2::Transform transform;
    try {
      tf2::fromMsg(buffer_->lookupTransform(frame_,odom,rclcpp::Time(stamp),rclcpp::Duration(0,0)).transform,transform);
    } catch (const tf2::TransformException &) {return;}
    const auto count=ground_->refine(points,x,y,grade,[&](const auto & p){
      const auto q=transform*tf2::Vector3(p.x,p.y,p.z);
      return std::array<double,3>{q.x(),q.y(),q.z()};
    },observed_plane_ ? same_stamp_scan : nullptr);
    if (count) {RCLCPP_INFO_THROTTLE(node_->get_logger(),*node_->get_clock(),1000,
      "Reclassified %zu obstacle-height returns on surveyed ground",count);}
  }
private:
  rclcpp::Node::SharedPtr node_;
  std::optional<SurveyedGround> ground_;
  bool observed_plane_{};
  std::string frame_;
  std::shared_ptr<tf2_ros::Buffer> buffer_;
  std::shared_ptr<tf2_ros::TransformListener> listener_;
};
}  // namespace terrain_analysis
