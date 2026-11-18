---
doc_type: architecture
slug: architecture
scope: 已实现模块与实际证据
summary: Windows UE接触车辆、弗兰德斯保存关卡与源材质适配、原LK和传感器/导航已有分项实证；Ubuntu仍离线准备
status: current
last_reviewed: 2026-11-18
tags: [unreal, vehicle, controls]
depends_on: []
implements: []
---

# 当前架构

## 现有数据流

用户模型只读 → tools/assets导出FBX/PBR与manual_asset_rig_v1 → UE Editor Python导入 → ue_mesh_rig.json → VehicleRig → 手动输入/相机/HUD。

另有受限URDF离线校验/规范化路径；Python检查成功不代表任意车型动力学成立。

地图有三条数据路径：flat map.json → MapStage primitive/collision/region；来源几何 → paired FBX/mesh-source.json → UE导入 → mesh map.json → MapStage/MeshMap；原图数据 → scene-source.json/实例文件/高度场 → Import-WTScene → 保存的World.umap → rm_scene_map描述 → SceneMap身份与碰撞就绪检查。三条路径均向真实Chaos车辆提供静态场地。默认地图为RMUC2027；Play-WarThunder.cmd直接加载已导入的弗兰德斯World。旧轮位跟随保留为显式KinematicPreview。

## 模块

| 模块 | 真实职责 |
|---|---|
| rm_vehicle_tools | 包路径、URDF树、惯量和引用校验，规范化导出 |
| import_vehicle_assets.py | FBX/材质槽/PBR导入，生成UE资产路径映射 |
| VehicleRig | 部件层级、关节变换、视觉轮转、相机、保守碰撞查询 |
| ImportLabGameMode | 场景、PC输入适配、共用ApplyManualInput、车体/瞄准镜相机和HUD |
| ManualControlProbe | 在真实引擎里向共用输入路径送控制帧，并记录真实位姿/相机结果 |
| MapStage / MeshMap / SceneMap | 三类地图的数据边界、场地几何、出生点、平地区域；saved scene核对关卡路径、源SHA标签、Landscape/HISM和碰撞注册 |
| SceneRenderReadiness | saved scene启动时等待实际资产/着色器编译和近景流送，弗兰德斯预载出生点附近RVT页并完成渲染帧后开放驾驶 |
| VehicleTerrain | 轮位射线、支撑平面、保守移动/转向扫掠、台阶/缺地面拒绝；不计算接触力 |
| TerrainProbe | 共用操控的7项真实地形检查，保存轮位、位姿和PNG |
| TrainingRange / CombatProbe | 单车训练回合、弹丸球体扫掠、目标HP/弹热/弹量/时序，14项实际验证 |
| MapImportLibrary | 编辑器Landscape/HISM导入、实际网格三角/材质槽读回、源UV载荷全精度构建；运行时加载保存关卡 |
| import_wt_scene / wt_materials | 原图资源哈希、稳定材质ID、严格三角与退化证明门槛，源树叶与地表贴花材质适配 |
| wt_runtime_virtual_texture | 原地表贴花仅绘入RVT，Landscape和外围landmesh采样合成；保存后独立核对Volume、写入组件和缓存配置 |
| extract/prepare_wt_landclass / wt_landclass | 从原 lmap 恢复五类颜色、1,024 cell 与 448 页权重 atlas，保留原坐标与末层权重公式，向 Land/Terrain 材质提供源绑定 |
| wt_water / update_wt_water | 按固定源地图、材质和水面 FBX 身份消费原 level 颜色，创建专用水面父材质；旧保存场景支持独立原生更新 |
| prepare_wt_foliage / wt_grass / MapVegetationLibrary | 七个源网格、独立 alpha 与 13 层规则准备；真实 RHI 烘焙 Landscape GrassData、按视点流送，以及原生类型/权重/几何与样点回读 |
| prepare_wt_overview / MapOverview | 原 tankmap 标定与导入；512 米跟车小地图和 M 键全幅原图，显示实际 Rig 位姿、出生点、坐标及尺度 |
| rm_map_tools | 平地box地图校验与同几何PGM/YAML导出，不支持坡地导航投影 |
| tools/maps / import_mesh_map.py | 来源核验、RMUC/WT准备、UE triangle collision导入与独立bounds核对 |
| tools/unreal | Windows/Linux启动，构建、导入、报告门禁；Linux尚未实跑 |
| tools/environment | 隔离Windows Humble环境、原节点构建、局部DLL部署与通信/数值证明 |
| RosBridgeComponent | 可选loopback单客户端、严格速度指令/会话校验、固定视场RGB采集与Chaos时钟，PostPhysics采样 |
| rmsim_bridge | 原生rclpy sidecar，真实消息转发、光学TF与独立评测真值；有界drive_demo，不是完整sentry_ws |
| VehicleMotorDrive / lk_adapter | 可选RosMotorControl：原LK电流消息、真实轴角/轴速反馈、等大反作用扭矩；失效转受电流上限约束的安全停车 |
| chassis_controllers / custom_msgs | 用户原包工作副本与固定上游完整消息包；原生MSVC构建，原PID/功率限制，源码补丁和pristine分开保留 |
| RosAimController / aim_adapter | 原视觉/扫描消息的高层直连；独立200ms瞄准/发射有效期、世界方向、限位扫描、关节反馈和实际训练弹道 |
| RosImuComponent / imu.py | 可选底盘安装六轴IMU：实际点速度差、重力/安装变换、轴向角速度、噪声、独立采样和有界批传输；ROS原始消息及静态安装TF |
| RosLidarComponent / lidar.py | 实际场景首回波、逐物理帧滚动扫描、盲区/自身遮挡、有界点区；原Livox CustomMsg与PointCloud2 |
| point_lio / point_lio_runner | 固定原估计器原生运行；从传感器配置推导外参/SI单位，新会话重启局部地图，真值只用于外部评测 |
| 原哨兵导航工作包 / navigation_runner | 原位姿/扫描/地形链、AMCL、Theta*/Omni PID与代价地图在平地测试场地真实运行；地图身份校验、带协方差初值、生命周期ready编排 |

## 状态与坐标

车辆描述使用米/弧度/ROS右手系；运行边界转换到UE厘米/左手系。视觉bounds只包含实际车体mesh，碰撞轮proxy不随视觉轮转膨胀。

AimWorldTarget保存世界目标，鼠标从相机中心射线产生目标；炮口从自己的位置跟踪。自由观察和切镜保留该目标，返回第三人称时从较高pivot重新投影，避免静态视差。

MouseX/Y使用原始计数，按本项目灵敏度和FOV比例各应用一次。输入配置在DefaultGame.ini，项目关闭默认鼠标平滑。手动失焦立即归零运动并忽略输入，重获焦点丢弃首帧鼠标跳变。显式RosControl模式由ROS拥有底盘，失焦继续执行有效外部指令；Space人工制动、R复位。

mesh场地等碰撞body真实创建后落车。默认AIM样例随后启用VehicleContactPhysics：13独立刚体、4滑动弹簧/4舵向/4轮轴约束；真实世界帧解算位移，禁用旧TerrainPose行驶门限。旧运动学仅显式诊断。瞄准目标先转车体局部坐标，保留倾斜地面的世界瞄准行为。显示mesh无碰撞、独立隐藏mesh承担triangle查询/物理接触。

ContactProbe/GyroProbe/TractionProbe以真实世界帧运行路线、控制、响应和坡向抓地检查；OnComponentHit保存接触证据。关节投影/质量调节关闭。碰撞通过prepare/ensure_vehicle_collision.py在独立资产生成，bind_vehicle_collision.py核对哈希并保存旧rig。当前15视觉部件，body128凸包保留低处凹槽，四轮采用源胎冠凸包；缺少源轮胎几何的包回退圆柱。Win/Linux入口共享准备脚本。

VehicleDrive.cpp拥有带坐标声明的速度目标、世界平面速度大小/方向整形、舵向前馈和轮速PI执行。Control/SentrySwerveKinematics.h适配本机LK核心数学逻辑。车辆包drive-profile.json启动时读取，配置不依赖键盘来源。GameMode仅负责镜头相对WASD、Q/E、G/Z/X、Ctrl及HUD；GameMode在PostPhysics更新视图，车辆电机在PrePhysics更新。手动模式取消未使用离屏视图逐帧渲染，快捷入口防止重复实例。

## 已有证明

最新ROS流：物理车辆/独立固定相机 → RosBridgeComponent → 有界loopback TCP → rmsim_bridge → /clock、RGB8/CameraInfo、base→optical TF。TwistStamped反向进入同一个VehicleDrive；Rig在PrePhysics检查单调时钟租约。R更换epoch和连接session，清空旧指令；仅评测topic提供真值。相机标称10Hz、默认1280×720/60度；渲染回读同步，尚未标定。后续原LK电流与高层云台/发射接口见下方边界。

原生ROS11项、TCP19项、只读4项、stall1项、实际R复位及手动19项回归见artifacts/ros2/native-release-summary.json；旧控制45项与波浪/抓地记录仍保留在artifacts/gameplay/gyro-control。两者模块哈希不同，不将旧45项冒称为新模块全部重跑。

真实UE5.8.4编译与导入；7mesh尺寸核验；9项运动学检查；18项操控检查；实际GUI与键盘事件/位姿；原生Windows ROS2 smoke。数据在artifacts/unreal/2026-11-15及artifacts/environment。

两张平地地图各9项运动检查及占用一致性；RMUC与WT各7项地形检查，实际1.5°/5.214°支撑坡度；独立导入bounds和轮位法线核对。数据在artifacts/maps；源文件按artifacts/sources与assets/maps分开保存。

## 边界

RosImu从车辆包sensors/imu.json读取1–4个base_link安装配置，在真实Chaos帧后按物理时钟采样点速度及角速度。比力为相邻真实采样窗口的速度差减实际世界重力，再转传感器坐标；角速度按轴向矢量反射。每通道32样本有界队列，经50Hz状态批传输，ROS保留原采样时间。未采周期/缓冲丢样显式计数；不插值补足标称频率。R及显式物理位姿重置清理导数历史和待发样本。

RosLidar可选读取sensors/lidar.json，实际Visibility首回波查询包含自身。默认200k射线/仿真秒、10Hz、均匀未标定图样/常反射率；同物理帧共用真实时间/姿态，保留跨帧运动畸变。每帧4096射线、每扫描65536点、最多2待发帧，超额显式计数。新会话/位姿复位丢弃半帧和旧队列。

有LiDAR时状态升级v2：同一有界二进制体先RGB，再按JSON偏移放20字节little-endian点区；原命令/v1无LiDAR不变。Python经原Livox生成器/解码器验证的批量CDR发布原消息，并发布同点PointCloud2、静态安装TF。合计二进制16MiB，JSON64KiB，既有250ms执行租约不受传感器模式放宽。

原Point-LIO只订阅点云/IMU/clock，未消费评测真值。runner从两个实际安装变换求外参，按session/epoch重建进程/局部地图；原节点参数读取失败退出2。Windows默认/旋转安装/真实R/RMUC/原LK联合有分项证据，见artifacts/sensors/lidar。该层提供单车局部估计；地图定位由下述AMCL承担，原输出话题尚未多车隔离，完整感知闭环仍未完成。

导航新增当前子集：Point-LIO估计参考IMU，经原loam_interface和sensor_scan_generation输出odom→base；原terrain节点的odom_frame现可配；AMCL持有map→odom，扫描和代价地图使用实际传感器数据。原fake_vel_transform将导航固定朝向frame速度旋入base，仿真符号为+1。原Theta*/Omni PID/IntensityVoxelLayer在import_lab已实际绕障到达目标，不能外推为RMUC全场导航。

RosBridge发布已加载地图的id/kind/MD5，平地占用导出保存同源MD5；navigation_runner拒绝不匹配地图。初始化用显式有偏先验及非零协方差，等待原AMCL估计候选与Nav2生命周期全部active；没有真值定位输入。原诊断库显式DLL可见性解决Windows虚表崩溃，组件算法未替换。目标取消停车已有实测；当前2000/140悬挂下RMUC波浪自主往返、主动导航途中bridge断线/重连后重新定位和旧目标不重放均有分项通过证据。监督器以有界只读查询处理状态回复超时。Windows导航进程树使用经过严格校验的127.0.0.1 UDP配置；扫描与位姿适配器使用非阻塞安装TF查询，未就绪时跳过当前观测。最新正式入口两轮RMUC断线重连往返各14项与平地实际R恢复17项通过。多车隔离、更多出生点和全场通行图及完整自主行为继续验收。

raw Imu只提供六轴，orientation_covariance[0]=-1；安装TF只从base_link指向传感器，没有世界定位真值。偏置/白噪声是未标定模型，零协方差表示未知。默认200Hz可配通用SimStepSeconds=0.001运行，约0.3倍墙钟；不启用RosImu时没有IMU工作。该物理mesh-rig配置不等于通用URDF传感器支持。源码/原生证据在artifacts/sensors/imu；原Point-LIO已与实际IMU/LiDAR联跑，证据在artifacts/sensors/lidar。当前2000/140悬挂下原LK/传感器/Point-LIO联合11项通过，RMUC波浪自主往返与bridge会话恢复也已分项运行；完整范围见当前navigation checkpoint。

原LK电机路径采用固定1ms物理步长及配对反馈计算，PID按仿真采样时间差缩放，原增益保持。可慢于墙钟；HUD的FPS为墙钟实测、sim倍率独立显示。默认手动/速度桥不启用固定1ms步长。电机模式当前没有电气动态和实车参数标定，安全停车反馈的current只代表外部电流通道，不能当作停车伺服电流测量。具体使用与最新验证见docs/guides/sentry-lk-motor-loop.md及对应feature记录。

RosAimControl独立取得云台/训练发射控制，不续期底盘命令。高层云台仍为有限转速运动学关节/中立碰撞代理，不是硬件电流环。ROS模式训练时间跟随Chaos，发射时刻在前后炮口姿态间插值。自身弹量/热量与靶HP/命中评测分话题；只观察模式仍采集实际手动关节速度。sp_msgs/pb_rm_interfaces是固定原消息包，完整Hub/视觉算法没有因此被标为已运行。

后续范围包括实车动力学标定、比赛权威服务器、完整裁判、完整自主感知决策闭环与原生Ubuntu验收。AIM物理mesh-rig与通用vehicle.json导入分别跟踪，长期设计放在roadmap。

训练状态由本地GameMode协调的TrainingRange持有，输入表达trigger/reset，事件时间属于仿真；权威联机沿roadmap推进。原图导出与导入在编辑器阶段完成，手动运行通过保存的关卡和统一MapStage/VehicleContactPhysics进入车辆控制。运行时与原生验证的具体版本、哈希和覆盖范围见当前进度及弗兰德斯指南。

[约束](../attention.md) · [长期规划](../roadmap/rm-simulator/rm-simulator-roadmap.md) · [手动样例验收](../features/2026-11-13-manual-aim-sentry/manual-aim-sentry-acceptance.md)

当前本地舵轮驾驶的起步与改向分别限幅：车辆包方向加速度10m/s²/jerk100m/s³，起步0.8m/s²、松键3m/s²。2m/s改向专项有前后对照；高自转切慢行波动已由源胎冠及按需唤醒修复，见2026-11-16-drive-response-lag及gyro-precision-ripple的当前证据。原LK电流执行模式独立，未因本地参数修订而更换算法。

当前轮胎优先由源网格凸包提供collision_vertices_m，prepare/bind/ensure与importer共享几何哈希，旧圆柱仅作为未提供源轮胎几何时的回退。四轮刚体质量/控制参数沿用原配置，允许静止岛自然休眠，新运动指令主动唤醒。原gyro-precision-ripple已由源胎冠和按需唤醒修复，该版本92项原生回归可查。刚性凸包不等于橡胶接触标定。

## 当前悬挂与验证入口（2026-11-17）

AIM车辆包默认每模块弹簧2000N/m、阻尼140N·s/m，预载50N、总行程65mm；引擎通用回退值与其他包保持原状。SuspensionProbe在显式测试旗标下向实际车体施加相同载荷/冲量，并记录真实世界帧与chassis_awake；普通模式不运行该夹具。2026-11-17对应模块/配置92项原生回归与两次各4项悬挂专项通过，汇总artifacts/control/suspension-round2-20261117/release-summary.json。新悬挂原LK/Point-LIO联合11项和RMUC自主往返9项通过。原会话恢复间歇失败已保留在suspension-round2检查点；最新本机传输与TF修复后，正式入口两轮RMUC各14项、平地实际R恢复17项通过，共45项。当前证据见artifacts/navigation/reconnect-tf-20261117/release-summary.json，下一步扩展路线/出生点、公开包装与多车隔离。

## 已实现可视化导航与旋转安装传感器

Play-Navigation启动单实例生命周期监督器，编排UE、原Point-LIO、原AMCL/Nav2、桥与RViz。监控读取估计地图/位姿/action状态，采用127.0.0.1受限传输。真实GUI调用原NavigateToPose/FollowWaypoints和Cancel，默认视图聚焦已验证的红方波浪区域。

原源码95c66c2的导航偏航目标整形在桥中独立实现，输入实际导航速度、估计底盘yaw与关节反馈；高优先级扫描/获准自瞄保留，导航不发射。v2传感器配置增加yaw_link，逐物理帧安装TF、射线和共装IMU跟随实际关节；v1固定安装入口保持。Loam接口根据重力初始化的原估计与动态安装TF恢复底盘，单次平移/偏航对齐，避免重复应用45°安装旋转。Point-LIO归一化与原始饱和参数配对转换到SI。

2026-11-17导航版本原生84项、图形13项与离线120/2跳过完成验收，见artifacts/navigation/sentry-console-20261117/release-summary.json。硬件云台电流环、传感器标定、新组合实际R/断线、全场/多车和原生Ubuntu继续沿长期规划推进。


## 弗兰德斯保存关卡与地表覆盖（2026-11-18）

`SceneMap.ps1` 校验 map.json 的场景类型、包路径、源 SHA、单位、边界与出生点；启动 URL 直接选择已保存 World。`SceneMap.cpp` 再核对实际 World 和源标签，等待 Landscape/HISM 的真实碰撞注册，然后进入原接触车辆。脚本按规范化项目路径共享启动/导入锁，同一工作树的 Editor、commandlet 与构建互斥。

导入器逐个核对 726 视觉和 639 碰撞网格；材质绑定按稳定源 ID 去重。仅源中已证明的共点退化面允许从三角数扣除，证明文件和源/导出 FBX 的 SHA 一起核对。叶片 half4 与地表 short4n.w 通过全精度 UV 保存，独立原生重载再检查 UV 通道、三角和原材质分配。

原 world_decal HISM 保留几何、实例与材质，仅绘入独立 RVT；Landscape 与背景 landmesh 采样预乘颜色和遮罩合成地表。RVT normal 的压缩符号与 XY 分量按原生格式重建。与原游戏 clipmap 对应的适配经过源数据、公式和保存图连接三层检查。普通树分离 alpha 使用源纹理哈希注册表限定，避免把特例推广到全部树材质。

`SceneRenderReadiness` 以实际编译计数、同步 streaming 评估和完成渲染帧开放驾驶。RVT 在 render thread 从真实 allocation 计算出生点附近请求矩形、同步请求 API 返回后再完成两帧；报告记录请求矩形覆盖数量，本轮未计量 GPU 实际生产页数。物理控制仍通过原 VehicleDrive 与 Chaos 求解，ContactProbe 的新复位检查走共用 RestartRound。

历史基础版本的独立关卡 21 项、材质 88 资产与弗兰德斯/RMUC 各 5 项接触回归通过，绑定模块 SHA `eceebaa1634a2df9c6314556efcc0190f5c5b4b926ef13299669ca461d68de76`。完整导航/稳定 1× 及更广道路覆盖继续独立验收，功能保留在地图分支。

### 原 Land、水面、草层与地图 UI 增量

主 `Import-WTScene` 读取原 Land class 包和 `overview.json`，保存背景材质与原地图 UI 纹理；当前材质作者同时按源身份恢复水色。主导入完成后由独立 `Author-WTGrass` 在 D3D11 下安装 LandscapeGrassOutput 并烘焙 GrassData，默认四个 shader workers 只通过本次进程参数配置。严格恢复模式核对已保存来源、模型、权重、13 层绑定及 Terrain 连接，再继续烘焙。

草实例使用源规则重建，原七网格的 UV、尺寸和独立 alpha 保留，13 层权重覆盖完整 2,048 米核心区。`UpdateGrass(cameras,true)` 同次完成异步任务与预建树接收；`UGrassInstancedStaticMeshComponent::GetNumRenderInstances` 读取渲染实例数量。实例组件为按视点生成的临时对象，保存关卡中的 GrassData 由独立只读重载核验；装饰草关闭碰撞。

`grass-native-07-resume` 已保存 World：七网格、13 层，1,089 有效 / 986 有权重组件，两处预览视点 2,237 个 Grass 组件、52,984 个 render instances。`grass-reload-02-final` 在草重载模块 `1bf4126eb733b12880e170a5eb4830f50b5f792cdda55a6ca2fd1507ed26558a` 上 27 项通过，四处源样点最大四角误差 1 byte、插值误差 0.25 byte，58 文件 SHA 保持。`water-native-02` 原生退出 0、14.17 s。该模块同时包含并行 `WTFoliageCrushComponent`；其碰撞与破坏验收见独立记录。

后续组合模块 `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404` 已通过 `native-scene-04-final` 的 21 项检查，退出 0、14.043 s；`flanders-contact-04-final` 与 `rmuc-regression-03-final` 各 5 项接触/复位、8 PNG 通过。保存场景的源组计数与运行时动态破坏后的碰撞分层各自记录。`native-materials-08-final` 在 d79cf620… 模块上 104 个资源原生编译通过，退出 0、54.527 s；`native-materials-07` 的失败证据保留。最终 GUI 已在同一 d79cf620… 模块通过，8 张截图与 12 次真实 OS 动作记录了源地表、草与粉白花、车辆地图标记和 M 四次切换。实际车辆驶至 ROS `(1075.232351,-1012.656026)`，Q 旋转后两种地图箭头同步转向；出生标记独立。证据为 `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`。地图 UI 消费当前 Rig 变换用于显示；完整导航仍使用既有定位与规划链。
