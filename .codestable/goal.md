---
doc_type: goal
status: active
updated: 2026-11-17
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

本地Git基线及回退标签已建立，用户指定公开仓库SHENJLYL/RMsimUE已实际接收main及回退标签。AIM当前2000N/m、140N·s/m通过92项原生回归和两次各4项响应专项。新悬挂下原LK/Point-LIO联合11项已有证据。最新Windows导航重连修复已完成正式入口两轮RMUC往返各14项、平地实际R复位17项，共45项；源码/安装与实际模块哈希核对，原失败保留。统一证据artifacts/navigation/reconnect-tf-20261117/release-summary.json。长期goal保持active，下一步扩展RMUC出生点/实际R和路线覆盖、公开启动包装，继续通用车辆、多车隔离和完整自主/比赛/双平台目标。
