---
doc_type: feature-design
feature: 2026-11-16-native-navigation
status: approved
summary: Run the pinned sentry navigation chain against actual UE sensing, with AMCL localization and original Nav2 plugins.
tags: [navigation, amcl, ros2, native]
roadmap: rm-simulator
roadmap_item: native-navigation
authorization: Standing autonomous same-source simulator request; Windows first, Ubuntu offline preparation.
user_review: not_requested_under_standing_authorization
---

# 原生哨兵导航

## 1. 目标与依据

继续完整sentry_ws目标。原导航ffd68e5实车launch明确use_amcl=True；small_gicp仍为可选路径，本轮按当前默认链路做AMCL/Nav2，不以替代算法避开源码兼容问题。原loam_interface、sensor_scan_generation、terrain_analysis/ext、pointcloud_to_laserscan、fake_vel_transform、Omni PID控制器和自定义costmap插件应实际运行。

源副本位于third_party，工作副本/补丁分开。Windows原生使用已固定RoboStack Humble Nav2；Ubuntu复用源码/参数，只记离线准备。原长期比赛/联机/可换地图/新车目标不缩减。

## 2. 坐标与编排

现状：真实UE点云/IMU→原Point-LIO局部估计，但无定位TF/导航。变化：原接口节点将估计转成odom→base与实际扫描；原地形处理/扫描转换提供AMCL与costmap输入；AMCL持有map→odom；原Nav2插件输出速度，经原fake_vel_transform接仿真底盘。

Point-LIO当前估计位姿参考IMU，不能把它误当LiDAR中心。接口配置中的参考sensor frame必须对应IMU实际安装；静态安装由仿真提供，动态odom必须来自估计。实车translation_scale=-1不能照搬，仿真采用标准ROS符号并原生验证转向/侧移。

地图必须对应当前场景，不拿2025地图充当2027。先在可解析场景建立完整闭环，再准备RMUC2027合适的占用/通行表示；不把多高度场地的一张简单投影称为全部通行能力。给定初始定位可以作为显式先验，但需带可观察误差验证收敛，禁止连续真值定位。

会话/epoch变化须停止旧导航并重建估计/定位状态；算法进程退出或定位失效不可留下持续运动。沿用既有执行租约，不放宽控制超时来掩盖负载。

## 3. 验收

- 固定原仓库/子模块、保留许可和可审查补丁；原自定义节点和插件真实Windows构建/动态加载。
- 估计TF/点云/速度坐标合同实际测试，已知安装偏移、侧向运动、转动与时间重置均不使用评测真值作输入。
- 实际场景传感器驱动AMCL，从有偏初值收敛到地图；源码/ROS图确认没有真值订阅。
- 原Nav2规划/控制在真实车辆完成定义目标及绕障，不只发出cmd_vel；中止/失联/复位停车与恢复验证。
- 默认RMUC与其他地图的支持范围分别记录，地图尺度/轴向/来源一致。原操控/传感器/射击回归保持通过。

## 4. 实现挂载与记录

预期新增原导航工作包、原生构建/插件DLL部署、估计TF适配配置、地图占用准备及会话编排入口。保持处理算法原逻辑，必要移植/运行修复逐项保存根因与证据。已验证内容再归并architecture/requirement/roadmap。

依赖调整实际为107净新增包及4个图像/压缩包替换（blosc、ffmpeg、libjxl、snappy）；ROS/Python旧包未变。前后锁及差异在artifacts/navigation/before与artifacts/sources/navigation。安装阶段未正确用失败的“零替换”断言阻断下一条命令，已记录真实变化；不能再写为纯新增，须运行受影响功能验证。
