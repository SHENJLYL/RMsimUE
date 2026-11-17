---
doc_type: acceptance
status: done_windows_scoped
updated: 2026-11-17
user_review: not_requested_under_standing_authorization
---

# 可视化导航、导航云台跟随与云台安装Mid-360

## 已交付

- Play-Navigation.cmd以默认RMUC红方出生点启动实际UE、ROS桥、原Point-LIO/AMCL/Nav2和RViz；关闭RViz或Stop-Navigation.cmd只停止该会话拥有的进程。
- 原RViz Nav2插件可点选目标、累积多个航点、Start Waypoint Following、Cancel；显示地图、估计位姿、代价、路径、点云、云台与Mid-360坐标。
- 原sentry导航偏航目标整形规则按固定源码95c66c2接入；读取实际导航速度、估计底盘航向与真实关节反馈，高优先级扫描/获准自瞄仲裁保留，导航不触发开火。
- Mid-360定位到源模型radar_mid360/yaw_link，采用模型实际尺度和安装朝向；射线、共装IMU、动态TF跟随实际关节。原模型文件哈希不变。

## 实测

汇总入口：`artifacts/navigation/sentry-console-20261117/release-summary.json`。汇总脚本拒绝失败/缺失用例，并核对当前UE、参数、rig、安装Python、导航DLL及源模型哈希。

| 证据 | 检查 |
|---|---:|
| native-actions-02：完整单点、原FollowWaypoints双航点、取消、云台与传感器 | 14通过 |
| regression-01/mid360：旋转安装、IMU/比力、光学透射与实体遮挡 | 9通过 |
| regression-01/manual | 19通过/12PNG |
| regression-01/ros | 11通过 |
| regression-01/aim | 20通过 |
| regression-01/lidar-lk | 11通过 |
| gui-acceptance-08：真实RViz单点/航点队列/Cancel | 4+6+3通过 |
| final-offline-02 | 120通过、2权限跳过，共122 |

原生84项与GUI13项分别统计。完整原action路线为[5.8,6.4]→[9.45,6.4]及往返；单点实际误差0.188766m，航点终点0.179548m，取消停车0.053320m，之后漂移0m。12300个云台跟随样本的实际角度/整形目标中位误差0.00044835rad，累计实际转动12.5598rad。

GUI短路线明确为[5.8,6.4]与[6.8,6.4]；单点实际误差0.201611m，两个航点有序执行后误差0.178089m，Cancel后观察漂移0m。点击通过实际RViz工具QAction与OS鼠标，按钮由原UI调用；独立ROS观察器检查action结果与真实轨迹，未向定位/控制输入真值。

## 诊断与边界

保留原失败：旧SI适配遗漏加速度饱和阈值单位、一次启动失败、GUI相机投影/工具激活/低倍率选点偏差。固定Estimator.cpp在归一化前检查原始acc_avr，现成对转换acc_norm=9.81与satu_acc=29.43，维持原3g物理范围；6项单位测试和新原生路线证明。实际到点0.25m、原控制租约与碰撞门限未放宽。

当前动态雷达为理想射线/名义共装IMU模型，未标定专有扫描、反射、温漂及光学折射。高层偏航整形已接入，原硬件云台电流PID、完整Hub/视觉仲裁、Nav Through Poses按钮专项、新Mid-360组合实际R恢复、任意地图区域/多车和Ubuntu原生运行独立推进。长期goal保持active。
