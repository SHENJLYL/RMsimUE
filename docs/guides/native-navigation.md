# 原生 sentry 导航

日常使用 **Play-Navigation.cmd**，启动默认 RMUC2027、原生 ROS 桥、Point-LIO、AMCL/Nav2 与 RViz。选点、航点、取消、云台跟随和 Mid-360 的操作见[导航控制台指南](navigation-console.md)。当前工作分支的真实结果与验收状态见[当前进度](../progress/current.md)和[本轮变更](../changes/2026-11-18-navigation-realtime.md)。

## 构建与启动

在项目根目录准备原节点与 sidecar：

```powershell
& .\tools\environment\Build-PointLio.cmd
& .\tools\environment\Build-Navigation.cmd
& .\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-File','D:/simulator/tools/environment/Build-RosBridge.ps1')
```

`Play-Navigation.cmd import_lab` 选择原平地测试场。关闭 RViz 或运行 `Stop-Navigation.cmd` 结束本次拥有的进程。原生桥改动后须重新安装到 `ros2_ws/install_bridge`；Point-LIO 和导航库分别使用各自构建入口。

使用另一套 sentry_ws 配置：

```powershell
& .\tools\environment\Start-NavigationConsole.ps1 -ControlProfile simulation -NavigationSource 'D:/new_vehicle/sentry_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml'
```

默认源是 `ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml`，原始下载副本在 `third_party/sentry_nav_original`。源文件决定原控制器、平滑器、规划/避障、AMCL、扫描转换和支持的 Point-LIO 参数。当前前向上限5 m/s与AIM手动模式一致，导航源保留0.8 m/s²平移加速和3 m/s²减速。当前实际源码、运动结果和上限须分别查看，不把参数上限记成实测峰值。

Point-LIO监督器一次读取、校验并冻结源YAML原始字节，参数生成、状态和session/epoch重启共同使用该快照。当前 `common.imu_qos_depth=128`；允许1–4096，未提供时为原默认5。原500Hz循环使用有1ms预算的回调排空，估计公式保持原源实现。

## 时钟、场景与传感器

默认导航使用真实Chaos5ms固定步长和单调墙钟节拍，保留完整物理步。当前进程在创建物理场景前覆盖硬子步上限；全局手动设置仍为1/240s。原LK电机环等显式1ms入口继续按各自配置运行。

物理/IMU200Hz、场景Draw目标60Hz、Mid-360 200k射线/仿真秒与10Hz扫描分别计数。Windows混合核心平台的性能CPU Sets和UE核心预算按实际可用集合匹配，继承限制和系统策略有独立保留路径；`-CpuPolicy system`选择系统策略。

导航默认 `-CameraHz 0`，关闭未使用的RGB采集与读回，保留CameraInfo和TF。需要RGB时使用 `-CameraHz 10` 等显式频率。RGB开启版本的实时性单独验收。当前标准速度报告包括90秒或更长窗口、每秒移动的10秒滚动窗、真实Draw计数和全部传感器无丢样检查；实际可见窗口另做GUI记录。

ROS IMU按SI单位发布。源饱和阈值按 `satu_acc_source × 9.81 / acc_norm_source` 换算，当前simulation已为 `acc_norm=9.81`、`satu_acc=30.0`。外部YAML依其实际字段换算，源协方差保持。挂载外参和物理采样来自车辆传感器包。

## Windows通信与地图身份

控制台在ROS context创建前验证包内 `fastdds-loopback-udp.xml`，允许接口限定127.0.0.1。桥、Point-LIO、导航监督器、原节点和RViz继承同一会话环境。该会话使用经检查XML界定本机通信边界；所有环境修改仅传给拥有的子进程。

导航链为原Point-LIO → loam_interface → sensor_scan_generation → terrain_analysis/ext → pointcloud_to_laserscan → AMCL/原Nav2。map→odom来自AMCL，局部位姿来自估计，真值只用于独立评价。

RMUC包可声明独立几何定位图，原规划地图保持。正常启动先校验包内声明和源几何，缺失派生栅格/表面来源文件可按固定配方重建。运行中核对两路实际OccupancyGrid、frame、分辨率、原点与内容；输入变更会报告身份错误。详见控制台指南中的重建入口。

## 重建、取消与恢复

断线、session或epoch变化会停止旧导航进程，丢弃旧目标并重建定位。实际R复位使仿真时间重新开始，Point-LIO重建局部地图。安装TF查询采用零等待；变换尚未到达时丢弃当前观测，等待后续真实样本。

历史恢复测试入口：

```powershell
& .\tools\unreal\Test-Navigation.ps1 -RecoverSession -RoundTrip
& .\tools\unreal\Test-Navigation.ps1 -InteractiveReset
```

恢复夹具只操作自己启动的bridge与已核对的UE窗口。2026-11-17的两轮RMUC恢复及平地实际R记录在 `artifacts/navigation/reconnect-tf-20261117/release-summary.json`；后续组合的覆盖范围按当前结果另记。

## 诊断与验证入口

`tools/integration/run_navigation_realtime_acceptance.py` 将原Nav2单点、完整航点与取消放进同一90秒测量窗口，保留原14项动作检查。`--capture-localization-clouds` 是默认关闭的有界原始点云诊断；正式默认入口性能验收保持关闭。`Run-NavigationTopSpeed.ps1` 使用真实地图、原action、实际速度持续窗口和源制动曲线验证高速取消，准备阶段与正式测速独立记录。

离线测试入口：

```powershell
& .\tools\environment\run-env.ps1 -Command @('python','tools/integration/run_offline_tests.py','--output','artifacts/navigation/offline-new-run')
```

每次使用新的输出目录，保留旧失败。最终汇总核验当前源码/模块/配置、动作与时钟的完整覆盖、CPU策略、传感器接收、串行激光对照、GUI和兼容回归；未完成的门禁继续报告失败。

## 历史固定安装诊断

下列入口保留历史0.35m/s配置，适用于固定安装诊断：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:/simulator-tools/UE_5.8' -MapFile 'D:/simulator/assets/maps/import_lab/map.json' -RosControl -RosImu -RosLidar -SimStepSeconds 0.001
& .\tools\environment\Start-RosBridge.ps1 -Lidar
& .\tools\environment\Start-PointLio.ps1
& .\tools\environment\Start-Navigation.ps1
```

节点激活后可用 `Send-NavigationGoal.ps1 -X 5.5 -Y 1.0` 发送目标。该路径按显式1ms物理步长运行，其墙钟倍率独立记录。当前高速度可视化导航使用前述统一控制台入口。

源码补丁保存在 `third_party/patches`，固定原始副本、来源与精简结果分别保存。原始日志、截图、稠密轨迹和大型派生资产留本地。Windows结果与原生Ubuntu验收分开记录，完整自主决策、多车与权威联机继续按长期目标推进。
