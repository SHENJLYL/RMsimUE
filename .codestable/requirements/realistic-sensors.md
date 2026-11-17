---
doc_type: requirement
slug: realistic-sensors
pitch: 在真实光影和可校准传感器误差下测试机器人感知
status: draft
last_reviewed: 2026-11-13
implemented_by: []
tags: [camera, lidar, imu, radar, rendering]
user_review: not_requested_under_standing_authorization
---

# 可信视觉与传感器

## 用户故事

- 作为自瞄开发者，我希望测试装甲灯条在阴影、反光、曝光变化和运动中的识别效果。
- 作为小哨兵开发者，我希望在运动、遮挡与噪声下检查定位和导航。
- 作为雷达站开发者，我希望在符合安装位置和视场的输入上测试识别与定位。

## 为什么需要

高画质便于观察，但画面好看并不能保证相机或雷达数据符合实际设备。完全理想的输入也会掩盖算法问题。

## 怎么解决

场景保留可测量的尺寸、材质与光照，传感器显式配置安装、采样与误差。通过可重复场景和实测数据逐步校准。

## 边界

- 不承诺任意场景与真机完全一致；需要记录模型简化、测量依据与剩余误差。
- RoboMaster 雷达站算法与通用毫米波雷达传感器不是同一概念。
- 调试真值与参赛传感器分开，避免用理想数据替代感知闭环。

## 已落地子集

2026-11-16：Windows已有独立相机与原始底盘IMU。IMU的安装/采样/物理来源/可配白噪声单独记录在raw-chassis-imu.md；不将这部分通过扩大为完整可信成像、LiDAR或实车校准验收。本愿景仍为draft。

后续已实现物理滚动LiDAR与原Point-LIO最小定位闭环，独立能力记录lidar-inertial-estimation.md。扫描图样/反射率仍是未标定简化，完整真实成像、雷达和校准愿景继续保持draft。
