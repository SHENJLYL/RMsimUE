---
doc_type: feature-acceptance
feature: 2026-11-15-contact-vehicle
status: verified_baseline_superseded
summary: Original 11-part contact baseline passed real wave routes and contact integration, then evolved into swerve-gyro-control after user handfeel feedback.
tags: [contact, physics, terrain, baseline]
date: 2026-11-16
---

# 接触车辆历史基线验收

11部件/9独立刚体的接触基线在Windows原生通过了红蓝波浪完整往返：artifacts/gameplay/contact-physics/wave-native-09（1m/s）、wave-native-10-slow（0.35m/s），共4个路线运行、16项检查与24张PNG。

integration-03-recovery另有10项/12PNG：共用转向、发射命中、实体墙阻挡、R和跌出地图恢复。真实车体凸包、轮地接触与源文件只读完整性有独立记录。失败01–08保留，根因包括电机符号、粗凸包填平凹槽、关节质量调节。

用户随后指出手感及小陀螺需求，因此未把旧差速驾驶称为用户手感验收通过。该基线的资产/代码/Config保存在artifacts/assets/gyro-control/before；当前实现已由2026-11-15-swerve-gyro-control的15部件/13刚体接替。最新45项组合与GUI观察只在新feature报告维护。

旧基线的接触能力完成；实车标定、完整控制节点、比赛与网络仍不属于此done状态。
