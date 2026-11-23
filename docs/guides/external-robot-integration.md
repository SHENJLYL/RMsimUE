# 外部机器人与算法接入指南（车图解耦 + ROS 2 契约）

本指南说明如何在**不修改仿真器 C++ 源码**的前提下，向 `D:\simulator` 接入一台独立的机器人（包含车辆建模/URDF、物理参数、传感器与外部 `sentry_ws` 算法工作空间）。

---

## 1. 统一启动器（GUI 入口）

双击根目录的 [`Play-Simulator.cmd`](../../Play-Simulator.cmd) 打开统一图形启动器，支持：
* **运行模式选择**：纯仿真 + 标准 ROS 2 桥（外部机器人接入）、手动试驾、射击训练、内置 RMUC 37.82s 导航基线、弗兰德斯战雷原图。
* **车辆包选择**：自动发现 `assets/vehicles/*` 下的全部车辆包（`aim_sentry`、`training_diff`、`sentry_omni`），并支持点击 **浏览外部车辆包...** 加载任意外部目录的 `vehicle.json` / `vehicle.canonical.json`。
* **地图与出生点选择**：自动发现 `assets/maps/*`（`import_lab`、`rmuc2027`、`warehouse_lanes`、`wt_vlaanderen` 等）并自动组合车图物理模型。
* **外部 `sentry_ws` 独立对接**：先启动「纯仿真 + 标准 ROS 2 桥」，再点击 **单独对接外部 sentry_ws**（或在外部终端运行你自己的 `ros2 launch`）。

---

## 2. 接入一台新机器人（建模 + 物理模型）

准备一个标准车辆包目录（参考 `assets/vehicles/training_diff` 或 `assets/vehicles/sentry_omni`）：

```text
my_robot/
├── vehicle.json          # 车辆清单（schema_version: 1）
└── model/
    └── robot.urdf        # 标准 URDF（含 links / inertial / visual / collision / joints）
```

`vehicle.json` 关键字段：
* `id`：机器人唯一标识（同时作为默认 ROS 2 话题命名空间 `/<id>/...`）
* `control.type`：支持 `differential_drive`（差速轮）或 `holonomic`（全向底盘）
* `control.wheel_joints`：驱动轮关节名列表
* `control.gimbal`（可选）：`{ "yaw_joint": "...", "pitch_joint": "..." }`
* `control.limits`：`{ "linear_mps": 3.0, "angular_rps": 4.0 }`

### 命令行独立组合与启动

```powershell
# 1. 单独组合任意车辆 + 任意地图的 MuJoCo 场景（产出 map.xml + vehicle.xml + model.xml + manifest.json）
.\artifacts\environments\mujoco-4.0.1\Scripts\python.exe .\tools\mujoco\compose_scene.py `
  --vehicle .\assets\vehicles\sentry_omni `
  --map .\assets\maps\import_lab `
  --output .\artifacts\mujoco\composed\sentry_omni__import_lab `
  --overwrite

# 2. 启动纯仿真 + 标准 ROS 2 桥（自动完成组合并启动 UE5 + rmsim_bridge）
powershell -NoProfile -File .\tools\unreal\Start-Simulator.ps1 `
  -Vehicle .\assets\vehicles\sentry_omni `
  -Map .\assets\maps\import_lab `
  -RosBridge -RosControl -RobotId sentry_omni -RosPort 9451 -Domain 71
```

---

## 3. 外部 `sentry_ws` / ROS 2 算法接入契约

当以「纯仿真 + 标准 ROS 2 桥」启动时，仿真器**不会启动任何机器人业务算法节点**，仅通过 `rmsim_bridge` 暴露以下标准接口（假设 `ROS_DOMAIN_ID=71`，`robot_id=<id>`）：

| 方向 | 话题 / 接口 | 消息类型 | 说明 |
|---|---|---|---|
| 仿真 → 算法 | `/clock` | `rosgraph_msgs/msg/Clock` | 权威物理仿真时钟；外部节点必须设置 `use_sim_time:=true` |
| 仿真 → 算法 | `/tf` | `tf2_msgs/msg/TFMessage` | 发布 `<id>/base_link` → 传感器与 `<id>/yaw_link` 的安装/关节变换 |
| 仿真 → 算法 | `/<id>/camera/image_raw` | `sensor_msgs/msg/Image` | 相机 RGB8 图像（`-CameraHz > 0` 时发布） |
| 仿真 → 算法 | `/<id>/camera/camera_info` | `sensor_msgs/msg/CameraInfo` | 相机内参 |
| 仿真 → 算法 | `/<id>/imu/data_raw` | `sensor_msgs/msg/Imu` | 六轴物理 IMU（`-RosImu` 启用） |
| 仿真 → 算法 | `/<id>/lidar/livox` | `livox_ros_driver2/msg/CustomMsg` | Livox 格式点云（`-RosLidar` 启用） |
| 仿真 → 算法 | `/<id>/lidar/points` | `sensor_msgs/msg/PointCloud2` | 标准 PointCloud2 点云（`-RosLidar` 启用） |
| 仿真 → 算法 | `/<id>/gimbal/joint_states` | `sensor_msgs/msg/JointState` | 云台真实关节角与角速度 |
| 算法 → 仿真 | `/<id>/cmd_vel` | `geometry_msgs/msg/TwistStamped` | 底盘速度指令（`frame_id=<id>/base_link`，带 250ms 租约保护） |
| 评测真值（隔离） | `/rmsim/evaluation/<id>/ground_truth` | `geometry_msgs/msg/PoseStamped` | 仅用于独立评测，禁止算法消费 |

### 独立进程对接示例

```powershell
# 仿真器已在运行后，在另一个终端单独启动外部 sentry_ws 导航栈：
powershell -NoProfile -File .\tools\environment\Start-ExternalSentry.ps1 `
  -RobotId aim_sentry `
  -Domain 71 `
  -NavigationSource D:\path\to\your\sentry_ws\src\pb2025_nav_bringup\config\simulation\nav2_params.yaml
```
