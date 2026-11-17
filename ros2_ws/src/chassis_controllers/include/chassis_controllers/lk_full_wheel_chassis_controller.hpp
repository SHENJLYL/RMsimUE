#ifndef BUILD_LK_FULL_WHEEL_CHASSIS_CONTROLLER_HPP
#define BUILD_LK_FULL_WHEEL_CHASSIS_CONTROLLER_HPP

#pragma once

#include <rclcpp/rclcpp.hpp>
#include <chrono>
#include <algorithm>
#include <cmath>
#include <stdexcept>

#include <std_msgs/msg/string.hpp>

#include "chassis_controllers/msg/chassis_control.hpp"
#include "custom_msgs/msg/write_lk_motor_broadcast_current_control.hpp"
#include "custom_msgs/msg/read_lk_motor_multi.hpp"
#include "utils/pid.hpp"
#include "utils/power_limiter.hpp"
#include "utils/rls.hpp"

using namespace std::chrono_literals;
using namespace aim::algorithm;

class LKFullWheelChassisController : public rclcpp::Node {
public:
    explicit LKFullWheelChassisController(const rclcpp::NodeOptions &options = rclcpp::NodeOptions())
        : Node("lk_full_wheel_chassis_controller", options) {
        feedback_driven_=this->declare_parameter<bool>("feedback_driven",false);
        this->declare_parameter<int32_t>("front_left_ecd_zero", 30507);
        this->declare_parameter<int32_t>("front_right_ecd_zero", 21248);
        this->declare_parameter<int32_t>("back_left_ecd_zero", 23534);
        this->declare_parameter<int32_t>("back_right_ecd_zero", 1128);

        this->declare_parameter<double>("wheel_radius", 0.0425);
        wheel_radius_ = this->get_parameter("wheel_radius").as_double();

        this->declare_parameter<double>("chassis_radius", 0.40);
        chassis_radius_ = this->get_parameter("chassis_radius").as_double();
        chassis_l_ = chassis_radius_ / std::sqrt(2.0);

        this->declare_parameter<std::string>("steer_motor_write_topic", "/ecat/sn2228292/app2/write");
        this->declare_parameter<std::string>("steer_motor_read_topic", "/ecat/sn2228292/app2/read");
        this->declare_parameter<std::string>("wheel_motor_write_topic", "/ecat/sn2228292/app1/write");
        this->declare_parameter<std::string>("wheel_motor_read_topic", "/ecat/sn2228292/app1/read");
        this->declare_parameter<std::string>("chassis_cmd_read_topic", "/chassis_command");

        front_left_ecd_zero_ =
                this->get_parameter("front_left_ecd_zero").as_int();
        front_right_ecd_zero_ =
                this->get_parameter("front_right_ecd_zero").as_int();
        back_left_ecd_zero_ =
                this->get_parameter("back_left_ecd_zero").as_int();
        back_right_ecd_zero_ =
                this->get_parameter("back_right_ecd_zero").as_int();

        chassis_command_sub_ =
                this->create_subscription<
                    chassis_controllers::msg::ChassisControl>(
                    this->get_parameter("chassis_cmd_read_topic").as_string(),
                    rclcpp::SensorDataQoS().keep_last(1),
                    [this](chassis_controllers::msg::ChassisControl::SharedPtr msg) {
                        if (!std::isfinite(msg->x_speed) || !std::isfinite(msg->y_speed) || !std::isfinite(msg->spin_speed) ||
                            std::abs(msg->x_speed)>20 || std::abs(msg->y_speed)>20 || std::abs(msg->spin_speed)>20) {
                            control_command_.emergency_stop=1; last_command_={}; return;
                        }
                        this->control_command_ = *msg;
                        last_command_=std::chrono::steady_clock::now();
                    }
                );

        steer_motor_sub_ =
                this->create_subscription<
                    custom_msgs::msg::ReadLkMotorMulti>(
                    this->get_parameter("steer_motor_read_topic").as_string(),
                    rclcpp::SensorDataQoS().keep_last(1),
                    [this](custom_msgs::msg::ReadLkMotorMulti::SharedPtr msg) {
                        this->steer_motor_status_ = *msg;
                        last_steer_=std::chrono::steady_clock::now();
                        control_from_feedback();
                    }
                );

        wheel_motor_sub_ =
                this->create_subscription<
                    custom_msgs::msg::ReadLkMotorMulti>(
                    this->get_parameter("wheel_motor_read_topic").as_string(),
                    rclcpp::SensorDataQoS().keep_last(1),
                    [this](custom_msgs::msg::ReadLkMotorMulti::SharedPtr msg) {
                        this->wheel_motor_status_ = *msg;
                        last_wheel_=std::chrono::steady_clock::now();
                        control_from_feedback();
                    }
                );

        steer_motor_pub_ =
                this->create_publisher<
                    custom_msgs::msg::WriteLkMotorBroadcastCurrentControl>(
                    this->get_parameter("steer_motor_write_topic").as_string(),
                    rclcpp::SensorDataQoS().keep_last(1)
                );

        wheel_motor_pub_ =
                this->create_publisher<
                    custom_msgs::msg::WriteLkMotorBroadcastCurrentControl>(
                    this->get_parameter("wheel_motor_write_topic").as_string(),
                    rclcpp::SensorDataQoS().keep_last(1)
                );

        control_timer_ =
                this->create_wall_timer(
                    1ms,
                    [this] {
                        if (!feedback_driven_ || std::chrono::steady_clock::now()-last_steer_>100ms ||
                            std::chrono::steady_clock::now()-last_wheel_>100ms) control_loop();
                    });

#ifdef POWER_FITTING
        fit_loop_timer_ = this->create_wall_timer(
            1ms,
            [this] { rls_update_loop(); });
#endif

        RCLCPP_INFO(this->get_logger(), "LK full-wheel chassis controller started");
    }

private:
    bool feedback_driven_=false;
    int64_t last_feedback_stamp_=-1;
    void control_from_feedback() {
        if (!feedback_driven_) return;
        const auto &a=steer_motor_status_.header.stamp;
        const auto &b=wheel_motor_status_.header.stamp;
        if (a.sec!=b.sec || a.nanosec!=b.nanosec) return;
        const int64_t stamp=static_cast<int64_t>(a.sec)*1000000000+a.nanosec;
        if (stamp==last_feedback_stamp_) return;
        if (stamp<last_feedback_stamp_) last_command_={};
        const float ratio=last_feedback_stamp_>=0 && stamp>last_feedback_stamp_ ?
            std::clamp(static_cast<float>(stamp-last_feedback_stamp_)/1000000.f,.1f,100.f):1.f;
        for(auto &pid:steer_angle_pids_)pid.set_sample_ratio(ratio);
        for(auto &pid:steer_speed_pids_)pid.set_sample_ratio(ratio);
        for(auto &pid:wheel_speed_pids_)pid.set_sample_ratio(ratio);
        last_feedback_stamp_=stamp;
        control_loop();
    }
    std::chrono::steady_clock::time_point last_command_{},last_steer_{},last_wheel_{};
    int32_t front_left_ecd_zero_;
    int32_t front_right_ecd_zero_;
    int32_t back_left_ecd_zero_;
    int32_t back_right_ecd_zero_;

    double wheel_radius_;
    double chassis_radius_;
    double chassis_l_;

    struct {
        double v_front_left;
        double v_front_right;
        double v_back_left;
        double v_back_right;

        double direction_front_left;
        double direction_front_right;
        double direction_back_left;
        double direction_back_right;
    } chassis_ik_solution_;

    struct ShortestPath {
        int32_t err;

        // 1=clockwise
        // -1=counter-clockwise
        int8_t direction;
    };

    constexpr static float steer_speed_pid_kp = 5.5;
    constexpr static float steer_speed_pid_ki = 0.01;
    constexpr static float steer_speed_pid_kd = 0.0;
    constexpr static float steer_speed_pid_maxout = 850.0;
    constexpr static float steer_speed_pid_maxiout = 150.0;
    PID steer_speed_pids_[4]{
        PID(steer_speed_pid_kp, steer_speed_pid_ki, steer_speed_pid_kd, steer_speed_pid_maxout,
            steer_speed_pid_maxiout),
        PID(steer_speed_pid_kp, steer_speed_pid_ki, steer_speed_pid_kd, steer_speed_pid_maxout,
            steer_speed_pid_maxiout),
        PID(steer_speed_pid_kp, steer_speed_pid_ki, steer_speed_pid_kd, steer_speed_pid_maxout,
            steer_speed_pid_maxiout),
        PID(steer_speed_pid_kp, steer_speed_pid_ki, steer_speed_pid_kd, steer_speed_pid_maxout,
            steer_speed_pid_maxiout)
    };

    constexpr static float steer_angle_pid_kp = 0.2;
    constexpr static float steer_angle_pid_ki = 0.0;
    constexpr static float steer_angle_pid_kd = 0.15;
    constexpr static float steer_angle_pid_maxout = 150.0;
    constexpr static float steer_angle_pid_maxiout = 0.0;
    PID steer_angle_pids_[4]{
        PID(steer_angle_pid_kp, steer_angle_pid_ki, steer_angle_pid_kd, steer_angle_pid_maxout,
            steer_angle_pid_maxiout),
        PID(steer_angle_pid_kp, steer_angle_pid_ki, steer_angle_pid_kd, steer_angle_pid_maxout,
            steer_angle_pid_maxiout),
        PID(steer_angle_pid_kp, steer_angle_pid_ki, steer_angle_pid_kd, steer_angle_pid_maxout,
            steer_angle_pid_maxiout),
        PID(steer_angle_pid_kp, steer_angle_pid_ki, steer_angle_pid_kd, steer_angle_pid_maxout,
            steer_angle_pid_maxiout)
    };

    constexpr static float wheel_speed_pid_kp = 6.0;
    constexpr static float wheel_speed_pid_ki = 0.05;
    constexpr static float wheel_speed_pid_kd = 0.0;
    constexpr static float wheel_speed_pid_maxout = 2000.0;
    constexpr static float wheel_speed_pid_maxiout = 250.0;
    PID wheel_speed_pids_[4]{
        PID(wheel_speed_pid_kp, wheel_speed_pid_ki, wheel_speed_pid_kd, wheel_speed_pid_maxout,
            wheel_speed_pid_maxiout),
        PID(wheel_speed_pid_kp, wheel_speed_pid_ki, wheel_speed_pid_kd, wheel_speed_pid_maxout,
            wheel_speed_pid_maxiout),
        PID(wheel_speed_pid_kp, wheel_speed_pid_ki, wheel_speed_pid_kd, wheel_speed_pid_maxout,
            wheel_speed_pid_maxiout),
        PID(wheel_speed_pid_kp, wheel_speed_pid_ki, wheel_speed_pid_kd, wheel_speed_pid_maxout,
            wheel_speed_pid_maxiout)
    };

    static custom_msgs::msg::ReadLkMotorMulti steer_motor_status_;
    static custom_msgs::msg::ReadLkMotorMulti wheel_motor_status_;
    static custom_msgs::msg::WriteLkMotorBroadcastCurrentControl steer_motor_command_;
    static custom_msgs::msg::WriteLkMotorBroadcastCurrentControl wheel_motor_command_;
    static chassis_controllers::msg::ChassisControl control_command_;

    rclcpp::Subscription<
        chassis_controllers::msg::ChassisControl>::SharedPtr
    chassis_command_sub_;

    rclcpp::Publisher<
        custom_msgs::msg::WriteLkMotorBroadcastCurrentControl>::SharedPtr
    steer_motor_pub_;

    rclcpp::Publisher<
        custom_msgs::msg::WriteLkMotorBroadcastCurrentControl>::SharedPtr
    wheel_motor_pub_;

    rclcpp::Subscription<
        custom_msgs::msg::ReadLkMotorMulti>::SharedPtr
    steer_motor_sub_;

    rclcpp::Subscription<
        custom_msgs::msg::ReadLkMotorMulti>::SharedPtr
    wheel_motor_sub_;

    rclcpp::TimerBase::SharedPtr control_timer_;
    rclcpp::TimerBase::SharedPtr fit_loop_timer_;

    PowerLimiter wheel_motor_limiter_{0.14, 1e-5, 24.67, 4.4, 4};

    void control_loop();

#ifdef POWER_FITTING
    void rls_update_loop();
#endif

    int16_t mpstorpm(const float mps) const {
        return 60.f * mps / (2. * M_PI * wheel_radius_);
    }

    int16_t dpstorpm(const float dps) const {
        return dps * 60.f / 360.f;
    }

    double static calc_atan2(const double y, const double x) {
        const double atan2_res = atan2(y, x);
        if (atan2_res >= 0) {
            return atan2_res;
        }
        return M_PI * 2 + atan2_res;
    }

    ShortestPath static calc_shortest_path(const int32_t current_ecd, const int32_t target_ecd) {
        const int32_t current = ((current_ecd % 32768)+32768)%32768;
        const int32_t target = ((target_ecd % 32768)+32768)%32768;
        const int32_t forwardDistanceA = (target - current + 32768) % 32768;
        const int32_t backwardDistanceA = (current - target + 32768) % 32768;
        const int32_t forwardDistanceB = (target - current + 32768 + (32768 / 2)) % 32768;
        const int32_t backwardDistanceB = (current - target + 32768 + (32768 / 2)) % 32768;

        int32_t rotationAngle;
        ShortestPath res{};

        if (std::min(forwardDistanceA, backwardDistanceA) <= std::min(forwardDistanceB, backwardDistanceB)) {
            rotationAngle = (forwardDistanceA <= backwardDistanceA) ? forwardDistanceA : -backwardDistanceA;
            res.err = rotationAngle;
            res.direction = 1;
        } else {
            rotationAngle = (forwardDistanceB <= backwardDistanceB) ? forwardDistanceB : -backwardDistanceB;
            res.err = rotationAngle;
            res.direction = -1;
        }
        return res;
    }
};

#endif //BUILD_LK_FULL_WHEEL_CHASSIS_CONTROLLER_HPP
