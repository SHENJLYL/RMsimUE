---
doc_type: issue-report
issue: 2026-11-16-nav-lifecycle-reply-loss
status: reproduced
severity: medium
tags: [navigation, lifecycle, rpc, native]
---

# 导航启动卡在配置阶段

历史rmuc-04-wave-diagnostic的AMCL记录已配置，但发送/amcl/change_state回复出现“client will not receive response”超时；生命周期管理器停在Configuring amcl，导航未播种初值。原进程无崩溃，不能将观察超时误当终止。

本轮未发现遗留导航进程。原生隔离夹具在ROS_DOMAIN_ID178、本机通信中故意让配置状态提交后不返回回复；原管理器8秒后仍无is_active响应，只有一次configure调用，保留artifacts/navigation/lifecycle-reply-loss/before/verification.json。

新轮胎下原LK+真实传感器+Point-LIO RMUC11项已经通过，约1.386m路线的IMU点对齐误差约3.58mm。这证明该新轮胎联合链路可运行，不代表波浪Nav2已经通过。
