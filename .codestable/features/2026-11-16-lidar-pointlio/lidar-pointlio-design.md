---
doc_type: feature-design
feature: 2026-11-16-lidar-pointlio
status: approved
summary: Actual rolling LiDAR measurements and original sentry Point-LIO on native Windows, with source-preserving Ubuntu preparation.
tags: [lidar, point-lio, ros2, native]
roadmap: rm-simulator
roadmap_item: lidar-pointlio
requirement: lidar-inertial-estimation
authorization: Standing autonomous simulator and same-source sentry_ws implementation request.
user_review: not_requested_under_standing_authorization
---

# LiDAR 与原 Point-LIO

## 1. 决策与目标

继续完成用户同源哨兵感知目标。固定工作区实际引用的Point-LIO3099b9dd及Livox驱动a8230b02；原副本保存在third_party，工作副本和补丁独立保存。先构建原估计器，再由真实UE场景测距产生它实际消费的输入，最后验证算法估计的运动。不能用真值定位代替算法，不能把编译或话题存在写成定位通过。

本阶段Windows原生实现；Ubuntu保持同一源码和离线配置，原生运行后置。正式比赛、联机及其他原图工作仍保留，不因本切片完成而关闭长期goal。

## 2. 接口与编排

现状：实际底盘、相机及raw IMU存在，尚无LiDAR。原Point-LIO lidar_type1消费livox_ros_driver2/CustomMsg，使用header帧开始时间及CustomPoint.offset_time纳秒、xyz米、line和tag过滤。原消息逐字保留；仅接口构建包与硬件驱动明确分开，不假装Windows能运行Linux SDK。

变化：车辆包声明扫描安装与范围/采样参数；UE真实场景碰撞射线生成瞬时传感器系测距，随物理时间分批扫射，保留运动畸变与逐点实际采样时间。扫描图样/强度/噪声必须标记简化及是否标定；不能用规则多线扫描冒称真实Mid-360光学扫描复刻。传输有界，并与现有相机/IMU/底盘控制共享可靠的session/epoch纪律；新增大点云负载不能阻塞指令失效保护。

输入路径：真实场景/车辆运动→LiDAR采样→ROS原CustomMsg及可观察PointCloud2→原Point-LIO预处理/IMU融合/估计器→估计Odometry/点云。真值仅作为独立误差评估。IMU外参必须由两个实际安装变换推导，单位参数明确SI。

原算法的非便携构建配置、未使用依赖/头文件可定点调整；核心估计代码保持原行为。必要运行修复逐项以证据留补丁。包依赖与CMake对应；不下载或执行未知二进制。

## 3. 验收

- 原源提交/文件哈希、原消息字节一致；Windows原节点与消息成功构建、真实进程运行。
- LiDAR实际墙/地/遮挡距离、坐标及安装变换；自身遮挡/盲区/无返回语义可验证。
- 滚动扫描逐点时间和运动畸变；不得凭一个结束姿态生成整帧假瞬时点云。采样不足、传输拥塞和复位有明确记录。
- 原消息经真实ROS交给原预处理器，点数/时间通过；真实场景静止及移动后原估计器输出有限位姿，独立真值误差及失败场景留证。
- 原速度、瞄准、IMU与手动控制运行回归，不能以点云性能牺牲失效停车。原生Ubuntu与实车标定不在Windows证明范围。

## 4. 挂载与架构

预期挂载：可选LiDAR组件及VehicleRig传感器变换、RosBridge有界传输、Python ROS适配、车辆配置、启动入口、原Point-LIO/Livox接口包与本地构建/运行工具。按最终实现回填具体挂载点和协议版本，不把未实现部分写入当前architecture。永久记录包含来源、补丁、构建环境、采样模型局限、测试命令与可核验证据。

最终挂载：RosLidarComponent持有实际射线/扫描状态；RosBridge初始化、Advance、QueueState、断线和epoch钩子；GameMode可选RosLidar；RosImu UsesId跨传感器ID校验；Python protocol/lidar/node/point_lio_runner；Win/Linux入口和原包构建工具。无LiDAR仍v1，开启为v2多段二进制，命令仍v1。runner按实际mount求外参、session/epoch重启原节点；原参数异常启动退出2。这些运行修正由native-03失败证据驱动，估计公式保持原样。当前architecture已据此归并。
