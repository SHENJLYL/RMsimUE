#pragma once

#include <array>
#include <cmath>
#include <optional>
#include <string>
#include <utility>

#include <opencv2/core.hpp>

namespace sp_vision_25 {

constexpr double kPi = 3.14159265358979323846;

inline double deg2rad(double deg) { return deg * (kPi / 180.0); }
inline double rad2deg(double rad) { return rad * (180.0 / kPi); }
inline double wrap_angle(double rad) { return std::remainder(rad, 2.0 * kPi); }

struct AimSolution {
  bool locked{false};
  bool shoot{false};
  double target_yaw{0.0};
  double target_pitch{0.0};
  double err_yaw{0.0};
  double err_pitch{0.0};
  double distance_m{0.0};
  double tof_s{0.0};
  cv::Vec3d pos_cam{0.0, 0.0, 0.0};
};

struct WorldMeasurement {
  double yaw{0.0};
  double pitch{0.0};
  double dist{0.0};
};

struct TrackedState {
  double yaw{0.0};
  double pitch{0.0};
  double dist{0.0};
  double yaw_rate{0.0};
  double pitch_rate{0.0};
  int track_hits{0};
};

std::optional<cv::Vec3d> solve_armor_pnp(
  const std::array<cv::Point2f, 4> & keypoints,
  const std::string & armor_type,
  const cv::Matx33d & K,
  const cv::Mat & dist_coeffs = cv::Mat());

std::pair<double, double> ray_to_world_angles(
  double world_yaw, double world_pitch, double u, double v);

std::pair<double, double> ballistic_drop_angle(
  double dist_m, double raw_pitch_rad, double muzzle_speed_mps = 24.5, double g = 9.81);

class WorldAngularTracker {
public:
  explicit WorldAngularTracker(double max_lost_s = 0.90);

  void reset();

  std::optional<TrackedState> update(
    const std::optional<WorldMeasurement> & meas,
    double t_sec,
    const std::string & target_kind = "vehicle");

  bool initialized() const { return initialized_; }
  double yaw() const { return yaw_; }
  double pitch() const { return pitch_; }
  int track_hits() const { return track_hits_; }

private:
  double max_lost_s_;
  double yaw_{0.0};
  double pitch_{0.0};
  double dist_{3.0};
  double yaw_rate_{0.0};
  double pitch_rate_{0.0};
  std::optional<double> outpost_center_yaw_;
  double outpost_yaw_rate_{0.0};
  bool initialized_{false};
  double last_t_{0.0};
  int track_hits_{0};
};

class BallisticAimer {
public:
  BallisticAimer(
    double muzzle_speed_mps = 24.5,
    double gravity = 9.81,
    double latency_s = 0.08,
    double shoot_tol_rad = deg2rad(5.5),
    double structure_shoot_tol_rad = deg2rad(8.5),
    double yaw_offset_rad = 0.0,
    double pitch_offset_rad = 0.0,
    double cam_to_barrel_z_m = 0.0);

  AimSolution solve_world(
    double world_yaw,
    double world_pitch,
    double dist_m,
    double yaw_rate,
    double pitch_rate,
    double cur_yaw,
    double cur_pitch,
    int track_hits,
    const std::string & target_kind = "vehicle") const;

  AimSolution solve_cam(
    const cv::Vec3d & pos_cam,
    double cur_yaw,
    double cur_pitch,
    int track_hits,
    const std::string & target_kind = "vehicle") const;

  double v0{24.5};
  double g{9.81};
  double latency_s{0.08};
  double shoot_tol_rad{deg2rad(5.5)};
  double structure_shoot_tol_rad{deg2rad(8.5)};
  double yaw_offset_rad{0.0};
  double pitch_offset_rad{0.0};
  double cam_to_barrel_z_m{0.0};
};

}  // namespace sp_vision_25
