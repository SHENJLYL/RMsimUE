---
doc_type: issue-report
issue: 2026-11-16-drive-response-lag
status: investigating
severity: medium
tags: [swerve, gyro, latency, user-feedback]
---

# 用户再次反馈行进滞后

用户引用早期“舵向速度前馈”更新，明确表示滞后仍明显。继续按已有授权修复，不以历史低速通过代替本轮体验验收。暂以底盘移动/改向为主；已询问可选定位信息，尚未收到回复。

本轮单实例基线 artifacts/control/latency-20261116/baseline/default：19项/12PNG通过；静止四轮转80度101.4ms，持续自转舵角误差大多数数度。旧行进90度测试仅0.6m/s，无法覆盖默认3m/s前进/2m/s横移。

待证伪机制：VehicleDrive 的方向角速率由 LateralAccelerationMps2 / speed 限制；默认4m/s²、jerk40，在2m/s下预计明显慢于既有0.6m/s用例。先增加2m/s和持续小陀螺的同一物理链路测量，保留未改参数的失败证据。帧率、关节跟踪与命令整形分别评估，不称OS端到端测量。

范围：GyroProbe.cpp及Test-ManualControls.ps1诊断覆盖；若证实参数整形导致迟缓，仅调整车辆包内方向响应参数及一致默认值，不提高起步加速度、不改摩擦/碰撞、不替换用户LK电流环。
