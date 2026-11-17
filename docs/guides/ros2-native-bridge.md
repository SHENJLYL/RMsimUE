# Windows 原生 ROS 2 接入样例

2026-11-16 已实测 UE5.8.4 + RoboStack Humble 原生 Windows：ROS 速度指令驱动真实接触车辆，发布物理时钟、RGB 图像、相机参数和光学 TF。本文描述速度模式；另有已运行的[原LK电流模式](sentry-lk-motor-loop.md)和[高层云台/训练发射](ros-aim-fire.md)。完整sentry_ws、LiDAR/IMU、硬件云台电机环和联机仍未完成。

## 启动

以下 PowerShell 命令从 `D:\simulator` 执行。先关闭旧手动预览；快捷入口会拒绝重复实例，避免 GPU 争用造成延迟。已准备的本机资产无需重复导入。

终端一，打开 ROS 拥有底盘控制权的场景：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:\simulator-tools\UE_5.8' -RosControl
```

终端二，保持桥接进程运行：

```powershell
& .\tools\environment\Start-RosBridge.ps1
```

终端三，进入项目原生 ROS 环境，再加载已安装包并运行一个两秒的低速前进样例：

```powershell
& .\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-NoExit')
. D:\simulator\ros2_ws\install_bridge\local_setup.ps1
ros2 run rmsim_bridge drive_demo --ros-args -p vx:=0.2 -p duration_s:=2.0
```

第三终端后两行在新进入的环境内运行。示例结束发送零速；发布者异常退出也由 UE 的 250ms 指令租约触发制动。窗口失焦不会中断 ROS 控制。Space 按住时人工制动，R 复位车体并更换 epoch/session。测试期间不要再起另一个同项目预览。

只读取传感器而保留手动控制时，将 `-RosControl` 换成 `-RosBridge`。普通 `Play-ManualVehicle.cmd` 不启用 ROS，不产生传感器采集负担。

本机默认 `ROS_DOMAIN_ID=71`、`ROS_LOCALHOST_ONLY=1`、`rmw_fastrtps_cpp`；测试使用隔离域173。桥只监听 `127.0.0.1:9451`，一个客户端。修改端口时，UE 的 `-RosPort` 和 `Start-RosBridge.ps1 -Port` 必须一致。

## sentry 侧接口

- `/aim_sentry/cmd_vel` 使用 `geometry_msgs/msg/TwistStamped`，`frame_id=aim_sentry/base_link`；x 前、y 左、z 上，线速度 m/s，角速度 rad/s。只接收 vx/vy/wz，正 wz 左转。控制器须启用 `use_sim_time`，使用 `/clock` 时间戳，并持续发布新指令。
- 速度经同一个 `VehicleDrive` 的加减速、改向、舵轮解算和轮驱动路径执行。参数在 `assets/vehicles/aim_sentry/drive-profile.json`；当前演示参数尚未实车标定。
- `/aim_sentry/camera/image_raw`、`camera_info` 与 `/tf` 中 base_link→aim_camera_optical_frame 使用对齐时间戳。图像/相机参数采用 best-effort，订阅使用 sensor-data QoS。
- `/clock` 来自同步 Chaos 物理解算器；复位会回跳。算法要处理时间跳变，不能继续发旧时间戳。训练回合计时仍是独立演示逻辑。
- `/rmsim/aim_sentry/state` 提供指令接受序号、拒绝原因、连接和相机配置。`/rmsim/evaluation/aim_sentry/ground_truth` 仅供评测，没有作为 map/world→base 定位 TF 发布。

消息坐标、时间、会话、乱序、非有限值和超限在 UE 再校验。无指令250ms、连接关闭、游戏线程长时间停顿、无效指令触发制动；重新连接不重放旧指令。这里的250ms是指令有效期，不是停车耗时。

相机默认1280×720、10Hz、水平视场60度，独立于人的瞄准镜缩放。理想针孔、零畸变，尚无曝光/噪声/快门标定；同步 GPU 回读可能降低帧率。本速度模式不执行原LK电流环；原节点接入使用单独的电流模式及其验收记录。

## 重建与验证

原生环境和依赖由 `tools/environment/pixi.toml`、`pixi.lock` 固定。不要混用官方 Python3.10 扩展与本机 RoboStack Python3.14。

```powershell
Set-Location D:\simulator\ros2_ws
& D:\simulator\tools\environment\Build-PointLio.cmd
& D:\simulator\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-File','D:/simulator/tools/environment/Build-RosBridge.ps1')
Set-Location D:\simulator
& .\tools\unreal\Test-RosBridge.ps1 -EngineRoot 'E:\simulator-tools\UE_5.8' -EvidenceDirectory 'D:\simulator\artifacts\ros2\my-new-run' -RosTopics -CameraWidth 1280 -CameraHeight 720
```

证据目录必须全新。测试脚本退出时只关闭自己启动的 UE 进程。最终机器记录见 `artifacts/ros2/native-release-summary.json`，验收范围见 `.codestable/features/2026-11-16-ros2-native-bridge/ros2-native-bridge-acceptance.md`。

Ubuntu 使用相同 C++/Python 和 `Start-ManualVehicle.sh --ros-control`；依赖需离线准备，详见 [Ubuntu 交接](ubuntu-offline.md)。本机 Windows 测试不算 Ubuntu 验收。
