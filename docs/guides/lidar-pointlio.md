# 激光点云与原 Point-LIO

当前Windows单车切片已限定验收。真实UE场景射线产生原Livox消息和PointCloud2，原Point-LIO消费点云及IMU并输出里程计。复位、安装变化、RMUC、原LK联合、压力/遮挡及旧功能回归见artifacts/sensors/lidar/release-summary.json；未包含实机校准或完整导航。

## Windows 启动

依赖通过tools/environment/pixi.toml和lock管理。先从项目目录构建：

```powershell
& .\tools\environment\Build-PointLio.cmd
& .\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-File','D:/simulator/tools/environment/Build-RosBridge.ps1')
```

三个终端分别运行：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:/simulator-tools/UE_5.8' -RosImu -RosLidar -SimStepSeconds 0.001
& .\tools\environment\Start-RosBridge.ps1 -Lidar
& .\tools\environment\Start-PointLio.ps1
```

第一条可加RosControl交给ROS底盘；否则仍可WASD手动行驶。固定1ms是仿真步长，通常慢于墙钟，HUD会单列sim倍率。默认普通试驾不开LiDAR/IMU。

Point-LIO入口管理的是原生原估计器进程。外参从当前两个实际安装变换计算，而非复制实车配置：R_I_L=R_B_I逆乘R_B_L，T_I_L=R_B_I逆乘(p_B_L-p_B_I)。根据raw IMU设置SI acc_norm9.81，并使用当前实际世界重力。新仿真会话/复位会重新启动原进程，清空地图和局部定位原点；日志和最终参数在artifacts/localization/pointlio。原节点本身只拒绝时间倒退，不能直接长期跨R复位使用。

算法输出aft_mapped_to_init、cloud_registered和path；输入仅实际LiDAR/IMU。当前关闭原节点TF发布，避免将未知定位原点误绑到比赛地图。完整导航前还需要地图定位和坐标链接入。

当前原输出仍使用单车全局话题，尚未提供多机器人命名空间隔离；RobotId配置不等于联机/多车支持。

## 车辆配置及物理含义

assets/vehicles/aim_sentry/sensors/lidar.json为默认运行配置。单个base_link刚性安装LiDAR；ID须与IMU不同。默认[0,0,0.46]米是未标定演示安装，配置旋转用弧度。新配置重启生效，原模型目录保持只读。

默认采用360°水平、-7°至52°垂直、200000条射线/仿真秒、10Hz帧、0.1–40米量程。视场/点率参考[厂商Mid-360规格](https://www.livoxtech.com/mid-360/specs)，**扫描轨迹不是厂商光学图样复刻**：当前为spherical_fibonacci均匀方向序列，line0..3为传输槽位。它也没有真实反射率材料、多回波、束宽、透明折射或实际硬件噪声标定。

使用真实碰撞几何的首回波，包括车辆自身遮挡；盲区内遮挡不会穿透后继续测远墙。无命中不生成点。反射率为配置常数，距离高斯噪声可配，默认0代表理想距离而非实机零误差。

同一物理帧的射线共用该帧的真实姿态和时间。1ms物理步长下约200条射线共享一采样时刻；不会伪造5µs物理状态。跨帧扫描保留运动畸变，点位是在其采样瞬间的传感器系中，不能把整个扫描当帧末快照。

## 传输和时间

原CustomMsg发布于/aim_sentry/lidar/livox，PointCloud2发布于/aim_sentry/lidar/points。header/timebase为扫描开始时刻，offset_time为相对纳秒；PointCloud2包含同样时间、xyz、reflectivity/tag/line。raw Livox消息定义逐字来自原驱动a8230b02，接口包不包含Linux硬件SDK。

每物理帧最多4096条射线，每扫描最多65536点、待发最多2帧。超额记录missed_rays，待发溢出记录dropped_scans；这些不是实际设备保证。无连接不做无用采集，复位清空半帧及待发数据。

开启LiDAR时状态协议v2：同一有界二进制体先放camera_bytes个RGB字节，再放连续扫描块，每点20字节little-endian（uint32 offset，float32 xyz，uint8 reflectivity/tag/line/padding）。总量仍≤16MiB、JSON≤64KiB。未开启LiDAR仍是v1；控制命令格式维持v1。旧sidecar不能解v2，须重新安装。

原Livox生成器仍负责CDR头部和类型，批量点区经原解码器验证，避免逐点Python对象封装拖慢控制。CDR填充字节无语义，验证比较原解码后的全部字段，不要求填充内容相等。

## 同源与证据边界

third_party保留完整Point-LIO3099b9dd与Livox a8230b02原源码；ros2_ws/src是工作副本/消息投影，变更在third_party/patches/point-lio-native.patch。MSVC移植只调整构建/未使用依赖/两处模板语法，估计公式未替换。

解析回放5项只证明原节点移植。native-01真实UE7项证明测距/时序/畸变/控制；native-02联合9项证明原估计器使用真实模拟数据。定位误差必须在初始姿态对齐后比较实际IMU安装点，不能直接比较不同原点/坐标系。

Ubuntu入口已加入--ros-lidar/--lidar-profile，同一C++/Python可构建；尚无Ubuntu原生验收。完整sentry_ws/Nav2/比赛/联机和实车校准保持后续目标。
