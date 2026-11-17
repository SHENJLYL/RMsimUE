# RM Simulator

UE5 + ROS 2 RoboMaster 仿真工程。长期目标是原生 Windows/Ubuntu 同源算法测试、完整比赛规则、联机及地图/车辆扩展；目前已完成第一套 **Windows 手动 AIM 哨兵样例**。

## 直接运行

射击训练双击 **Play-Training.cmd**：左键开火、R重开，HUD显示靶血量/弹量/热量/倒计时。[训练功能与参数](docs/guides/training-combat.md)

双击根目录 **Play-ManualVehicle.cmd**，默认进入 **DJI RMUC2027 V1.0.0来源场地**。它使用本机 `E:\simulator-tools\UE_5.8` 和已构建、导入的资产；不重复编译或下载。其他引擎位置可设置 `UE_ROOT`。

战雷CDK地形：`Play-ManualVehicle.cmd assets\maps\wt_cdk_crop\map.json hillside`。[地图切换与来源](docs/guides/map-packages.md) · [Ubuntu离线准备](docs/guides/ubuntu-offline.md)

| 输入 | 行为 |
|---|---|
| W/S、A/D | 前后移动、转动车体 |
| Space | 制动 |
| 鼠标、C | 瞄准；按住C自由观察并保持目标 |
| Shift、右键 | 切瞄准镜；临时放大 |
| 滚轮 | 第三人称距离 / 瞄准镜倍率 |

[操作与灵敏度设置](docs/guides/manual-controls.md) · [构建、导入和测试](docs/guides/unreal-import-lab.md)

## 当前实证

- UE 5.8.4 / Win64 实际构建、7部件FBX与PBR导入、尺寸核验通过。
- 9项运动学碰撞/云台/相机检查通过；18项手动输入行为检查通过，包括自由观察、切镜、变倍、快速瞄准收敛。
- 真实窗口已观察到手动移动、转向，程序记录到 W/S/A/D/C/Shift/滚轮等输入。
- 原始 WarThunder 模型与贴图的29个文件哈希保持一致。
- 原生Windows ROS2 Humble的双进程通信、Python包构建已验证；尚未接入完整sentry_ws。
- RMUC2027与WT CDK地形各7项引擎检查通过，WT斜坡另7项通过；旧操控18/18保持通过。RMUC默认双击入口已实际显示。

当前支持平地/地形运动学和带弹道、伤害、弹热限制的单车训练。RMUC为静态CAD场地；弗兰德斯原资源已准备，尚待完整场景运行验收。无校准轮地动力学、完整赛季裁判、联机或原生Ubuntu运行验收。最新要求优先仿真功能、地图并行，先Windows并预备Ubuntu离线适配。

## 文件按类型组织

| 路径 | 内容 |
|---|---|
| `.codestable/` | 需求、架构、计划、设计、问题和验收 |
| `docs/` | 操作指南、调研、进度和逐次变更 |
| `assets/vehicles/` | 模型、贴图、车辆描述；aim_sentry为当前真实模型包 |
| `assets/maps/` | 地图描述、来源网格、导入描述与来源记录 |
| `schemas/` | 数据协议 |
| `src/rm_vehicle_tools/` | 标准库Python车辆校验与URDF规范化 |
| `src/rm_map_tools/`、`tools/maps/` | 平地占用导出、来源地图转换与验证 |
| `unreal/RMSim/Source/`、`Config/` | UE运行时代码与配置 |
| `tools/assets/`、`tools/unreal/`、`tools/environment/` | 导出、构建/导入/测试、ROS环境工具 |
| `tests/` | 离线验证用例 |
| `artifacts/` | 按类型和日期保存的日志、JSON结果和图像 |

## 新车与验证入口

手动visual rig通过 `asset_import.json` 导入；`Start-ManualVehicle.ps1 -EngineRoot <UE> -Manifest <包入口>` 使用同一个导入和运行流程。支持范围见[模型包说明](docs/guides/aim-sentry-assets.md)，不承诺任意CAD自动产生正确物理。

受限URDF工具示例：`python tools/vehicles/verify_samples.py`。它只验证离线数据，不能代替UE或真实动力学验证。

[当前进度](docs/progress/current.md) · [最新变更](docs/changes/2026-11-15.md) · [当前架构](.codestable/architecture/ARCHITECTURE.md) · [长期规划](.codestable/roadmap/rm-simulator/rm-simulator-roadmap.md)
