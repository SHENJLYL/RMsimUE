---
doc_type: goal
status: active
updated: 2026-11-18
authorization: user_request_in_current_chat
---

# 持续目标：RoboMaster 仿真具体样例

在 `D:\simulator` 持续实现 RoboMaster 仿真具体样例：保留 Windows/Ubuntu 原生同源 ROS 2、联机权威比赛、高画质与可换地图的工程目标；建立并持续更新 CodeStable 基线/变更/进度记录，按类型管理文件；实现并验证即插即用车辆包导入和可运行的最小样例。按真实验证结果记录完成与缺口，持续推进直到样例基本成功或外部阻碍无法解决。

## 本聊天接续顺序

1. 建立可回滚的 Git 基线并上传 GitHub。用户随后明确指定公开仓库 `SHENJLYL/RMsimUE`；main 与两个首轮标签已实际推送。源码、配置、来源和精简证据入库，构建产物、大型/受来源限制的资源和凭证保留本地。
2. 优先处理用户反馈的 AIM 哨兵悬挂偏软、阻尼感不足。保存原参数，在真实引擎中对比瞬态响应、制动和波浪路线；通过验证后更新默认车辆包。
3. 接续新轮胎下 RMUC 波浪导航终点精度、往返、断线及 R 恢复；之后继续完整自主链、通用车辆导入、裁判、联机和原生 Ubuntu。

## 验证与记录

每次功能变更记录命令、退出码、配置/模块哈希、实测结果与未覆盖范围。失败实验保留，验收门限不为通过而放宽。源码、离线测试、原生运行、GUI 操作和实车标定分别记录。

当前功能边界以 `docs/progress/current.md` 与时间更晚的原始 verification.json 为依据。文档状态 active 表示项目目标；它不代表有无人值守执行器或聊天外后台任务正在运行。

## 文件约定

需求/设计/问题：`.codestable/`；用户指南/变更/进度：`docs/`；源码：`unreal/RMSim/Source`、`ros2_ws/src`、`src`、`tools`；包与配置：`assets/`；实际运行产物：`artifacts/`。原模型与原控制器来源目录保持只读。

## 当前检查点（2026-11-17）

可视化导航、sentry导航偏航目标整形及云台安装Mid-360已完成Windows限定验收：84项原生、13项RViz实际选点/航点/取消，离线120通过、2权限跳过。Play-Navigation.cmd与Stop-Navigation.cmd为用户入口；源模型和2000/140悬挂保持。汇总artifacts/navigation/sentry-console-20261117/release-summary.json。长期goal继续active，下一步补新Mid-360组合实际R/断线、更多RMUC路线，再推进通用车辆、多机器人隔离、完整感知决策/裁判、权威联机及Ubuntu原生验收。

## 当前工作切片：原 sentry_ws 控速与实时性能

2026-11-17 增加实验目标：导航速度、变速、避障和滤波参数从 sentry_ws ROS YAML 读取；AIM 当前 3m/s 前进上限在该 ROS 源文件调整，允许新车替换自己的文件。长波浪路 0.25m 验收及标准 1× 实时仍 open，详细问题与试验在 .codestable/issues/2026-11-17-navigation-source-realtime。原先长期 goal 继续 active，main 已验收基线保持。

## 2026-11-18 最新用户优先级：精度与性能

用户追加要求继续减小导航误差并提高当前模拟器性能。当前2000/140悬挂、真实接触和原源插件保持；正式性能验收采用用户默认的无重型CSV记录配置。rmuc-09的90秒clock 32项已通过，整体0.999865484×、滚动十秒窗0.987387732–1.011459269、Draw59.668945Hz；同次航点真实误差0.285641m使动作门禁失败，完整版本仍未验收。

接续任务是恢复所选sentry YAML对到点容差的控制权（适配层现有固定0.10m仅保留legacy），当前Simulation先选择0.05m；区分真实停车误差、同步地图定位偏差和规划路径末点差。新增LaserScan、/plan端点、姿态及map→odom TF的只读证据；真值只用于独立评价。目标争取实际到点误差10cm以内，结果按实测记录，原25cm、取消、1×实时及传感器预算门槛保持。完成当前功能、可视化和兼容验收后提交用户指定GitHub，长期可换车/地图、Windows/Ubuntu同源、权威联机目标继续active。
