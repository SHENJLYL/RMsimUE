---
doc_type: requirement
slug: raw-chassis-imu
pitch: 为同源哨兵算法提供有实际物理来源和明确时间语义的原始六轴输入
status: current
last_reviewed: 2026-11-16
implemented_by: [2026-11-16-native-imu]
tags: [imu, ros2, physics]
user_review: not_requested_under_standing_authorization
---

# 底盘原始 IMU

作为哨兵算法开发者，可在车辆包中声明安装点、采样频率、固定偏置与白噪声，使用原生ROS sensor_msgs/Imu接收实际底盘运动产生的比力和角速度。移动安装点可观测转动引起的向心加速度；改变安装朝向会改变传感器系读数。

每条消息保留物理采样时间；帧率不足、缓冲拥塞和复位有可观测记录，不制造缺失样本。启动配置错误应拒绝运行。

本次范围为未标定底盘刚性安装六轴，最多4路。姿态估计明确不可用，不能把世界真值当定位输入。尚无AHRS、LiDAR/Point-LIO闭环、硬件温漂/带宽校准或原生Ubuntu验收。

2026-11-16：从长期realistic-sensors愿景中独立落档已实现的raw IMU能力；其余成像/雷达/校准愿景保持未完成。证据入口artifacts/sensors/imu/release-summary.json。
