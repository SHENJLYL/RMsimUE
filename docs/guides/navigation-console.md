# 可视化导航、云台跟随与 Mid-360

## 打开和关闭

在已准备资源的本机，双击根目录 `Play-Navigation.cmd`。启动器打开 UE 场景，依次启动原生 ROS 桥、Point-LIO、AMCL/Nav2，定位就绪后打开 RViz。默认场地为 RMUC2027 红方波浪路出生点；平地示例可运行 `Play-Navigation.cmd import_lab`。

关闭 RViz 会结束本次启动器拥有的整套导航进程。也可双击 `Stop-Navigation.cmd`。手动驾驶入口仍为 `Play-ManualVehicle.cmd`，训练入口仍为 `Play-Training.cmd`。同一工程使用单个 UE 游戏实例；启动导航时会正常关闭已有手动预览，遇到编辑器、测试或另一套导航则拒绝重复启动。

当前 Windows 环境已安装 RViz2 和原 Nav2 RViz 插件。新机器先按仓库说明准备引擎、合法资源和 ROS 依赖，再运行 `tools/environment/Prepare-NavigationConsole.cmd` 完成本地 RViz DLL 部署。该准备入口只部署现有依赖，不自动下载来源受限地图或模型。完整 Ubuntu 原生运行单独验收。

## 单点导航

在 RViz 左侧确认 `Navigation: active`、`Localization: active`。点击顶栏 **Nav2 Goal**，在白色可通行区域按下鼠标左键、拖动箭头、松开。原 Nav2 action server接收目标，蓝色路径显示规划结果；反馈区显示状态、剩余距离等。鼠标拖动箭头提供目标朝向，当前位置导航的宽松终点朝向策略沿用原配置。

导航途中点击左侧 **Cancel** 取消当前任务。UE 窗口仍保留 Space 人工制动和 R 复位；R 会重建本会话的定位与导航，旧目标失效。

## 多航点

点击左侧 **Waypoint / Nav Through Poses Mode** 进入累积模式，然后用顶栏 **Nav2 Goal** 逐一选取地点。选择 **Start Waypoint Following** 后，原 `FollowWaypoints` action依序执行。**Start Nav Through Poses** 对应另一原生 action；其完成范围以当前验收记录为准。**Cancel Accumulation** 清空尚未启动的点列，行驶中的 **Cancel** 取消活动任务。

地图可以用 Move Camera 平移、旋转和滚轮缩放。默认视图聚焦已验证的出生点和波浪路。自动验收夹具读取当前视角换算点击坐标，实际用户无需运行该夹具。

## 画面与坐标

RViz固定坐标为 `aim_sentry/map`，显示导航栅格、局部障碍代价、机器人估计位置和外形包络、规划路径、实时激光、Point-LIO配准点云、云台航向和Mid-360安装坐标。地图定位由AMCL提供；局部位姿与点云来自原Point-LIO。显示器与导航均使用估计位姿，仿真真值仅进入独立验收观察器。

当前导航使用场景几何生成的候选栅格，默认已测试区域为红方波浪路线。其他出生点、跨高度结构及全场任意点需要逐项扩展通行验收。

## sentry_ws 云台跟随

车辆包 `navigation-follow.json` 固定参考 `unnc-aim/universal_controller` 的提交 `95c66c28717e0b4d0f586c2ebf8df99d47a24ab0`。原Hub/云台控制器按导航速度方向形成目标航向，并采用最短角度、最大1rad/s角速度、2rad/s²角加速度、0.3rad/s最低转动段速度、0.02rad容差及0.2s输入有效期。

仿真桥把通过校验的实际导航速度、估计底盘航向和关节反馈送入同一目标整形规则。停车时保持航向，失效输入停止跟随；扫描/获准自瞄优先。导航跟随不开火。当前为高层云台运动接口，原硬件电流PID及完整Hub/视觉仲裁继续单独跟踪。

## Mid-360 实际安装

原模型 `radar_mid360` 随 `bone_turret` 转动，已对应到 `yaw_link`。零位光学中心相对底盘约 `[0.19812,0.07376,0.36119]m`，前倾约45°；来源、源文件哈希与几何拟合记录在 `assets/vehicles/aim_sentry/sensors/mid360-source.json`。传感器相对云台安装配置在 `mid360.json`，共装IMU在 `mid360-imu.json`。

光学原点、点云射线、共装IMU和安装TF随实际关节运动；自己的已审计光学罩可透射，其余车体保留遮挡。范围采用360°水平、-7°到52°俯仰、每仿真秒20万条射线、10Hz扫描。当前是规格约束下的理想首回波模型，专有扫描轨迹、反射强度、温漂、折射和实车标定尚待补充。

Point-LIO接收ROS SI惯性数据。原3g加速度饱和阈值同步换算为29.43m/s²，`acc_norm`为9.81；保留同一物理范围和原饱和逻辑。场景重力单独按UE实际9.8m/s²传入。

## 证据和复测

最新完整通过项及缺口以 `docs/progress/current.md` 和该功能的发布汇总为准。`Test-Mid360Mount.ps1` 验证真实旋转安装和惯性测量；`probe_sentry_navigation.py` 验证原Nav2航点、取消和云台/传感器联动；`test_navigation_console_ui.py` 单独验证真实RViz操作。禁止将后台action成功写成鼠标界面验收成功。

## sentry_ws 导航速度由源配置管理（实验分支）

当前实验分支默认读取 `ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml`，该 ROS 2 工作副本暂将 AIM 前进上限调为 3.0m/s，平滑器的 x/y 轴限分别匹配车包手动参数，原控制器、曲率减速、接近目标减速及 Theta*/代价地图照常工作。原始上游配置保存在只读的 `third_party/sentry_nav_original`。

对于未来不同底盘，可直接向启动器传入其自己的 `sentry_ws` 配置文件，覆盖控制、平滑、避障与 Point-LIO 滤波，而不在仿真中新增该车专属速度常数：

```powershell
cd D:\simulator
.\tools\environment\Start-NavigationConsole.ps1 -ControlProfile simulation -NavigationSource 'D:\new_vehicle\sentry_ws\src\pb2025_nav_bringup\config\simulation\nav2_params.yaml'
```

可选的 `-NavigationFollowProfile` 接收车辆自己的云台目标整形配置。当前 UE mesh-rig、Mid-360 等仍默认 AIM，其他车辆完整自动导入和运行另行验收。

**验证边界：** 本次 3m/s 配置单点导航已在原生 UE/ROS 上通过；长波浪路双航点和稳定 1× 实时运行尚未通过。完整证据 `artifacts/navigation/nav-speed-20261117/checkpoint-summary.json`。图形界面原来 0.35m/s 版本的 84+13 项成功记录仍属于先前已验收版本。
