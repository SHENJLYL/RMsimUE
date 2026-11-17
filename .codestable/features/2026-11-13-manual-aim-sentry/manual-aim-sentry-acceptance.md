---
doc_type: feature-acceptance
feature: 2026-11-13-manual-aim-sentry
status: passed
summary: Windows UE 5.8.4 manual AIM sentry sample runs with imported assets, collision guards, cameras and WT-inspired controls.
tags: [manual, unreal, acceptance]
date: 2026-11-15
user_review: interactive feedback incorporated; no claim of full project acceptance
---

# Windows 手动样例验收

本验收只覆盖用户优先要求的现有哨兵手动样例。完整双平台、联机与机器人算法系统仍未完成。

## 1. 接口契约

真实模型通过 asset_import.json → FBX/PBR → UE资产 → ue_mesh_rig.json → VehicleRig 加载。7部件尺寸从实际UE对象读取，与源描述的最大差约6.32e-6cm。验收路径不是另一个硬编码替代模型。

## 2. 行为与决定

手动输入和控制回归共用 ApplyManualInput；底盘与云台仍使用原 MoveManual/NudgeGimbal。相机视差重新投影与单次鼠标缩放已修复；保留真实炮口标记，未隐藏误差。碰撞为保守盒查询与轮参考姿态proxy，不是轮地接触动力学。

## 3. 验收场景

| 条目 | 证据与结论 |
|---|---|
| A1 源文件保留 | artifacts/assets/source-recheck-2026-11-15.json：29个源文件哈希一致 |
| A2 导入尺寸/pivot | imported-mesh-bounds.json、mesh-scale-verification.json及真实PNG，7部件通过 |
| A3 手动/碰撞 | probe-07-final-controls 9/9；controls-04-aim-fix 18/18；wt-input-aim-gap-before-fix.json记录真实按键与位姿 |
| A4 两种相机 | controls-04-aim-fix 的chase/free-look/scope/scope-zoom/aim-converged共10张PNG；已视觉检查车辆完整、镜头不同 |
| A5 可复现运行 | build.log成功，Run/Test脚本真实退出0并校验报告及PNG；快速.cmd为当前安装路径的直接启动入口 |

输入回归调用与键盘相同的控制函数，不等同OS输入压力测试。用户真实窗口操作另有按键计数和位姿证据；自由观察精确保持、切镜与收敛由引擎自动数值检查补充。

## 4. 术语

manual sample、kinematic probe、control probe、真实硬件标定分别标记。图像预览不宣称真实相机噪声或成像校准。

## 5. 架构归并

当前架构记录加载器、手动输入、相机与独立验证模块；原生Windows运行已成立。比赛服务器和ROS适配仍列未来计划。

## 6. Requirement

extensible-worlds-vehicles增加已运行的手动visual rig子集；其完整地图/车型物理扩展愿景仍为draft。

## 7. Roadmap

manual-aim-sentry标done，仅表示本Windows手动样例。native-sentry-profile、ue-vehicle-runtime（含真实惯量/驱动）、比赛规则、联机与地图包仍未完成。

## 8. 工程约束

保留EngineRoot显式路径、真实JSON结果门禁和PNG解码；不同层级的“通过”不可互相代替。

## 9. 后续

原生Ubuntu、可打包独立发布、物理参数标定、场地包、射击与裁判、联机、ROS2算法接入仍需单独实施和验收。现存关节限位是样例参数。
