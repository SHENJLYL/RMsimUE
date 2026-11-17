---
doc_type: research
status: current
last_reviewed: 2026-11-13
scope: 公开源码与官方文档的只读调研，不含运行验收
---

# 来源与复用审查

审查日期：2026-11-13（Asia/Shanghai）。以下分开记录“来源宣称”“源码所见”“本机验证”。本文件没有宣称在本机运行过被调研仿真或 `sentry_ws`。链接的 `main/latest` 内容可能变化；重要代码优先使用固定提交。

## 1. 项目关系与复用边界

| 来源 | 已确认事实 | 对本项目的含义 |
|---|---|---|
| [深北莫 rmu_gazebo_simulator](https://github.com/SMBU-PolarBear-Robotics-Team/rmu_gazebo_simulator) | README 提供 Humble/Fortress、2024/2025 RMUL/RMUC 场地、网页操作、底盘/云台/射击 | 可复用模型、接口和算法联调经验；不是完整规则引擎 |
| [简化裁判源码](https://github.com/SMBU-PolarBear-Robotics-Team/rmu_gazebo_simulator/blob/main/rmu_gazebo_simulator/scripts/referee_system/simple_competition_1v1.py) | 1v1 两机器人、固定命中扣血、弹量统计/兑换、死亡/复活；计时使用 wall clock；RFID 回调仅保存消息 | 当前方案需独立版本化裁判与统一仿真时钟；不能沿用“完整裁判”表述 |
| [底层弹丸插件](https://github.com/SMBU-PolarBear-Robotics-Team/rmoss_gazebo/blob/7443ff06c345752d0e23172c479d99b27207e2c7/rmoss_gz_plugins/plugins/projectile_shooter/ProjectileShooter.cc) | 创建实体、设置初速度、接触后发命中事件并删除 | 有基础物理事件；未见可证明真实弹道校准的证据 |
| [rmul2027_simulation Dockerfile](https://github.com/bombomdragon/rmul2027_simulation/blob/main/docker/Dockerfile) | 继承深北莫 1.0.0 镜像，覆盖地图/world/launch | 是同一技术路线的适配包，不是独立引擎 |
| [rmul2027 README](https://github.com/bombomdragon/rmul2027_simulation) | 当前包默认单红车；文档明确静态场地、简化裁判、没有该场景命中判定及能量机关/飞镖 | 可用作地图接入参考，不能据仓库名称推定完整 2027 规则 |
| [rmul2027 NOTICE](https://github.com/bombomdragon/rmul2027_simulation/blob/main/NOTICE) | 代码 Apache-2.0 与场地原始版权分开声明 | 导入/分发地图时单独核验资产授权 |

## 2. 用户程序的真实接入点

检查入口：[27UL_Sentry_ws](https://github.com/unnc-aim/27UL_Sentry_ws)、[子模块配置](https://github.com/unnc-aim/27UL_Sentry_ws/blob/main/.gitmodules)。本次观察 main 指向的子模块含：`behavior aa4d82b7`、`pb2025_sentry_nav ffd68e5f`、`sp_vision 2bab801d`、`universal_controller 95c66c28`。在实施外部源码接入前应重新解析并锁定完整 commit。

| 边界 | 固定源码所见 | 实现要求 |
|---|---|---|
| [裁判与目标输入](https://github.com/unnc-aim/27UL_Sentry_behavior/blob/aa4d82b73ae3c404b3037f2630eb2950dc0bbea4/src/behavior_server.cpp) | 当前新行为树直接接 `dji_referee_protocol` 的 `/referee/common/*`、`/referee/parsed/common/*`，目标为 `auto_aim_interfaces` | 不用上游 `rmoss_interfaces/RobotStatus` 替代完整消息契约 |
| [底盘速度](https://github.com/unnc-aim/pb2025_sentry_nav/blob/ffd68e5f13882c08d806d1f2dcc715b4094ba4da/fake_vel_transform/src/fake_vel_transform.cpp) | 输入 Twist `cmd_vel_nav2_result`，叠加 `cmd_spin`，输出 TwistStamped `cmd_vel` | 不能仅同名连接上游 Gazebo Twist 接口 |
| [实车坐标参数](https://github.com/unnc-aim/pb2025_sentry_nav/blob/ffd68e5f13882c08d806d1f2dcc715b4094ba4da/pb2025_nav_bringup/config/reality/nav2_params.yaml) | `base_footprint`、`gimbal_yaw`、`gimbal_yaw_fake`；`translation_scale=-1.0` | 固定硬件符号补偿不能无核对地用于仿真 |
| [云台扫描](https://github.com/unnc-aim/27UL_Sentry_behavior/blob/aa4d82b73ae3c404b3037f2630eb2950dc0bbea4/plugins/action/pub_gimbal_velocity.cpp) | `pb_rm_interfaces/GimbalCmd` 含速度与扫描范围 | 实现限位、扫描和真实动态 TF，而非仅显示转动 |
| [仿真点云转换](https://github.com/unnc-aim/pb2025_sentry_nav/blob/ffd68e5f13882c08d806d1f2dcc715b4094ba4da/ign_sim_pointcloud_tool/src/point_cloud_converter.cpp) | 加入 ring/time，intensity 设 0，时间由点序号生成 | 这不是经过实机标定的 Livox 扫描；新传感器需验证逐点时间与字段 |
| [行为树主流程](https://github.com/unnc-aim/27UL_Sentry_behavior/blob/aa4d82b73ae3c404b3037f2630eb2950dc0bbea4/behavior_trees/competition_phase1.xml) | 使用绝对 `/map`、map→base_footprint、硬编码巡航点、NavigateThroughPoses | 多机器人和换地图需要 namespace/命名目标适配 |
| [行为树 launch](https://github.com/unnc-aim/27UL_Sentry_behavior/blob/aa4d82b73ae3c404b3037f2630eb2950dc0bbea4/launch/pb2025_sentry_behavior_launch_new.py) | namespace 默认空、use_sim_time 默认 false，支持覆盖 | 必须显式统一仿真时钟与命名空间 |
| [实际服务配置](https://github.com/unnc-aim/27UL_Sentry_ws/blob/main/ros2_services.yaml) | 启用硬件/视觉/控制服务，旧 behavior/nav 条目注释；新 behavior 未列为活动项 | 仓库存在新代码不代表默认服务运行它 |

公开 `scripts/coordinate_sim` 的 README 描述了有价值的 TF/导航回归，但本次 tree 未见其引用的 launch.py、scenario.py、test_bridge.py。可复用验证思路，不能声称公开克隆立即可复现该记录。[目录](https://github.com/unnc-aim/27UL_Sentry_ws/tree/main/scripts/coordinate_sim)

## 3. 原生 Windows/Ubuntu 的已知障碍

| 来源 | 事实 | 状态 |
|---|---|---|
| [Point-LIO 构建](https://github.com/unnc-aim/point_lio/blob/3099b9dd65345f6ba2f2c56404501f96610d2a15/CMakeLists.txt) | 无条件 GNU flags；强制依赖 `livox_ros_driver2`、PCL、PythonLibs 等 | 需要同源构建调整，尚无 Windows 验证 |
| [Livox 构建](https://github.com/unnc-aim/livox_ros_driver2/blob/a8230b02c1f862371c554673cc4f7d029d8f1928/CMakeLists.txt) | 自定义消息和硬件 target 绑定，链接 Linux `.so` | 需要保留 IDL/包契约的接口构建模式；不能直接跳过包 |
| [视觉顶层构建](https://github.com/unnc-aim/sp_vision_25_rosy/blob/2bab801d91c6944dbdb99cbb4d4d431ed15d6e0d/CMakeLists.txt)、[IO 构建](https://github.com/unnc-aim/sp_vision_25_rosy/blob/2bab801d91c6944dbdb99cbb4d4d431ed15d6e0d/io/CMakeLists.txt) | `/opt/ros/humble`、OpenVINO 目录写死；相机/串口后端同时构建 | 算法复用需要输入后端和依赖发现改造 |
| [行为树构建](https://github.com/unnc-aim/27UL_Sentry_behavior/blob/aa4d82b73ae3c404b3037f2630eb2950dc0bbea4/CMakeLists.txt) | GNU flags 无条件；已有 BT_PLUGIN_EXPORT 和 RUNTIME 安装项 | 需验证 MSVC/DLL/BT/Nav2 版本，不能武断称完全不支持 Windows |
| [控制器构建](https://github.com/unnc-aim/universal_controller/blob/95c66c28717e0b4d0f586c2ebf8df99d47a24ab0/CMakeLists.txt) | Hub 和底盘/云台/发射控制器合编，依赖 custom_msgs 与 chassis_controllers | 尚未证明移除硬件节点后整个依赖闭包可构建 |
| [small_gicp 构建](https://github.com/unnc-aim/small_gicp_relocalization/blob/a0d2048317ed0810d60fcb368dc97f547f833e7e/CMakeLists.txt) | FetchContent 使用 master | 需要锁定依赖以支持双平台可重复结果 |

导航子模块 `.gitmodules` 中 Point-LIO/small_gicp URL 指向 SMBU，而被锁定 SHA 可从 UNNC fork 取得。首次原生环境验证必须核对仓库 URL 与 SHA 来源，不把此审查当成已完成 clone/build。

## 4. 平台文档与推论边界

- [Isaac Sim 当前 Nav2 官方教程](https://docs.isaacsim.omniverse.nvidia.com/latest/ros2_tutorials/robot_control/tutorial_ros2_navigation.html) 明确支持 Linux/Windows 的 Pixi 原生路线。这证明存在原生示例路线，不证明用户定制的 Humble/Nav2/Point-LIO 已兼容。Isaac 是调研对照；主路线仍按用户选择为 UE5。
- [RoboStack Getting Started](https://robostack.github.io/GettingStarted.html) 展示 win-64/linux-64、Humble、平台构建任务与 Windows C++ 工具要求。可作为候选依赖管理办法，具体依赖求解和 ABI 尚未验证。
- 新平台是否达到所需帧率、点云采样率、延迟或物理精度，必须本机运行测量；本次调研没有生成这些结果。

## 5. 本地证据入口

- `.codestable/features/2026-11-13-vehicle-package-v1/`：首切片 design/checklist/验收的记录位置。
- `.codestable/architecture/ARCHITECTURE.md`：只记录已实现结构。
- `.codestable/roadmap/rm-simulator/rm-simulator-items.yaml`：各阶段未开始/进行中/已验收状态。

后续安装或运行时新增实际日志位置和命令，不覆盖以上只读调研的证据性质。

2026-11-16后续执行：Point-LIO3099b9dd在现名unnc-aim/26UL_Sentry_point_lio、Livox a8230b02在26UL_Sentry_livox_ros_driver2取得完整原checkout。Windows原消息及估计器已构建，解析回放/实际UE点云与IMU联跑有证据。原消息保留，硬件SDK未假装移植；源码补丁处理构建兼容和配置读取失败继续运行的问题。当前证据在artifacts/sources/point_lio和artifacts/sensors/lidar；原生Ubuntu、完整导航及实车校准仍未完成。
