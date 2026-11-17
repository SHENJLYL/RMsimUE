---
doc_type: requirement
slug: lidar-inertial-estimation
pitch: 使用车辆实际激光和IMU输入运行同源Point-LIO，并独立评估定位
status: current
last_reviewed: 2026-11-16
implemented_by: [2026-11-16-lidar-pointlio]
tags: [lidar, point-lio, localization, ros2]
user_review: not_requested_under_standing_authorization
---

# 激光惯性定位最小闭环

作为哨兵开发者，可配置底盘上的激光和IMU安装，使用原Livox消息及原Point-LIO进行定位；看到测量时序、丢样和独立真值误差，复位后不会沿用旧地图。原LK底盘可同时参与该闭环。

已实现单车Windows原生：实际滚动射线、六轴IMU、原估计器、由安装配置推导外参、独立评测和复位管理。不同安装、默认RMUC及原LK联合有实际证据。

当前扫描图样/反射率/参数未标定；不是Mid-360光学复刻。输出是局部定位，尚无地图重定位、Nav2/行为树/视觉闭环、多车命名空间或Ubuntu原生验收。真实感知和比赛的完整愿景保持不变。

2026-11-16：从realistic-sensors和native-algorithm-testing中落档当前已证明的子集。验收入口artifacts/sensors/lidar/release-summary.json。
