#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "behaviortree_cpp/decorator_node.h"
#include "behaviortree_ros2/plugins.hpp"
#include "dji_referee_protocol/msg/allowed_shoot.hpp"
#include "dji_referee_protocol/msg/constants.hpp"
#include "dji_referee_protocol/msg/damage_state.hpp"
#include "dji_referee_protocol/msg/field_event.hpp"
#include "dji_referee_protocol/msg/game_status.hpp"
#include "dji_referee_protocol/msg/rfid_status.hpp"
#include "dji_referee_protocol/msg/robot_heat.hpp"
#include "dji_referee_protocol/msg/robot_hp.hpp"
#include "dji_referee_protocol/msg/robot_performance.hpp"
#include "dji_referee_protocol/msg/robot_position.hpp"
#include "dji_referee_protocol/msg/self_color.hpp"
#include "dji_referee_protocol/msg/sentry_decision_sync.hpp"
#include "example_interfaces/msg/float32.hpp"
#include "geometry_msgs/msg/twist.hpp"
#include "nlohmann/json.hpp"
#include "pb_rm_interfaces/msg/gimbal_cmd.hpp"
#include "std_msgs/msg/int32.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_msgs/msg/u_int32.hpp"

namespace
{
using Json = nlohmann::json;
using WallClock = std::chrono::steady_clock;

constexpr double kPi = 3.14159265358979323846;
// Enemy home supply safety boundary (meters in map frame).
// Allow advancing up to +/-8.0m to engage AFK/overheated/overpowered enemies across the enemy half,
// while staying clear of the deep enemy base/supply corner (>9.5m).
constexpr double kEnemyRampBoundaryX = 9.5;
constexpr double kChaseClampX = 8.0;
constexpr double kChaseClampY = 5.0;
constexpr double kChaseHoldDistM = 3.2;
constexpr double kChaseStandoffM = 2.4;

// Official RMUC 2027 outpost armor center in ROS map frame (m)
constexpr double kRedOutpostX = 3.10;
constexpr double kRedOutpostY = 3.89;
constexpr double kOutpostArmorZ = 1.49;
constexpr double kHighlandMuzzleZ = 0.72;  // highland z=0.30 + sentry muzzle height ~0.42
constexpr double kRedBaseX = 11.50;
constexpr double kRedBaseY = 0.0;

// Enemy memory extrapolation parameters
constexpr double kMemoryExpireSec = 18.0;
constexpr double kMemoryScanSec = 12.0;
constexpr double kMemoryDecayTauSec = 2.5;

double WrapAngle(double a)
{
  return std::atan2(std::sin(a), std::cos(a));
}

enum class Mode : std::size_t
{
  RefereeUnavailable, Wait, Inactive, NavigationUnavailable, Patrol, OwnOutpostHeal, OwnBaseSupply,
  OwnOutpostAttack, OwnOutpostRebuild, EnemyOutpostAttack, EnemyOutpostCapture, EnemyFortress,
  EnemyBaseAttack, OwnFortress, ChaseEnemy
};

constexpr std::array ModeNames{
  "referee_unavailable", "wait", "inactive", "navigation_unavailable", "patrol",
  "own_outpost_heal", "own_base_supply",
  "own_outpost_attack", "own_outpost_rebuild", "enemy_outpost_attack", "enemy_outpost_capture",
  "enemy_fortress", "enemy_base_attack", "own_fortress", "chase_enemy"
};
constexpr auto Index(Mode mode) { return static_cast<std::size_t>(mode); }
constexpr bool HasRoute(Mode mode) { return Index(mode) >= Index(Mode::Patrol); }
constexpr bool HasStaticPort(Mode mode)
{
  return Index(mode) >= Index(Mode::Patrol) && mode != Mode::ChaseEnemy;
}
constexpr bool CanEngageHold(Mode mode)
{
  return mode == Mode::Patrol || mode == Mode::OwnOutpostAttack ||
    mode == Mode::EnemyOutpostAttack || mode == Mode::EnemyBaseAttack || mode == Mode::ChaseEnemy;
}

struct Referee
{
  int progress;
  int hp;
  int max_hp;
  int armor_id;
  int hp_deduction_reason;
  bool is_hp_deduced;
  double body_yaw_rad;
  int ammo;
  int heat;
  int heat_limit;
  std::string stance;
  double stance_cooldown_s;
  double power_limit_w;
  double buffer_energy_j;
  int opponent_hp;
  std::array<bool, 6> enemy_alive;
  int own_base_hp;
  int own_outpost_hp;
  int enemy_base_hp;
  int enemy_outpost_hp;
  bool shooter_locked;
  bool own_outpost_available;
  bool own_outpost_active;
  bool own_outpost_card;
  bool enemy_outpost_card;
  bool own_outpost_controlled;
  bool own_outpost_enemy_owned;
  bool own_outpost_enemy_controlled;
  bool enemy_outpost_hostile;
  bool enemy_base_protected;
  bool enemy_base_open;
  bool own_fortress_available;
  bool own_fortress_active;
  bool enemy_fortress_available;
  bool enemy_fortress_active;
  bool supply_active;
  bool local_ammo_available;
  bool remote_ammo_available;
  bool remote_ammo_pending;

  static Referee Decode(const Json & data, bool red)
  {
    const auto & game = data.at("game_status");
    const auto & robot = data.at("robot_status");
    const auto & sites = data.at("field_devices");
    const auto & own = sites.at(red ? "red" : "blue");
    const auto & enemy = sites.at(red ? "blue" : "red");
    const auto & rfid = data.at("rfid_status");
    const char * outpost_card = red ? "outpost_card_red_occupied" : "outpost_card_blue_occupied";
    const int own_post_hp = own.at("outpost_hp");
    const bool own_outpost_enemy_owned = own.at("outpost_owner_red") != red;
    const bool full_outpost_control = own.at("outpost_control_full");
    const bool own_outpost_controlled =
      own.at("outpost_controller_red") == red && full_outpost_control;
    const std::string stance = data.value("sentry_stance", std::string("MOVE"));
    const double stance_cooldown_s = data.value("stance_cooldown_seconds", 0.0);
    const double default_power = (stance == "MOVE") ? 150.0 : 100.0;
    const double power_limit = data.value("chassis_power_limit_w", default_power);
    const auto & all_hp = data.at("all_robot_hp");
    const std::string ep = red ? "blue_" : "red_";
    const std::array<bool, 6> enemy_alive{
      all_hp.value(ep + "1_robot_hp", 0) > 0,
      all_hp.value(ep + "2_robot_hp", 0) > 0,
      all_hp.value(ep + "3_robot_hp", 0) > 0,
      all_hp.value(ep + "4_robot_hp", 0) > 0,
      all_hp.value(ep + "5_robot_hp", 0) > 0,
      all_hp.value(ep + "7_robot_hp", 0) > 0
    };
    return {
      game.at("game_progress"), robot.at("current_hp"), robot.at("maximum_hp"),
      robot.value("armor_id", 0), robot.value("hp_deduction_reason", 0), robot.value("is_hp_deduced", false),
      robot.value("yaw_rad", 0.0),
      robot.at("projectile_allowance_17mm"), robot.at("shooter_17mm_1_barrel_heat"),
      robot.at("shooter_barrel_heat_limit"),
      stance,
      stance_cooldown_s,
      power_limit,
      data.value("chassis_buffer_energy_j", 60.0),
      all_hp.at(red ? "blue_7_robot_hp" : "red_7_robot_hp"),
      enemy_alive,
      own.at("base_hp"), own_post_hp, enemy.at("base_hp"), enemy.at("outpost_hp"),
      data.value("shooter_locked", true),
      own_post_hp > 0 && !own_outpost_enemy_owned && own_outpost_controlled &&
        own.value("can_heal_at_own_outpost", true),
      rfid.at("friendly_outpost_gain_point"),
      own.value(outpost_card, false), enemy.value(outpost_card, false),
      own_outpost_controlled,
      own_outpost_enemy_owned, full_outpost_control && !own_outpost_controlled,
      enemy.at("outpost_owner_red") != red, enemy.at("base_protected"), enemy.at("base_armor_open"),
      own.value("own_fortress_available", false), own.value("fortress_active", false),
      own.value("enemy_fortress_available", false), own.value("enemy_fortress_active", false),
      own.at("supply_active"), own.at("local_ammo_purchase_available"),
      own.value("remote_ammo_purchase_available", false), own.value("remote_ammo_purchase_pending", false)
    };
  }
};

struct EnemyMemorySlot
{
  bool valid = false;
  double last_x = 0.0;
  double last_y = 0.0;
  double vx = 0.0;
  double vy = 0.0;
  WallClock::time_point last_seen{};

  std::pair<double, double> Predict(double age_sec) const
  {
    const double t = std::max(0.0, age_sec);
    // Damped constant-velocity extrapolation: predicts direction of motion while saturating cleanly
    const double eff_dt = kMemoryDecayTauSec * (1.0 - std::exp(-t / kMemoryDecayTauSec));
    const double px = std::clamp(last_x + vx * eff_dt, -12.5, 12.5);
    const double py = std::clamp(last_y + vy * eff_dt, -6.5, 6.5);
    return {px, py};
  }
};

struct MemoryPrediction
{
  bool valid = false;
  int label = -1;
  double pred_x = 0.0;
  double pred_y = 0.0;
  double age_sec = 999.0;
};

struct VisionInfo
{
  bool enemy_visible = false;
  bool has_enemy_xy = false;
  double enemy_x = 0.0;
  double enemy_y = 0.0;
  double distance_m = 99.0;
  bool has_self_xy = false;
  double self_x = 0.0;
  double self_y = 0.0;
  MemoryPrediction memory{};
};

std::optional<bool> CardContact(Mode mode, const Referee & ref)
{
  switch (mode) {
    case Mode::OwnOutpostHeal: return ref.own_outpost_active;
    case Mode::OwnOutpostRebuild: return ref.own_outpost_card;
    case Mode::OwnBaseSupply: return ref.supply_active;
    case Mode::EnemyOutpostCapture: return ref.enemy_outpost_card;
    case Mode::OwnFortress: return ref.own_fortress_active;
    case Mode::EnemyFortress: return ref.enemy_fortress_active;
    default: return std::nullopt;
  }
}

struct Inputs
{
  std::mutex mutex;
  bool side_auto = true;
  bool side_red = true;
  bool side_known = false;
  // Primary native dji_referee_protocol state (works identically in simulator AND real robot!)
  bool dji_referee_seen = false;
  WallClock::time_point dji_referee_received{};
  Referee dji_referee{
    0, 400, 400, 0, 0, false, 0.0, 300, 0, 260, "MOVE", 0.0, 150.0, 60.0, 400,
    {true, true, true, true, true, true},
    10000, 1000, 10000, 1000, false, true, false, false, false, true, false, false,
    true, true, false, false, false, false, false, false, false, false, false
  };
  bool dji_damage_pending = false;
  int dji_gold = 800;
  // Optional simulator-only enrichment overlay (/rmsim/referee/state)
  std::optional<Referee> sim_referee;
  WallClock::time_point referee_received{};
  bool nav_topic_seen = false;
  bool nav_ready = false;
  WallClock::time_point nav_received{};
  bool enemy_vehicle_visible = false;
  WallClock::time_point enemy_vehicle_seen{};
  bool has_enemy_xy = false;
  double enemy_x = 0.0;
  double enemy_y = 0.0;
  double enemy_distance_m = 99.0;
  bool has_self_xy = false;
  double self_x = 0.0;
  double self_y = 0.0;
  std::array<EnemyMemorySlot, 6> enemy_memory{};
};

class Tactics : public BT::DecoratorNode
{
public:
  Tactics(const std::string & name, const BT::NodeConfig & config, const BT::RosNodeParams & params)
  : BT::DecoratorNode(name, config), node_(params.nh.lock())
  {
    if (!node_) {
      throw BT::RuntimeError("RMUC2027Tactics needs the runner ROS node");
    }
    const auto side = getInput<std::string>("side");
    if (side && (*side == "red" || *side == "blue")) {
      is_red_ = (*side == "red");
      side_auto_ = false;
      inputs_->side_auto = false;
      inputs_->side_red = is_red_;
      inputs_->side_known = true;
    } else {
      is_red_ = true;
      side_auto_ = true;
      inputs_->side_auto = true;
      inputs_->side_red = true;
      inputs_->side_known = false;
    }
    LoadRoutesForSide(is_red_, true);

    const auto qos = rclcpp::QoS(1).reliable();
    const auto dji_qos = rclcpp::QoS(5).reliable();

    // 1. Native dji_referee_protocol subscriptions (EXACT same topics in simulator and real robot!)
    dji_color_ = node_->create_subscription<dji_referee_protocol::msg::SelfColor>(
      "/referee/parsed/common/self_color", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::SelfColor & msg) {
        if (msg.color != dji_referee_protocol::msg::Constants::COLOR_RED &&
            msg.color != dji_referee_protocol::msg::Constants::COLOR_BLUE) {
          return;
        }
        std::lock_guard<std::mutex> lock(inputs->mutex);
        if (inputs->side_auto) {
          inputs->side_red = (msg.color == dji_referee_protocol::msg::Constants::COLOR_RED);
          inputs->side_known = true;
        }
      });
    dji_game_ = node_->create_subscription<dji_referee_protocol::msg::GameStatus>(
      "/referee/common/game_status", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::GameStatus & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        inputs->dji_referee.progress = static_cast<int>(msg.game_progress);
      });
    dji_perf_ = node_->create_subscription<dji_referee_protocol::msg::RobotPerformance>(
      "/referee/common/robot_performance", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::RobotPerformance & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        if (inputs->side_auto && msg.robot_id > 0) {
          inputs->side_red = (msg.robot_id < 100);
          inputs->side_known = true;
        }
        inputs->dji_referee.hp = static_cast<int>(msg.current_hp);
        inputs->dji_referee.max_hp = std::max(1, static_cast<int>(msg.maximum_hp));
        inputs->dji_referee.heat_limit = static_cast<int>(msg.shooter_barrel_heat_limit);
        if (msg.chassis_power_limit > 0) {
          inputs->dji_referee.power_limit_w = static_cast<double>(msg.chassis_power_limit);
        }
        inputs->dji_referee.shooter_locked =
          (msg.current_hp == 0) || (!msg.power_management_shooter_output);
      });
    dji_hp_ = node_->create_subscription<dji_referee_protocol::msg::RobotHP>(
      "/referee/common/robot_hp", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::RobotHP & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        const bool red = inputs->side_red;
        inputs->dji_referee.enemy_alive = {
          (red ? msg.blue_1_robot_hp : msg.red_1_robot_hp) > 0,
          (red ? msg.blue_2_robot_hp : msg.red_2_robot_hp) > 0,
          (red ? msg.blue_3_robot_hp : msg.red_3_robot_hp) > 0,
          (red ? msg.blue_4_robot_hp : msg.red_4_robot_hp) > 0,
          true,  // 2026 RobotHP omits robot 5; keep 5-armor tracking enabled
          (red ? msg.blue_7_robot_hp : msg.red_7_robot_hp) > 0
        };
        inputs->dji_referee.opponent_hp = static_cast<int>(red ? msg.blue_7_robot_hp : msg.red_7_robot_hp);
        inputs->dji_referee.own_base_hp = static_cast<int>(red ? msg.red_base_hp : msg.blue_base_hp);
        inputs->dji_referee.own_outpost_hp = static_cast<int>(red ? msg.red_outpost_hp : msg.blue_outpost_hp);
        inputs->dji_referee.enemy_base_hp = static_cast<int>(red ? msg.blue_base_hp : msg.red_base_hp);
        inputs->dji_referee.enemy_outpost_hp = static_cast<int>(red ? msg.blue_outpost_hp : msg.red_outpost_hp);
        inputs->dji_referee.own_outpost_available = (inputs->dji_referee.own_outpost_hp > 0);
        inputs->dji_referee.own_outpost_controlled = (inputs->dji_referee.own_outpost_hp > 0);
        inputs->dji_referee.enemy_outpost_hostile = (inputs->dji_referee.enemy_outpost_hp > 0);
        inputs->dji_referee.enemy_base_protected = (inputs->dji_referee.enemy_outpost_hp > 0);
        inputs->dji_referee.own_fortress_available = (inputs->dji_referee.own_outpost_hp == 0);
        for (std::size_t idx = 0; idx < 6; ++idx) {
          if (!inputs->dji_referee.enemy_alive[idx]) {
            inputs->enemy_memory[idx].valid = false;
          }
        }
      });
    dji_heat_ = node_->create_subscription<dji_referee_protocol::msg::RobotHeat>(
      "/referee/common/robot_heat", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::RobotHeat & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        inputs->dji_referee.heat = static_cast<int>(msg.shooter_17mm_barrel_heat);
        inputs->dji_referee.buffer_energy_j = static_cast<double>(msg.buffer_energy);
      });
    dji_ammo_ = node_->create_subscription<dji_referee_protocol::msg::AllowedShoot>(
      "/referee/common/allowed_shoot", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::AllowedShoot & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        inputs->dji_referee.ammo = static_cast<int>(msg.projectile_allowance_17mm);
        inputs->dji_gold = static_cast<int>(msg.remaining_gold_coin);
        inputs->dji_referee.local_ammo_available = (inputs->dji_gold >= 10);
      });
    dji_rfid_ = node_->create_subscription<dji_referee_protocol::msg::RFIDStatus>(
      "/referee/common/rfid_status", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::RFIDStatus & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        const uint32_t bits = msg.detected_rfid_bits_low;
        inputs->dji_referee.own_fortress_active = ((bits & (1u << 17)) != 0u);
        inputs->dji_referee.own_outpost_active = ((bits & (1u << 18)) != 0u);
        inputs->dji_referee.own_outpost_card = inputs->dji_referee.own_outpost_active;
        inputs->dji_referee.supply_active = ((bits & ((1u << 19) | (1u << 20))) != 0u);
      });
    dji_field_ = node_->create_subscription<dji_referee_protocol::msg::FieldEvent>(
      "/referee/common/field_event", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::FieldEvent & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        if (msg.supply_area_1) inputs->dji_referee.supply_active = true;
        if (msg.fortress_buff_point != 0) inputs->dji_referee.own_fortress_active = true;
        if (msg.outpost_buff_point != 0) inputs->dji_referee.own_outpost_active = true;
      });
    dji_damage_ = node_->create_subscription<dji_referee_protocol::msg::DamageState>(
      "/referee/common/damage_state", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::DamageState & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        inputs->dji_referee.armor_id = static_cast<int>(msg.armor_id);
        inputs->dji_referee.hp_deduction_reason = static_cast<int>(msg.damage_type);
        inputs->dji_damage_pending = true;
      });
    dji_pos_ = node_->create_subscription<dji_referee_protocol::msg::RobotPosition>(
      "/referee/common/robot_position", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::RobotPosition & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        inputs->dji_referee.body_yaw_rad = WrapAngle((90.0 - static_cast<double>(msg.angle)) * kPi / 180.0);
      });
    dji_sentry_sync_ = node_->create_subscription<dji_referee_protocol::msg::SentryDecisionSync>(
      "/referee/common/sentry_decision_sync", dji_qos,
      [inputs = inputs_](const dji_referee_protocol::msg::SentryDecisionSync & msg) {
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->dji_referee_seen = true;
        inputs->dji_referee_received = WallClock::now();
        if (msg.sentry_posture == dji_referee_protocol::msg::Constants::SENTRY_POSTURE_ATTACK) {
          inputs->dji_referee.stance = "ATTACK";
        } else if (msg.sentry_posture == dji_referee_protocol::msg::Constants::SENTRY_POSTURE_DEFENSE) {
          inputs->dji_referee.stance = "DEFENSE";
        } else if (msg.sentry_posture == dji_referee_protocol::msg::Constants::SENTRY_POSTURE_MOVE) {
          inputs->dji_referee.stance = "MOVE";
        }
        inputs->dji_referee.remote_ammo_available =
          msg.in_disengage && (msg.remaining_ammo_exchange >= 100) && (inputs->dji_gold >= 150);
      });

    // 2. Optional simulator-only enrichment overlay (/rmsim/referee/state) for RMUC2027 outpost-capture extras
    referee_ = node_->create_subscription<std_msgs::msg::String>(
      "/rmsim/referee/state", qos,
      [inputs = inputs_](const std_msgs::msg::String & msg) {
        try {
          const auto data = Json::parse(msg.data);
          std::lock_guard<std::mutex> lock(inputs->mutex);
          const bool msg_red = data.at("friendly_red").get<bool>();
          if (inputs->side_auto) {
            inputs->side_red = msg_red;
            inputs->side_known = true;
          } else if (msg_red != inputs->side_red) {
            return;
          }
          if (data.at("available") == true) {
            inputs->sim_referee = Referee::Decode(data, inputs->side_red);
            inputs->referee_received = WallClock::now();
            for (std::size_t idx = 0; idx < 6; ++idx) {
              if (!inputs->sim_referee->enemy_alive[idx]) {
                inputs->enemy_memory[idx].valid = false;
              }
            }
          } else {
            inputs->sim_referee.reset();
          }
        } catch (const Json::exception &) {
          std::lock_guard<std::mutex> lock(inputs->mutex);
          inputs->sim_referee.reset();
        }
      });
    nav_ = node_->create_subscription<std_msgs::msg::String>(
      "tactical_navigation_status", qos, [inputs = inputs_](const std_msgs::msg::String & msg) {
        const auto data = Json::parse(msg.data, nullptr, false);
        const bool active = data.is_object() && data.contains("phase") &&
          data["phase"] == "running" && data.contains("localization_map_verified") &&
          data["localization_map_verified"] == true && data.contains("initial_pose_sent") &&
          data["initial_pose_sent"] == true;
        std::lock_guard<std::mutex> lock(inputs->mutex);
        inputs->nav_topic_seen = true;
        inputs->nav_ready = active;
        inputs->nav_received = WallClock::now();
      });
    vision_track_ = node_->create_subscription<std_msgs::msg::String>(
      "tactical_vision_track", qos, [inputs = inputs_](const std_msgs::msg::String & msg) {
        const auto data = Json::parse(msg.data, nullptr, false);
        if (!data.is_object()) return;
        std::lock_guard<std::mutex> lock(inputs->mutex);
        if (data.contains("self_x") && data["self_x"].is_number() &&
            data.contains("self_y") && data["self_y"].is_number()) {
          inputs->has_self_xy = true;
          inputs->self_x = data["self_x"].get<double>();
          inputs->self_y = data["self_y"].get<double>();
        }
        const bool visible = data.value("visible", false);
        const int label = (data.contains("label") && data["label"].is_number_integer())
          ? data["label"].get<int>() : -1;
        // Support all fielded enemy vehicles (1-5 and 7 sentry), gated by referee alive HP
        const auto & alive_arr = inputs->sim_referee ? inputs->sim_referee->enemy_alive : inputs->dji_referee.enemy_alive;
        const bool has_ref = inputs->sim_referee.has_value() || inputs->dji_referee_seen;
        const bool is_vehicle = (label >= 0 && label <= 5) &&
          (!has_ref || alive_arr[static_cast<std::size_t>(label)]);
        if (visible && is_vehicle) {
          const auto seen_now = WallClock::now();
          inputs->enemy_vehicle_visible = true;
          inputs->enemy_vehicle_seen = seen_now;
          if (data.contains("distance_m") && data["distance_m"].is_number()) {
            inputs->enemy_distance_m = data["distance_m"].get<double>();
          }
          if (data.contains("enemy_x") && data["enemy_x"].is_number() &&
              data.contains("enemy_y") && data["enemy_y"].is_number()) {
            const double ex = data["enemy_x"].get<double>();
            const double ey = data["enemy_y"].get<double>();
            const double evx = (data.contains("enemy_vx") && data["enemy_vx"].is_number())
              ? std::clamp(data["enemy_vx"].get<double>(), -4.0, 4.0) : 0.0;
            const double evy = (data.contains("enemy_vy") && data["enemy_vy"].is_number())
              ? std::clamp(data["enemy_vy"].get<double>(), -4.0, 4.0) : 0.0;
            inputs->has_enemy_xy = true;
            inputs->enemy_x = ex;
            inputs->enemy_y = ey;
            auto & slot = inputs->enemy_memory[static_cast<std::size_t>(label)];
            slot.valid = true;
            slot.last_x = ex;
            slot.last_y = ey;
            slot.vx = evx;
            slot.vy = evy;
            slot.last_seen = seen_now;
          }
        } else {
          inputs->enemy_vehicle_visible = false;
        }
      });
    spin_ = node_->create_publisher<example_interfaces::msg::Float32>("cmd_spin", qos);
    stop_ = node_->create_publisher<geometry_msgs::msg::Twist>("cmd_vel", qos);
    scan_ = node_->create_publisher<pb_rm_interfaces::msg::GimbalCmd>("gimbal_scan_cmd", qos);
    aim_ = node_->create_publisher<std_msgs::msg::Int32>("auto_aim_switch", qos);
    fire_ = node_->create_publisher<std_msgs::msg::Int32>("tactical_fire_permit", qos);
    priority_ = node_->create_publisher<std_msgs::msg::String>("tactical_target_priority", qos);
    ammo_ = node_->create_publisher<std_msgs::msg::UInt32>("tactical_ammo_purchase", qos);
    stance_ = node_->create_publisher<std_msgs::msg::String>("tactical_sentry_stance", qos);
    state_ = node_->create_publisher<std_msgs::msg::String>("tactical_state", qos);
  }

  static BT::PortsList providedPorts()
  {
    BT::PortsList ports{
      BT::InputPort<std::string>("side"),
      BT::OutputPort<std::string>("route")
    };
    for (std::size_t i = Index(Mode::Patrol); i < ModeNames.size(); ++i) {
      if (HasStaticPort(static_cast<Mode>(i))) {
        ports.insert(BT::InputPort<std::string>(ModeNames[i]));
      }
    }
    return ports;
  }

  BT::NodeStatus tick() override
  {
    const double now = node_->now().seconds();
    const auto [ref, nav_ready, vision] = ReadInputs();
    const bool clock_reset = now < last_tick_;
    if (clock_reset) mode_.reset();
    if (clock_reset || (ref && ref->progress != 4)) {
      retry_at_.fill(0.0);
      next_purchase_ = 0.0;
      patrol_waypoint_ = 0;
      healing_ = false;
      reloading_ = false;
      heat_throttled_ = false;
      power_throttled_ = false;
      spin_boost_ = false;
      engaging_ = false;
      last_own_outpost_hp_ = -1;
      own_outpost_attacked_until_ = 0.0;
      last_self_hp_ = -1;
      self_under_attack_until_ = 0.0;
      last_stance_cmd_.clear();
      last_stance_cmd_at_ = -100.0;
      chase_route_.clear();
      chase_updated_at_ = 0.0;
      chase_entered_at_ = -100.0;
      chase_exited_at_ = -100.0;
      scan_phase_ = 0.0;
    }
    const double dt = (last_tick_ > 0.0 && now >= last_tick_) ? std::min(0.25, now - last_tick_) : 0.05;
    last_tick_ = now;

    if (ref) {
      if (last_own_outpost_hp_ > 0 && ref->own_outpost_hp < last_own_outpost_hp_ &&
          !ref->own_outpost_enemy_owned) {
        own_outpost_attacked_until_ = now + 6.0;
      }
      last_own_outpost_hp_ = ref->own_outpost_hp;

      // Legal 0x0206 referee hurt detection: ignore self rule penalties (3=overheat, 4=overpower)
      const bool armor_hurt = (ref->hp_deduction_reason == 0) &&
        ((last_self_hp_ > 0 && ref->hp < last_self_hp_) || ref->is_hp_deduced);
      if (armor_hurt) {
        const bool fresh_attack = (now >= self_under_attack_until_ - 3.5);
        self_under_attack_until_ = now + 5.0;
        static constexpr std::array<double, 4> kArmorYawOffset{0.0, kPi * 0.5, kPi, -kPi * 0.5};
        const int aid = std::clamp(ref->armor_id, 0, 3);
        const double next_yaw = WrapAngle(ref->body_yaw_rad + kArmorYawOffset[static_cast<std::size_t>(aid)]);
        if (fresh_attack || std::abs(WrapAngle(next_yaw - hurt_search_yaw_)) > 0.6) {
          scan_phase_ = 0.0;  // Snap dead-center onto hit armor first before sweeping!
        }
        hurt_search_yaw_ = next_yaw;
      }
      last_self_hp_ = ref->hp;

      // Heat safety hysteresis: stop 35 below limit (225/260), resume 60 below (200/260)
      heat_throttled_ = heat_throttled_
        ? (ref->heat + 60 > ref->heat_limit)
        : (ref->heat + 35 >= ref->heat_limit);
      // Chassis buffer energy guard: drop spin if buffer < 20 J, resume >= 40 J
      power_throttled_ = power_throttled_
        ? (ref->buffer_energy_j < 40.0)
        : (ref->buffer_energy_j < 20.0);
      // Stationary supercap spin boost hysteresis: burst at 9.5 rad/s when buffer > 38 J, settle back < 25 J
      spin_boost_ = spin_boost_
        ? (ref->buffer_energy_j > 25.0)
        : (ref->buffer_energy_j > 38.0);
    }

    const Mode next = Select(ref, nav_ready, vision, now);
    if (!mode_ || *mode_ != next) {
      if (mode_ && *mode_ == Mode::ChaseEnemy) chase_exited_at_ = now;
      if (next == Mode::ChaseEnemy) chase_entered_at_ = now;
      resetChild();
      stop_->publish(geometry_msgs::msg::Twist());
      mode_ = next;
      arrived_.reset();
      left_card_.reset();
      arrived_on_card_ = false;
      engaging_ = false;
      objective_hp_ = -1;
      progress_at_ = now;
      if (next == Mode::ChaseEnemy) {
        UpdateChaseRoute(vision, now, true);
      } else if (next == Mode::Patrol && vision.memory.valid) {
        SelectMemoryPatrolWaypoint(vision.memory);
      }
      active_route_ = RouteFor(next);
      RCLCPP_INFO(node_->get_logger(), "RMUC_TACTICS mode=%s route=%s",
        ModeNames[Index(next)], active_route_.c_str());
    } else if (next == Mode::ChaseEnemy && UpdateChaseRoute(vision, now, false)) {
      resetChild();
      arrived_.reset();
      active_route_ = chase_route_;
    }

    const auto contact = ref ? CardContact(next, *ref) : std::nullopt;
    if (contact.has_value()) UpdateCardArrival(next, *contact, now);

    const bool can_fire_now = ref && !ref->shooter_locked && !heat_throttled_ &&
      (ref->ammo > 0 || ref->own_fortress_active);
    const bool enemy_in_home = EnemyInHomeZone(vision);
    const bool hold_in_place =
      (next == Mode::ChaseEnemy && vision.enemy_visible && vision.distance_m <= kChaseHoldDistM) ||
      (next != Mode::ChaseEnemy && CanEngageHold(next) && vision.enemy_visible &&
       (enemy_in_home || vision.distance_m <= kChaseHoldDistM));
    const bool want_engage = hold_in_place && can_fire_now && !healing_;

    if (want_engage && !engaging_) {
      resetChild();
      stop_->publish(geometry_msgs::msg::Twist());
      engaging_ = true;
    } else if (!want_engage && engaging_) {
      engaging_ = false;
    }

    bool navigating = HasRoute(next) && !arrived_ && !engaging_;
    if (navigating) {
      active_route_ = RouteFor(next);
      if (active_route_.empty()) {
        navigating = false;
      } else {
        const auto result = setOutput("route", active_route_);
        if (!result) {
          throw BT::RuntimeError(result.error());
        }
        const auto status = child_node_->executeTick();
        navigating = status == BT::NodeStatus::RUNNING;
        if (!navigating) {
          resetChild();
          stop_->publish(geometry_msgs::msg::Twist());
          if (status == BT::NodeStatus::SUCCESS) {
            arrived_ = now;
            progress_at_ = now;
            if (next == Mode::Patrol) {
              if (vision.memory.valid) {
                SelectMemoryPatrolWaypoint(vision.memory);
              } else {
                patrol_waypoint_ = (patrol_waypoint_ + 1) % patrol_routes_.size();
              }
            }
          } else {
            RCLCPP_WARN(node_->get_logger(), "RMUC_TACTICS navigation failed: %s",
              ModeNames[Index(next)]);
            retry_at_[Index(next)] = now + 8.0;
          }
        }
      }
    }
    if (ref && HasRoute(next)) {
      PurchaseAmmo(*ref, next, now);
      if (arrived_ && !engaging_) {
        MonitorDestination(*ref, next, contact, now);
      }
    }

    Publish(next, ref, vision, navigating, contact, dt);
    return BT::NodeStatus::RUNNING;
  }

  void halt() override
  {
    BT::DecoratorNode::halt();
    stop_->publish(geometry_msgs::msg::Twist());
    Publish(Mode::Inactive, std::nullopt, {}, false, std::nullopt, 0.05);
    mode_.reset();
  }

private:
  // Unified RMUC 2027 official field coordinates (same CAD map in simulation & real field!).
  // Automatically mirrors Red vs Blue once self_color/robot_id is known; XML ports can still override.
  void LoadRoutesForSide(bool red, bool allow_xml_override)
  {
    is_red_ = red;
    if (red) {
      routes_[Index(Mode::Patrol)] = "2.96;0.0;3.14159265|2.95;1.8;2.61|2.95;-1.8;-2.61";
      routes_[Index(Mode::OwnOutpostHeal)] = "2.84;4.59;3.14159265";
      routes_[Index(Mode::OwnBaseSupply)] = "12.0;6.0;3.14159265";
      routes_[Index(Mode::OwnOutpostAttack)] = "3.0;0.8;1.54";
      routes_[Index(Mode::OwnOutpostRebuild)] = "2.84;4.59;3.14159265";
      routes_[Index(Mode::EnemyOutpostAttack)] = "-3.0;-0.8;-1.60";
      routes_[Index(Mode::EnemyOutpostCapture)] = "-2.67;-3.21;3.14159265";
      routes_[Index(Mode::EnemyFortress)] = "-6.82;0.0;3.14159265";
      routes_[Index(Mode::EnemyBaseAttack)] = "-9.0;-0.5;2.95";
      routes_[Index(Mode::OwnFortress)] = "6.82;0.0;3.14159265";
    } else {
      routes_[Index(Mode::Patrol)] = "-2.96;0.0;0.0|-2.95;-1.8;-0.53|-2.95;1.8;0.53";
      routes_[Index(Mode::OwnOutpostHeal)] = "-2.67;-3.21;0.0";
      routes_[Index(Mode::OwnBaseSupply)] = "-12.0;-6.0;0.0";
      routes_[Index(Mode::OwnOutpostAttack)] = "-3.0;-0.8;-1.60";
      routes_[Index(Mode::OwnOutpostRebuild)] = "-2.67;-3.21;0.0";
      routes_[Index(Mode::EnemyOutpostAttack)] = "3.0;0.8;1.54";
      routes_[Index(Mode::EnemyOutpostCapture)] = "2.84;4.59;0.0";
      routes_[Index(Mode::EnemyFortress)] = "6.82;0.0;0.0";
      routes_[Index(Mode::EnemyBaseAttack)] = "9.0;0.5;-0.19";
      routes_[Index(Mode::OwnFortress)] = "-6.82;0.0;0.0";
    }
    if (allow_xml_override) {
      for (std::size_t i = Index(Mode::Patrol); i < ModeNames.size(); ++i) {
        if (!HasStaticPort(static_cast<Mode>(i))) continue;
        const auto route = getInput<std::string>(ModeNames[i]);
        if (route && !route->empty()) {
          routes_[i] = *route;
        }
      }
    }
    patrol_routes_.clear();
    patrol_xy_.clear();
    for (const auto waypoint : BT::splitString(routes_[Index(Mode::Patrol)], '|')) {
      patrol_routes_.emplace_back(waypoint);
      auto parts = BT::splitString(waypoint, ';');
      if (parts.size() >= 2) {
        patrol_xy_.emplace_back(std::stod(std::string(parts[0])), std::stod(std::string(parts[1])));
      } else {
        patrol_xy_.emplace_back(0.0, 0.0);
      }
    }
    if (patrol_routes_.empty()) {
      throw BT::RuntimeError("RMUC2027Tactics needs a nonempty patrol");
    }
    patrol_waypoint_ = 0;
  }

  bool EnemyInHomeZone(const VisionInfo & vision) const
  {
    if (!vision.has_enemy_xy) return false;
    return is_red_ ? (vision.enemy_x < -kEnemyRampBoundaryX) : (vision.enemy_x > kEnemyRampBoundaryX);
  }

  void SelectMemoryPatrolWaypoint(const MemoryPrediction & mem)
  {
    if (patrol_xy_.empty()) return;
    std::size_t best_idx = 0;
    double best_dist = 1e9;
    for (std::size_t i = 0; i < patrol_xy_.size(); ++i) {
      const double d = std::hypot(patrol_xy_[i].first - mem.pred_x, patrol_xy_[i].second - mem.pred_y);
      if (d < best_dist) {
        best_dist = d;
        best_idx = i;
      }
    }
    patrol_waypoint_ = best_idx;
  }

  bool UpdateChaseRoute(const VisionInfo & vision, double now, bool force)
  {
    if (!vision.has_enemy_xy) return false;
    if (!force && (now - chase_updated_at_ < 3.0)) return false;

    const double tx = vision.memory.valid ? vision.memory.pred_x : vision.enemy_x;
    const double ty = vision.memory.valid ? vision.memory.pred_y : vision.enemy_y;
    const double sx = vision.has_self_xy ? vision.self_x : (is_red_ ? 3.0 : -3.0);
    const double sy = vision.has_self_xy ? vision.self_y : 0.0;
    const double dx = tx - sx;
    const double dy = ty - sy;
    const double dist = std::hypot(dx, dy);

    // Never back up when already within combat range; only advance FORWARD toward enemy
    if (dist <= kChaseHoldDistM) {
      chase_route_.clear();
      return false;
    }
    const double advance = std::max(0.5, dist - kChaseStandoffM);
    const double ux = dx / dist;
    const double uy = dy / dist;
    const double gx = std::clamp(sx + ux * advance, -kChaseClampX, kChaseClampX);
    const double gy = std::clamp(sy + uy * advance, -kChaseClampY, kChaseClampY);
    const double yaw = std::atan2(ty - gy, tx - gx);

    if (!force && !chase_route_.empty() &&
        std::hypot(gx - chase_goal_x_, gy - chase_goal_y_) < 1.8) {
      return false;
    }
    chase_goal_x_ = gx;
    chase_goal_y_ = gy;
    chase_updated_at_ = now;
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(2) << gx << ';' << gy << ';' << yaw;
    chase_route_ = oss.str();
    return true;
  }

  const std::string & RouteFor(Mode mode) const
  {
    if (mode == Mode::ChaseEnemy) return chase_route_;
    return mode == Mode::Patrol ? patrol_routes_[patrol_waypoint_] : routes_[Index(mode)];
  }

  void UpdateCardArrival(Mode mode, bool contact, double now)
  {
    if (contact) {
      left_card_.reset();
      if (!arrived_on_card_) {
        if (!arrived_) {
          stop_->publish(geometry_msgs::msg::Twist());
          resetChild();
          RCLCPP_INFO(node_->get_logger(), "RMUC_TACTICS card acquired, holding: %s",
            ModeNames[Index(mode)]);
        }
        arrived_on_card_ = true;
        arrived_ = now;
        progress_at_ = now;
      }
    } else if (arrived_on_card_) {
      if (!left_card_) left_card_ = now;
      if (now - *left_card_ >= 0.65) {
        arrived_on_card_ = false;
        arrived_.reset();
      }
    }
  }

  std::tuple<std::optional<Referee>, bool, VisionInfo> ReadInputs()
  {
    const auto now = WallClock::now();
    const auto max_age = std::chrono::seconds(2);
    const auto engage_hold = std::chrono::milliseconds(1500);
    const auto chase_memory = std::chrono::milliseconds(4000);
    std::lock_guard<std::mutex> lock(inputs_->mutex);
    if (side_auto_ && inputs_->side_known && inputs_->side_red != is_red_) {
      LoadRoutesForSide(inputs_->side_red, false);
      RCLCPP_INFO(node_->get_logger(), "RMUC_TACTICS auto-selected unified field coordinates for %s",
        is_red_ ? "RED" : "BLUE");
    }
    const bool enemy_recent =
      inputs_->enemy_vehicle_visible || (now - inputs_->enemy_vehicle_seen <= engage_hold);
    const bool chase_recent =
      inputs_->enemy_vehicle_visible || (now - inputs_->enemy_vehicle_seen <= chase_memory);
    VisionInfo info;
    info.enemy_visible = enemy_recent;
    info.has_enemy_xy = chase_recent && inputs_->has_enemy_xy;
    info.enemy_x = inputs_->enemy_x;
    info.enemy_y = inputs_->enemy_y;
    info.distance_m = inputs_->enemy_distance_m;
    info.has_self_xy = inputs_->has_self_xy;
    info.self_x = inputs_->self_x;
    info.self_y = inputs_->self_y;

    // Pick freshest valid enemy unit from multi-unit memory table and extrapolate its trajectory
    double best_age = kMemoryExpireSec;
    for (int lbl = 0; lbl < 6; ++lbl) {
      const auto & slot = inputs_->enemy_memory[static_cast<std::size_t>(lbl)];
      if (!slot.valid) continue;
      const double age = std::chrono::duration<double>(now - slot.last_seen).count();
      if (age >= 0.0 && age < best_age) {
        best_age = age;
        const auto [px, py] = slot.Predict(age);
        info.memory = {true, lbl, px, py, age};
      }
    }
    std::optional<Referee> merged_ref;
    const bool sim_fresh = inputs_->sim_referee && (now - inputs_->referee_received <= max_age);
    const bool dji_fresh = inputs_->dji_referee_seen && (now - inputs_->dji_referee_received <= max_age);
    if (sim_fresh) {
      merged_ref = inputs_->sim_referee;
      if (inputs_->dji_damage_pending) {
        merged_ref->is_hp_deduced = true;
        merged_ref->armor_id = inputs_->dji_referee.armor_id;
        merged_ref->hp_deduction_reason = inputs_->dji_referee.hp_deduction_reason;
        inputs_->dji_damage_pending = false;
      }
    } else if (dji_fresh) {
      merged_ref = inputs_->dji_referee;
      merged_ref->is_hp_deduced = inputs_->dji_damage_pending;
      inputs_->dji_damage_pending = false;
    }
    // On real robot (no simulator /rmsim/{robot}/navigation topic), IsMapReady+IsTfReady+IsNav2Ready
    // already verified Nav2 is ready before ticking RMUC2027Tactics.
    const bool nav_ready = inputs_->nav_topic_seen
      ? (inputs_->nav_ready && now - inputs_->nav_received <= max_age)
      : true;
    return {merged_ref, nav_ready, info};
  }

  Mode Select(const std::optional<Referee> & ref, bool nav_ready, const VisionInfo & vision, double now)
  {
    if (!ref) return Mode::RefereeUnavailable;
    if (ref->progress != 4) return ref->progress < 4 ? Mode::Wait : Mode::Inactive;
    if (ref->hp <= 0 || ref->max_hp <= 0) return Mode::Inactive;
    if (!nav_ready) return Mode::NavigationUnavailable;

    const auto ready = [&](Mode mode) { return now >= retry_at_[Index(mode)]; };
    healing_ = healing_ ? ref->hp * 100 < ref->max_hp * 85 : ref->hp <= 150;
    reloading_ = reloading_ ? ref->ammo < 110 : ref->ammo <= 35;
    const bool can_reload_remote = ref->remote_ammo_available || ref->remote_ammo_pending;
    const bool prefer_local = ref->supply_active && ref->local_ammo_available &&
      !ref->remote_ammo_pending;
    const bool needs_supply = reloading_ && (!can_reload_remote || prefer_local);
    const bool overwhelmed =
      (ref->own_base_hp <= 4000 && ref->enemy_base_hp >= ref->own_base_hp + 2000) ||
      (ref->hp * 4 <= ref->max_hp && ref->opponent_hp * 3 >= ref->max_hp * 2);

    // 1. Survival / defensive emergency
    if (overwhelmed && ref->own_fortress_available && ready(Mode::OwnFortress)) {
      return Mode::OwnFortress;
    }
    // If already arrived & healing/resupplying at OwnBaseSupply, finish healing instead of bouncing back to outpost!
    const bool finishing_base_supply = mode_ && *mode_ == Mode::OwnBaseSupply && arrived_;
    if (healing_ && !needs_supply && !finishing_base_supply && ref->own_outpost_available && ready(Mode::OwnOutpostHeal)) {
      return Mode::OwnOutpostHeal;
    }
    if ((healing_ || needs_supply) && ready(Mode::OwnBaseSupply)) {
      return Mode::OwnBaseSupply;
    }

    // 2. Defend / reclaim own outpost
    if (ref->own_outpost_enemy_owned && ref->own_outpost_hp > 0 &&
        (ref->ammo > 35 || can_reload_remote) && ready(Mode::OwnOutpostAttack)) {
      return Mode::OwnOutpostAttack;
    }
    const bool own_tower_live_enemy = ref->own_outpost_enemy_owned && ref->own_outpost_hp > 0;
    const bool need_reclaim_own_outpost =
      !own_tower_live_enemy &&
      (ref->own_outpost_enemy_controlled ||
       (!ref->own_outpost_controlled && (ref->own_outpost_hp == 0 || ref->own_outpost_enemy_owned)));
    if (need_reclaim_own_outpost && ready(Mode::OwnOutpostRebuild)) {
      return Mode::OwnOutpostRebuild;
    }

    // 3. Active enemy pursuit (if under attack, break outpost siege immediately to fight/chase attacker!)
    const bool combat_ready = !healing_ && (ref->ammo > 35 || can_reload_remote);
    const bool under_attack = now < self_under_attack_until_;
    const bool holding_highland_outpost =
      !under_attack && mode_ && (*mode_ == Mode::EnemyOutpostAttack || *mode_ == Mode::OwnOutpostAttack) && arrived_;
    const bool in_chase = mode_ && *mode_ == Mode::ChaseEnemy;
    const bool chase_dwell_hold = in_chase && (now - chase_entered_at_ < 3.5);
    const bool chase_cooldown_ok = in_chase || under_attack || (now - chase_exited_at_ >= 2.5);
    const bool enemy_in_pursuit_range =
      vision.has_enemy_xy && !EnemyInHomeZone(vision) && vision.distance_m <= 11.0;
    if (combat_ready && !holding_highland_outpost && enemy_in_pursuit_range && chase_cooldown_ok &&
        (vision.enemy_visible || chase_dwell_hold) &&
        ready(Mode::ChaseEnemy)) {
      return Mode::ChaseEnemy;
    }
    if (combat_ready && now < own_outpost_attacked_until_ && ref->own_outpost_hp > 0 &&
        !ref->own_outpost_enemy_owned && ready(Mode::OwnOutpostAttack)) {
      return Mode::OwnOutpostAttack;
    }

    // 4. Offensive progression from highland / outpost gain positions
    if (combat_ready) {
      if (ref->enemy_outpost_hostile && ref->enemy_outpost_hp > 0 && ready(Mode::EnemyOutpostAttack)) {
        return Mode::EnemyOutpostAttack;
      }
      if (ref->enemy_outpost_hostile && ref->enemy_outpost_hp == 0 && ready(Mode::EnemyOutpostCapture)) {
        return Mode::EnemyOutpostCapture;
      }
      if (!ref->enemy_base_protected && ref->enemy_base_hp > 0) {
        if (!ref->enemy_base_open && ref->enemy_fortress_available && ready(Mode::EnemyFortress)) {
          return Mode::EnemyFortress;
        }
        if (ready(Mode::EnemyBaseAttack)) {
          return Mode::EnemyBaseAttack;
        }
      }
    }

    // 5. Memory-guided highland patrol fallback
    if (!ready(Mode::Patrol)) return Mode::Inactive;
    return Mode::Patrol;
  }

  void PurchaseAmmo(const Referee & ref, Mode mode, double now)
  {
    if (!reloading_ || now < next_purchase_) return;

    std_msgs::msg::UInt32 request;
    if (mode == Mode::OwnBaseSupply && ref.supply_active && ref.local_ammo_available &&
        !ref.remote_ammo_pending) {
      request.data = 1;
    } else if (ref.remote_ammo_available && !ref.remote_ammo_pending) {
      request.data = 2;
    } else {
      return;
    }
    ammo_->publish(request);
    next_purchase_ = now + (request.data == 1 ? 0.7 : 2.0);
  }

  void MonitorDestination(const Referee & ref, Mode mode, std::optional<bool> contact, double now)
  {
    if (now < retry_at_[Index(mode)]) return;
    const double held = now - *arrived_;
    auto retry = [&](double cooldown) { retry_at_[Index(mode)] = now + cooldown; };
    if (contact == false) {
      if (!arrived_on_card_ && held >= 2.5) {
        if (mode == Mode::OwnOutpostHeal && outpost_heal_recenter_tries_ < 2) {
          // Re-center onto the outpost RFID card instead of prematurely running all the way home!
          ++outpost_heal_recenter_tries_;
          arrived_.reset();
          RCLCPP_INFO(node_->get_logger(),
            "RMUC_TACTICS outpost card not yet read, re-centering (%d/2)", outpost_heal_recenter_tries_);
        } else {
          outpost_heal_recenter_tries_ = 0;
          retry(12.0);
        }
      }
      return;
    }
    if (mode == Mode::OwnOutpostHeal && contact == true) {
      outpost_heal_recenter_tries_ = 0;
    }

    switch (mode) {
      case Mode::Patrol:
        if (held >= 1.5) arrived_.reset();
        break;
      case Mode::OwnBaseSupply:
        if (!healing_ && held >= 16.0) retry(20.0);
        break;
      case Mode::OwnOutpostRebuild:
      case Mode::EnemyOutpostCapture:
        if (held >= 13.0) retry(15.0);
        break;
      case Mode::EnemyFortress:
        if (held >= 14.0) retry(20.0);
        break;
      case Mode::OwnOutpostAttack:
      case Mode::EnemyOutpostAttack:
      case Mode::EnemyBaseAttack: {
        const int hp = mode == Mode::OwnOutpostAttack ? ref.own_outpost_hp :
          (mode == Mode::EnemyOutpostAttack ? ref.enemy_outpost_hp : ref.enemy_base_hp);
        if (objective_hp_ < 0 || hp < objective_hp_) progress_at_ = now;
        objective_hp_ = hp;
        if (now - progress_at_ >= 16.0) {
          retry(20.0);
        }
        break;
      }
      default:
        break;
    }
  }

  // Intelligent predictive gimbal sector scanner (uses POSITION mode so +/-pi wrap is seamless!)
  void PublishSmartScan(Mode mode, const VisionInfo & vision, bool navigating, double dt)
  {
    const double sx = vision.has_self_xy ? vision.self_x : (is_red_ ? 3.0 : -3.0);
    const double sy = vision.has_self_xy ? vision.self_y : 0.0;

    // Default: sweep toward enemy half-field instead of staring at own back wall
    double center_yaw = is_red_ ? kPi : 0.0;
    double amp_yaw = 1.90;    // +/- 109 deg broad front sweep
    double freq = 1.1;        // rad/s phase speed
    double center_pitch = 0.0;  // Hub scan convention: up-positive!
    double amp_pitch = 0.04;
    bool publish_while_moving = false;

    const bool under_attack = (last_tick_ < self_under_attack_until_);
    const bool targeting_enemy_outpost = (mode == Mode::EnemyOutpostAttack);
    const bool targeting_own_outpost = (mode == Mode::OwnOutpostAttack);
    if (under_attack && !vision.enemy_visible) {
      // Highest priority: snap gimbal toward hit armor direction and sweep wide/fast to acquire attacker!
      center_yaw = hurt_search_yaw_;
      amp_yaw = 2.15;   // +/- 123 deg rapid counter-sweep covering full flank/rear even while spinning
      freq = 3.6;
      center_pitch = 0.0;
      amp_pitch = 0.04;
      publish_while_moving = true;
    } else if (targeting_enemy_outpost || targeting_own_outpost) {
      const bool target_red_post = targeting_own_outpost ? is_red_ : !is_red_;
      const double ox = target_red_post ? kRedOutpostX : -kRedOutpostX;
      const double oy = target_red_post ? kRedOutpostY : -kRedOutpostY;
      const double horiz = std::max(1.2, std::hypot(ox - sx, oy - sy));
      center_yaw = std::atan2(oy - sy, ox - sx);
      // Cover all 3 rotating outpost plates at different heights (135.6 / 146.4 / 155.3 cm)
      center_pitch = std::clamp(std::atan2(kOutpostArmorZ - kHighlandMuzzleZ, horiz), 0.08, 0.30);
      amp_yaw = 0.28;
      amp_pitch = 0.08;
      freq = 2.0;
      publish_while_moving = (horiz <= 5.5);
    } else if (vision.memory.valid && vision.memory.age_sec <= kMemoryScanSec) {
      // Predictive sector scan centered on extrapolated enemy trajectory!
      center_yaw = std::atan2(vision.memory.pred_y - sy, vision.memory.pred_x - sx);
      const double age = vision.memory.age_sec;
      if (age <= 3.0) {
        amp_yaw = 0.60;   // +/- 34 deg rapid re-acquisition
        freq = 2.4;
      } else {
        const double k = std::clamp((age - 3.0) / 9.0, 0.0, 1.0);
        amp_yaw = 0.60 + k * 0.75;  // gradually widen to +/- 77 deg
        freq = 2.0 - k * 0.7;
      }
      center_pitch = 0.0;
      amp_pitch = 0.05;
      publish_while_moving = (mode == Mode::ChaseEnemy || age <= 5.0);
    } else if (mode == Mode::EnemyBaseAttack) {
      const double bx = is_red_ ? -kRedBaseX : kRedBaseX;
      center_yaw = std::atan2(kRedBaseY - sy, bx - sx);
      amp_yaw = 0.45;
      freq = 1.6;
      center_pitch = 0.0;
      amp_pitch = 0.05;
      publish_while_moving = false;
    }

    if (navigating && !publish_while_moving) {
      return;
    }

    scan_phase_ = std::fmod(scan_phase_ + freq * dt, 2.0 * kPi);
    const double yaw_cmd = WrapAngle(center_yaw + amp_yaw * std::sin(scan_phase_));
    const double pitch_cmd = std::clamp(center_pitch + amp_pitch * std::sin(scan_phase_ * 2.0), -0.25, 0.32);

    pb_rm_interfaces::msg::GimbalCmd scan;
    scan.header.stamp = node_->now();
    scan.yaw_type = pb_rm_interfaces::msg::GimbalCmd::ABSOLUTE_ANGLE;
    scan.pitch_type = pb_rm_interfaces::msg::GimbalCmd::ABSOLUTE_ANGLE;
    scan.position.yaw = static_cast<float>(yaw_cmd);
    scan.position.pitch = static_cast<float>(pitch_cmd);
    scan_->publish(scan);
  }

  void Publish(Mode mode, const std::optional<Referee> & ref, const VisionInfo & vision,
    bool navigating, std::optional<bool> contact, double dt)
  {
    const bool active = ref && HasRoute(mode);
    const bool quiet_supply = active && !navigating && mode == Mode::OwnBaseSupply && !vision.enemy_visible;
    std_msgs::msg::Int32 aim;
    aim.data = active;
    std_msgs::msg::Int32 fire;
    // Always allow fighting back while healing/resupplying if an enemy is visible and ammo/heat permit!
    fire.data = active && !ref->shooter_locked && !heat_throttled_ &&
      (ref->ammo > 0 || ref->own_fortress_active);

    const bool under_attack = (last_tick_ < self_under_attack_until_);
    std_msgs::msg::String target;
    // If taking armor fire while sieging an outpost/base, immediately drop the structure lock
    // (ENEMY_VEHICLE_FIRST forces sp_vision_25 to release outpost/base) so gimbal counter-sweep
    // turns around right away to acquire and return fire on the attacking enemy vehicle!
    const bool attack_outpost = !under_attack && (mode == Mode::EnemyOutpostAttack ||
      (mode == Mode::OwnOutpostAttack && ref && ref->own_outpost_enemy_owned && ref->own_outpost_hp > 0));
    const bool attack_base = !under_attack && mode == Mode::EnemyBaseAttack;
    target.data = attack_outpost ? "ENEMY_VEHICLE_THEN_OUTPOST" :
      (attack_base ? "ENEMY_VEHICLE_THEN_BASE" : "ENEMY_VEHICLE_FIRST");
    priority_->publish(target);
    fire_->publish(fire);
    aim_->publish(aim);

    // RMUC 2027 Auto Sentry Three-Stance Selection (5.0s referee cooldown):
    //   DEFENSE (100W, 10/s cool, +25% def, 0% vuln): heal/supply/fortress/card-hold/low-HP survival
    //   ATTACK  (100W, 30/s cool, 0% def, +25% vuln): active combat, stationary outpost/base siege, pursuit, cooling burst
    //   MOVE    (150W, 10/s cool, 0% def, +25% vuln): fast cross-field patrol/transit without active combat
    std::string desired_stance = ref ? ref->stance : "MOVE";
    if (active) {
      const bool low_hp_defense = ref->hp <= 180 || healing_;
      const bool defensive_objective =
        mode == Mode::OwnOutpostHeal || mode == Mode::OwnBaseSupply || mode == Mode::OwnFortress ||
        mode == Mode::OwnOutpostRebuild || mode == Mode::EnemyOutpostCapture || mode == Mode::EnemyFortress;
      const bool offensive_fire =
        vision.enemy_visible || mode == Mode::ChaseEnemy ||
        (!navigating && (mode == Mode::EnemyOutpostAttack || mode == Mode::OwnOutpostAttack || mode == Mode::EnemyBaseAttack)) ||
        (under_attack && !low_hp_defense) ||
        (!navigating && ref->heat >= 100);

      bool urgent = false;
      if (low_hp_defense || (defensive_objective && (!vision.enemy_visible || low_hp_defense))) {
        desired_stance = "DEFENSE";
        urgent = low_hp_defense || under_attack;
      } else if (offensive_fire) {
        desired_stance = "ATTACK";
        urgent = vision.enemy_visible || under_attack || ref->heat >= 140;
      } else if (navigating) {
        desired_stance = "MOVE";
        urgent = ref->buffer_energy_j < 45.0;
      } else if (heat_throttled_) {
        desired_stance = "DEFENSE";
      } else {
        desired_stance = "ATTACK";
      }

      const double since_cmd = last_tick_ - last_stance_cmd_at_;
      const double eff_cooldown = std::max(ref->stance_cooldown_s, std::max(0.0, 5.0 - since_cmd));
      if (desired_stance != ref->stance && eff_cooldown <= 0.05 &&
          (urgent ? since_cmd >= 0.3 : since_cmd >= 1.2)) {
        std_msgs::msg::String stance_msg;
        stance_msg.data = desired_stance;
        stance_->publish(stance_msg);
        last_stance_cmd_ = desired_stance;
        last_stance_cmd_at_ = last_tick_;
      }
    }

    example_interfaces::msg::Float32 spin;
    const bool calm_outpost_siege =
      !navigating && !under_attack && (mode == Mode::EnemyOutpostAttack || mode == Mode::OwnOutpostAttack) && !vision.enemy_visible;
    if (!active || quiet_supply || power_throttled_) {
      spin.data = 0.0f;
    } else if (navigating) {
      // In MOVE stance (150W), 4.5 m/s translation + 3.8 rad/s spin stays safely under 150W;
      // in ATTACK/DEFENSE (100W), cap transit spin at 2.5 rad/s to preserve buffer energy.
      spin.data = (ref->power_limit_w >= 140.0) ? 3.8f : 2.5f;
    } else if (calm_outpost_siege) {
      // Steady spin when focused on destroying an uncontested outpost so gimbal keeps razor-sharp accuracy
      spin.data = 3.5f;
    } else {
      // Stationary combat / fortress / heal / contested:
      // Pour ALL available chassis power into maximum safe spin (12 + 1.8*w^2 <= P_limit)!
      const double steady_w = std::sqrt(std::max(0.0, ref->power_limit_w - 16.0) / 1.8);
      const double cap_w = (ref->power_limit_w >= 140.0) ? 10.5 : 9.5;
      spin.data = static_cast<float>(spin_boost_ ? std::min(cap_w, steady_w + 2.6) : steady_w);
    }
    spin_->publish(spin);

    if (active || mode == Mode::Wait || mode == Mode::NavigationUnavailable) {
      PublishSmartScan(mode, vision, navigating, dt);
    }

    std_msgs::msg::String message;
    const char * phase = engaging_ ? "engaging" :
      (navigating ? "moving" : (arrived_ && active ? (contact == false ? "checking_card" : "holding") : "idle"));
    message.data = Json{
      {"mode", ModeNames[Index(mode)]}, {"route", active ? active_route_ : ""},
      {"phase", phase},
      {"stance", ref ? ref->stance : "MOVE"},
      {"desired_stance", desired_stance},
      {"stance_cooldown_s", ref ? ref->stance_cooldown_s : 0.0},
      {"power_limit_w", ref ? ref->power_limit_w : 150.0},
      {"aim_allowed", aim.data != 0}, {"fire_allowed", fire.data != 0},
      {"target_priority", target.data},
      {"healing_needed", healing_}, {"ammo_needed", reloading_},
      {"heat_throttled", heat_throttled_}, {"power_throttled", power_throttled_},
      {"memory_valid", vision.memory.valid},
      {"memory_pred_x", vision.memory.valid ? vision.memory.pred_x : 0.0},
      {"memory_pred_y", vision.memory.valid ? vision.memory.pred_y : 0.0}
    }.dump();
    state_->publish(message);
  }

  rclcpp::Node::SharedPtr node_;
  std::shared_ptr<Inputs> inputs_ = std::make_shared<Inputs>();
  rclcpp::Subscription<dji_referee_protocol::msg::SelfColor>::SharedPtr dji_color_;
  rclcpp::Subscription<dji_referee_protocol::msg::GameStatus>::SharedPtr dji_game_;
  rclcpp::Subscription<dji_referee_protocol::msg::RobotPerformance>::SharedPtr dji_perf_;
  rclcpp::Subscription<dji_referee_protocol::msg::RobotHP>::SharedPtr dji_hp_;
  rclcpp::Subscription<dji_referee_protocol::msg::RobotHeat>::SharedPtr dji_heat_;
  rclcpp::Subscription<dji_referee_protocol::msg::AllowedShoot>::SharedPtr dji_ammo_;
  rclcpp::Subscription<dji_referee_protocol::msg::RFIDStatus>::SharedPtr dji_rfid_;
  rclcpp::Subscription<dji_referee_protocol::msg::FieldEvent>::SharedPtr dji_field_;
  rclcpp::Subscription<dji_referee_protocol::msg::DamageState>::SharedPtr dji_damage_;
  rclcpp::Subscription<dji_referee_protocol::msg::RobotPosition>::SharedPtr dji_pos_;
  rclcpp::Subscription<dji_referee_protocol::msg::SentryDecisionSync>::SharedPtr dji_sentry_sync_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr referee_, nav_, vision_track_;
  rclcpp::Publisher<example_interfaces::msg::Float32>::SharedPtr spin_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr stop_;
  rclcpp::Publisher<pb_rm_interfaces::msg::GimbalCmd>::SharedPtr scan_;
  rclcpp::Publisher<std_msgs::msg::Int32>::SharedPtr aim_, fire_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr priority_, stance_, state_;
  rclcpp::Publisher<std_msgs::msg::UInt32>::SharedPtr ammo_;
  bool is_red_ = true;
  bool side_auto_ = true;
  std::array<std::string, ModeNames.size()> routes_;
  std::vector<std::string> patrol_routes_;
  std::vector<std::pair<double, double>> patrol_xy_;
  std::string active_route_;
  std::string chase_route_;
  double chase_goal_x_ = 0.0;
  double chase_goal_y_ = 0.0;
  double chase_updated_at_ = 0.0;
  double chase_entered_at_ = -100.0;
  double chase_exited_at_ = -100.0;
  std::array<double, ModeNames.size()> retry_at_{};
  std::optional<Mode> mode_;
  std::optional<double> arrived_, left_card_;
  std::size_t patrol_waypoint_ = 0;
  bool arrived_on_card_ = false;
  bool engaging_ = false;
  double last_tick_ = 0.0;
  double scan_phase_ = 0.0;
  double next_purchase_ = 0.0;
  double progress_at_ = 0.0;
  int objective_hp_ = -1;
  int last_own_outpost_hp_ = -1;
  double own_outpost_attacked_until_ = 0.0;
  int last_self_hp_ = -1;
  double self_under_attack_until_ = 0.0;
  double hurt_search_yaw_ = 0.0;
  bool healing_ = false;
  bool reloading_ = false;
  bool heat_throttled_ = false;
  bool power_throttled_ = false;
  bool spin_boost_ = false;
  std::string last_stance_cmd_;
  double last_stance_cmd_at_ = -100.0;
  int outpost_heal_recenter_tries_ = 0;
};
}  // namespace

CreateRosNodePlugin(Tactics, "RMUC2027Tactics");
