#include "sp_vision_25/detector.hpp"
#include "sp_vision_25/solver_tracker.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <filesystem>
#include <iomanip>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

#include <ament_index_cpp/get_package_share_directory.hpp>
#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <nlohmann/json.hpp>
#include <opencv2/imgproc.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <std_msgs/msg/int32.hpp>
#include <std_msgs/msg/string.hpp>

#ifdef SP_VISION_HAS_SP_MSGS
#include <sp_msgs/msg/auto_aim_command_msg.hpp>
#endif

#ifdef SP_VISION_HAS_DJI_REFEREE
#include <dji_referee_protocol/msg/game_status.hpp>
#include <dji_referee_protocol/msg/robot_hp.hpp>
#include <dji_referee_protocol/msg/robot_performance.hpp>
#include <dji_referee_protocol/msg/self_color.hpp>
#include <dji_referee_protocol/msg/shoot_data.hpp>
#endif

namespace sp_vision_25 {
namespace {

constexpr int64_t kMatchFireObservationNs = 150'000'000LL;
constexpr int64_t kBehaviorCommandNs = 300'000'000LL;
constexpr int64_t kTrackHoldNs = 1'100'000'000LL;
constexpr int64_t kMaxFeedbackGapNs = 60'000'000LL;
constexpr int64_t kSimHandoffHoldNs = 220'000'000LL;
constexpr int64_t kSimHandoffGapNs = 250'000'000LL;

const std::map<std::string, int> kArmorLabelIds = {
  {"one", 0}, {"two", 1}, {"three", 2}, {"four", 3},
  {"five", 4}, {"sentry", 5}, {"outpost", 6}, {"base", 7},
};

const std::map<std::string, int> kTargetPriority = {
  {"sentry", 5}, {"one", 4}, {"three", 3}, {"four", 3},
  {"five", 3}, {"two", 2}, {"outpost", 2}, {"base", 1},
};

const std::set<std::string> kVehicleNames = {
  "one", "two", "three", "four", "five", "sentry"
};

std::string to_lower_trim(std::string s)
{
  auto not_space = [](unsigned char c) { return !std::isspace(c); };
  s.erase(s.begin(), std::find_if(s.begin(), s.end(), not_space));
  s.erase(std::find_if(s.rbegin(), s.rend(), not_space).base(), s.end());
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) {
    return static_cast<char>(std::tolower(c));
  });
  return s;
}

std::string trim_str(std::string s)
{
  auto not_space = [](unsigned char c) { return !std::isspace(c); };
  s.erase(s.begin(), std::find_if(s.begin(), s.end(), not_space));
  s.erase(std::find_if(s.rbegin(), s.rend(), not_space).base(), s.end());
  return s;
}

double round_dec(double v, int digits)
{
  const double p = std::pow(10.0, digits);
  return std::round(v * p) / p;
}

int64_t stamp_to_ns(const builtin_interfaces::msg::Time & t)
{
  return static_cast<int64_t>(t.sec) * 1'000'000'000LL + static_cast<int64_t>(t.nanosec);
}

std::pair<double, double> quat_to_yaw_pitch(double qx, double qy, double qz, double qw)
{
  const double siny_cosp = 2.0 * (qw * qz + qx * qy);
  const double cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz);
  const double yaw = std::atan2(siny_cosp, cosy_cosp);
  const double sinp = std::clamp(2.0 * (qw * qy - qz * qx), -1.0, 1.0);
  const double pitch_down = std::asin(sinp);
  return {yaw, pitch_down};
}

std::filesystem::path default_assets_dir()
{
  try {
    const auto share_assets =
      std::filesystem::path(ament_index_cpp::get_package_share_directory("sp_vision_25")) / "assets";
    if (std::filesystem::is_regular_file(share_assets / "yolov8.xml")) {
      return share_assets;
    }
  } catch (const std::exception &) {
  }
  const auto cwd_assets = std::filesystem::current_path() / "ros2_ws" / "src" / "sp_vision_25" / "assets";
  if (std::filesystem::is_regular_file(cwd_assets / "yolov8.xml")) {
    return cwd_assets;
  }
  return std::filesystem::path("assets");
}

struct PlateObservation {
  std::string name;
  double yaw;
  double pitch;
  int64_t stamp_ns;
  int count{1};
};

double observation_separation(const PlateObservation & a, const PlateObservation & b)
{
  return std::hypot(wrap_angle(a.yaw - b.yaw), a.pitch - b.pitch);
}

struct AttitudeSample {
  int64_t stamp_ns{0};
  double yaw{0.0};
  double pitch{0.0};
};

std::optional<std::pair<double, double>> attitude_at(
  const std::deque<AttitudeSample> & feedback,
  int64_t stamp,
  int64_t max_gap_ns = kMaxFeedbackGapNs)
{
  std::optional<AttitudeSample> before;
  for (auto it = feedback.rbegin(); it != feedback.rend(); ++it) {
    if (it->stamp_ns <= stamp) {
      before = *it;
      break;
    }
  }
  std::optional<AttitudeSample> after;
  for (const auto & s : feedback) {
    if (s.stamp_ns >= stamp) {
      after = s;
      break;
    }
  }
  if (before && after &&
      (stamp - before->stamp_ns) <= max_gap_ns &&
      (after->stamp_ns - stamp) <= max_gap_ns)
  {
    if (before->stamp_ns == after->stamp_ns) {
      return std::make_pair(before->yaw, before->pitch);
    }
    const double t = static_cast<double>(stamp - before->stamp_ns) /
      static_cast<double>(after->stamp_ns - before->stamp_ns);
    return std::make_pair(
      before->yaw + t * wrap_angle(after->yaw - before->yaw),
      before->pitch + t * (after->pitch - before->pitch));
  }
  if (!feedback.empty() && std::llabs(stamp - feedback.back().stamp_ns) <= max_gap_ns) {
    return std::make_pair(feedback.back().yaw, feedback.back().pitch);
  }
  return std::nullopt;
}

cv::Mat decode_image_to_bgr(const sensor_msgs::msg::Image & msg)
{
  const std::string & enc = msg.encoding;
  int ch = 0;
  bool is_rgb = false;
  if (enc == "bgr8") {
    ch = 3;
    is_rgb = false;
  } else if (enc == "rgb8") {
    ch = 3;
    is_rgb = true;
  } else if (enc == "bgra8") {
    ch = 4;
    is_rgb = false;
  } else if (enc == "rgba8") {
    ch = 4;
    is_rgb = true;
  } else {
    return cv::Mat();
  }

  const size_t min_step = static_cast<size_t>(msg.width) * static_cast<size_t>(ch);
  if (msg.height == 0 || msg.width == 0 || msg.step < min_step ||
      msg.data.size() < static_cast<size_t>(msg.height) * static_cast<size_t>(msg.step))
  {
    return cv::Mat();
  }

  const int cv_type = (ch == 3) ? CV_8UC3 : CV_8UC4;
  cv::Mat raw(
    static_cast<int>(msg.height),
    static_cast<int>(msg.width),
    cv_type,
    const_cast<uint8_t *>(msg.data.data()),
    static_cast<size_t>(msg.step));

  cv::Mat bgr;
  if (ch == 3 && !is_rgb) {
    bgr = raw.clone();
  } else if (ch == 3 && is_rgb) {
    cv::cvtColor(raw, bgr, cv::COLOR_RGB2BGR);
  } else if (ch == 4 && is_rgb) {
    cv::cvtColor(raw, bgr, cv::COLOR_RGBA2BGR);
  } else {
    cv::cvtColor(raw, bgr, cv::COLOR_BGRA2BGR);
  }
  return bgr;
}

}  // namespace

class SpVisionSimNode : public rclcpp::Node {
public:
  SpVisionSimNode()
  : Node("sp_vision_25_sim")
  {
    robot_id_ = declare_parameter<std::string>("robot_id", "aim_sentry");
    std::string assets_param = trim_str(declare_parameter<std::string>("assets_dir", ""));
    const std::filesystem::path assets_dir =
      assets_param.empty() ? default_assets_dir() : std::filesystem::path(assets_param);

    const double muzzle_v = declare_parameter<double>("muzzle_speed_mps", 24.5);
    const double min_cls_conf = declare_parameter<double>("min_classify_conf", 0.60);
    require_behavior_switch_ = declare_parameter<bool>("require_behavior_switch", false);

    enemy_color_override_ = to_lower_trim(declare_parameter<std::string>("enemy_color", "auto"));
    referee_mode_ = to_lower_trim(declare_parameter<std::string>("referee_mode", "auto"));
    pitch_up_positive_ = declare_parameter<bool>("pitch_up_positive", false);
    debug_image_enabled_ = declare_parameter<bool>("debug_image", false);
    max_image_age_ns_ = static_cast<int64_t>(
      declare_parameter<double>("max_image_age_ms", 180.0) * 1'000'000.0);
    const int num_threads = static_cast<int>(declare_parameter<int64_t>("num_threads", 4));

    const double latency_s = declare_parameter<double>("latency_s", 0.08);
    const double shoot_tol_deg = declare_parameter<double>("shoot_tol_deg", 5.5);
    const double structure_shoot_tol_deg = declare_parameter<double>("structure_shoot_tol_deg", 8.5);
    const double yaw_offset_deg = declare_parameter<double>("yaw_offset_deg", 0.0);
    const double pitch_offset_deg = declare_parameter<double>("pitch_offset_deg", 0.0);
    const double cam_to_barrel_z_m = declare_parameter<double>("cam_to_barrel_z_m", 0.0);

    std::string camera_topic = trim_str(declare_parameter<std::string>("camera_topic", ""));
    if (camera_topic.empty()) camera_topic = "/" + robot_id_ + "/camera/image_raw";
    std::string camera_info_topic = trim_str(declare_parameter<std::string>("camera_info_topic", ""));
    if (camera_info_topic.empty()) camera_info_topic = "/" + robot_id_ + "/camera/camera_info";
    std::string joint_state_topic = trim_str(declare_parameter<std::string>("joint_state_topic", ""));
    if (joint_state_topic.empty()) joint_state_topic = "/" + robot_id_ + "/joint_states";
    const std::string imu_topic = trim_str(declare_parameter<std::string>("imu_topic", ""));
    std::string command_topic = trim_str(declare_parameter<std::string>("command_topic", ""));
    if (command_topic.empty()) command_topic = "/" + robot_id_ + "/autoaim_command";
    std::string amcl_topic = trim_str(declare_parameter<std::string>("amcl_topic", "/amcl_pose"));
    if (amcl_topic.empty()) amcl_topic = "/amcl_pose";

    detector_ = std::make_unique<SpVisionYoloV8Detector>(
      assets_dir, 0.42f, 0.45f, static_cast<float>(min_cls_conf), num_threads);
    tracker_ = WorldAngularTracker(static_cast<double>(kTrackHoldNs) * 1e-9);
    aimer_ = BallisticAimer(
      muzzle_v,
      9.81,
      latency_s,
      deg2rad(shoot_tol_deg),
      deg2rad(structure_shoot_tol_deg),
      deg2rad(yaw_offset_deg),
      deg2rad(pitch_offset_deg),
      cam_to_barrel_z_m);

    is_red_ = (enemy_color_override_ == "red") ? false : true;
    game_running_ = !require_behavior_switch_;
    behavior_permits_ = !require_behavior_switch_;

    K_ = cv::Matx33d(1108.5, 0.0, 640.0, 0.0, 1108.5, 360.0, 0.0, 0.0, 1.0);
    D_ = cv::Mat::zeros(5, 1, CV_64F);

    live_targets_ = kVehicleNames;
    live_targets_.insert("outpost");
    live_targets_.insert("base");

    auto reliable_qos = rclcpp::QoS(rclcpp::KeepLast(5)).reliable();
    auto img_qos = rclcpp::QoS(rclcpp::KeepLast(1)).best_effort();
    auto sensor_qos = rclcpp::SensorDataQoS();

    if (referee_mode_ == "auto" || referee_mode_ == "sim") {
      sim_ref_sub_ = create_subscription<std_msgs::msg::String>(
        "/rmsim/referee/state", reliable_qos,
        [this](std_msgs::msg::String::ConstSharedPtr msg) { on_referee(*msg); });
    }

#ifdef SP_VISION_HAS_DJI_REFEREE
    if (referee_mode_ == "auto" || referee_mode_ == "dji") {
      dji_game_sub_ = create_subscription<dji_referee_protocol::msg::GameStatus>(
        "/referee/common/game_status", reliable_qos,
        [this](dji_referee_protocol::msg::GameStatus::ConstSharedPtr msg) { on_dji_game(*msg); });
      dji_perf_sub_ = create_subscription<dji_referee_protocol::msg::RobotPerformance>(
        "/referee/common/robot_performance", reliable_qos,
        [this](dji_referee_protocol::msg::RobotPerformance::ConstSharedPtr msg) { on_dji_perf(*msg); });
      dji_hp_sub_ = create_subscription<dji_referee_protocol::msg::RobotHP>(
        "/referee/common/robot_hp", reliable_qos,
        [this](dji_referee_protocol::msg::RobotHP::ConstSharedPtr msg) { on_dji_hp(*msg); });
      dji_color_sub_ = create_subscription<dji_referee_protocol::msg::SelfColor>(
        "/referee/parsed/common/self_color", reliable_qos,
        [this](dji_referee_protocol::msg::SelfColor::ConstSharedPtr msg) { on_dji_color(*msg); });
      dji_shoot_sub_ = create_subscription<dji_referee_protocol::msg::ShootData>(
        "/referee/common/shoot_data", reliable_qos,
        [this](dji_referee_protocol::msg::ShootData::ConstSharedPtr msg) { on_dji_shoot(*msg); });
    }
#endif

    joints_sub_ = create_subscription<sensor_msgs::msg::JointState>(
      joint_state_topic, sensor_qos,
      [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) { on_joints(*msg); });
    if (!imu_topic.empty()) {
      imu_sub_ = create_subscription<sensor_msgs::msg::Imu>(
        imu_topic, sensor_qos,
        [this](sensor_msgs::msg::Imu::ConstSharedPtr msg) { on_imu(*msg); });
    }
    cam_info_sub_ = create_subscription<sensor_msgs::msg::CameraInfo>(
      camera_info_topic, sensor_qos,
      [this](sensor_msgs::msg::CameraInfo::ConstSharedPtr msg) { on_info(*msg); });
    image_sub_ = create_subscription<sensor_msgs::msg::Image>(
      camera_topic, img_qos,
      [this](sensor_msgs::msg::Image::ConstSharedPtr msg) { on_image(msg); });
    sim_state_sub_ = create_subscription<std_msgs::msg::String>(
      "/rmsim/" + robot_id_ + "/state", rclcpp::QoS(5),
      [this](std_msgs::msg::String::ConstSharedPtr msg) { on_state(*msg); });
    amcl_sub_ = create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
      amcl_topic, reliable_qos,
      [this](geometry_msgs::msg::PoseWithCovarianceStamped::ConstSharedPtr msg) { on_amcl_pose(*msg); });
    bt_switch_sub_ = create_subscription<std_msgs::msg::Int32>(
      "/" + robot_id_ + "/behavior/auto_aim_switch", rclcpp::QoS(5),
      [this](std_msgs::msg::Int32::ConstSharedPtr msg) { on_behavior(*msg); });
    bt_fire_sub_ = create_subscription<std_msgs::msg::Int32>(
      "/" + robot_id_ + "/behavior/fire_permit", rclcpp::QoS(5),
      [this](std_msgs::msg::Int32::ConstSharedPtr msg) { on_fire_permit(*msg); });
    bt_prio_sub_ = create_subscription<std_msgs::msg::String>(
      "/" + robot_id_ + "/behavior/target_priority", rclcpp::QoS(5),
      [this](std_msgs::msg::String::ConstSharedPtr msg) { on_priority(*msg); });

#ifdef SP_VISION_HAS_SP_MSGS
    cmd_pub_ = create_publisher<sp_msgs::msg::AutoAimCommandMsg>(command_topic, rclcpp::QoS(1));
#endif
    permit_pub_ = create_publisher<std_msgs::msg::Int32>("/" + robot_id_ + "/auto_aim_switch", rclcpp::QoS(1));
    track_pub_ = create_publisher<std_msgs::msg::String>("/" + robot_id_ + "/behavior/vision_track", rclcpp::QoS(1));
    status_pub_ = create_publisher<std_msgs::msg::String>("/sp_vision/status", reliable_qos);
    if (debug_image_enabled_) {
      debug_pub_ = create_publisher<sensor_msgs::msg::Image>("/sp_vision/debug_image", img_qos);
    }

    watchdog_timer_ = create_wall_timer(
      std::chrono::milliseconds(50),
      [this]() { watchdog(); });

#ifdef SP_VISION_HAS_SP_MSGS
    const bool has_sp_msgs = true;
#else
    const bool has_sp_msgs = false;
#endif
#ifdef SP_VISION_HAS_DJI_REFEREE
    const bool has_dji_ref = true;
#else
    const bool has_dji_ref = false;
#endif

    RCLCPP_INFO(
      get_logger(),
      "sp_vision_25 C++17 dual-engine world-tracker ready "
      "(robot_id=%s, backend=%s, sp_msgs=%s, dji_ref=%s, v0=%.1fm/s, "
      "enemy_color=%s, pitch_up_pos=%s, require_bt=%s, outpost_tpl=%s)",
      robot_id_.c_str(),
      detector_->backend_name().c_str(),
      has_sp_msgs ? "true" : "false",
      has_dji_ref ? "true" : "false",
      muzzle_v,
      enemy_color_override_.c_str(),
      pitch_up_positive_ ? "true" : "false",
      require_behavior_switch_ ? "true" : "false",
      detector_->has_outpost_reference() ? "true" : "false");
  }

  void release()
  {
    tracker_.reset();
    track_name_.reset();
    sim_observation_.reset();
    sim_challenger_.reset();
    track_visible_ = false;
    last_solution_.reset();
    enemy_world_xy_.reset();
    enemy_world_vel_ = {0.0, 0.0};
    enemy_xy_stamp_s_ = 0.0;
    command_firing_ = false;
    if (permitted_) {
      std_msgs::msg::Int32 msg;
      msg.data = 0;
      permit_pub_->publish(msg);
      permitted_ = false;
    }
  }

private:
  int64_t now_ns()
  {
    return get_clock()->now().nanoseconds();
  }

  bool simulator_observations() const
  {
    return referee_mode_ != "dji" && (sim_state_seen_ || sim_referee_seen_ || referee_mode_ == "sim");
  }

  std::string enemy_color() const
  {
    if (sim_team_known_) {
      return is_red_ ? "blue" : "red";
    }
    if (enemy_color_override_ == "red" || enemy_color_override_ == "blue") {
      return enemy_color_override_;
    }
    return is_red_ ? "blue" : "red";
  }

  bool behavior_active(int64_t now) const
  {
    if (!require_behavior_switch_ && !behavior_seen_) {
      return true;
    }
    const int64_t age = now - behavior_stamp_;
    return behavior_permits_ && age >= 0 && age <= kBehaviorCommandNs;
  }

  bool fire_gate_open(int64_t now) const
  {
    if (!fire_permitted_.has_value()) {
      return true;
    }
    if (!*fire_permitted_ || !fire_permit_stamp_.has_value()) {
      return false;
    }
    const int64_t age = now - *fire_permit_stamp_;
    return age >= 0 && age <= kBehaviorCommandNs;
  }

  std::set<std::string> allowed_names() const
  {
    std::set<std::string> allowed = kVehicleNames;
    if (target_priority_ == "ENEMY_VEHICLE_THEN_OUTPOST") {
      allowed.insert("outpost");
    } else if (target_priority_ == "ENEMY_VEHICLE_THEN_BASE") {
      allowed.insert("base");
    }
    std::set<std::string> out;
    for (const auto & name : allowed) {
      if (live_targets_.count(name) > 0) {
        out.insert(name);
      }
    }
    return out;
  }

  void stop_firing(int64_t now)
  {
    if (command_firing_ && last_solution_.has_value() &&
        track_stamp_ > 0 && (now - track_stamp_) >= 0 &&
        (now - track_stamp_) <= kTrackHoldNs)
    {
      publish_cmd(*last_solution_, now, false);
    }
    command_firing_ = false;
  }

  void record_attitude_and_fast_fire(int64_t stamp_ns, double world_yaw, double world_pitch_down)
  {
    cur_yaw_ = world_yaw;
    cur_pitch_ = world_pitch_down;
    feedback_.push_back({stamp_ns, world_yaw, world_pitch_down});
    while (feedback_.size() > 128) {
      feedback_.pop_front();
    }

    if (pending_image_) {
      auto img = pending_image_;
      process_image(img);
    } else if (track_visible_ && !command_firing_ && last_solution_.has_value() && track_name_.has_value()) {
      const int64_t now = now_ns();
      const int64_t age = now - track_stamp_;
      if (age >= 0 && age <= kMatchFireObservationNs &&
          game_running_ && robot_alive_ &&
          behavior_active(now) && fire_gate_open(now))
      {
        const bool is_outpost = (*track_name_ == "outpost");
        const bool is_base = (*track_name_ == "base");
        const int min_hits = is_base ? 1 : 2;
        const double tol_y = is_outpost ? std::min(aimer_.structure_shoot_tol_rad, 0.060)
          : (is_base ? aimer_.structure_shoot_tol_rad : aimer_.shoot_tol_rad);
        const double tol_p = is_outpost ? std::min(aimer_.structure_shoot_tol_rad, 0.045)
          : (is_base ? aimer_.structure_shoot_tol_rad : aimer_.shoot_tol_rad);
        // Extrapolate target yaw at 50-100Hz between 15Hz camera frames using tracked yaw_rate
        AimSolution pred = *last_solution_;
        const double dt_s = std::clamp(static_cast<double>(age) * 1e-9, 0.0, 0.15);
        const double raw_ty = pred.target_yaw + last_yaw_rate_ * dt_s;
        pred.target_yaw = std::atan2(std::sin(raw_ty), std::cos(raw_ty));
        const double ey = std::abs(wrap_angle(pred.target_yaw - world_yaw));
        const double ep = std::abs(pred.target_pitch - world_pitch_down);
        if (tracker_.track_hits() >= min_hits && ey <= tol_y && ep <= tol_p) {
          publish_cmd(pred, now, true);
        } else if (is_outpost && std::abs(last_yaw_rate_) > 0.04) {
          publish_cmd(pred, now, false);
        }
      }
    }
  }

  void on_referee(const std_msgs::msg::String & msg)
  {
    nlohmann::json ref;
    try {
      ref = nlohmann::json::parse(msg.data);
    } catch (const std::exception &) {
      return;
    }
    if (!ref.is_object() || (ref.contains("available") && ref["available"] == false)) return;

    sim_referee_seen_ = true;
    const auto rs = ref.contains("robot_status") && ref["robot_status"].is_object()
      ? ref["robot_status"] : nlohmann::json::object();
    std::optional<bool> team_red;
    if (ref.contains("friendly_red") && ref["friendly_red"].is_boolean()) {
      team_red = ref["friendly_red"].get<bool>();
    } else if (rs.contains("robot_id") && rs["robot_id"].is_number_integer()) {
      const int id = rs["robot_id"].get<int>();
      if (id > 0 && id < 200) team_red = id < 100;  // Older simulator referee payload.
    }
    if (team_red) {
      if (!sim_team_known_ || *team_red != is_red_) release();
      is_red_ = *team_red;
      sim_team_known_ = true;
    }
    if (ref.contains("game_status") && ref["game_status"].is_object()) {
      const auto & gs = ref["game_status"];
      if (gs.contains("game_progress") && gs["game_progress"].is_number()) {
        game_running_ = (gs["game_progress"].get<int>() == 4);
      }
    }
    if (rs.contains("current_hp") && rs["current_hp"].is_number()) {
      robot_alive_ = rs["current_hp"].get<int>() > 0;
    }

    const std::string enemy = enemy_color();
    // The HP map does not distinguish an absent class from an eliminated vehicle.
    std::set<std::string> live = kVehicleNames;

    if (ref.contains("field_devices") && ref["field_devices"].is_object()) {
      const auto & fd = ref["field_devices"];
      for (const char * site_name : {"red", "blue"}) {
        if (fd.contains(site_name) && fd[site_name].is_object()) {
          const auto & site = fd[site_name];
          const int hp = (site.contains("outpost_hp") && site["outpost_hp"].is_number())
            ? site["outpost_hp"].get<int>() : 0;
          if (hp > 0 && site.contains("outpost_owner_red") && site["outpost_owner_red"].is_boolean()) {
            if (site["outpost_owner_red"].get<bool>() == (!is_red_)) {
              live.insert("outpost");
            }
          }
        }
      }
      if (fd.contains(enemy) && fd[enemy].is_object()) {
        const auto & enemy_site = fd[enemy];
        const int base_hp = (enemy_site.contains("base_hp") && enemy_site["base_hp"].is_number())
          ? enemy_site["base_hp"].get<int>() : 0;
        const bool base_prot = (enemy_site.contains("base_protected") && enemy_site["base_protected"].is_boolean())
          ? enemy_site["base_protected"].get<bool>() : true;
        if (base_hp > 0 && !base_prot) {
          live.insert("base");
        }
      }
    }
    live_targets_ = std::move(live);

    if (!robot_alive_ || !game_running_ ||
        (track_name_ && allowed_names().count(*track_name_) == 0)) {
      release();
    }
  }

#ifdef SP_VISION_HAS_DJI_REFEREE
  void on_dji_game(const dji_referee_protocol::msg::GameStatus & msg)
  {
    if (sim_referee_seen_) return;
    game_running_ = (static_cast<int>(msg.game_progress) == 4);
    if (!game_running_) release();
  }

  void on_dji_color(const dji_referee_protocol::msg::SelfColor & msg)
  {
    if (sim_referee_seen_ || enemy_color_override_ != "auto") return;
    const int color = static_cast<int>(msg.color);
    if (color == 1 || color == 2) {
      const bool is_red = (color == 1);
      if (is_red != is_red_) release();
      is_red_ = is_red;
    }
  }

  void on_dji_perf(const dji_referee_protocol::msg::RobotPerformance & msg)
  {
    if (sim_referee_seen_) return;
    const int rid = static_cast<int>(msg.robot_id);
    if (rid > 0 && enemy_color_override_ == "auto") {
      const bool is_red = (rid < 100);
      if (is_red != is_red_) release();
      is_red_ = is_red;
    }
    robot_alive_ = (static_cast<int>(msg.current_hp) > 0);
    if (!robot_alive_) release();
  }

  void on_dji_hp(const dji_referee_protocol::msg::RobotHP & msg)
  {
    if (sim_referee_seen_) return;
    const bool enemy_is_red = (enemy_color() == "red");
    std::set<std::string> live;
    if (enemy_is_red) {
      if (msg.red_1_robot_hp > 0) live.insert("one");
      if (msg.red_2_robot_hp > 0) live.insert("two");
      if (msg.red_3_robot_hp > 0) live.insert("three");
      if (msg.red_4_robot_hp > 0) live.insert("four");
      live.insert("five");  // 2026 protocol omits 5_robot_hp
      if (msg.red_7_robot_hp > 0) live.insert("sentry");
      if (msg.red_outpost_hp > 0) live.insert("outpost");
      if (msg.red_base_hp > 0) live.insert("base");
    } else {
      if (msg.blue_1_robot_hp > 0) live.insert("one");
      if (msg.blue_2_robot_hp > 0) live.insert("two");
      if (msg.blue_3_robot_hp > 0) live.insert("three");
      if (msg.blue_4_robot_hp > 0) live.insert("four");
      live.insert("five");
      if (msg.blue_7_robot_hp > 0) live.insert("sentry");
      if (msg.blue_outpost_hp > 0) live.insert("outpost");
      if (msg.blue_base_hp > 0) live.insert("base");
    }
    live_targets_ = std::move(live);
  }

  void on_dji_shoot(const dji_referee_protocol::msg::ShootData & msg)
  {
    const double speed = static_cast<double>(msg.initial_speed);
    if (speed >= 18.0 && speed <= 29.5) {
      aimer_.v0 = 0.75 * aimer_.v0 + 0.25 * speed;
    }
  }
#endif

  void on_behavior(const std_msgs::msg::Int32 & msg)
  {
    behavior_seen_ = true;
    behavior_permits_ = (msg.data == 1);
    behavior_stamp_ = now_ns();
    if (!behavior_permits_) {
      release();
    }
  }

  void on_fire_permit(const std_msgs::msg::Int32 & msg)
  {
    fire_permitted_ = (msg.data == 1);
    fire_permit_stamp_ = now_ns();
    if (!*fire_permitted_) {
      stop_firing(*fire_permit_stamp_);
    }
  }

  void on_priority(const std_msgs::msg::String & msg)
  {
    if (msg.data == "ENEMY_VEHICLE_FIRST" ||
        msg.data == "ENEMY_VEHICLE_THEN_OUTPOST" ||
        msg.data == "ENEMY_VEHICLE_THEN_BASE")
    {
      target_priority_ = msg.data;
      if (track_name_.has_value() && allowed_names().count(*track_name_) == 0) {
        release();
      }
    }
  }

  void on_amcl_pose(const geometry_msgs::msg::PoseWithCovarianceStamped & msg)
  {
    const auto & p = msg.pose.pose.position;
    self_pos_m_ = cv::Vec3d(p.x, p.y, p.z);
  }

  void on_state(const std_msgs::msg::String & msg)
  {
    nlohmann::json d;
    try {
      d = nlohmann::json::parse(msg.data);
    } catch (const std::exception &) {
      release();
      return;
    }
    if (!d.is_object()) {
      release();
      return;
    }
    sim_state_seen_ = true;
    const nlohmann::json session_val = d.contains("session") ? d["session"] : nlohmann::json(nullptr);
    const nlohmann::json epoch_val = d.contains("epoch") ? d["epoch"] : nlohmann::json(nullptr);
    const auto new_id = std::make_pair(session_val, epoch_val);
    if (!identity_.has_value() || *identity_ != new_id) {
      identity_ = new_id;
      fire_permit_stamp_.reset();
      last_cmd_stamp_ = 0;
      feedback_.clear();
      pending_image_.reset();
      if (simulator_observations()) {
        last_sim_frame_stamp_ = -1;
        sim_team_known_ = false;
      }
      release();
    }

    const bool state_fresh = d.contains("state_fresh") && d["state_fresh"].is_boolean() && d["state_fresh"].get<bool>();
    const bool match_autonomy =
      d.contains("drive") && d["drive"].is_object() &&
      d["drive"].contains("match_autonomy") && d["drive"]["match_autonomy"].is_boolean() &&
      d["drive"]["match_autonomy"].get<bool>();
    const bool aim_ok = d.contains("aim") && d["aim"].is_object();
    const bool aim_enabled = aim_ok && d["aim"].contains("enabled") && d["aim"]["enabled"].is_boolean() && d["aim"]["enabled"].get<bool>();
    const bool has_match_fire = aim_ok && d["aim"].contains("match_fire");

    sim_match_ = state_fresh && match_autonomy && aim_enabled && has_match_fire;
    if (sim_match_ && d["aim"].contains("stamp_ns") &&
        d["aim"].contains("world_yaw_rad") && d["aim"].contains("world_pitch_rad"))
    {
      const int64_t stamp = d["aim"]["stamp_ns"].get<int64_t>();
      const double cur_y = d["aim"]["world_yaw_rad"].get<double>();
      const double cur_p = d["aim"]["world_pitch_rad"].get<double>();
      record_attitude_and_fast_fire(stamp, cur_y, cur_p);
    } else if (!sim_match_) {
      release();
    }
  }

  void on_joints(const sensor_msgs::msg::JointState & msg)
  {
    double yaw = cur_yaw_;
    double pitch_down = cur_pitch_;
    bool updated = false;
    const size_t n = std::min(msg.name.size(), msg.position.size());
    for (size_t i = 0; i < n; ++i) {
      const std::string low = to_lower_trim(msg.name[i]);
      if (low.find("yaw") != std::string::npos) {
        yaw = msg.position[i];
        updated = true;
      } else if (low.find("pitch") != std::string::npos) {
        const double raw_p = msg.position[i];
        pitch_down = pitch_up_positive_ ? -raw_p : raw_p;
        updated = true;
      }
    }
    if (updated && !sim_state_seen_) {
      int64_t stamp = stamp_to_ns(msg.header.stamp);
      if (stamp <= 0) stamp = now_ns();
      record_attitude_and_fast_fire(stamp, yaw, pitch_down);
    } else if (updated) {
      cur_yaw_ = yaw;
      cur_pitch_ = pitch_down;
    }
  }

  void on_imu(const sensor_msgs::msg::Imu & msg)
  {
    if (sim_state_seen_) return;
    const auto & q = msg.orientation;
    const auto [yaw, pitch_down] = quat_to_yaw_pitch(q.x, q.y, q.z, q.w);
    int64_t stamp = stamp_to_ns(msg.header.stamp);
    if (stamp <= 0) stamp = now_ns();
    record_attitude_and_fast_fire(stamp, yaw, pitch_down);
  }

  void on_info(const sensor_msgs::msg::CameraInfo & msg)
  {
    if (msg.k[0] > 1.0) {
      K_ = cv::Matx33d(
        msg.k[0], msg.k[1], msg.k[2],
        msg.k[3], msg.k[4], msg.k[5],
        msg.k[6], msg.k[7], msg.k[8]);
    }
    if (msg.d.size() >= 4) {
      D_ = cv::Mat(static_cast<int>(msg.d.size()), 1, CV_64F);
      for (size_t i = 0; i < msg.d.size(); ++i) {
        D_.at<double>(static_cast<int>(i), 0) = msg.d[i];
      }
    }
  }

  const ArmorDetection * pick_target(
    const std::vector<ArmorDetection> & detections,
    double cur_yaw,
    double cur_pitch,
    int64_t stamp_ns)
  {
    const bool simulate = simulator_observations();
    const std::string enemy_col = enemy_color();
    const auto allowed = allowed_names();
    const double fx = K_(0, 0);
    const double fy = K_(1, 1);
    const double cx = K_(0, 2);
    const double cy = K_(1, 2);

    const ArmorDetection * best = nullptr;
    const ArmorDetection * continued = nullptr;
    std::optional<PlateObservation> best_observation;
    std::optional<PlateObservation> continued_observation;
    std::tuple<int, int, double, float> best_score{-1, -1, -1e9, -1.0f};
    const int64_t lost_ns = stamp_ns - track_stamp_;
    const double same_track_margin = 0.11 + std::clamp(0.55e-9 * lost_ns, 0.0, 0.11);
    double best_separation = same_track_margin;

    for (const auto & d : detections) {
      if (d.color != enemy_col || allowed.count(d.name) == 0) {
        continue;
      }
      const int is_veh = (kVehicleNames.count(d.name) > 0) ? 1 : 0;
      const auto pit = kTargetPriority.find(d.name);
      const int prio = (pit != kTargetPriority.end()) ? pit->second : 0;
      double ang_pen = 0.0;
      std::optional<PlateObservation> observation;
      if (simulate || tracker_.initialized()) {
        const auto [wy, wp] = ray_to_world_angles(
          cur_yaw, cur_pitch, (d.center.x - cx) / fx, (d.center.y - cy) / fy);
        if (tracker_.initialized()) {
          ang_pen = -std::hypot(wrap_angle(wy - tracker_.yaw()), wp - tracker_.pitch());
        }
        if (simulate) {
          observation = PlateObservation{d.name, wy, wp, stamp_ns};
          if (sim_observation_ && d.name == sim_observation_->name) {
            const double separation = observation_separation(*observation, *sim_observation_);
            if (separation < same_track_margin) {
              if (separation < best_separation) {
                best_separation = separation;
                continued = &d;
                continued_observation = observation;
              }
              continue;
            }
          }
        }
      }
      const auto score = std::make_tuple(is_veh, prio, ang_pen, d.confidence);
      if (best == nullptr || score > best_score) {
        best = &d;
        best_score = score;
        best_observation = observation;
      }
    }

    if (!simulate) return best;
    if (continued) {
      sim_observation_ = continued_observation;
      sim_challenger_.reset();
      return continued;
    }
    if (!sim_observation_) {
      sim_observation_ = best_observation;
      return best;
    }
    if (!best_observation) return nullptr;

    // Keep the visible plate across class/priority fluctuations. A different plate
    // needs three distinct, nearby observations after the current one disappears.
    if (sim_challenger_ && best_observation->name == sim_challenger_->name &&
        stamp_ns - sim_challenger_->stamp_ns <= kSimHandoffGapNs &&
        observation_separation(*best_observation, *sim_challenger_) < 0.14) {
      best_observation->count = sim_challenger_->count + 1;
    }
    sim_challenger_ = best_observation;
    if (sim_challenger_->count < 3 || lost_ns < kSimHandoffHoldNs) return nullptr;

    release();
    sim_observation_ = best_observation;
    return best;
  }

  void publish_cmd(const AimSolution & sol, int64_t stamp_ns, bool shoot)
  {
#ifdef SP_VISION_HAS_SP_MSGS
    if (!cmd_pub_) return;
    const int64_t safe_stamp = std::max(stamp_ns, last_cmd_stamp_);
    last_cmd_stamp_ = safe_stamp;

    sp_msgs::msg::AutoAimCommandMsg cmd;
    cmd.timestamp.sec = static_cast<int32_t>(safe_stamp / 1'000'000'000LL);
    cmd.timestamp.nanosec = static_cast<uint32_t>(safe_stamp % 1'000'000'000LL);
    cmd.control = sol.locked;
    cmd.shoot = shoot;
    cmd.yaw = sol.target_yaw;
    cmd.pitch = pitch_up_positive_ ? -sol.target_pitch : sol.target_pitch;

    std_msgs::msg::Int32 permit_msg;
    permit_msg.data = 1;
    permit_pub_->publish(permit_msg);
    permitted_ = true;
    command_firing_ = cmd.shoot;
    if (cmd.shoot) ++trigger_frames_;
    cmd_pub_->publish(cmd);
#else
    (void)sol;
    (void)stamp_ns;
    (void)shoot;
#endif
  }

  void publish_debug_image(
    const cv::Mat & bgr,
    const std::vector<ArmorDetection> & dets,
    const ArmorDetection * best,
    const std::optional<AimSolution> & sol,
    bool can_shoot,
    const std_msgs::msg::Header & header)
  {
    if (!debug_pub_) return;
    cv::Mat vis = bgr.clone();
    for (const auto & d : dets) {
      const cv::Scalar col = (&d == best)
        ? cv::Scalar(0, 255, 0)
        : (d.color == "blue" ? cv::Scalar(255, 160, 0) : cv::Scalar(0, 80, 255));
      std::vector<cv::Point> pts;
      pts.reserve(4);
      for (const auto & kp : d.keypoints) {
        pts.emplace_back(static_cast<int>(kp.x), static_cast<int>(kp.y));
      }
      cv::polylines(vis, pts, true, col, 2);
      std::ostringstream oss;
      oss << static_cast<char>(std::toupper(d.color[0])) << ":" << d.name
          << " " << std::fixed << std::setprecision(2) << d.confidence;
      cv::putText(
        vis, oss.str(),
        cv::Point(std::max(0, d.box.x), std::max(18, d.box.y - 4)),
        cv::FONT_HERSHEY_SIMPLEX, 0.55, col, 2);
    }
    if (sol.has_value()) {
      std::ostringstream oss;
      oss << "LOCK " << (track_name_ ? *track_name_ : "none")
          << " d=" << std::fixed << std::setprecision(2) << sol->distance_m << "m"
          << " v0=" << std::setprecision(1) << aimer_.v0
          << " err=(" << std::showpos << rad2deg(sol->err_yaw)
          << "," << rad2deg(sol->err_pitch) << ")deg"
          << " FIRE=" << (can_shoot ? "ON" : "OFF");
      cv::putText(
        vis, oss.str(), cv::Point(12, 28),
        cv::FONT_HERSHEY_SIMPLEX, 0.7,
        can_shoot ? cv::Scalar(0, 255, 255) : cv::Scalar(220, 220, 220), 2);
    }

    sensor_msgs::msg::Image out;
    out.header = header;
    out.height = static_cast<uint32_t>(vis.rows);
    out.width = static_cast<uint32_t>(vis.cols);
    out.encoding = "bgr8";
    out.is_bigendian = false;
    out.step = static_cast<uint32_t>(vis.cols * 3);
    out.data.resize(static_cast<size_t>(out.height) * static_cast<size_t>(out.step));
    if (vis.isContinuous()) {
      std::memcpy(out.data.data(), vis.data, out.data.size());
    } else {
      for (int r = 0; r < vis.rows; ++r) {
        std::memcpy(out.data.data() + static_cast<size_t>(r) * out.step, vis.ptr<uint8_t>(r), out.step);
      }
    }
    debug_pub_->publish(out);
  }

  void watchdog()
  {
    const int64_t now = now_ns();
    if ((require_behavior_switch_ || behavior_seen_) && behavior_permits_) {
      const int64_t age = now - behavior_stamp_;
      if (age < 0 || age > kBehaviorCommandNs) {
        behavior_permits_ = false;
        release();
      }
    }
    if (track_name_.has_value()) {
      const int64_t age = now - track_stamp_;
      if (age < 0 || age > kTrackHoldNs || allowed_names().count(*track_name_) == 0) {
        release();
      } else if (age > kMatchFireObservationNs) {
        track_visible_ = false;
        if (last_solution_.has_value() && behavior_active(now) && game_running_ && robot_alive_) {
          publish_cmd(*last_solution_, now, false);
        }
      }
    }
    if (command_firing_ && !(track_visible_ && game_running_ && robot_alive_ &&
                             behavior_active(now) && fire_gate_open(now)))
    {
      stop_firing(now);
    }

    nlohmann::json track_json;
    track_json["stamp_ns"] = now;
    track_json["session"] = identity_ ? identity_->first : nlohmann::json(nullptr);
    track_json["epoch"] = identity_ ? identity_->second : nlohmann::json(nullptr);
    if (track_name_.has_value()) {
      track_json["track_id"] = track_id_;
      const auto lit = kArmorLabelIds.find(*track_name_);
      track_json["label"] = (lit != kArmorLabelIds.end()) ? nlohmann::json(lit->second) : nlohmann::json(nullptr);
      track_json["observation_stamp_ns"] = track_stamp_;
    } else {
      track_json["track_id"] = nullptr;
      track_json["label"] = nullptr;
      track_json["observation_stamp_ns"] = nullptr;
    }
    track_json["visible"] = track_visible_;
    track_json["firing"] = command_firing_;
    if (fire_permitted_.has_value()) {
      track_json["fire_permit"] = fire_gate_open(now);
    } else {
      track_json["fire_permit"] = nullptr;
    }
    track_json["target_priority"] = target_priority_;
    track_json["distance_m"] = last_solution_ ? nlohmann::json(round_dec(last_solution_->distance_m, 2)) : nlohmann::json(nullptr);
    track_json["self_x"] = self_pos_m_ ? nlohmann::json(round_dec((*self_pos_m_)[0], 2)) : nlohmann::json(nullptr);
    track_json["self_y"] = self_pos_m_ ? nlohmann::json(round_dec((*self_pos_m_)[1], 2)) : nlohmann::json(nullptr);
    track_json["enemy_x"] = enemy_world_xy_ ? nlohmann::json(round_dec(enemy_world_xy_->first, 2)) : nlohmann::json(nullptr);
    track_json["enemy_y"] = enemy_world_xy_ ? nlohmann::json(round_dec(enemy_world_xy_->second, 2)) : nlohmann::json(nullptr);
    track_json["enemy_vx"] = enemy_world_xy_ ? round_dec(enemy_world_vel_.first, 2) : 0.0;
    track_json["enemy_vy"] = enemy_world_xy_ ? round_dec(enemy_world_vel_.second, 2) : 0.0;

    std_msgs::msg::String tmsg;
    tmsg.data = track_json.dump();
    track_pub_->publish(tmsg);

    if (now - last_diagnostic_ >= 2'000'000'000LL) {
      last_diagnostic_ = now;
      RCLCPP_INFO(
        get_logger(),
        "sp_vision_25 frames=%llu tracked=%llu trigger=%llu permitted=%s v0=%.1f target=%s",
        static_cast<unsigned long long>(frames_),
        static_cast<unsigned long long>(tracked_),
        static_cast<unsigned long long>(trigger_frames_),
        permitted_ ? "True" : "False",
        aimer_.v0,
        track_name_ ? track_name_->c_str() : "none");
    }
  }

  void on_image(const sensor_msgs::msg::Image::ConstSharedPtr & msg)
  {
    ++frames_;
    pending_image_ = msg;
    process_image(msg);
  }

  void process_image(const sensor_msgs::msg::Image::ConstSharedPtr & msg)
  {
    const int64_t now = now_ns();
    const bool simulate = simulator_observations();
    if (!game_running_ || !robot_alive_ || !behavior_active(now) ||
        (simulate && !sim_team_known_)) {
      return;
    }
    if (!msg || msg->height == 0 || msg->width == 0) {
      return;
    }

    int64_t stamp = stamp_to_ns(msg->header.stamp);
    if (stamp <= 0) {
      stamp = now;
    }

    double cur_yaw = cur_yaw_;
    double cur_pitch = cur_pitch_;
    if (sim_state_seen_) {
      if (!sim_match_ || feedback_.empty()) {
        return;
      }
      if (now < stamp) {
        return;
      }
      if ((now - stamp) > max_image_age_ns_) {
        pending_image_.reset();
        return;
      }
      const auto sample = attitude_at(feedback_, stamp);
      if (!sample.has_value()) {
        return;
      }
      cur_yaw = sample->first;
      cur_pitch = sample->second;
    } else if (!feedback_.empty()) {
      const auto sample = attitude_at(feedback_, stamp);
      if (sample.has_value()) {
        cur_yaw = sample->first;
        cur_pitch = sample->second;
      } else {
        cur_yaw = feedback_.back().yaw;
        cur_pitch = feedback_.back().pitch;
      }
    }

    pending_image_.reset();
    if ((track_name_ && stamp < track_stamp_) ||
        (simulate && stamp <= last_sim_frame_stamp_)) {
      return;
    }

    cv::Mat bgr = decode_image_to_bgr(*msg);
    if (bgr.empty()) {
      return;
    }

    const std::string enemy_col = enemy_color();
    const double t_sec = (stamp > 0)
      ? (static_cast<double>(stamp) * 1e-9)
      : std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    const bool prefer_outpost = (target_priority_ == "ENEMY_VEHICLE_THEN_OUTPOST");
    if (simulate) {
      last_sim_frame_stamp_ = stamp;
      if (track_name_ && stamp - track_stamp_ > kTrackHoldNs) release();
    }
    const auto dets = detector_->detect(bgr, enemy_col, prefer_outpost, simulate);
    const ArmorDetection * best = pick_target(dets, cur_yaw, cur_pitch, stamp);

    const std::string active_name = best ? best->name : (track_name_ ? *track_name_ : "");
    const std::string target_kind =
      (active_name == "outpost") ? "outpost" : ((active_name == "base") ? "base" : "vehicle");
    const bool is_structure = (target_kind == "outpost" || target_kind == "base");

    std::optional<WorldMeasurement> meas;
    if (best != nullptr) {
      const double fx = K_(0, 0);
      const double fy = K_(1, 1);
      const double cx = K_(0, 2);
      const double cy = K_(1, 2);
      const auto pnp = solve_armor_pnp(best->keypoints, best->armor_type, K_, D_);
      double u = 0.0;
      double v = 0.0;
      double dist = 0.0;
      if (pnp.has_value()) {
        dist = cv::norm(*pnp);
        const double z_safe = std::max(0.1, (*pnp)[2]);
        u = (*pnp)[0] / z_safe;
        v = (*pnp)[1] / z_safe;
      } else {
        u = (best->center.x - cx) / fx;
        v = (best->center.y - cy) / fy;
        const double bar_h = std::max(4.0, static_cast<double>(best->bar_height_px));
        dist = std::clamp(0.056 * fy / bar_h, 0.5, 16.0);
      }
      const auto [wyaw, wpitch] = ray_to_world_angles(cur_yaw, cur_pitch, u, v);
      meas = WorldMeasurement{wyaw, wpitch, dist};
    }

    const auto tracked = tracker_.update(meas, t_sec, target_kind);
    if (!tracked.has_value()) {
      track_visible_ = false;
      stop_firing(now);
      publish_debug_image(bgr, dets, nullptr, std::nullopt, false, msg->header);
      return;
    }

    if (best != nullptr) {
      if (!track_name_.has_value() || *track_name_ != best->name) {
        ++track_id_;
      }
      track_name_ = best->name;
      track_stamp_ = stamp;
      track_visible_ = true;
      ++tracked_;
    } else {
      track_visible_ = false;
    }

    const double eval_yaw = feedback_.empty() ? cur_yaw : feedback_.back().yaw;
    const double eval_pitch = feedback_.empty() ? cur_pitch : feedback_.back().pitch;
    const AimSolution sol = aimer_.solve_world(
      tracked->yaw, tracked->pitch, tracked->dist,
      tracked->yaw_rate, tracked->pitch_rate,
      eval_yaw, eval_pitch, tracked->track_hits, target_kind);
    last_solution_ = sol;
    last_yaw_rate_ = tracked->yaw_rate;

    if (self_pos_m_.has_value() && !is_structure && tracked->track_hits >= 3 && sol.distance_m <= 8.0) {
      const double horiz = sol.distance_m * std::cos(sol.target_pitch);
      const double new_ex = (*self_pos_m_)[0] + horiz * std::cos(sol.target_yaw);
      const double new_ey = (*self_pos_m_)[1] + horiz * std::sin(sol.target_yaw);
      if (enemy_world_xy_.has_value()) {
        const double dt_xy = t_sec - enemy_xy_stamp_s_;
        if (dt_xy >= 0.03 && dt_xy <= 0.6) {
          const double raw_vx = std::clamp((new_ex - enemy_world_xy_->first) / dt_xy, -4.0, 4.0);
          const double raw_vy = std::clamp((new_ey - enemy_world_xy_->second) / dt_xy, -4.0, 4.0);
          enemy_world_vel_.first = 0.65 * enemy_world_vel_.first + 0.35 * raw_vx;
          enemy_world_vel_.second = 0.65 * enemy_world_vel_.second + 0.35 * raw_vy;
        }
      }
      enemy_world_xy_ = {new_ex, new_ey};
      enemy_xy_stamp_s_ = t_sec;
    } else if (is_structure) {
      enemy_world_xy_.reset();
      enemy_world_vel_ = {0.0, 0.0};
    }

    const bool can_shoot = sol.shoot && track_visible_ && game_running_ &&
      robot_alive_ && behavior_active(now) && fire_gate_open(now);
    const int64_t cmd_stamp = sim_state_seen_ ? std::max(stamp, now - 20'000'000LL) : stamp;
    publish_cmd(sol, cmd_stamp, can_shoot);
    publish_debug_image(bgr, dets, best, sol, can_shoot, msg->header);

    nlohmann::json st;
    st["target"] = track_name_ ? *track_name_ : "tracked";
    st["color"] = best ? best->color : enemy_col;
    st["distance_m"] = round_dec(sol.distance_m, 2);
    st["tof_s"] = round_dec(sol.tof_s, 3);
    st["yaw_deg"] = round_dec(rad2deg(sol.target_yaw), 2);
    st["pitch_deg"] = round_dec(rad2deg(sol.target_pitch), 2);
    st["shoot"] = can_shoot;
    st["bt_permit"] = behavior_active(now);
    st["fire_permit"] = fire_gate_open(now);

    std_msgs::msg::String st_msg;
    st_msg.data = st.dump();
    status_pub_->publish(st_msg);
  }

  std::string robot_id_;
  std::string enemy_color_override_;
  std::string referee_mode_;
  bool require_behavior_switch_{false};
  bool pitch_up_positive_{false};
  bool debug_image_enabled_{false};
  int64_t max_image_age_ns_{180'000'000LL};

  std::unique_ptr<SpVisionYoloV8Detector> detector_;
  WorldAngularTracker tracker_;
  BallisticAimer aimer_;

  bool is_red_{true};
  bool game_running_{true};
  bool robot_alive_{true};
  double cur_yaw_{0.0};
  double cur_pitch_{0.0};
  std::optional<cv::Vec3d> self_pos_m_;
  std::optional<std::pair<double, double>> enemy_world_xy_;
  std::pair<double, double> enemy_world_vel_{0.0, 0.0};
  double enemy_xy_stamp_s_{0.0};
  cv::Matx33d K_;
  cv::Mat D_;

  bool behavior_seen_{false};
  bool behavior_permits_{true};
  int64_t behavior_stamp_{-1};
  std::optional<bool> fire_permitted_;
  std::optional<int64_t> fire_permit_stamp_;
  std::string target_priority_{"ENEMY_VEHICLE_FIRST"};
  std::set<std::string> live_targets_;

  bool sim_state_seen_{false};
  bool sim_match_{false};
  bool sim_referee_seen_{false};
  bool sim_team_known_{false};
  int64_t last_sim_frame_stamp_{-1};
  std::optional<PlateObservation> sim_observation_;
  std::optional<PlateObservation> sim_challenger_;
  std::optional<std::pair<nlohmann::json, nlohmann::json>> identity_;
  std::deque<AttitudeSample> feedback_;
  sensor_msgs::msg::Image::ConstSharedPtr pending_image_;

  int track_id_{0};
  std::optional<std::string> track_name_;
  int64_t track_stamp_{0};
  bool track_visible_{false};
  std::optional<AimSolution> last_solution_;
  double last_yaw_rate_{0.0};
  bool command_firing_{false};
  bool permitted_{false};
  int64_t last_cmd_stamp_{0};
  uint64_t frames_{0};
  uint64_t tracked_{0};
  uint64_t trigger_frames_{0};
  int64_t last_diagnostic_{0};

  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sim_ref_sub_;
#ifdef SP_VISION_HAS_DJI_REFEREE
  rclcpp::Subscription<dji_referee_protocol::msg::GameStatus>::SharedPtr dji_game_sub_;
  rclcpp::Subscription<dji_referee_protocol::msg::RobotPerformance>::SharedPtr dji_perf_sub_;
  rclcpp::Subscription<dji_referee_protocol::msg::RobotHP>::SharedPtr dji_hp_sub_;
  rclcpp::Subscription<dji_referee_protocol::msg::SelfColor>::SharedPtr dji_color_sub_;
  rclcpp::Subscription<dji_referee_protocol::msg::ShootData>::SharedPtr dji_shoot_sub_;
#endif
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joints_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub_;
  rclcpp::Subscription<sensor_msgs::msg::CameraInfo>::SharedPtr cam_info_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr image_sub_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sim_state_sub_;
  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr amcl_sub_;
  rclcpp::Subscription<std_msgs::msg::Int32>::SharedPtr bt_switch_sub_;
  rclcpp::Subscription<std_msgs::msg::Int32>::SharedPtr bt_fire_sub_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr bt_prio_sub_;

#ifdef SP_VISION_HAS_SP_MSGS
  rclcpp::Publisher<sp_msgs::msg::AutoAimCommandMsg>::SharedPtr cmd_pub_;
#endif
  rclcpp::Publisher<std_msgs::msg::Int32>::SharedPtr permit_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr track_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr status_pub_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr debug_pub_;
  rclcpp::TimerBase::SharedPtr watchdog_timer_;
};

}  // namespace sp_vision_25

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<sp_vision_25::SpVisionSimNode>();
  rclcpp::spin(node);
  node->release();
  rclcpp::shutdown();
  return 0;
}
