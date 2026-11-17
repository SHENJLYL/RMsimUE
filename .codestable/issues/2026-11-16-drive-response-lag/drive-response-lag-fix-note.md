---
doc_type: issue-fix-note
issue: 2026-11-16-drive-response-lag
status: scoped_native_verified_user_handfeel_pending
tags: [swerve, gyro, response, windows]
---

# 行进方向限幅修复

用户反馈滞后仍明显。原低速检查遗漏常用速度：2m/s时目标本身需约0.75秒转90度，真实轮转向约0.10秒。最终产品只改方向加速度4→10m/s²、方向jerk40→100m/s³，在VehicleDrive默认值和外部drive-profile.json保持一致。起步0.8、松键3、Space6m/s²以及摩擦、质量、电机力矩均未改。

| 实测条件 | 修复前实际改向 | 修复后实际改向 |
|---|---:|---:|
| 0.6m/s普通 | 254.6ms | 149.9ms |
| 2m/s普通 | 714.6ms | 358.5ms |
| 2m/s、4rad/s小陀螺 | 732.8ms | 361.4ms |

计时为提交输入至实际速度方向进入15度误差，真实引擎时序；新检查还验证1.5秒时方向<5度、速度误差<15%。不是OS键鼠到显示器时延。2m/s普通/自转稳态方向误差0.09/0.67度。旧门限没有放宽。

## 验证与边界

- 原生Win64构建成功。
- fast-after5项/4PNG；controls-after19/12PNG；红蓝波浪各5/6PNG；traction-after6/4PNG；ros-after11。合计51通过项/32PNG，同一模块和profile哈希，机器汇总artifacts/control/latency-20261116/summary.json。
- gyro组合spin-after仅改向分项通过，随后0.35m/s慢行实际0.4283m/s导致整组失败。此问题独立记录gyro-precision-ripple，未冒充完整通过。
- 刚度160、外环反馈1/0.5、物理子步1ms候选未解决该波动，全部恢复；汇总脚本逐字核对DefaultEngine.ini恢复，并断言profile只有两个方向参数变化。
- 原LK节点/电流环、用户原模型与源码没有修改；ROS SI速度入口回归通过。Ubuntu原生、实车标定与主观手感仍未验收。

## 文件

产品：assets/vehicles/aim_sentry/drive-profile.json、unreal/RMSim/Source/RMSim/VehicleDrive.cpp。
诊断：GyroProbe.cpp、tools/unreal/Test-ManualControls.ps1增加ResponseSpeedMps/ResponseSpinRadS覆盖与稳定后的速度/方向校验。
记录：本issue三份文档、gyro-precision-ripple report、docs/guides/swerve-gyro-controls.md、progress/current、changes及attention。

本次未提交Git。相关长期目标仍未完成，用户后续反馈优先于本次有限检查。

收尾GUI：新默认手动PID35612已实际显示RMUC2027，HUD235FPS/0km/h/gyro off。旧diagnostics_probe系统弹窗仍覆盖，PickerHost无可操作窗口；之前用户取消交接仍待完成，未再次索取许可，未放行防火墙。GUI键盘试驾不标通过。

后续更新：本轮发现的gyro-precision-ripple已在同名issue中完成92项原生回归，根因为错误轮胎圆柱和停车逐帧唤醒。当前新几何下2m/s普通/自转响应368.4/380.0ms，旧359/361ms为此前几何的历史数据；当前证据以artifacts/control/gyro-ripple-20261116/release-summary.json为准。
