# Ubuntu 离线适配交接

按用户要求先做Windows。Ubuntu仅准备同源入口和资产，尚无原生构建/运行验收，不使用WSL替代。

## 搬运内容

保留目录结构，带上：

- unreal/RMSim/RMSim.uproject、Source、Config。
- assets/vehicles/aim_sentry及需要的assets/maps/<id>：FBX/PBR、map.json、mesh-source.json、SOURCE.md。
- tools/unreal、tools/maps、src、schemas和指南。
- ros2_ws/src/rmsim_bridge：同源ROS桥和drive_demo；不要携带Windows的install_bridge或build_bridge作为Linux安装产物。
- unreal/RMSim/Content可一并搬运；脚本也可从FBX重新导入。

无需Windows的Binaries、Intermediate、Saved或DerivedDataCache。原始STEP、下载ZIP及preview.blend不是运行依赖。离线机器应预先准备匹配版本的Linux UE Editor、工具链、系统图形依赖和GPU；脚本不联网下载引擎。

## 同源运行

```bash
bash tools/unreal/Start-ManualVehicle.sh --engine-root /path/to/UE
bash tools/unreal/Start-ManualVehicle.sh --engine-root /path/to/UE \
  --map assets/maps/wt_cdk_crop/map.json --spawn hillside
```

流程：Linux构建 → 同一个车辆Python导入器 → 同一个碰撞缓存检查/重建脚本 → 同一个地图Python导入器（有mesh-source.json时）→ 同一个C++接触车辆运行时。默认RMUC2027。UE资源使用虚拟路径，入口按脚本位置解析，不依赖D盘/E盘。当前AIM碰撞绑定和派生Content建议一并搬运；缺失时由本地视觉资产重建。诊断旧运动学可传`--kinematic-preview`。

Windows上的Bash语法/预检只证明离线准备。Ubuntu还须实际构建、显示、运行地形和控制检查。Point-LIO/Livox已有Windows原生证据，但完整sentry_ws依赖闭包、自瞄和雷达尚未验收，此交接不代表Ubuntu算法迁移完成。

## ROS 桥离线准备

预先准备原生Humble、colcon/ament_python、rclpy、geometry_msgs、sensor_msgs、rosgraph_msgs、std_msgs、tf2_msgs及ros2run；这份Windows Pixi锁不是Ubuntu依赖锁。UE同源组件使用平台Socket和Chaos接口；当前只有Windows实跑证据。

```bash
bash tools/unreal/Start-ManualVehicle.sh --engine-root /path/to/UE --ros-control
# 其他终端，source本机Humble后：
cd ros2_ws
colcon build --packages-up-to rmsim_bridge
source install/setup.bash
export ROS_DOMAIN_ID=71 ROS_LOCALHOST_ONLY=1
ros2 run rmsim_bridge bridge
# 同样source的第三终端：
ros2 run rmsim_bridge drive_demo --ros-args -p vx:=0.2 -p duration_s:=2.0
```

端口默认9451，使用`--ros-bridge`仅观察，`--ros-control`交出底盘控制。还需在Ubuntu实际验证图像、TF、时钟、制动和复位；离线准备不宣称运行通过。

云台/训练发射还需一起搬运`ros2_ws/src/sp_msgs`、`pb_rm_interfaces`，同源colcon构建后为桥设置`aim_control:=true`，UE入口添加`--ros-aim-control --training`。原消息、扫描范围和许可定义见[云台指南](ros-aim-fire.md)。可与`--ros-motor-control`组合；当前只有Windows实跑证据。

## LiDAR / Point-LIO 离线准备

一并搬运point_lio、livox_ros_driver2接口投影、rmsim_bridge及车辆sensors配置。为目标Ubuntu/Humble准备ament_cmake、rosidl_default_generators、PCL及pcl_conversions、tf2_ros、visualization_msgs、Eigen3、原生C++17/OpenMP工具链和原有ROS消息依赖；Windows DLL/Python扩展不能复制过去使用。Livox投影仅生成原IDL，不需要Linux硬件SDK。

```bash
cd ros2_ws
colcon build --packages-up-to point_lio rmsim_bridge --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
# UE入口加 --ros-imu --ros-lidar --sim-step-seconds 0.001
ros2 run rmsim_bridge bridge
# 另一个已source的终端，输出目录自行选本地工作区：
ros2 run rmsim_bridge point_lio --ros-args -p output_directory:=../artifacts/localization/pointlio
```

同源runner按当前传感器安装求外参，并在新会话重启原估计器。命令只是离线准备，Ubuntu原生执行及离线安装包完整性仍须在目标机验证；本轮没有声称已下载完整Ubuntu依赖闭包。
