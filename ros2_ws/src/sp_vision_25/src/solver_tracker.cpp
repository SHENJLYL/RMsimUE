#include "sp_vision_25/solver_tracker.hpp"

#include <algorithm>
#include <vector>

#include <opencv2/calib3d.hpp>

namespace sp_vision_25 {
namespace {

const std::vector<cv::Point3d> kSmallArmor3D = {
  {-0.0675, -0.028, 0.0},
  {+0.0675, -0.028, 0.0},
  {+0.0675, +0.028, 0.0},
  {-0.0675, +0.028, 0.0},
};

const std::vector<cv::Point3d> kBigArmor3D = {
  {-0.115, -0.028, 0.0},
  {+0.115, -0.028, 0.0},
  {+0.115, +0.028, 0.0},
  {-0.115, +0.028, 0.0},
};

}  // namespace

std::optional<cv::Vec3d> solve_armor_pnp(
  const std::array<cv::Point2f, 4> & keypoints,
  const std::string & armor_type,
  const cv::Matx33d & K,
  const cv::Mat & dist_coeffs)
{
  const auto & obj_pts = (armor_type == "big") ? kBigArmor3D : kSmallArmor3D;
  std::vector<cv::Point2d> img_pts(4);
  for (int i = 0; i < 4; ++i) {
    img_pts[i] = cv::Point2d(keypoints[i].x, keypoints[i].y);
  }
  cv::Mat dist = (dist_coeffs.total() >= 4)
    ? dist_coeffs
    : cv::Mat::zeros(5, 1, CV_64F);

  cv::Mat rvec, tvec;
  const bool ok = cv::solvePnP(obj_pts, img_pts, K, dist, rvec, tvec, false, cv::SOLVEPNP_IPPE);
  if (!ok || tvec.total() != 3) {
    return std::nullopt;
  }
  cv::Vec3d p(tvec.at<double>(0), tvec.at<double>(1), tvec.at<double>(2));
  if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2]) ||
      p[2] < 0.25 || p[2] > 18.0)
  {
    return std::nullopt;
  }
  return p;
}

std::pair<double, double> ray_to_world_angles(
  double world_yaw, double world_pitch, double u, double v)
{
  const double cy = std::cos(world_yaw);
  const double sy = std::sin(world_yaw);
  const double cp = std::cos(world_pitch);
  const double sp = std::sin(world_pitch);
  const double forward = cp - v * sp;
  const double wx = forward * cy + u * sy;
  const double wy = forward * sy - u * cy;
  const double wz = -sp - v * cp;
  return {std::atan2(wy, wx), std::atan2(-wz, std::hypot(wx, wy))};
}

std::pair<double, double> ballistic_drop_angle(
  double dist_m, double raw_pitch_rad, double muzzle_speed_mps, double g)
{
  const double d = std::clamp(dist_m, 0.4, 18.0);
  const double v0 = std::max(10.0, muzzle_speed_mps);
  const double horiz = std::max(0.3, d * std::cos(raw_pitch_rad));
  double drop_rad = 0.0;
  double tof = horiz / v0;
  for (int i = 0; i < 3; ++i) {
    const double eff_pitch = raw_pitch_rad - drop_rad;
    const double vx = std::max(5.0, v0 * std::cos(eff_pitch));
    tof = horiz / vx;
    const double drop_m = 0.5 * g * tof * tof;
    drop_rad = std::atan2(drop_m, horiz);
  }
  return {std::clamp(drop_rad, 0.0, 0.32), tof};
}

WorldAngularTracker::WorldAngularTracker(double max_lost_s)
: max_lost_s_(max_lost_s)
{
}

void WorldAngularTracker::reset()
{
  initialized_ = false;
  yaw_rate_ = 0.0;
  pitch_rate_ = 0.0;
  outpost_center_yaw_.reset();
  outpost_yaw_rate_ = 0.0;
  track_hits_ = 0;
}

std::optional<TrackedState> WorldAngularTracker::update(
  const std::optional<WorldMeasurement> & meas,
  double t_sec,
  const std::string & target_kind)
{
  if (!meas.has_value()) {
    if (!initialized_ || (t_sec - last_t_) > max_lost_s_) {
      reset();
      return std::nullopt;
    }
    const double dt = std::clamp(t_sec - last_t_, 0.0, 0.25);
    if (target_kind == "outpost") {
      const double hold_yaw = outpost_center_yaw_.value_or(yaw_);
      return TrackedState{hold_yaw, pitch_, dist_, outpost_yaw_rate_, 0.0, track_hits_};
    }
    if (target_kind == "base") {
      return TrackedState{yaw_, pitch_, dist_, 0.0, 0.0, track_hits_};
    }
    const double pred_yaw = std::atan2(
      std::sin(yaw_ + yaw_rate_ * dt * 0.5),
      std::cos(yaw_ + yaw_rate_ * dt * 0.5));
    const double pred_pitch = std::clamp(pitch_ + pitch_rate_ * dt * 0.5, -0.45, 0.45);
    return TrackedState{pred_yaw, pred_pitch, dist_, yaw_rate_ * 0.5, pitch_rate_ * 0.5, track_hits_};
  }

  const double myaw = meas->yaw;
  const double mpitch = meas->pitch;
  const double mdist = meas->dist;

  if (!initialized_ || (t_sec - last_t_) > max_lost_s_) {
    yaw_ = myaw;
    pitch_ = mpitch;
    dist_ = mdist;
    yaw_rate_ = 0.0;
    pitch_rate_ = 0.0;
    if (target_kind == "outpost") {
      outpost_center_yaw_ = myaw;
    } else {
      outpost_center_yaw_.reset();
    }
    outpost_yaw_rate_ = 0.0;
    initialized_ = true;
    last_t_ = t_sec;
    track_hits_ = 1;
    return TrackedState{yaw_, pitch_, dist_, 0.0, 0.0, track_hits_};
  }

  const double dt = std::clamp(t_sec - last_t_, 0.015, 0.30);
  const double dyaw = wrap_angle(myaw - yaw_);
  const double dpitch = mpitch - pitch_;
  const double ang_jump = std::hypot(dyaw, dpitch);

  if (target_kind == "outpost") {
    if (!outpost_center_yaw_.has_value()) {
      outpost_center_yaw_ = myaw;
    } else {
      const double cdiff = wrap_angle(myaw - *outpost_center_yaw_);
      if (std::abs(cdiff) < 0.35) {
        const double next_c = *outpost_center_yaw_ + 0.12 * cdiff;
        outpost_center_yaw_ = std::atan2(std::sin(next_c), std::cos(next_c));
      } else {
        outpost_center_yaw_ = myaw;
      }
    }

    const double max_phys_rate = std::min(0.65, 1.15 / std::max(1.0, mdist));
    const bool plate_switched = (std::abs(dyaw) >= 0.075) || (std::abs(dpitch) >= 0.032);
    if (!plate_switched && dt <= 0.16) {
      const double raw_yr = dyaw / dt;
      if (std::abs(raw_yr) <= max_phys_rate * 1.4) {
        const double clamped_yr = std::clamp(raw_yr, -max_phys_rate, max_phys_rate);
        outpost_yaw_rate_ = 0.60 * outpost_yaw_rate_ + 0.40 * clamped_yr;
      }
    } else if (ang_jump > 0.45) {
      outpost_yaw_rate_ = 0.0;
      track_hits_ = 0;
    } else if (plate_switched) {
      // Plate switched to the next rotor blade at a different height; keep learned spin rate
      // but reset track_hits so the gimbal settles onto the new blade before firing.
      track_hits_ = 0;
    }

    yaw_ = myaw;
    pitch_ = mpitch;
    dist_ = 0.4 * dist_ + 0.6 * mdist;
    yaw_rate_ = outpost_yaw_rate_;
    pitch_rate_ = 0.0;
    last_t_ = t_sec;
    ++track_hits_;
    return TrackedState{yaw_, pitch_, dist_, outpost_yaw_rate_, 0.0, track_hits_};
  }

  if (ang_jump > 0.45) {
    yaw_ = myaw;
    pitch_ = mpitch;
    dist_ = mdist;
    yaw_rate_ = 0.0;
    pitch_rate_ = 0.0;
    last_t_ = t_sec;
    track_hits_ = 1;
    return TrackedState{yaw_, pitch_, dist_, 0.0, 0.0, track_hits_};
  }

  double alpha = 1.0;
  if (target_kind == "base") {
    yaw_rate_ = 0.0;
    pitch_rate_ = 0.0;
    alpha = 0.85;
  } else {
    const double raw_yr = std::clamp(dyaw / dt, -1.6, 1.6);
    const double raw_pr = std::clamp(dpitch / dt, -0.8, 0.8);
    yaw_rate_ = 0.55 * yaw_rate_ + 0.45 * raw_yr;
    pitch_rate_ = 0.55 * pitch_rate_ + 0.45 * raw_pr;
    alpha = 1.0;
  }

  const double next_yaw = yaw_ + alpha * dyaw;
  yaw_ = std::atan2(std::sin(next_yaw), std::cos(next_yaw));
  pitch_ = pitch_ + alpha * dpitch;
  dist_ = 0.4 * dist_ + 0.6 * mdist;
  last_t_ = t_sec;
  ++track_hits_;
  return TrackedState{yaw_, pitch_, dist_, yaw_rate_, pitch_rate_, track_hits_};
}

BallisticAimer::BallisticAimer(
  double muzzle_speed_mps,
  double gravity,
  double latency_s_in,
  double shoot_tol_rad_in,
  double structure_shoot_tol_rad_in,
  double yaw_offset_rad_in,
  double pitch_offset_rad_in,
  double cam_to_barrel_z_m_in)
: v0(muzzle_speed_mps),
  g(gravity),
  latency_s(latency_s_in),
  shoot_tol_rad(shoot_tol_rad_in),
  structure_shoot_tol_rad(structure_shoot_tol_rad_in),
  yaw_offset_rad(yaw_offset_rad_in),
  pitch_offset_rad(pitch_offset_rad_in),
  cam_to_barrel_z_m(cam_to_barrel_z_m_in)
{
}

AimSolution BallisticAimer::solve_world(
  double world_yaw,
  double world_pitch,
  double dist_m,
  double yaw_rate,
  double pitch_rate,
  double cur_yaw,
  double cur_pitch,
  int track_hits,
  const std::string & target_kind) const
{
  const auto [drop_rad, tof] = ballistic_drop_angle(dist_m, world_pitch, v0, g);
  const double lead_t = (target_kind == "base") ? 0.0 : (tof + latency_s);
  const double max_lead = (target_kind == "outpost") ? 0.14 : 0.22;
  const double lead_yaw = std::clamp(yaw_rate * lead_t, -max_lead, max_lead);
  const double lead_pitch = (target_kind != "vehicle")
    ? 0.0
    : std::clamp(pitch_rate * lead_t, -0.10, 0.10);

  const double horiz = std::max(0.35, dist_m * std::cos(world_pitch));
  const double parallax_pitch = (std::abs(cam_to_barrel_z_m) > 1e-5)
    ? std::atan2(cam_to_barrel_z_m, horiz)
    : 0.0;

  const double raw_ty = world_yaw + lead_yaw + yaw_offset_rad;
  const double target_yaw = std::atan2(std::sin(raw_ty), std::cos(raw_ty));
  const double target_pitch = std::clamp(
    world_pitch + lead_pitch - drop_rad - parallax_pitch + pitch_offset_rad, -0.45, 0.45);

  const double err_yaw = wrap_angle(target_yaw - cur_yaw);
  const double err_pitch = target_pitch - cur_pitch;
  const bool is_outpost = (target_kind == "outpost");
  const bool is_base = (target_kind == "base");
  const int min_hits = is_base ? 1 : 2;
  // Tighter precision gate on rotating 13.5x5.6cm outpost plates so shots land instead of spraying wide.
  const double tol_yaw = is_outpost ? std::min(structure_shoot_tol_rad, 0.048)
    : (is_base ? structure_shoot_tol_rad : shoot_tol_rad);
  const double tol_pitch = is_outpost ? std::min(structure_shoot_tol_rad, 0.036)
    : (is_base ? structure_shoot_tol_rad : shoot_tol_rad);
  const bool can_shoot = (track_hits >= min_hits) &&
    (std::abs(err_yaw) <= tol_yaw) && (std::abs(err_pitch) <= tol_pitch);

  AimSolution sol;
  sol.locked = true;
  sol.shoot = can_shoot;
  sol.target_yaw = target_yaw;
  sol.target_pitch = target_pitch;
  sol.err_yaw = err_yaw;
  sol.err_pitch = err_pitch;
  sol.distance_m = dist_m;
  sol.tof_s = tof;
  sol.pos_cam = cv::Vec3d(0.0, 0.0, dist_m);
  return sol;
}

AimSolution BallisticAimer::solve_cam(
  const cv::Vec3d & pos_cam,
  double cur_yaw,
  double cur_pitch,
  int track_hits,
  const std::string & target_kind) const
{
  const double aim_z = std::max(0.1, pos_cam[2]);
  const double u = pos_cam[0] / aim_z;
  const double v = pos_cam[1] / aim_z;
  const auto [wyaw, wpitch] = ray_to_world_angles(cur_yaw, cur_pitch, u, v);
  const double dist = cv::norm(pos_cam);
  return solve_world(wyaw, wpitch, dist, 0.0, 0.0, cur_yaw, cur_pitch, track_hits, target_kind);
}

}  // namespace sp_vision_25
