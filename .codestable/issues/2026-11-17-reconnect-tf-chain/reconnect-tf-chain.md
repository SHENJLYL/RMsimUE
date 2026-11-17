---
doc_type: issue-report
status: reproduced_root_cause_pending
updated: 2026-11-17
---

# 重连后扫描坐标链间歇不可用

## 已验证与当前缺口

2000/140悬挂下，rmuc-07原Point-LIO/AMCL/Nav2波浪自主往返9项通过；rmuc-09主动导航断开自有bridge、重连、原节点重新定位、停止旧目标并完成新往返14项通过。监督器只读状态查询2秒超时清理重试实际处理3次丢回复。

随后最终源码rmuc-12复测在断线后重连阶段超时，6个先行检查通过；完整恢复保持in-progress，不能仅用rmuc-09的一次通过标为稳定恢复。

## 本次直接观察

- 原Point-LIO第二次进程启动且IMU初始化完成；导航starts=2、session已更新，epoch仍0。
- 原map_server与AMCL日志显示active，AMCL收到560×322、0.05m地图。
- NavigationRunner停在localizing，initial_pose_sent=false，未向AMCL播种初值。
- pointcloud_to_laserscan连续收到aim_sentry/odom点云，但TF MessageFilter队列满，扫描链不能向稳定底盘坐标系输出有效数据。原始传感器/地形链并未被替换为真值。
- 根因尚未确定，后续应在失败运行期间采集odom→base_link→base_stabilized的实际TF、odom时间戳与/clock，以及各节点订阅匹配情况，区分时钟新鲜度、消息匹配与转换链恢复。

证据：`artifacts/navigation/rmuc-12-final-recovery/ros/verification.json`；同目录navigation子目录的02-pointcloud_to_laserscan.log、02-amcl.log、02-lifecycle_manager_localization.log及pointlio/run-0002.log。统一检查点：`artifacts/navigation/suspension-round2-20261117/checkpoint-summary.json`。

## 其他已修复与环境记录

地图metadata通过utf-8-sig同时接受有/无BOM，3项离线测试证明身份必填和错误JSON仍被拒绝；rmuc-11错图保护两项通过。此输入边界修正没有改定位/控制算法。

pixi.toml激活配置使用ROS_DOMAIN_ID=71、ROS_LOCALHOST_ONLY=1。外层shell设置176会被pixi覆盖，诊断必须读取实际子进程环境或在激活后显式设定域，不能只依赖外层变量。一次独立诊断没有发现发布者时，主验证已在清理阶段，该空图不能用来断言此前没有数据。

后续继续保留0.25m实际终点门限、命令租约和碰撞，不以真值TF、自动重生或无条件重试导航目标通过测试。实际R重置、全场路线和Ubuntu另行验收。
