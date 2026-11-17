---
doc_type: roadmap
slug: rm-simulator
status: active
created: 2026-11-13
last_reviewed: 2026-11-13
tags: [ue5, ros2, robomaster, native, multiplayer]
related_requirements: [native-algorithm-testing, authoritative-match, realistic-sensors, extensible-worlds-vehicles]
related_architecture: [architecture]
user_review: not_requested_under_standing_authorization
---

# RoboMaster 原生跨平台仿真规划

## 1. 背景与授权

目标是在 Windows 和 Ubuntu 原生运行同一套 `sentry_ws` 源码，在具备可校准光影、物理和传感器属性的比赛环境中测试自瞄、小哨兵、雷达与对抗策略，并支持换地图和新增车辆。

2026-11-13 用户明确授权持续自主实现及长期记录。本文据此形成工程计划，未声称用户逐篇审阅过自动生成的细节。技术依赖决定以下先后顺序；范围变化和新的不可逆外部动作另行处理。

同日最新指示：先使用 `E:\WarThunderModding` 的现有 AIM 哨兵做手动控制样例，自动车辆后置。第一可运行验收改为 `manual-aim-sentry`；长期原生同源算法、真实物理/传感器与联机目标保留。

## 2. 范围与明确不做

本 roadmap 覆盖：UE5 场景/物理/渲染、ROS 2 数据交换、两端同源算法构建与验证、车辆与地图资源包、年度裁判规则、权威联机比赛及证据记录。

- 不用 WSL 满足 Windows 原生要求。
- 不以游戏 AI、直接真值定位或替换后的算法冒充原 `sentry_ws` 闭环。
- 不承诺任意 URDF/传动机构一键可用；导入器对支持范围显式报错。
- 不将首切片 manifest 校验、离线导出、工具探测称为 UE 运行成功。
- 不把现有简化 1v1 裁判称为完整 RMUL/RMUC；赛事/赛季/规则版本必须显式绑定。
- 不另造第二套权威物理来同步比赛；客户端仅显示权威状态或执行自身算法。

## 3. 模块拆分

| 模块 | 职责 | 首次对应 feature |
|---|---|---|
| 资源包工具 | 检查车辆/地图描述、引用与支持范围，输出导入数据 | `vehicle-package-v1` |
| 手动导入样例 | 从用户现有 Blender 模型导出分部资产，控制底盘/云台并显示相机 | `manual-aim-sentry` |
| 原生算法构建 | 固定同源 `sentry_ws` 与依赖，构建 Windows/Ubuntu 算法 | `native-sentry-profile` |
| UE 运行时 | 加载车辆/场地，拥有权威物理状态并生成传感器 | `ue-vehicle-runtime` |
| ROS 2 接入 | 转换数据与控制契约，维护时钟、TF、QoS 和机器人隔离 | `ros2-sensor-control-bridge` |
| 比赛服务 | 维护版本化规则、裁判状态、合法事件和比赛结束 | `rmul-referee-v1` |
| 联机会话 | 分配机器人、路由传感器/命令、处理断线并记录对抗 | `authoritative-multiplayer` |
| 校准与评测 | 比较仿真和实测、记录性能与可复现实验 | `sensor-physics-calibration` |

资源包不是执行代码的插件。需要新驱动机构或传感器模型时，扩展受版本控制的运行时能力，再由资源包声明使用。

## 4. 模块间接口契约

本节是当前规划约束，不表示对应运行时已实现。车辆包第一版字段已与实现责任人对齐；规范化导出完整字段以实现中的 schema/类型定义为唯一字段源，在接入 UE 前再次检查一致性。

### 4.1 车辆包 v1：资源提供者 → 校验器 → 导入器

入口为包目录中的 `vehicle.json`。字段约定：

```text
schema_version: 1
id: string
display_name: string
model: {format: "urdf", path: string}
control:
  type: "differential_drive" | "holonomic"
  base_link: string
  wheel_joints: string[]
  gimbal?: {yaw_joint: string, pitch_joint: string}
  limits: {linear_mps: number, angular_rps: number}
sensors:
  - id: string
    type: "camera" | "imu" | "lidar"
    parent_link: string
    origin: {xyz: number[3], rpy: number[3]}
    rate_hz: number
    parameters:
      camera: width, height, horizontal_fov_rad
      lidar: horizontal_samples, range_min_m, range_max_m
      imu: empty object
```

上面的 `parameters` 按 sensor type 选择其键，不嵌套额外 `camera`/`lidar` 子对象。精确合法值、必填项和未知字段策略由 `schemas/vehicle-v1.schema.json` 与运行校验共同定义。

URDF 第一版支持固定、连续和转动关节、primitive 几何；mesh 仅保留通过路径检查的资产引用，不产生 UE 网格导入或材质兼容保证。校验检查引用、连通关系与惯量；导出包含 links/geometry/inertials/joints、sensors、control，保留 SI 单位和 ROS 右手坐标。

接口职责：`validate PACKAGE` 判断可导入范围，`inspect PACKAGE` 输出完整规范化 JSON，`export PACKAGE --output FILE` 生成规范化文件；PACKAGE 可为目录或 `vehicle.json`。成功返回 0；校验/文件错误返回 2 并写 stderr。任何非法输入不能以默认值静默修复成看似有效的车辆；export 不覆盖源 manifest、URDF 或引用资产。

已对齐的规范化输出根字段如下（代码入口 `load_vehicle`）：

```text
schema_version: 1
kind: "rm_vehicle_canonical"
id, display_name
units: {length: "m", angle: "rad", mass: "kg", inertia: "kg*m^2"}
coordinate_system: "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP"
root_link
links[]:
  name
  inertial: {mass, origin: {xyz, rpy}, inertia: {ixx, ixy, ixz, iyy, iyz, izz}}
  visuals[]: {origin, geometry, material?}
  collisions[]: {origin, geometry}
joints[]: {name, type, parent, child, origin, axis, limits?}
sensors, control
source:
  manifest_sha256, urdf_path, urdf_sha256
  assets[]: {path, sha256}
```

`geometry` 是带 `type` 的 box(size)、cylinder(radius,length)、sphere(radius) 或 mesh(path,scale)。`limits` 按关节包含 lower/upper/effort/velocity；material 支持 name/rgba。URDF 中的源名称、轴与 pose 保留，不在导出阶段转换 UE 坐标。资产哈希用于发现资源变更，不是物理真实性证明。消费端拒绝不支持的 schema_version/kind。

### 4.2 坐标与单位：资源包/ROS 2 ↔ UE

- 持久化描述统一米、千克、秒、弧度；ROS 右手坐标约定不变。
- UE 内部厘米/左手约定只由边界适配器转换；同一数据不能转换两次。
- 关节轴、姿态、惯量、线速度和角速度分别验证；不能只检查位置。
- 传感器的 `parent_link` 必须解析为机器人真实 link；相机 optical frame 的变换由接入层显式发布。

### 4.3 算法契约：机器人进程 ↔ ROS 2 接入层

按已检查的 `27UL_Sentry_ws` 固定源码复用以下类型；多机器人通过 remap 将硬编码绝对 topic 绑定到被授权的机器人，保留消息结构。

| 数据 | 类型/约定 | 所有者 |
|---|---|---|
| 仿真时钟 | `rosgraph_msgs/Clock`，所有仿真节点 `use_sim_time=true` | 比赛服务器唯一发布 |
| 底盘控制 | `cmd_vel`: `geometry_msgs/TwistStamped`，frame_id 明确 | 机器人算法输出 |
| 云台扫描 | `gimbal_scan_cmd`: `pb_rm_interfaces/GimbalCmd` | 行为树/控制仲裁输出 |
| 自瞄许可 | `auto_aim_switch`: `std_msgs/Int32` | 行为树输出 |
| 点云/IMU | `sensor_msgs/PointCloud2`、`sensor_msgs/Imu`；逐点时间及 ring 按 Point-LIO输入定义核验 | 传感器生成端 |
| 裁判 | `/referee/common/*` 与 `/referee/parsed/common/*` 的 `dji_referee_protocol` 类型 | 比赛服务 |
| 视觉目标 | `detector/armors`、`tracker/target` 的 `auto_aim_interfaces` 类型 | 真实视觉算法；仿真不伪造识别成功 |
| 导航 | `NavigateToPose`、`NavigateThroughPoses`、地图与代价地图 | 真实 Nav2 |

现有视觉是否直接订阅图像、底层控制器需要哪些虚拟硬件数据，见来源审查；上述表格不是对现有程序零修改即可运行的承诺。

### 4.4 权威状态与联机：会话 → 比赛服务 → 机器人

拟实现的跨模块信封字段固定为 `protocol_version`、`match_id`、`epoch`、`tick`、`sim_time_ns`、`robot_instance_id`、`sequence` 和业务 `payload`。这是服务边界的计划数据契约，传输编码在对应 feature 设计中确定。

- 服务器生成 `match_id`；重置比赛增加 `epoch`；旧 epoch 的命令必须拒绝。
- `tick` 与 `sim_time_ns` 由服务器单调推进；暂停时不推进仿真状态，网络会话心跳可继续使用独立墙钟。
- 命令通过绑定身份确定 `robot_instance_id`，客户端不能声明控制其他机器人。
- 控制命令带 sequence 和有效期；重复/过期命令不重新施加。断线按已配置失效策略归零控制，不自动续用无限期旧指令。
- 命中、血量、弹量、热量、规则区域事件由服务器产生，客户端不能写入比赛结果。
- 错误类别至少区分 `version_mismatch`、`unknown_robot`、`stale_epoch`、`expired_command`、`invalid_payload`；日志包含信封身份和原因。
- 传感器流只发给对应机器人及显式授权观察者。真值记录流不混入比赛传感器流。

### 4.5 地图包：场景提供者 → 运行时/导航/裁判

下一版地图资源需同时包含版本标识、场景资产入口、单位/坐标声明、碰撞入口、导航地图入口、出生点和语义区域。区域由唯一 ID、用途、所属阵营与闭合几何范围组成；出生点由机器人实例 ID 与 pose 组成。首切片不建立地图 schema，禁止用车辆 schema 代替地图结构；`map-package-v1` 启动前需独立定义并校验其文件协议。

### 4.6 验收记录

每个 feature 的验收文件记录：源码版本、命令、平台与工具版本、输入场景、实际输出、退出状态、证据路径和未验证范围。没有命令输出/测量记录的环节不标通过。各端通过各自验收；Windows 通过不推定 Ubuntu 通过。

## 5. 子 feature 清单

| 顺序 | slug / 描述 | 依赖 | 当前状态 | 退出信号与证据入口 |
|---|---|---|---|---|
| 1 | `vehicle-package-v1`：manifest、受限 URDF、规范化导出、两示例和预检 | 无 | done | Windows 26项测试24通过2权限跳过，两包导出通过；仅离线工具验收 |
| 1a | `manual-aim-sentry`：用户现有模型、手动底盘/云台、相机 | 1 | done | Windows UE5.8.4运行，18操控+9碰撞检查通过；仅本手动样例 |
| 2 | `native-sentry-profile`：同源源码与原生依赖闭包 | 1 | planned | Windows/Ubuntu 真实算法均构建且同段回放可运行；双平台构建与回放日志 |
| 3 | `ue-vehicle-runtime`：导入受支持车辆，验证几何/惯量/关节 | 1 | planned | UE 中生成两个不同驱动示例，尺寸/运动可测；运行截图与数据 |
| 4 | `ros2-sensor-control-bridge`：时钟、控制、TF、传感器桥接 | 2、3 | in-progress | Windows速度/原LK电流/瞄准射击/相机/IMU/LiDAR与原Point-LIO切片通过；完整算法闭环/校准/Ubuntu尚未通过 |
| 4a | `native-imu`：原始底盘六轴、采样与安装 | contact-vehicle、4的基础桥 | done | Windows最终12/自由落体3/坡面1/帧率不足11及配置拒绝、旧功能回归通过；无AHRS/Point-LIO/实车标定 |
| 4b | `lidar-pointlio`：真实滚动扫描及原Point-LIO | 4a、4的基础桥 | done | Windows原节点/消息、复位13/旋转安装11/RMUC10/原LK联合11与压力/遮挡/回归通过；未校准、完整导航/Ubuntu未验收 |
| 5 | `single-sentry-closed-loop`：单车真实视觉/导航/决策闭环 | 4 | planned | 同源程序在两端完成定义路线和目标跟踪；rosbag与独立误差记录 |
| 6 | `rmul-referee-v1`：显式赛季 RMUL 最小裁判闭环 | 5 | planned | 发射→命中→裁判→行为响应；逐规则用例与事件记录 |
| 7 | `authoritative-multiplayer`：跨电脑比赛与隔离 | 6 | planned | 两端各自运行算法参与同一场比赛，断线/重置/身份隔离可测 |
| 8 | `sensor-physics-calibration`：成像、采样、动力学校准 | 5 | planned | 有测量依据的误差曲线与性能预算，不仅是画面截图 |
| 9 | `map-package-v1`：平地地图、区域和静态占用一致性 | 1a | done | 两地图各9项原生检查，独立bounds/出生/区域/栅格核对；无真实定位闭环 |
| 9a | `source-mesh-maps`：RMUC2027默认场地与WT CDK地形 | 1a、9 | done | RMUC/WT各7项，实际坡地支撑/驾驶/瞄准、默认入口显示；无比赛机构/真实动力学 |
| 10 | `rmuc-roles-and-radar`：RMUC角色/雷达与扩展规则 | 7、8、9 | planned | 按规则版本验收角色与雷达流程，列出仍未实现项 |

首 feature 目录为 `.codestable/features/2026-11-13-vehicle-package-v1/`。后续条目启动时才创建对应 feature 目录，不能预填验收结论。

**用户当前要求的第一可运行样例**是第 1a 项。第 5 项保留为后续自主算法闭环。第 1 项是可独立运行的资源处理切片，提供必要的输入边界，但不宣称比赛或机器人闭环已打通。

## 6. 排期思路

先证明资源描述并实现用户指定的手动车辆样例，再验证现有算法接入与自主单车闭环；随后加入裁判和联机。光影/传感器校准、地图扩展在已有闭环上验证。以退出证据推动，不给未实测的工作虚构耗时或性能保证。

UE 安装/授权状态和双平台依赖是当前外部前置条件。无法运行 UE 时继续资源校验、同源依赖审计和可离线验证的契约工作，并保留对应运行门槛未通过。

## 7. 观察项

- 公开 `sentry_ws` 的部分依赖 URL 与固定 SHA 所在 fork 不一致，复现前必须锁定来源。
- 原版与定制导航的 Twist/TwistStamped、namespace、gimbal frame 不一致；不能仅改 topic 名。
- 实车固定地图目标不能直接用于换地图；后续由地图包命名目标提供。
- 旧 Gazebo 可继续作为对照基线；本 roadmap 主运行时为 UE5，不承诺同时实现两套引擎。
- 当前规划接口比首切片更广。只有第一版车辆契约进入当前实现，其他计划不会提前作为“已支持”写入公开接口。

## 8. 变更日志

- 2026-11-13：依据用户持续实现授权建立 roadmap；车辆包第一版开始实现，其余条目保持 planned；未声称用户逐篇审阅。
- 2026-11-13：用户将第一样例收敛为现有 AIM 哨兵手动控制；增加 manual-aim-sentry，自动算法闭环后置。原生 ROS2基础通信已通过但不是完整 native-sentry-profile。

- 2026-11-15：Windows手动AIM样例运行验收完成；战雷式控制与准星视差反馈已实现/回归。原生Ubuntu、完整算法、裁判和联机不受此done状态覆盖。


## 2026-11-15 地图接口落地补充

地图包v1先实现平地box geometry、出生、区域语义和占用栅格；不依赖先完成裁判。技术依赖改为manual-aim-sentry，裁判以后消费region数据。没有减少原生双平台、真实物理或完整赛事目标。

map.json精确结构见schemas/map-v1.schema.json和map-package-v1 design：floor(size_m/surface_z_m/thickness_m/color_rgb)、boundary(height_m/thickness_m)、obstacles(id/center_m/size_m/yaw_rad/color_rgb)、spawns(id/team/position_m/yaw_rad)、regions(id/kind/team/center_m/size_m)。inspection_obstacle_id是可选测试地标，正常地图可以无障碍。

C++ MapStage与Python rm_map_tools使用同一米/ROS右手坐标约定。runtime按车体完整collision proxy校验出生，等physics bodies真正创建后才开放输入。地图视觉标记不自动产生加血、占点得分或裁判事件。

## 2026-11-15 最新优先级

按用户最新指令先Windows，Ubuntu只预先离线适配；不以缺少Ubuntu机器/SSH阻塞当前工作。RMUC2027已成为第一地图，几何来自官方V1.0.0；WT已运行的是官方CDK高度场裁剪，完整游戏小地图仍未提取。两者静态场景不替代裁判/联机/传感器与真实物理目标。

mesh地图新增kind=rm_mesh_map，描述显示/碰撞虚拟资产路径、源哈希、bounds和出生。当前regions为空；有界地形跟随只服务手动样例。Ubuntu同源脚本会调用与Windows相同的地图导入Python。原生Ubuntu验收和完整sentry_ws保持未完成。

2026-11-15后续：新增并完成contact-vehicle最小切片，默认AIM改真实接触/悬挂/轮电机。红蓝波浪路两档速度往返与物理操控/射击/墙阻挡已通过，碰撞离线重建流程Win/Linux共用。通用ue-vehicle-runtime及实车校准不随此子集标done。原战雷弗兰德斯已提取/准备，但完整场景导入与实际驾驶仍待验收。

2026-11-16：swerve-gyro-control最小切片已实现并通过45项原生检查，输入/控制参数移入车辆包，独立舵轮、小陀螺及坡上保持生效。本地LK核心数学与配置预备件为后续同源节点接入提供入口；完整电机消息/功率标定、ROS感知、比赛和网络仍是后续目标。

2026-11-16后续：在AIM接触样例上先完成原生ROS桥的速度/相机/时钟切片，未等待完整sentry依赖闭包。11项真实ROS、19项TCP、只读/停顿/复位与19项手动回归通过，证据artifacts/ros2/native-release-summary.json。该切片不改变原长期双平台同源、完整感知/电机控制和联网目标，也不将native-sentry-profile标完成。
