---
doc_type: feature-acceptance
feature: 2026-11-16-ros-aim-fire
status: accepted-scoped
summary: Native original aim/scan messages control the scene gimbal and real training projectiles with independent leases and scoped feedback.
tags: [ros2, aiming, training, native]
last_reviewed: 2026-11-16
user_review: not_requested_under_standing_authorization
---

# 验收范围

Windows原生消息、控制、弹道和状态接口通过。原消息定义未改，20个源文件的Git blob与工作副本核对通过。测试按已知校准靶坐标计算瞄准角度，没有运行或伪造真实视觉识别。云台仍为有限转速运动学执行器和中立位碰撞代理；原硬件云台/发射电机环、动态云台碰撞和标定未完成。长期goal保持active。

## 最终证据

统一记录 `artifacts/control/ros-aim-fire/release-summary.json`，含模块/源码/报告哈希、安装源码一致性与GUI观察。

| 范围 | 结果 | 目录 |
|---|---|---|
| 普通ROS瞄准/发射 | 21项通过；原消息、扫描、世界方向、限位、实际命中、热量/弹量、独立有效期、错包/重连、真实R和CLI | native-06-release/aim |
| 原LK底盘联合 | 20项通过；原电流底盘在固定1ms物理时间下与外部瞄准/发射并行 | native-04-lk-combined/aim |
| 只观察与手动反馈 | 4项通过；ROS和wire都不能取得瞄准控制，真实鼠标运动有角速度反馈，R重连 | native-05-observe/aim |
| 射击影响面 | 15项/10PNG通过；含新增150ms炮口转动、两发实际碰撞与1/3时间分割一致性 | regression-combat |
| 普通速度ROS | 11项通过 | regression-velocity/ros |
| 普通wire | 19项/106张RGB通过 | regression-tcp/tcp |
| 手动小陀螺 | 19项/12PNG通过 | regression-manual/default |

14项协议测试、9个PowerShell语法检查和Bash离线语法检查通过。原生Windows结果不计为Ubuntu运行。UE测试包装器在客户端检查完成后终止自己启动的游戏进程，不将包装器退出0误作UE正常退出0；手动/射击probe单独有真实进程退出0。

## 行为证明

- 真实球形扫掠弹丸击中校准装甲：一发、一命中、HP100→80；没有通过直接设置HP构造结果。
- 2秒连续触发只允许14发（受当前热量预算约14.2发限制），弹量扣减与发射数一致；自身状态没有靶HP或命中明细，评测数据在单独topic。
- 世界方向yaw0.5/pitch0.15rad到位；底盘转动期间保持世界瞄准；JointState与真实相机光学TF一致。精度仅为运动学模型数值结果，不是硬件性能。
- 持续瞄准流量不能延长旧底盘运动，持续底盘流量不能维持旧发射。断线/R撤销许可，必须收到新许可；R只使训练epoch增加一次。
- 实际GUI见普通外部瞄准模式240–270FPS，R后ammo100、靶HP恢复；随后安装aim_demo以新许可驱动云台。只观察模式实测手动yaw=-0.194658rad、velocity=-9.424778rad/s；其R完成后窗口自动关闭，未记录复位后截图。
- 最终普通RMUC手动窗口已恢复并R复位，观察235–245FPS，未启用外部ROS控制。

## 实现与缺陷修正

RosAimController独立管理接受序号、200ms有效期、世界角目标、速度扫描与发射许可。消息路由不续期其他通道。GameMode只处理明确所有权，训练时间用Chaos差分，先推进旧指令再接收新指令；发射事件在帧前后炮口姿态中插值。

原扫描pitch向上、自瞄pitch向下，依据固定Hub/行为树消费者转换。上游扫描空frame_id有明确世界系默认；未知frame拒绝。原Hub另有RC/friction门控且未直接消费shoot，因此本功能准确命名为高层仿真直连，不宣称已运行完整原Hub。

联合测试曾暴露新指令清除mechanically_limited的瞬时反馈错误，实际角度始终正确。修正后以执行结果更新标志，既有门限未放宽。只观察模式角速度改为真实角度差采样，不再固定报0。原失效证据保留在native-03-lk-combined。

## 未完成范围

完整SP视觉输入后端/识别、原硬件云台与发射器电气闭环、动态云台碰撞、IMU/LiDAR、官方裁判与联机、Ubuntu原生运行及实车标定均未通过本切片。训练射击模型不等于RMUC年度裁判规则。

使用见 [ROS云台与发射指南](../../../docs/guides/ros-aim-fire.md)。
