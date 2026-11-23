# 可视化导航控制台与云台 Mid-360

## 打开与关闭

在已准备资产的本机，双击根目录 **Play-Navigation.cmd**，或运行 `D:\simulator\Play-Navigation.cmd`，无需附加参数。标准入口依次打开 **MuJoCo 观察窗口、UE5 场景窗口和 RViz 导航窗口**，同时启动原生 ROS 桥、Point-LIO、AMCL/Nav2。MuJoCo 观察窗口先加载当前物理模型；UE 内的 MuJoCo 开始计算后，它显示同一车辆的原生状态。定位、Nav2 和观察窗口首帧均就绪后，RViz 自动出现。

当前已准备的完整原生场景是 **RMUC2027**，默认位于红方波浪路线。`Play-Navigation.cmd import_lab` 会被完整 MuJoCo 场景校验阻止；该参数对应的完整原生模型尚待准备。

关闭 RViz 或 MuJoCo 观察窗口会结束本次启动器拥有的整套导航进程，也可双击 **Stop-Navigation.cmd**。手动驾驶入口是 `Play-ManualVehicle.cmd`，训练入口是 `Play-Training.cmd`。同一工程使用一个 UE 游戏实例；启动器会检测已有实例，先关闭当前手动预览再打开导航。

新机器先按仓库说明准备 UE、合法来源资产、原生 ROS 环境，以及 `tools/mujoco/requirements.txt` 对应的独立 MuJoCo 环境，再运行 `tools/environment/Prepare-NavigationConsole.cmd` 准备 RViz 依赖。启动器会检查依赖和模型身份；会话日志、三个窗口的进程信息及同步状态保存在 `artifacts/navigation/console-live` 下。当前实际运行证据来自 Windows；Ubuntu 同源入口与原生运行结果分别记录在[当前进度](../progress/current.md)。

## MuJoCo 观察窗口

窗口显示当前车辆的完整动力学模型，默认跟随底盘。相机输入只改变观察位置。

| 操作 | 效果 |
|---|---|
| 左键拖动 | 环绕观察；按住 Shift 切换旋转方式 |
| 右键拖动 | 平移观察位置，并切换到自由视角；Shift 切换平移方式 |
| 中键拖动 / 滚轮 | 缩放 |
| F | 在跟随底盘与自由视角之间切换 |
| Home | 全场总览 |
| Esc / 关闭窗口 | 结束本次三窗口导航会话 |

观察窗口的状态和模型身份在会话目录的 `mujoco-viewer/status.json` 中；会话根目录 `status.json` 的 `pids.mujoco_viewer` 记录实际窗口进程，`launcher_pids` 另存 Windows 启动转发进程。启动或运行异常可从 `mujoco_viewer.log` 和控制台顶层错误定位。

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

RViz 固定坐标系是 `aim_sentry/map`，显示导航地图、代价地图、估计位姿与协方差、规划路径、激光扫描、Point-LIO 配准点云和安装 TF。定位使用原 Point-LIO 和 AMCL。导航算法使用传感器估计；仿真原生状态供独立评价以及 MuJoCo 观察窗口的显示与校验使用。

RMUC 默认地图包由 `assets/maps/rmuc2027/arena-shell.json` 的 `navigation_package` 选择，当前为 `navigation-console-platform`。该包通过 `assets/maps/rmuc2027/navigation-console-platform/SOURCE.json` 声明独立的几何定位图。AMCL 接收该定位图，原可通行地图继续供规划与代价地图使用。显式 `-LocalizationMapYaml` 优先；没有包内声明的其他地图沿用单地图流程。

包内固定来源、坐标、栅格和生成器都进行身份校验。PGM/NPZ 缺失时，启动准备流程从已存在的原碰撞几何重建；已有文件校验失败时会报告具体文件。手动重建入口：

```powershell
.\tools\environment\run-env.ps1 -Command @('python','tools/maps/export_localization_surface_map.py','--restore-package','assets/maps/rmuc2027/navigation-console-platform/SOURCE.json')
```

该流程需要本地已准备的原几何资源。地图包记录生成配方，源码仓库按来源与大小规则保存数据。当前已覆盖路线、实际误差和未覆盖区域均列在验收记录中。

## 同源运动参数与原导航算法

当前默认导航算法源是 `ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml`；原始下载副本保存在 `third_party/sentry_nav_original`。手动和仿真导航的车辆能力统一读取 `assets/vehicles/aim_sentry/drive-profile.json`。启动时生成并记录同源参数覆盖，原导航 YAML 的文件字节保持不变。

| 生效参数 | 当前值 |
|---|---|
| 车辆前进 / 后退 / 侧向上限 | 5.0 / 2.5 / 4.0 m/s |
| `FollowPath.v_linear_max / v_linear_min` | 5.0 / −2.5 m/s |
| 普通导航 yaw 上限 | ±π/2 rad/s |
| `velocity_smoother.max_velocity` | `[5.0, 4.0, π/2]`，底盘 x / y / yaw |
| `velocity_smoother.min_velocity` | `[-2.5, -4.0, -π/2]` |
| `velocity_smoother.max_accel` | `[2.0, 2.0, 3.0]` |
| `velocity_smoother.max_decel` | `[-4.0, -4.0, -5.0]` |
| 车辆加速 / 普通减速 / 制动 | 2.0 / 4.0 / 7.0 m/s² |
| 加速 / 减速 jerk 上限 | 6.0 / 20.0 m/s³ |
| `general_goal_checker.xy_goal_tolerance` | 0.05 m，作用于导航估计位姿 |

实际速度由加减速、路径曲率、障碍代价、接近目标减速和真实轮地接触共同决定。真实到点误差另用独立评价位置测量，包含定位偏差和停车行为。

原 Omni PID、Theta*、速度平滑器和局部/全局代价地图继续工作。原 `fake_vel_transform` 先把导航指令转换到真实底盘坐标系，再进入速度平滑器。最终导航指令与手动输入共用车辆速度、加减速、jerk、方向变化和角速度整形，并保留舵轮运动学、转向前馈、轮速 PI 和真实接触。方向整形继续使用同源的侧向加速度 8 m/s²、方向 jerk 70 m/s³，角加速 / 减速为 3 / 5 rad/s²。

明确的全零停止目标立即进入与手动相同的车辆制动路径；新鲜有效的非零目标到来前，停止锁存抑制上游平滑器的残余非零输出。正常行驶继续使用原平滑算法。接近目标减速、曲率和避障等策略参数按原导航配置执行。

接入自己的源文件：

```powershell
.\tools\environment\Start-NavigationConsole.ps1 -ControlProfile simulation -NavigationSource 'D:\new_vehicle\sentry_ws\src\pb2025_nav_bringup\config\simulation\nav2_params.yaml'
```

`-NavigationFollowProfile` 可选择车辆的云台目标整形配置。当前默认 mesh-rig 和传感器仍是 AIM 样例；换车后的几何、安装和整套动力学需按车辆包验收。导航和 Point-LIO 读取选定源文件的快照；修改配置后重新启动会话生效。

## 标准仿真速度与渲染

标准手动和导航均采用 UE 进程内的原生 MuJoCo。动力学以 **1 ms** 步长积分，UE 每 **5 ms / 200 Hz** 交换状态并驱动传感器；普通帧迟到时继续完成全部物理步，按绝对墙钟截止时间追赶。场景绘制采用独立 **60 Hz** 目标节拍，地图、车辆网格与光照由原 UE 场景渲染。

MuJoCo 观察窗口以单向 loopback UDP 接收 **30 Hz 仿真时间**的原生状态快照，展示当前动力学模型、底盘和关节姿态。它按模型身份和完整车体姿态核对显示结果，摄像机操作用于观察；车辆动力学和控制由 UE 内的同一求解器执行。快照过期时窗口显示冻结状态，控制台进入恢复状态；异常退出保留错误并清理本次会话。

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

原模型 `radar_mid360 / bone_turret` 对应 `yaw_link`。光学原点相对 `yaw_link` 为 `[0.203510156093, 0.073759915267, 0.175951734002] m`，按设备底面以上 47 mm 和真实连接器方向提取，保留源装配约45°的倾斜。来源与测量在 `sensors/mid360-source.json`，运行参数在 `mid360.json` 和 `mid360-imu.json`。光学原点、TF、射线和共装 IMU 随真实关节运动；光学窗口可透射，其他车体的实际命中参与首回波计算。

当前 Mid-360 模型采用360°水平、-7°至52°俯仰、200k射线/仿真秒、10Hz点云和200Hz内置IMU；逐点时间间隔为5μs。公开指标、扫描覆盖近似和未标定噪声 / 反射模型的范围见[传感器模型说明](../sensors/mid360-physical-model.md)。Point-LIO 使用 ROS SI 单位；当前 source `acc_norm=9.81`、`satu_acc=30.0`，实际饱和阈值为30.0 m/s²。源参数决定滤波、协方差和 IMU 队列深度，安装外参由车辆传感器包提供。

## 实际验证记录

最新结果、命令、模块/配置身份和完成范围见[当前进度](../progress/current.md)、[三窗口导航入口记录](../changes/2026-11-20-three-window-navigation.md)及[MuJoCo 集成记录](../changes/2026-11-20-mujoco-integration.md)。三窗口专项验证使用 `artifacts/navigation/unified-entry-20261120`；此前导航实时性证据保存在 `artifacts/navigation/realtime-20261118` 和 `artifacts/sensors/mid360-physical-20261120`，各轮结果保留对应输入身份。

导航25cm真实到点、取消停车和完整传感器预算门限保持；10cm作为额外精度目标单列。GUI选点/航点/取消、三个实际可见窗口、标准实时性和原生控制兼容性分别记录。此前完整导航功能已通过；分段和滚动10秒实时倍率门槛仍是性能待项，三窗口负载的结果按专项实测记录。更多出生点、完整场地、原生Ubuntu、多车与权威联机沿长期目标继续推进。
