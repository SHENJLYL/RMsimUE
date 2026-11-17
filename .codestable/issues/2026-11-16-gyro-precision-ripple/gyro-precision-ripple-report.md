---
doc_type: issue-report
issue: 2026-11-16-gyro-precision-ripple
status: native_verified_gui_input_pending
severity: medium
tags: [gyro, precision, physics, control]
---

# 高自转慢行速度波动

新增组合用例发现：2m/s横移、4rad/s自转、90度改向，再Ctrl慢行至0.35m/s。改向361.4ms通过，但慢行2秒时参考原点实际速度0.4283m/s，原有±0.03m/s门限失败。phase18采样显示周期波动；不能将质心速度冒充底盘参考原点速度。该组合旧用例未覆盖，尚无证据证明由本轮方向参数变化引入。

证据：artifacts/control/latency-20261116/spin-after/control-result.json。原生同一物理链路，无车体位置/速度覆盖，只有4轮接触地面，没有carrier/车体碰地证据。当前舵向速度前馈确实较早期无前馈减少跟踪滞后；历史目标变化率/角误差回归约0.115s→0.014s。

已排查并撤回的候选：
- 舵向刚度80→160：末速0.4242m/s，仍失败；恢复80。
- LinearVelocityFeedback1、YawVelocityFeedback0.5：静止90度转向拖到313.4ms；恢复0/0。
- 物理子步4.1667ms→1ms：末速0.4351m/s，仍失败；DefaultEngine.ini已逐字恢复。

未放宽门限，不将spin-after整组标为通过。主改向滞后修复与本问题分开：前者有2m/s普通/自转前后对照，本问题还需受力/舵向/轮速时序诊断或模型参数校准。用户源控制器、源模型目录未修改。

最终根因、撤回记录和92项回归见同目录fix-note；历史失败数据继续保留。
