---
doc_type: issue-report
status: native_windows_verified
updated: 2026-11-17
scope: 当前Windows单车、2000/140悬挂、RMUC往返与平地实际R恢复
---

# 重连后的扫描链与导航恢复

## 已验收结果

正式安装入口的 `release-01`、`release-02` 各14项通过：行驶中断开自有bridge、停止导航节点、新session重建、重新定位、断线停车、旧目标不重放、重新发送目标及RMUC波浪往返。第二轮独立实际终点误差为0.183015m/0.102587m；0.25m门限保持。

`reset-01` 共17项通过：包含平地导航断线恢复、目标取消停车、实际OS R键复位、仿真时钟回退后的Point-LIO/导航重建与旧目标清空。R后定位误差0.063997m，观察窗口内旧目标重放位移0m。自有RMSim窗口经PID、项目命令行、证据目录与前台窗口校验后发送一次R；首次前台切换未成功时没有发送按键，随后正常AppActivate激活后完成。窗口截图仅留本地。

汇总：`artifacts/navigation/reconnect-tf-20261117/release-summary.json`。45项导航检查通过；最终安装Python文件、导航DLL、UE模块、车辆profile和rig均与记录核对。Ubuntu、全场路线、多车和实车标定继续单独推进。

## 定位到的故障环节

原 `rmuc-12-final-recovery` 及本轮 `before-01`、`diagnostic-01`、`after-01`、`diagnostic-02` 保留失败证据。后四次均完成6项前置检查，重连后等待恢复超时。

真实场景诊断同时采样原点云、里程计、扫描、TF与/clock。失败运行中外部观察者持续收到 `/lidar_odometry`、`/registered_scan` 与地形点云，重启后的 `sensor_scan_generation` 两路输入计数却一直为0；/clock继续前进。另一次运行扫描链有输入，而虚拟底盘frame节点没有更新。现场故障集中在默认Windows本机通信路径的重启后消息交付。

同一已构建导航运行目录的 `udp-01` 对照，仅使用IPv4 loopback UDP，恢复链完整通过14项。生产配置进一步收敛为仅导航监督器及其子进程采用该传输，桥与Point-LIO保持原入口；随后两轮真实RMUC往返和实际R测试通过。小型合成接口测试中默认/UDP均连续重启6次通过，因此该小夹具单独不能复现完整栈的间歇故障。

## 实现

- `native_transport.py` 校验包内 `config/fastdds-loopback-udp.xml`：一个UDPv4传输、唯一127.0.0.1白名单、关闭内建传输，拒绝额外接口、SHM、重复结构、错误RMW和外部环境覆盖。14项离线输入边界测试通过。
- Windows的已安装 `navigation` 入口在创建ROS context前应用进程级配置；状态中报告实际模式、接口白名单与配置哈希。Humble的ROS_LOCALHOST_ONLY=1会额外加入SHM，因此本进程用经过校验的XML承担本机边界。没有修改系统网络/防火墙、全局环境或其他ROS模式。
- 独立原生夹具另复现：缺失安装TF且仿真时钟停止时，0.5仿真秒的同步lookup会阻塞消息回调。loam_interface与sensor_scan_generation改为零等待查询；缺少真实变换时丢弃当前观测并在后续真实数据重试。扫描/里程计两类原生夹具各4项通过。
- 扫描节点增加低频接收/配对/输出计数；集成测试保存有界逐秒链路诊断。原Point-LIO、AMCL、Theta*/Omni PID、命令租约、碰撞和实际误差门限保留。
- C++增量补丁 `third_party/patches/navigation-reconnect-tf.patch` 相对项目基线 `2b63f2b`，与原上游适配补丁分开保存。

## 环境与证据边界

本机RMW为6.2.10、Fast DDS为2.6.12。pixi基础域为71；实际导航夹具在激活后设置域173，原生启动夹具180、重启夹具181。诊断已读取实际运行域。所有案例使用本机通信。

完整定位真值仅用于独立误差评估。最终离线基础、车辆、地图、IMU/LiDAR/协议与传输配置合计92项，90通过、2项Windows符号链接权限跳过。

参考实现：[Humble participant transport](https://github.com/ros2/rmw_fastrtps/blob/humble/rmw_fastrtps_shared_cpp/src/participant.cpp)、[Humble tf2 buffer](https://github.com/ros2/geometry2/blob/humble/tf2_ros/src/buffer.cpp)。外部同类SHM重启问题仅作为排查参考，本项目结论由上述本机对照和实际运行证据支撑。
