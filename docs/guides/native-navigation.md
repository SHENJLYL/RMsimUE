# 原生哨兵导航

日常使用可双击 **Play-Navigation.cmd**：包含RViz选点/连续航点、sentry偏航跟随和云台安装Mid-360。[图形操作指南](navigation-console.md)。本页四终端入口保留为固定安装传感器/原导航诊断方式。

Windows当前已验证原Point-LIO、原地形/扫描节点、AMCL、Theta*和Omni PID：平地绕障、RMUC波浪往返、导航中断线重连、目标取消以及平地实际R复位恢复。最新验收为两轮RMUC各14项与平地17项，汇总在 `artifacts/navigation/reconnect-tf-20261117/release-summary.json`。完整自主视觉/决策、多车、全场路线和Ubuntu继续分项验收。

## 构建和启动

从项目根目录准备原节点与sidecar：

```powershell
& .\tools\environment\Build-PointLio.cmd
& .\tools\environment\Build-Navigation.cmd
& .\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-File','D:/simulator/tools/environment/Build-RosBridge.ps1')
```

关闭手动预览，分别在四个终端运行默认平地样例：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:/simulator-tools/UE_5.8' -MapFile 'D:/simulator/assets/maps/import_lab/map.json' -RosControl -RosImu -RosLidar -SimStepSeconds 0.001
& .\tools\environment\Start-RosBridge.ps1 -Lidar
& .\tools\environment\Start-PointLio.ps1
& .\tools\environment\Start-Navigation.ps1
```

定位与节点激活后，在另一个终端发送位置目标：

```powershell
& .\tools\environment\Send-NavigationGoal.ps1 -X 5.5 -Y 1.0
```

上述公开包装的独立整套操作验收仍待补充；本轮真实验收通过Test-Navigation的已安装ros2 run入口完成。速度上限0.35m/s、实际车体0.52m包络，固定1ms物理步长可能慢于墙钟。目标到达按独立实际位置检查，当前门限0.25m。

## Windows本机传输

已安装的 `rmsim_bridge navigation` 入口默认使用包内 `config/fastdds-loopback-udp.xml`。配置仅允许127.0.0.1 UDP，严格校验后才创建ROS context，缺失/非法配置立即停止。实际模式、接口和哈希发布在navigation状态中。

Humble的ROS_LOCALHOST_ONLY=1会额外加入SHM；此导航进程改由XML白名单承担本机范围，避免已复现的重启后消息交付中断。设置仅影响导航监督器和其子进程，桥、Point-LIO、其他测试及系统环境保持原配置。没有新增防火墙许可。Ubuntu默认原模式，尚无原生Ubuntu验收。

`RMSIM_NAV_TRANSPORT=default` 是故障对照入口，保留旧传输行为；`loopback_udp` 显式选择当前本机配置。配置校验拒绝其他接口、内建传输、SHM、额外结构及冲突环境覆盖。完整栈的真实场景验证优先于小型接口夹具。

## 坐标、地图和复位

处理链：原Point-LIO → loam_interface → sensor_scan_generation → terrain_analysis/ext → pointcloud_to_laserscan → AMCL/原Nav2插件。Point-LIO参考IMU安装点，代价地图观测原点使用LiDAR frame。map→odom来自AMCL，odom→base来自估计；评测真值保持独立。

默认初值[0.6,-0.4,0.15]带非零0.25/0.25/0.09协方差。RMUC专项使用其专用初值、候选栅格和出生点。metadata的地图id/kind/source_md5必须匹配实际UE场景，错图拒绝启动。RMUC候选图已覆盖当前波浪路线，其他区域继续扩展验证。

断线/新session/epoch变化会停止自有导航进程、清理旧目标并重建定位。用户通过实际R复位后，仿真时钟重新开始，原Point-LIO重新建立局部地图。真实传感器尚未提供安装TF时，适配器跳过当前观测，后续样本重试，避免停顿仿真时钟下同步等待阻塞。

## 复测入口

`tools/unreal/Test-Navigation.ps1 -RecoverSession` 注入自有bridge断线，验证停车、重新定位、目标不重放及后续导航；`-RoundTrip` 增加返程。`-InteractiveReset` 打开可见测试窗口并等待一次实际R输入。每次使用全新的EvidenceDirectory；不要与手动预览并行。

`tools/integration/Invoke-NavigationResetInput.ps1` 仅接受证据已请求复位、项目命令行匹配的自有RMSim PID，并要求目标处于前台才发送一次R；焦点校验失败时不发送输入。真实ROS epoch与定位恢复由另一侧独立检查。

证据会记录UE/profile/rig、导航DLL及安装Python文件哈希，完整日志/截图/有界链路时序留本地。`tools/integration/summarize_navigation_recovery.py` 检查结果集合和当前文件一致性。原生适配补丁在third_party/patches，源副本、工作包和验证产物分目录保存。
