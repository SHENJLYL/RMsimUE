# MuJoCo 与 UE5 融合

## 启动

当前车辆为真实尺度的 AIM 哨兵，场景为当前 RMUC2027 校园体育馆。

- 根目录 **Play-ManualVehicle.cmd**：标准手动驾驶，车辆动力学直接采用 MuJoCo。参数顺序保持为地图文件、出生点 ID、可选 training。
- 根目录 **Play-Navigation.cmd**：标准 ROS 2 / Point-LIO / AMCL / Nav2 / RViz 导航，同样直接采用 MuJoCo。
- **Play-Training.cmd**：复用标准手动入口。

独立的 Play-MuJoCo.cmd 与 Play-MuJoCo-Navigation.cmd 已合并移除。标准入口当前加载已验证的完整 RMUC2027 模型；指定其他地图须准备与其物理身份对应的模型包，校验失败会明确停止。弗兰德斯专用场景见本文末的迁移范围。

标准入口采用 1 ms 动力学步长、5 ms UE 世界更新和 60 Hz 视口目标。MID-360 v3 的每束测量采用 native 位姿历史；其频率与组帧独立于画面刷新。正常驾驶最高速度读取车辆包 drive-profile.json 中的 5 m/s。C、Shift、G、Q/E、Ctrl、Space、R 与既有操控映射一致。

Native 实时节拍以当前仿真 epoch 的起点和实际 solver 时间维护固定的墙钟目标时刻。普通迟到帧保留原目标，完整推进物理与传感器后继续追赶；领先于目标时刻时才等待。初始化、motion generation 改变或 solver 时间回退时重新建立锚点。50 ms 为限频告警阈值，迟到时长与真实锚点次数分别记录。

正式入口先校验当前车辆、源碰撞资产、场地物理边界、MJCF 和实际部署的运行库。模型不匹配时应重新生成，再启动；错误详情保存在本次 artifacts/verification/mujoco-launch/ 目录。

## 物理与表现的职责

| 部分 | 权威状态与实现 |
|---|---|
| 底盘运动、悬挂、轮轴、转向和云台关节 | 同一个原生 MuJoCo 世界，由 mj_step 推进 |
| 源车辆外观和场地材质 | 现有 UE 资产 |
| 车辆可视部件 | 跟随 MuJoCo 已完成的关节与刚体状态 |
| UE 车辆碰撞代理 | QueryOnly，供相机、遮挡和传感器查询使用 |
| 场地接触 | 与当前 UE 静态实体对应的 MuJoCo 几何 |
| IMU、LiDAR 安装姿态 | 真实底盘与云台关节状态 |
| RGB 图像与射线查询 | UE 渲染和场景查询 |
| ROS 时间 | MuJoCo 仿真时间，状态标识 synchronous_MuJoCo_solver |
| R 重置 | 同时重置求解器、指令租约、关节目标、传感器历史与 ROS epoch |

运行中的车辆没有第二套 Chaos 刚体积分。初始化与明确的 R 重置可以设置初始位姿；日常驾驶由轮轴力矩、舵向伺服、悬挂力和接触约束产生。

模型采用右手 SI 坐标；UE 表现采用左手厘米坐标。位置转换为 (x,-y,z) × 100，四元数转换为 (-qx,qy,-qz,qw)，角速度与力矩采用轴向量转换 (-x,+y,-z)。轴向量转换用于保持转向、IMU 和云台旋转的一致性。

## 车辆和地图

模型使用 15 个源部件、249 个当前车辆凸壳，包含四组独立悬挂、转向和轮轴以及 yaw/pitch 云台。车辆总配置质量为 25.6 kg；车体 20 kg、yaw/pitch 各 0.1 kg，对应原 UE 焊接后上部实际 20.2 kg。惯量来自源几何和配置质量，标定状态保留 calibrated=false。

悬挂读取当前 2000 N/m、140 N·s/m 配置。43 项驾驶与物理参数来自独立的 drive-profile.json，装配文件的内嵌快照已同步。手动模式的数值是导航的统一基准；Nav2 的运动限值由这份车辆配置生成，控制器、规划器、避障算法和原速度平滑器继续运行。

### 统一的驾驶参数

| 参数 | 手动与导航共同值 |
|---|---:|
| 前进上限 | 5 m/s |
| 倒车上限 | 2.5 m/s |
| 横移上限 | 4 m/s |
| 平面合速度上限 | 5 m/s |
| 线性加速度 | 2 m/s² |
| 普通减速度 | 4 m/s² |
| 主动/失联制动减速度 | 7 m/s² |
| 加速 / 减速 jerk | 6 / 20 m/s³ |
| 普通转向角速度 | π/2 rad/s（90°/s） |
| 角加速度 / 角减速度 | 3 / 5 rad/s² |
| 方向变化的横向加速度 / jerk | 8 m/s² / 70 m/s³ |

手动输入沿云台方向生成世界速度；导航输入使用 base_link 速度。两种输入在车辆端复用同一个连续整形器，连续导航消息保留加速度和方向变化状态。轮速分配、转向伺服、力矩、悬挂及接触继续读取同一车辆包。手动小陀螺使用既有独立模式参数。

导航先由原 fake_vel_transform 将 navigation_frame 指令转到 base_link，再进入原 velocity_smoother。运行参数为 max_velocity=[5,4,π/2]、min_velocity=[−2.5,−4,−π/2]、max_accel=[2,2,3]、max_decel=[−4,−4,−5]；方向变化的 8 m/s² 限制由共同车辆整形器执行。两个消息类型适配位于已有 NavigationRunner 中，公共 /aim_sentry/cmd_vel 继续发布带仿真时钟的 TwistStamped。

导航明确的零速度目标经坐标、时戳及 session/epoch 校验后，立即通过公共 cmd_vel 进入车辆制动，同时仍交给原 smoother。停止锁存期间将 smoother 的剩余非零尾段替换为零；只有时间戳严格更新的合法非零目标才能解除锁存。状态失效、断连和 epoch 更新清理适配器；恢复后等待新的合法输入。运行状态以 velocity_stop_policy=immediate_zero_target_latch_v1 和对应计数记录实际行为。

原 sentry YAML 保存算法来源，标准仿真启动时将车辆运动限值覆盖到生成配置，并记录源 SHA256。修改驾驶参数后，先同步装配快照及重建受影响的物理模型；运行时的源绑定校验用于防止旧快照与新导航配置混用。

MuJoCo 将原车体线性/角阻尼映射为质心阻力及惯量加权阻尼力矩；轮角阻尼保持原值 0.01。实际 RMUC 碰撞材质均使用 UE 默认摩擦 0.7，轮地与车体接触按原 Average 规则合成，分别约为 1.25 与 0.6。模型使用切向 Coulomb 接触，未添加原 UE 未配置的独立滚动/扭转摩擦项。车辆重力采用实际 UE 世界的 9.8 m/s²，并在启动时核对。

完整 RMUC 地图使用 14 组固定三角面、891,028 个顶点和 1,608,500 个三角形，并包含四个实际场地边界。源坡面、波浪、凹槽和孔洞保留原拓扑。体育馆装饰是否产生碰撞，以现有 UE 场景构造中的碰撞设置为准。

正式包：

    assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json
    assets/vehicles/aim_sentry/mujoco/rmuc2027/model.xml

生成的大模型、SDK、二进制和密集运行证据在本地保存；Git 保存生成工具、车辆配置、上游补丁、许可证及紧凑验收摘要。

## 开源实现与版本

采用官方 [MuJoCo](https://github.com/google-deepmind/mujoco) 4.0.1 原生 C API，基于提交：

    4ed093a9f06b170bd1564942fa37e1bb907d93e3

已研究 [UnrealRoboticsLab](https://github.com/URLab-Sim/UnrealRoboticsLab) 的 MuJoCo/UE 组织方式。本项目在既有 RMSim 模块内接入动力学，复用当前场景、输入、传感器与 ROS 通信。

完整地图使用独立构建的 4.0.1+rmsim.static-world-flex.3 运行库。补丁缓存全部顶点固定在 world 的静态三角面，消除重复顶点转换、AABB/BVH 更新、空间投影和自由度扫描。碰撞面、接触求解和积分算法继续采用同一 MuJoCo 实现。原版 SDK 单独保留用于逐态对照。

补丁位于：

    third_party/patches/mujoco/mujoco_static_world_flex_4.0.1-rmsim3.patch

详细生命周期和缓存失效约定见 docs/physics/mujoco-static-flex-cache.md。运行时动态修改静态几何需要显式使缓存失效；当前正式 RMUC 模型在初始化后保持静态。

## MID-360 与内部 IMU

默认参数为 360° 水平视场、−7°…52° 垂直视场、200,000 发射槽/秒、4 个通道及 10 Hz 组帧；可配置 5/10/20/50/100 Hz 发布窗口。每点保留 5 μs 时间与通道，缺失返回以零点保留槽位。数据处理继续采用游戏线程编排、UE 线程池批量求交、游戏线程材质处理和有界发送队列。

机械外参由源模型重新提取：点云原点距传感器底面 47 mm，+X 远离侧连接器，保留实际约 45° 安装和云台父链。内部 IMU 使用官方相对偏移。点云与 IMU 共享 MuJoCo epoch；完整建模范围、公开规格和近似项见 [MID-360 物理模型](../sensors/mid360-physical-model.md)。

## 在当前 Windows 环境重建

先安装独立 Python 环境和官方 SDK：

    python -m venv artifacts/environments/mujoco-4.0.1
    artifacts/environments/mujoco-4.0.1/Scripts/python.exe -m pip install -r tools/mujoco/requirements.txt
    python tools/environment/install_mujoco_sdk.py

构建带缓存的原生运行库。CMakeExe 和 Generator 使用本机安装的 CMake 与 Visual Studio；本次机器使用 Visual Studio 18 2026：

    powershell -NoProfile -File tools/mujoco/Build-MujocoRuntime.ps1 -SourceRoot artifacts/dependencies/mujoco/source-4.0.1-staticflex -BuildRoot artifacts/dependencies/mujoco/build-staticflex -InstallRoot third_party/mujoco-sdk/4.0.1-rmsim-static-world-flex.3/windows-x86_64 -PatchPath third_party/patches/mujoco/mujoco_static_world_flex_4.0.1-rmsim3.patch -CMakeExe "C:\path\to\cmake.exe" -Generator "Visual Studio 18 2026"

脚本检查固定提交和补丁 SHA256。失败后重用相同、已应用精确补丁的工作目录可传 -Resume；它通过临时 Git index 校验源码，没有更改原 checkout 的 index。已生成的独立 SDK 保留，重新构建使用新的安装目录。

准备物理模型并编译 UE 模块：

    powershell -NoProfile -File tools/mujoco/Prepare-MujocoModel.ps1
    powershell -NoProfile -File tools/unreal/Build-ImportLab.ps1 -EngineRoot $env:UE_ROOT

已有包的物理输入改变时，使用 Prepare-MujocoModel.ps1 -RebuildModel。旧包会保存到 artifacts/mujoco/model-history/。可用 -GeometryDirectory 指定已核验的原始场地三角网缓存。

UE 的 include 路径使用 SDK 的 c-api/include，该目录只包含 mujoco/ 头文件，避免 Windows 大小写不敏感路径下其他图形库与 UE Math 目录重名。

## 验收与适用范围

本轮验收按以下内容分别记录，结果以 docs/changes/2026-11-20-mujoco-integration.md 和紧凑验收摘要为准：

1. 当前源资产、物理边界和模型的一致性。
2. 原版 MuJoCo 与缓存版的逐态等价。
3. 实际 UE 车辆接触、行驶、制动、云台、重置和既有操控。
4. 完整场地波浪路线与边界碰撞。
5. ROS 时间、传感器与导航接口。
6. 同一最终构建的墙钟运行倍率。

--vehicle-only 生成的平面模型带有 diagnostic_plane_only=true，原生运行需显式 -MujocoAllowDiagnosticPlane，用于独立诊断。正式手动与导航入口使用完整 RMUC 包。

当前 MuJoCo 场景契约覆盖 RMUC 静态场地。弗兰德斯的运行时破坏、植被和动态碎屑继续由现有战雷入口负责；这些对象需要另外建立 MuJoCo 动态实体及移除/缓存失效映射后，才能接入该后端。Windows 原生验收与 Ubuntu 原生验收分别记录。
