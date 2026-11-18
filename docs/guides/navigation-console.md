# 可视化导航控制台与云台 Mid-360

## 打开与关闭

在已准备资产的本机，双击根目录 **Play-Navigation.cmd**。它启动 UE 场景、原生 ROS 桥、Point-LIO、AMCL/Nav2，并在定位就绪后打开 RViz。默认地图为 RMUC2027 红方波浪路线；平地示例使用 `Play-Navigation.cmd import_lab`。

关闭 RViz 会结束本次启动器拥有的整套导航进程，也可双击 **Stop-Navigation.cmd**。手动驾驶入口是 `Play-ManualVehicle.cmd`，训练入口是 `Play-Training.cmd`。同一工程使用一个 UE 游戏实例；启动器会检测已有实例，先关闭当前手动预览再打开导航。

新机器先按仓库说明准备 UE、合法来源资产和原生 ROS 环境，再运行 `tools/environment/Prepare-NavigationConsole.cmd`。当前实际运行证据来自 Windows；Ubuntu 同源入口与原生运行结果分别记录在[当前进度](../progress/current.md)。

## 单点与多航点

RViz 显示 `Navigation: active`、`Localization: active` 后，点击顶部 **Nav2 Goal**，在白色可通行区域按住左键并拖出目标箭头，松开后发送原 Nav2 action。绿色路径和导航面板显示规划结果及任务状态。途中点击面板 **Cancel** 取消任务。

多航点操作顺序：

1. 打开 **Waypoint / Nav Through Poses Mode** 累积模式。
2. 用 **Nav2 Goal** 逐一选择地点。
3. 点击 **Start Waypoint Following**，由原 `FollowWaypoints` action 顺序执行。
4. **Cancel Accumulation** 清除尚未发送的点队列；行驶中的 **Cancel** 取消活动任务。

**Start Nav Through Poses** 保留另一个原生 action 入口，其专项覆盖见验收记录。目标箭头提供朝向，位置到达后的最终朝向行为由所选源配置控制。

UE 窗口保留 Space 人工制动与 R 复位。R 会产生新的 epoch、清除旧目标并重建定位导航；当前组合的实际 OS 按键恢复范围见当前进度。地图视图可用 Move Camera 平移、旋转和滚轮缩放。

## 地图与定位显示

RViz 固定坐标系是 `aim_sentry/map`，显示导航地图、代价地图、估计位姿与协方差、规划路径、激光扫描、Point-LIO 配准点云和安装 TF。定位使用原 Point-LIO 和 AMCL，仿真真值只进入独立评价话题。

RMUC 地图包通过 `assets/maps/rmuc2027/navigation-console/SOURCE.json` 声明独立的几何定位图。AMCL 接收该定位图，原可通行地图继续供规划与代价地图使用。显式 `-LocalizationMapYaml` 优先；没有包内声明的其他地图沿用单地图流程。

包内固定来源、坐标、栅格和生成器都进行身份校验。PGM/NPZ 缺失时，启动准备流程从已存在的原碰撞几何重建；已有文件校验失败时会报告具体文件。手动重建入口：

```powershell
.\tools\environment\run-env.ps1 -Command @('python','tools/maps/export_localization_surface_map.py','--restore-package','assets/maps/rmuc2027/navigation-console/SOURCE.json')
```

该流程需要本地已准备的原几何资源。地图包记录生成配方，源码仓库按来源与大小规则保存数据。当前已覆盖路线、实际误差和未覆盖区域均列在验收记录中。

## 由 sentry_ws 决定速度、平滑和避障

当前默认源是 `ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml`；原始下载副本保存在 `third_party/sentry_nav_original`。前向上限与当前 AIM 手动配置的 5 m/s 对齐，其余导航曲线保留源值。

| 源参数 | 当前值 |
|---|---|
| `FollowPath.v_linear_max` | 5.0 m/s |
| `velocity_smoother.max_velocity` | `[5.0, 2.0, 3.0]`，前进/侧向/yaw |
| `velocity_smoother.min_velocity` | `[-1.5, -2.0, -3.0]` |
| `velocity_smoother.max_accel` | `[0.8, 0.8, 3.0]` |
| `velocity_smoother.max_decel` | `[-3.0, -3.0, -5.0]` |
| `general_goal_checker.xy_goal_tolerance` | 0.05 m，作用于导航估计位姿 |

实际速度由加减速、路径曲率、障碍代价、接近目标减速和真实轮地接触共同决定。真实到点误差另用独立评价位置测量，包含定位偏差和停车行为。

原 Omni PID、Theta*、速度平滑器和局部/全局代价地图继续工作。车辆的 `external_shaped_velocity` 执行原 ROS 已整形的目标，保留舵轮运动学、转向前馈、轮速 PI 和真实接触；手动输入的另一套平滑曲线保持在手动分支。

接入自己的源文件：

```powershell
.\tools\environment\Start-NavigationConsole.ps1 -ControlProfile simulation -NavigationSource 'D:\new_vehicle\sentry_ws\src\pb2025_nav_bringup\config\simulation\nav2_params.yaml'
```

`-NavigationFollowProfile` 可选择车辆的云台目标整形配置。当前默认 mesh-rig 和传感器仍是 AIM 样例；换车后的几何、安装和整套动力学需按车辆包验收。导航和 Point-LIO 读取选定源文件的快照；修改配置后重新启动会话生效。

## 标准仿真速度与渲染

默认导航采用真实 Chaos **5 ms 固定步长 / 200 Hz**，按单调墙钟推进完整物理步。该硬子步设置只覆盖当前导航进程；手动入口保留全局 1/240 s 设置。场景绘制采用独立 **60 Hz** 节拍，并保留上一帧纹理；地图、车辆网格与光照继续由原 UE 场景渲染。

Windows 混合核心平台默认 `-CpuPolicy performance`：从可用 CPU Sets 选择性能核心，并在启动前设置匹配的 UE 核心预算。已有 CPU/affinity 限制和显式预算会保留或严格核对。需要使用系统调度策略时：

```powershell
.\tools\environment\Start-NavigationConsole.ps1 -CpuPolicy system
```

策略只作用于本次启动的 UE 子进程。CPU 集合、核心预算、完整命令和读回结果进入会话来源记录。

当前导航默认 `-CameraHz 0`，关闭未使用的 ROS RGB 图像采集与同步读回，保留 CameraInfo 和安装 TF。UE 可见场景维持 60 Hz 目标；Mid-360 为 200k 射线/仿真秒、10 Hz 点云，两路 IMU 为 200 Hz。需要 RGB 算法输入时显式开启：

```powershell
.\tools\environment\Start-NavigationConsole.ps1 -Scene rmuc2027 -CameraHz 10
```

`-CameraHz` 接受 0 或 1–60 Hz。RGB 开启配置的实时性能独立验证；正式性能报告会明确记录相机状态、真实时钟比例、滚动时间窗、Draw 调用与传感器计数。

## sentry 云台跟随与实际 Mid-360 安装

`assets/vehicles/aim_sentry/navigation-follow.json` 参考原 `unnc-aim/universal_controller` 固定提交 `95c66c28717e0b4d0f586c2ebf8df99d47a24ab0`：最短角、最大目标转速 1 rad/s、角加速度 2 rad/s²、最低转动段速度 0.3 rad/s、容差 0.02 rad、输入有效期 0.2 s。实际导航速度、估计底盘航向和关节反馈共同转换云台目标；扫描/获准自瞄优先，导航不发射。

原模型 `radar_mid360 / bone_turret` 对应 `yaw_link`。零位光学中心相对底盘约 `[0.19812, 0.07376, 0.36119] m`，前倾约45°；来源与测量在 `sensors/mid360-source.json`，运行参数在 `mid360.json` 和 `mid360-imu.json`。光学原点、TF、射线和共装 IMU 随真实关节运动，光学窗口可透射，其余车体保留遮挡。

当前理想扫描模型为360°水平、-7°至52°俯仰、200k射线/仿真秒、10Hz；设备专有扫描图样、反射强度与实车标定继续按传感器范围跟踪。Point-LIO 使用 ROS SI 单位；当前 source `acc_norm=9.81`、`satu_acc=30.0`，实际饱和阈值为30.0 m/s²。源参数决定滤波、协方差和 IMU 队列深度，安装外参由车辆传感器包提供。

## 实际验证记录

最新结果、命令、模块/配置身份和完成范围见[当前进度](../progress/current.md)及[本轮变更](../changes/2026-11-18-navigation-realtime.md)。`artifacts/navigation/realtime-20261118` 保留每轮原生结果和失败实验；最终汇总严格关联同一套当前输入。

原14项导航动作、25cm真实到点、取消停车和完整传感器预算门限保持；10cm作为额外精度目标单列。GUI选点/航点/取消、实际可见画面、标准实时性和原生控制兼容性分别记录。更多出生点、完整场地、原生Ubuntu、多车与权威联机沿长期目标继续推进。
