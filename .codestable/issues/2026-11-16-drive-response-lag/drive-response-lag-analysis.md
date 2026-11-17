---
doc_type: issue-analysis
issue: 2026-11-16-drive-response-lag
status: confirmed
authorization: standing_user_request_to_implement_and_verify
tags: [swerve, gyro, command-shaping]
---

# 原因与定点修复

fast-before 已实测复现：2m/s横移后请求90度前进，速度目标进入10度误差747.8ms，实际速度进入15度误差714.6ms；静止四轮80度98.6ms。原0.6m/s门限并未覆盖常用横移速度。VehicleDrive.cpp UpdateContactDrive 中方向限幅4m/s²、方向jerk40m/s³使目标自身滞后，物理跟踪不是此次主要瓶颈。

本轮选择：仅提高方向限幅到10m/s²、方向jerk100m/s³。车辆包和无profile的同名默认值保持一致；起步0.8m/s²、松键3m/s²、Space6m/s²不变，轮胎/碰撞/力矩不变。仍保留有限加速度和真实轮转向，不直接写车体速度或姿态。该方向参数属于待标定的演示配置，可通过车辆包替换。

范围：VehicleDrive.cpp、assets/vehicles/aim_sentry/drive-profile.json；GyroProbe.cpp和Test-ManualControls.ps1增加速度/自转覆盖；CodeStable、指南与机器汇总。用户原LK源码、电流环及外部只读目录不修改。

验证：保留普通/4rad/s自转失败基线，沿同路径检查2m/s改向、低速全套控制、红蓝波浪4rad/s慢行、坡面停车、普通ROS速度接入。单实例运行，记录模块/配置哈希，GUI与引擎内时序分开。

## 扩展诊断：持续小陀螺慢行

spin-after 的改向已通过361.4ms，但新组合用例(2m/s、4rad/s改向后Ctrl慢行)末速0.4283m/s超过0.35±0.03，整组失败。稳态样本方向误差可达9.7度，车速随旋转周期波动。该组合过去没有检查瞬时慢行速度；不宣称是本轮引入，也不将方向通过当整组成功。增加局部舵向刚度候选80→160Nm/rad验证；仍保留8Nm力矩上限/900deg/s舵向速率与所有接触动力学。若无改善恢复，诊断资料留存。

候选结果：刚度160、车体速度反馈1/0.5、物理子步1ms均未解决新增高自转慢行误差，全部恢复。当前产品只保留方向10m/s²、100m/s³两个参数调整。新增组合慢行波动单独记录在gyro-precision-ripple，不放宽门限也不标为通过。
