---
doc_type: feature-acceptance
feature: 2026-11-15-swerve-gyro-control
status: native_verified_gui_observed
summary: Camera-relative swerve/gyro controls, bounded response, source-derived kinematics and real terrain/contact gates pass on native Windows.
tags: [swerve, gyro, control, native-evidence]
date: 2026-11-16
user_review: subjective_handfeel_pending_latest_preview
---

# 舵轮与小陀螺验收

## 1. 接口与实现

键盘与后续控制来源共用SetContactPlanarDrive的SI速度边界，线速度区分ROS车体系/世界平面。输入先映射到世界平面，速度大小和方向分别整形，再转换为每轮方向/轮速。每轮有真实转向/轮轴约束和有限电机力矩；行驶不修改车体姿态。

用户本机LK控制器的向量/编码器最短路径已适配到实际调用的Control/SentrySwerveKinematics.h。900组向量及跨界/反转检查编译执行通过；其完整PID电流节点、custom_msgs及UE通信尚未端到端接入。原目录只读，参数预备件在车辆包adapters/sentry_lk.yaml。

## 2. 行为与关键决策

普通/小陀螺均WASD镜头相对平移；Q/E普通旋转，G自转开关，Z/X调速，Ctrl慢行，鼠标瞄准俯仰。松键主动减速并用轮速积分抵消坡面重力。轮向抓地以真实滚动/横向滑移实现；诊断关闭的是轮驱动，舵向仍保持。

原尺寸/中立几何不变，转向carrier独立拆出。15视觉部件、13独立刚体、12约束。关节投影/质量调节关闭。手动窗口只渲染玩家视图，快捷入口拒绝重复实例；截图/验证仍保留独立采集。

## 3. 原生证据

证据根：artifacts/gameplay/gyro-control。release-summary.json核对全部报告、进程退出、模块/配置哈希。

| 证据 | 检查/PNG | 结果 |
|---|---:|---|
| release-controls-01/default | 19 / 12 | 普通/小陀螺、调速、制动、俯仰、换镜头方向、响应、慢行通过 |
| release-waves-precision/wave_red | 5 / 6 | 4rad/s自转、Ctrl全油门0.35m/s，原红侧3.4m往返通过 |
| release-waves-precision/wave_blue | 5 / 6 | 同条件原蓝侧3.4m往返通过 |
| release-traction | 6 / 4 | 20度坡顺/横轮向差异及松键停车通过 |
| release-integration | 10 / 12 | 驱动、旋转、命中、实体墙、复位/跌落恢复通过 |

引擎内响应：目标舵角约5.1ms开始更新，四轮到80度约100.8ms；0.6m/s行进90度改向实际速度方向在约256.7ms内进入15度误差，修复前966.7ms。该计时不包含OS键鼠/显示器延迟。

波浪路四次终点横偏最大约1.53cm，源几何未改。测试驾驶员用真实位姿生成有限侧向纠偏输入，属于可通行性/控制验证，不是自主导航或Point-LIO验收。自动路线禁用跌落恢复；每次完整行驶都有真实世界帧和接触证据。

20度坡：轮驱动关断，顺坡轮滚动78.095cm，横坡轮下移0.054cm；零移动输入主动停车3秒下移0.609cm且末速0。参数属于演示标定，不承诺所有表面/载荷绝对零滑移。

## 4. 来源与术语

车辆仍约503.850×501.985×419.192mm，轮半径42.5mm。29个车辆源文件、12个控制器源文件只读哈希一致，15个派生碰撞绑定有效；中立15部件唯一覆盖33源对象、1260698三角形。

“接触真实运行”与“实车参数已校准”分开；“复用解算逻辑”与“完整源ROS节点闭环”分开；“Windows原生”与“Ubuntu离线准备”分开。云台碰撞仍中立焊接，完整裁判/联机/传感器尚未完成。

## 5. 架构与交接

architecture、extensible-worlds-vehicles、roadmap、progress、attention和操作指南同步更新。用户约定与每次失败/修复证据保留。

最终Windows窗口实际观察到RMUC场地、车辆和训练HUD，现场215–229FPS。通过真实OS按键验证G开启、X把设定2.0升到2.5rad/s、Z回到2.0、R复位关闭小陀螺并重启训练；GUI显示实际转速相应变化。gui-verification.json与gui-input-evidence.json留证，窗口在出生点保持打开。该观察仍不是OS到显示器的毫秒延迟测量，用户手感反馈继续保留。
