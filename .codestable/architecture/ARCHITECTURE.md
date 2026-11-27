---
doc_type: architecture
slug: architecture
scope: 已实现模块与实际证据
summary: Windows原生车辆、导航与弗兰德斯共用项目；源速率、200Hz接触物理、60Hz绘制与地图适配分层验证
status: current
last_reviewed: 2026-11-18
tags: [unreal, vehicle, controls]
depends_on: []
implements: []
---

# 当前架构

## 2026-11-27 Mid-360 GPU BVH

`FGpuLidarScene` 在专用 MuJoCo 快照上组装实例 BVH，复用 Chaos 烹焙几何建场地及连杆 BVH。HLSL 全局计算着色器合批追踪，两路 `URosLidarComponent` 通过有界 FIFO 回读共用 MID-360 物理测量和 ROS 扫描协议。显式开关选择 GPU，现有 CPU 路径用于默认工作流及逐射线参考。

## 2026-11-27 RMUC 六车内置步兵 AI

`ARMUCGameReferee` 在比赛阶段切换时向所有载具同步底盘和云台权限。`AVehicleRig` 在运动指令入口执行锁止，ROS 云台控制器清理倒计时内的旧目标。

席位控制标签由比赛入口交给裁判。六车对局中的 I3、I4 AI 共用 UE 镜像路点图，昂贵的场景查询由各席轮转。功率、读卡、扣金币与实际弹丸由既有裁判结算。手操和 ROS 席各自拥有独立控制路径。 [实现和验收](../../docs/changes/2026-11-27-rmuc-infantry-ai.md)

## 2026-11-27 RMUC 用户飞镖外观

比赛站体由地图包继续提供盖板、导轨和底部滑台。`dart-launcher/mount.json` 指定挂在滑台上的光辉 2.0 渲染网格，镜像变换共用同一模型。飞行展示加载 XD-1 渲染网格，镖头沿局部 +X，设备舱盖位于上侧，尾端连接阵营曳光。红方在镖头盖的独立材质槽绑定红色，蓝方使用原材质。裁判与场地查询使用既有仿真接口。 [资产换版](../../docs/changes/2026-11-27-rmuc-user-dart-visuals.md) · [上下核对与镖头配色](../../docs/changes/2026-11-27-rmuc-xd1-up-and-color.md)

## 2026-11-27 RMUC 复活、后勤与场地增益

裁判在每个比赛步按实体读卡和补给区车载视觉读取本车状态。视觉投影读取车辆标称图传视场与实际挂载位姿，静态标牌依附 CAD 公路侧墙。原始卡信号供高地跨越顺序和解除虚弱，补给区允许视觉途径参与区域资格。机器人保存自身复活、免费弹与已接单交付状态，付费额度和金币由队伍共享。 [规则与验证](../../docs/changes/2026-11-27-rmuc-field-rule-parity.md)

## 2026-11-27 RMUC 现场购弹资格

裁判使用本车已经激活的己方补给区、基地、制高点或己方完全控制的前哨站判定现场兑换。遥测 `local_ammo_exchange_active` 表示点位资格，`local_ammo_purchase_available` 增加金币与额度检查，`supply_active` 继续描述补给区。仿真哨兵行为树按这两个购弹字段选择现场请求和驻停，其余比赛状态继续从裁判读取。 [验收](../../docs/changes/2026-11-27-rmuc-ammo-exchange-points.md)

## 2026-11-27 RMUC 3v3 补给、定位与目标保持

UE 裁判在同一比赛步内更新每辆车的补给读卡状态并处理该席的购弹请求。各 ROS 席收到本车的购弹资格和队伍金币。双席 MID360 已按整组采样降载，Point-LIO 运行器对该仿真档位保留源点索引，后续仍应用原有体素过滤。比赛视觉以裁判阵营和实拍灯条为依据，当前目标持续可见时优先延续跟踪，交接等待独立图像确认。测试的真实车位只供误差对照。 [验收记录](../../docs/changes/2026-11-27-rmuc-match-supply-and-autonomy.md)

## 2026-11-26 RMUC 3、4 号步兵外观

可选编队字段 armor_number 只选择 AIM 装甲的中央纹理。红蓝着色仍由阵营灯条材质提供。红 3／蓝 4 和红 4／蓝 3 的样例随车辆包存放，覆盖层继续使用原透视、安装与碰撞合同。证据见 docs/changes/2026-11-26-rmuc-infantry-appearance.md。

## 2026-11-26 RMUC 比赛小陀螺配置

共享 MuJoCo 比赛 world 的两席均按车辆包读取小陀螺限值，比赛的 UE ROS 桥沿用相同车辆限值。非比赛入口保留已有 5 rad/s 策略。模型清单与原配置重新绑定，物理 XML 保持字节一致。

## 2026-11-26 RMUC 哨兵移动交战与场地战术

现有 C++ 服务器加载专用 `rmuc2027_match` XML。插件根据 UE 权威裁判状态和已激活的导航进程选择阵营航点，沿用 Point-LIO、AMCL 和 Nav2。场地控制、购买结算、堡垒开甲继续由 UE 裁判负责。视觉锁与开火许可独立，桥接层按视觉锁、有效导航平移及驻守扫视仲裁。用于测试的真值存放在独立评估与录包通路。

证据、当前战术阈值、场地规则来源与验收范围见 `docs/changes/2026-11-26-rmuc-sentry-tactics.md` 和 `docs/changes/2026-11-26-rmuc-field-control-and-ammo.md`。

## 2026-11-26 RMUC 原版 C++ 哨兵行为树

UE 裁判 → rmsim_bridge 原版 DJI 消息及既有 PB/JSON → 独立的 BehaviorTree.CPP v4 哨兵节点 → 既有 Nav2 动作、云台扫描、视觉自瞄许可和底盘控制。Point-LIO、AMCL 与 Humble Nav2 保持原运行层。Windows 行为树隔离安装在 install_behavior，原版 XML 与比赛生成副本同时保留。比赛副本绑定双方场地航点、实际 map/TF、Nav2 多点执行树的帧和仿真小陀螺设定。ROS 图中 rmsim_sentry_bt_server 避开 Nav2 behavior_server 名称。

固定上游版本、构建命令、Windows 红蓝双方实机闭环及原始日志见 docs/changes/2026-11-26-rmuc-native-sentry-behavior.md。

## 2026-11-20：可选校园远景与入口资产

`CampusGym.cpp` 支持可选 `outdoor_background`：半径 8 km 天空与外边长 4 km 的四条正交地面环带，中央孔洞匹配原校园地面 160×140 m，顶面采用真实 ExtGrass 高度。该分支独立校验尺寸、源地面孔洞、环带接缝、球面余量及 NoCollision/NoNav/NoShadow/NoDF；普通 layout 的 100 m 范围限制和比赛物理合同保持。缺少配置项时保持旧背景行为。

`SchoolEntry` 命名空间保存 CC0 Grab3D 砖墙和新远草材质；入口新增 24 个模块，原馆及校园 23 mesh / 57 实例保持。远草由原生最高草面两三角形拟合 UV，四象限先独立采样，再按源矩形外距离以最大 16 m 半宽混合线性颜色及已解码法线，世界法线最终归一化。源边缘相位和原四地块保持，天空填充配置为 512。装饰层保持 `closed_perimeter_geometry` 的既有输入，导航物理身份继续为 f416f652…126e。

本轮原生五视图、逐图视觉检查、源布置 92/92、导航身份与 60.03 s 可见观察均通过；最终图像与交互使用 DLL 27a87dda…8ec2b。实际模块、活动文件和局部材质更新身份见 `artifacts/fixes/school-entry-masonry-20261120/release-summary.json`。

## 2026-11-20 当前 RMUC 平台围墙

`CampusGym` 将原 CAD `bounds_m` 与 `perimeter.target_bounds_m` 分开处理。活动 `outside_platform_bounds_v1` 只收拢长侧 Y 边界，X/Z 源边界保持；目标 Y 同时与已加载的两侧波浪平台 visual/collision mesh world bounds 核对。玻璃、框架、门饰与四面连续碰撞墙共用目标矩形，厚度向外，短墙长度随之改变。长玻璃内面、碰撞内面、角部闭合和落地高度分别查询；source 与 target 侵入结果单列。

`perimeter_geometry.py` 输出相同几何及身份，`export_boundary_navigation_package.py` 生成规划/定位图。活动 `navigation-console-platform` 由生产 `prepare_scene` 与 installed `navigation_map_identity` 校验；两种 outside 合同均拒绝缺少 runtime_boundary 的包。旧 source-bound 及 inset 合同保持兼容。两个 scene.json 原始字节通过精确 `.gitattributes` 规则保存，复建 recipe 使用相同身份。

本轮真实证据为三视角96查询/314构件、南侧六阶段真实轮驱动接触、14项离线与60.02秒可见运行。原平台、源馆、材质/外景、车辆与悬挂保持。详细坐标、源斜侧面关系、物理AABB余量范围和未覆盖的完整导航/Ubuntu见 `docs/changes/2026-11-20-wave-platform-glass.md`。以下旧馆/全底板边界描述保留各阶段来源。

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
| ImportLabGameMode | 场景、PC输入适配、共用ApplyManualInput、相机/HUD；导航真实物理步的单调截止时间调度 |
| RMSimViewportClient / SceneDrawCadence | 导航入口选择独立60Hz场景绘制节拍，记录真实Draw/跳帧/追赶计数；导航物理按200Hz推进，手动入口保留原绘制方式 |
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
| WTFoliageCrushComponent / WTBreakableGeometry | 明确资源白名单、精确实例接触、源三角局部裁切、受限 Chaos 冲量和局部叶片下压；固定结构保护与延迟回收 |
| DestructionProbe / Test-WTDestruction | 原生正反向碰撞、持续顶压、固定结构负例、植被与几何生命周期；逐报告绑定退出码、截图、模块/源码/地图哈希 |
| rm_map_tools | 平地box地图校验与同几何PGM/YAML导出，不支持坡地导航投影 |
| tools/maps / import_mesh_map.py | 来源核验、RMUC/WT准备、UE triangle collision导入与独立bounds核对 |
| tools/unreal | Windows/Linux启动，构建、导入、报告门禁；Linux尚未实跑 |
| tools/environment | 隔离Windows Humble环境、原节点构建、局部DLL部署与通信/数值证明 |
| RosBridgeComponent | 可选loopback单客户端、严格速度指令/会话校验、按需RGB采集/标定与Chaos时钟，PostPhysics采样 |
| rmsim_bridge | 原生rclpy sidecar、真实消息/安装TF与独立评测真值；接入原导航速度/平滑与高层云台跟随 |
| VehicleMotorDrive / lk_adapter | 可选RosMotorControl：原LK电流消息、真实轴角/轴速反馈、等大反作用扭矩；失效转受电流上限约束的安全停车 |
| chassis_controllers / custom_msgs | 用户原包工作副本与固定上游完整消息包；原生MSVC构建，原PID/功率限制，源码补丁和pristine分开保留 |
| RosAimController / aim_adapter | 原视觉/扫描消息的高层直连；独立200ms瞄准/发射有效期、世界方向、限位扫描、关节反馈和实际训练弹道 |
| RosImuComponent / imu.py | 可选底盘安装六轴IMU：实际点速度差、重力/安装变换、轴向角速度、噪声、独立采样和有界批传输；ROS原始消息及静态安装TF |
| RosLidarComponent / lidar.py | 同一物理帧姿态下并行首回波查询，200k射线/秒、10Hz滚动扫描、盲区/自身遮挡；原Livox CustomMsg与PointCloud2 |
| point_lio / point_lio_runner | 原估计器、源YAML快照/单位适配、传感器安装外参；源控IMU队列、同线程1ms预算排空，新会话重建局部地图 |
| 原哨兵导航工作包 / navigation_runner | 原位姿/扫描/地形链、AMCL、Theta*/Omni PID、平滑器和代价地图；读取车辆源YAML、核对地图/协方差初值及生命周期ready |

## 状态与坐标

车辆描述使用米/弧度/ROS右手系；运行边界转换到UE厘米/左手系。视觉bounds只包含实际车体mesh，碰撞轮proxy不随视觉轮转膨胀。

AimWorldTarget保存世界目标，鼠标从相机中心射线产生目标；炮口从自己的位置跟踪。自由观察和切镜保留该目标，返回第三人称时从较高pivot重新投影，避免静态视差。

MouseX/Y使用原始计数，按本项目灵敏度和FOV比例各应用一次。输入配置在DefaultGame.ini，项目关闭默认鼠标平滑。手动失焦立即归零运动并忽略输入，重获焦点丢弃首帧鼠标跳变。显式RosControl模式由ROS拥有底盘，失焦继续执行有效外部指令；Space人工制动、R复位。

mesh场地等碰撞body真实创建后落车。默认AIM样例随后启用VehicleContactPhysics：13独立刚体、4滑动弹簧/4舵向/4轮轴约束；真实世界帧解算位移，禁用旧TerrainPose行驶门限。旧运动学仅显式诊断。瞄准目标先转车体局部坐标，保留倾斜地面的世界瞄准行为。显示mesh无碰撞、独立隐藏mesh承担triangle查询/物理接触。

ContactProbe/GyroProbe/TractionProbe以真实世界帧运行路线、控制、响应和坡向抓地检查；OnComponentHit保存接触证据。关节投影/质量调节关闭。碰撞通过prepare/ensure_vehicle_collision.py在独立资产生成，bind_vehicle_collision.py核对哈希并保存旧rig。当前15视觉部件，body128凸包保留低处凹槽，四轮采用源胎冠凸包；缺少源轮胎几何的包回退圆柱。Win/Linux入口共享准备脚本。

VehicleDrive.cpp拥有带坐标声明的速度目标、舵向前馈和轮速PI。外部ROS速度使用external_shaped_velocity模式，直接跟踪源平滑器给出的时间剖面并驱动实际舵轮；手动输入保留车辆包的世界平面速度大小/方向整形。两种来源共用真实轮地接触；ROS路径受独立命令租约约束，切回手动时重置整形内部加速度/方向速率。Control/SentrySwerveKinematics.h适配本机LK核心数学逻辑。车辆包drive-profile.json启动时读取，配置不依赖键盘来源。GameMode仅负责镜头相对WASD、Q/E、G/Z/X、Ctrl及HUD；GameMode在PostPhysics更新视图，车辆电机在PrePhysics更新。手动模式取消未使用离屏视图逐帧渲染，快捷入口防止重复实例。

## 已有证明

最新ROS流：物理车辆/独立固定相机 → RosBridgeComponent → 有界loopback TCP → rmsim_bridge → /clock、RGB8/CameraInfo、base→optical TF。TwistStamped反向进入同一个VehicleDrive；Rig在PrePhysics检查单调时钟租约。R更换epoch和连接session，清空旧指令；仅评测topic提供真值。通用ROS相机默认10Hz、1280×720/60度；导航控制台选择320×240并默认CameraHz=0，保留标定与安装TF，RGB按需开启。开启采集时使用有界异步 GPU 读回，实车标定继续跟踪。后续原LK电流与高层云台/发射接口见下方边界。

原生ROS11项、TCP19项、只读4项、stall1项、实际R复位及手动19项回归见artifacts/ros2/native-release-summary.json；旧控制45项与波浪/抓地记录仍保留在artifacts/gameplay/gyro-control。两者模块哈希不同，不将旧45项冒称为新模块全部重跑。

真实UE5.8.4编译与导入；7mesh尺寸核验；9项运动学检查；18项操控检查；实际GUI与键盘事件/位姿；原生Windows ROS2 smoke。数据在artifacts/unreal/2026-11-15及artifacts/environment。

两张平地地图各9项运动检查及占用一致性；RMUC与WT各7项地形检查，实际1.5°/5.214°支撑坡度；独立导入bounds和轮位法线核对。数据在artifacts/maps；源文件按artifacts/sources与assets/maps分开保存。

## 边界

RosImu从车辆包sensors/imu.json读取1–4个base_link安装配置，在真实Chaos帧后按物理时钟采样点速度及角速度。比力为相邻真实采样窗口的速度差减实际世界重力，再转传感器坐标；角速度按轴向矢量反射。每通道32样本有界队列，经50Hz状态批传输，ROS保留原采样时间。未采周期/缓冲丢样显式计数；不插值补足标称频率。R及显式物理位姿重置清理导数历史和待发样本。

RosLidar可选读取sensors/lidar.json，实际场景首回波查询包含自身。默认200k射线/仿真秒、10Hz、均匀未标定图样/常反射率；同物理帧共用真实时间/姿态后并行查询、按确定顺序组帧，保留跨帧运动畸变。每帧4096射线、每扫描65536点、最多2待发帧，超额显式计数。新会话/位姿复位丢弃半帧和旧队列。

有LiDAR时状态升级v2：同一有界二进制体先放可选RGB字节段，再按JSON偏移放20字节little-endian点区。CameraHz=0时camera_bytes=0，点区仍按声明的偏移解码；原命令/v1无LiDAR保持兼容。Python经原Livox生成器/解码器验证的批量CDR发布原消息，并发布同点PointCloud2、静态安装TF。合计二进制16MiB，JSON64KiB，既有250ms执行租约不受传感器模式放宽。

原Point-LIO只订阅点云/IMU/clock，未消费评测真值。runner从两个实际安装变换求外参，按session/epoch重建进程/局部地图；原节点参数读取失败退出2。Windows默认/旋转安装/真实R/RMUC/原LK联合有分项证据，见artifacts/sensors/lidar。该层提供单车局部估计；地图定位由下述AMCL承担，原输出话题尚未多车隔离，完整感知闭环仍未完成。

导航新增当前子集：Point-LIO估计参考IMU，经原loam_interface和sensor_scan_generation输出odom→base；原terrain节点的odom_frame现可配；AMCL持有map→odom，扫描和代价地图使用实际传感器数据。原fake_vel_transform将导航固定朝向frame速度旋入base，仿真符号为+1。原Theta*/Omni PID/IntensityVoxelLayer在import_lab已实际绕障到达目标，不能外推为RMUC全场导航。

RosBridge发布已加载地图的id/kind/MD5，平地占用导出保存同源MD5；navigation_runner拒绝不匹配地图。初始化用显式有偏先验及非零协方差，等待原AMCL估计候选与Nav2生命周期全部active；没有真值定位输入。原诊断库显式DLL可见性解决Windows虚表崩溃，组件算法未替换。目标取消停车已有实测；当前2000/140悬挂下RMUC波浪自主往返、主动导航途中bridge断线/重连后重新定位和旧目标不重放均有分项通过证据。监督器以有界只读查询处理状态回复超时。Windows控制台监控、桥、Point-LIO、导航监督器及其子进程、RViz统一使用经过严格校验的127.0.0.1 UDP配置；扫描与位姿适配器使用非阻塞安装TF查询，未就绪时跳过当前观测。2026-11-17入口两轮RMUC断线重连往返各14项与平地实际R恢复17项作为历史版本证据保留。多车隔离、更多出生点和全场通行图及完整自主行为继续验收。

raw Imu只提供六轴，orientation_covariance[0]=-1；安装TF只从base_link指向传感器，没有世界定位真值。偏置/白噪声是未标定模型，零协方差表示未知。当前导航控制台以SimStepSeconds=0.005运行真实200Hz物理与IMU采样，时钟验收按相机开关与当次配置分别记录，rmuc-06保留为历史分项证据。旧1ms配置约0.3倍墙钟属于历史诊断记录；固定1ms电机诊断入口单独保留。该物理mesh-rig配置不等于通用URDF传感器支持。源码/原生证据在artifacts/sensors/imu；原Point-LIO已与实际IMU/LiDAR联跑，证据在artifacts/sensors/lidar。当前2000/140悬挂下原LK/传感器/Point-LIO联合11项通过，RMUC波浪自主往返与bridge会话恢复也已分项运行；完整范围见当前navigation checkpoint。

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

原源码95c66c2的导航偏航目标整形在桥中独立实现，输入实际导航速度、估计底盘yaw与关节反馈；高优先级扫描/获准自瞄保留，导航不发射。v2传感器配置增加yaw_link，逐物理帧安装TF、射线和共装IMU跟随实际关节；v1固定安装入口保持。Loam接口根据重力初始化的原估计与动态安装TF恢复底盘，单次平移/偏航对齐，避免重复应用45°安装旋转。Point-LIO归一化与原始饱和参数配对转换到SI。 物理 IMU 从同一 5 ms 窗口计算测点速度差和旋转增量。仿真比赛在原 AMCL 的参数装配处使用基于验证结果的运动噪声，真值继续只供独立评估。

2026-11-17版本的Windows原生84项、图形13项与离线120/2跳过已验收，见artifacts/navigation/sentry-console-20261117/release-summary.json；当前源控速与实时修改的发布/GUI/兼容回归由新汇总确认。硬件云台电流环、传感器标定、新组合实际R/断线、全场/多车和原生Ubuntu继续沿长期规划推进。

## 当前源控速与实时导航（2026-11-18）

控制台默认读取 `ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml`；外部 `-NavigationSource` 可指定车辆自己的sentry_ws YAML，`-NavigationFollowProfile` 指定云台高层目标整形。原始上游源码与配置保持在独立来源目录；工作包、适配补丁和运行产物分开保存。

AIM当前源文件选择前进/后退/横移3.0/1.5/2.0m/s，原平滑器平移加速0.8m/s²、减速3.0m/s²。Omni PID、曲率/接近目标减速、平滑器、Theta*和代价地图承担导航行为；VehicleDrive在external_shaped_velocity路径执行原ROS输出，手动整形继续在manual_input_shaper路径工作。速度上限与实测速度分别记录，真实25cm到点门限保持。

Point-LIO监督器一次读取并校验源YAML，缓存原始字节SHA-256和支持的参数；状态、生成参数及会话重建共用快照。当前源控字段为滤波、测量协方差、网格分辨率、IMU单位和 `common.imu_qos_depth=128`；接收深度缺省5、有效整数范围1–4096。实际话题、安装外参和采样周期来自车辆传感器配置。源配置变化在监督器重新启动后生效。

ROS SI饱和阈值按 `satu_acc_source × 9.81 / acc_norm_source` 求取；当前simulation源为9.81和30，所以运行阈值30m/s²。reality或外部配置按其文件换算；旧29.43m/s²属于历史3g基线。IMU测量残差协方差沿用源值。

原Point-LIO映射线程使用1ms预算的 `spin_all` 重复收取已就绪回调，保留原500Hz循环和估计计算。原生接收日志保存消息数、时间戳缺口、重复/倒退和回调间隔；深度128用于承接短时批量到达。rmuc-05曾在每次spin_some只取一个就绪订阅样本的循环中出现约131Hz接收和0.66s里程计积压，触发原0.5s TF新鲜度限制；该失败证据保留。统一导航ROS传输与同线程排空共同在rmuc-06验证通过，各项修改的独立贡献尚未拆分。

当前导航实时路径为5ms真实Chaos步进与200Hz采样。`navigation_console.py` 在场景/Chaos创建前追加进程级 `MaxSubstepDeltaTime=0.005` ini覆盖，并记录对应来源；5ms最大子步仅作用于该导航入口。全局 `DefaultEngine.ini` 保持1/240s（约4.167ms）最大子步，手动和legacy继续沿用，固定1ms等诊断步长由各自入口选择。

导航Viewport保存独立60Hz绘制节拍，Mid-360实际场景查询并行化并保留200k射线/秒。物理截止时间、场景Draw与传感器计数分别上报；超预算由实际时间与计数反映。

rmuc-06对应此前带10Hz RGB、全局5ms最大物理子步配置，原生导航14项通过：单点/航点实际误差0.193394/0.211837m，早期取消制动0.095606m、漂移0。90秒观察实时倍率0.999960912、实际场景Draw59.818251Hz，两项滚动10秒窗口全部通过。Point-LIO动作包围区间35.12秒应收/实收7,024条IMU，缺样、重复和倒退均为0；二进制、源码、补丁、配置与会话身份绑定。完整报告位于 `artifacts/navigation/realtime-20261118/rmuc-06/{actions,clock,imu-receipts}/verification.json`。

### 按需ROS RGB与验收范围

导航控制台新增 `--camera-hz`，Start/Run包装新增 `-CameraHz`，默认0，允许0或1–60Hz浮点值。UE `-RosCameraHz=0` 关闭RGB CaptureScene、Flush/ReadPixels和图像字节传输；相机组件、CameraInfo标定信息与安装TF继续存在/发布。导航尺寸保持320×240，通用ROS入口默认10Hz保持。

新启动的0Hz会话报告 `camera.nominal_hz=0`、`capture_enabled=false`、`camera_bytes=0` 和零采集图像数。桥状态透传 `camera_config` 与 `published_images`；控制台状态含 `camera_hz`、`camera_capture_enabled`，运行来源另保存 `camera_width`、`camera_height`。主Viewport60Hz、200k LiDAR和200Hz IMU独立保持。

当前标准倍率回归范围采用导航RGB关闭配置；`Start-NavigationConsole.ps1 -CameraHz 10` 可开启10Hz RGB。启用RGB后的实时性独立待验证。此前带RGB的rmuc-07整体90秒倍率接近1，两个滚动10秒窗口约0.972176和1.028096超出原门限，`rmuc-07/clock/verification.json` 继续保留failed；历史rmuc-06成功和其他失败记录按各自配置身份保留。

当前按需RGB版本的原生核心、高速、兼容性和真实GUI正在统一回归，最终结果以当前进度和新版汇总为准。新车辆即插即用闭环、原生Ubuntu、全场地图、完整感知决策/裁判和权威联机继续沿长期goal推进。

## 弗兰德斯保存关卡与地表覆盖：合并前检查点（2026-11-18）

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


## 统一工程与生成补充（2026-11-18）

当前可执行工程统一在 `D:/simulator`。弗兰德斯、RMUC、训练和导航入口共享 RMSim 模块、车辆包、DefaultGame/DefaultEngine 设置和原接触控制；按 MapFile/SpawnId 选择地图。旧地图工作树保留在 `artifacts/archive/wt-vlaanderen-before-unified-20261118`，回滚快照与逐文件三方合并记录位于 `artifacts/integration/unified-20261118`。

`WTGeneratedCollisionComponent` 在车辆完成出生放置后初始化；GameMode 保持 TG_PostPhysics，并在每帧调用 Advance 为下一物理帧准备碰撞范围。原源碰撞与生成补充使用不同标签。917 个 cell 描述 18,454 个枕木/路肩源实例；10 个主形状共享 872 个三角形，完整仿射变换保存在 193 个 cell 网格中。203 个保存资产使用带源 SHA/配方 SHA 的 AssetUserData 验证。手动最小查询范围和实际 LiDAR 量程统一进入加载半径；32 m 余量、8 m 刷新阈值、滞回释放和固定出生锚点参与范围管理。

当前草地包为 10 个模型、16 层，原地类与真实道路/铁路/水/建筑足迹共同确定权重。原色、原packed normal与透光端点参与草材质。最终材质重编后真实重新烘焙 GrassData；只读重载逐组件比较保存与当前 generation hash，1,089 个组件全部 valid/fresh。装饰草不注册碰撞。

地表保留 23 层地类细节数组和 12 槽原微细节数组。34 个 HeightDecal 恢复反射通道，alpha 法线适配使用显式强度 0.2；LC1 在固定 slice/rotation/mip 内求局部 alpha 差分，先生成法线，再以源 pow4 权重混合。最终独立重载验证 307 资产和 12 个父材质编译，Ground40 原生确认草输出闭包保持。源公式、UE适配及真实画面分别记录。

共用手动 profile SHA `fd1ece7218f9465f94090e31853cc191006427608e467e058a8081aa234f6a87`，前进上限 5 m/s、悬挂 2000/140。组合模块 `37daaf0e…` 完成 C/Shift/速度 6 项、弗兰德斯与 RMUC 各 5 项接触/复位；最终 World `33113995…` 完成场景21项、草72项和材质307项。实际GUI确认坡格线/碎石斜黑纹消除、真实W移动、M开关和R复位。并行Arena/破坏工作归属独立保留。

本轮结构、来源和逐项验收入口见 `docs/changes/2026-11-18-unified-flanders.md`、碰撞与草地专项指南。完整导航 UE/ROS 门禁继续采用其独立证据，feature 检查点与 main 发布状态分别管理。


## 弗兰德斯局部破坏与材料保护（2026-11-18）

运行时在保存场景就绪后建立明确的资源策略：19 个薄木板组合可局部断裂；1,479 个旧硬候选恢复保护，4,647 个软植被实例进行局部非阻挡压倒。`prepare_wt_destruction_geometry.py` 使 11 个派生视觉 StaticMesh 保持 CPU 可读，源 FBX/RMI/材质/World 身份保持。`ProceduralMeshComponent` 复制原网格属性并承载保留几何与 Chaos 碎片。

`VehicleRig::DispatchPhysicsCollisionHit` 保存带精确实例号、接触时间和运动代数的有限事件队列，原碰撞通知继续传递。破坏组件独立读取游标并用真实接触匹配实例；高速破坏同时要求法向速度与冲量，持续破坏要求同面真实接触和持续驱动意图。力的分母采用接触事件间隔，间断、松开、反向与换面清除累计，累计值不作为碎片发射动量。

最终 build-06 模块 `b9278d1a8dab8cdcd27b3fa5b9e295c05fb042a4ea1bae7c6ab762b503af84b8` 在 Windows UE5.8.4 通过六项破坏用例和五项驾驶/复位回归。最终 92 PNG 与 2,075 个输入身份绑定于 `artifacts/maps/wt-destruction-fix-20261118/release-verification.json`。静态余部的二次破坏与 Ubuntu 原生执行保留为明确覆盖边界；详细策略、旧模块与失败记录见 `docs/changes/2026-11-18-flanders-destruction-realism.md`。

## 弗兰德斯钢轨补充与任务出生来源（2026-11-19）

生成补充覆盖扩展为 237 个资产、951 个 cell、18,488 个源实例。34 个 `world_static_000` 中的钢轨 SCN 对象按 `railroad_rails_tile_a` 精确选择，共 160,336 个原三角形；每对象一个中心局部 BVH 和 identity 旋转 RMI。源 SCN、NPZ、视觉 FBX、原 identity RMI、每面索引及材质归属共同绑定。仅钢轨新导出规范正交 FBX 节点为精确 -90°；逐元素指纹保留其他数据。既有同步空间加载、QueryAndPhysics / BlockAll、出生锚点和迟滞策略继续使用。

默认出生来自只读 mis.vromfs 任务链中的 Dom Team A 第二坦克重生组 `t1_tankSpawn02_zone01`，经 missionMarkAsRespawnPoint 的 tank/team 注册核对。map.json 继续保持严格字段，来源放在独立 spawn-source.json。原区水平坐标按 Dagor→ROS 转换，GroundMetres 垂直投影到原 HM2；派生航向与原显式区域变换分别记录。

`RailContactProbe` 仅由显式测试旗标分派，使用世界通道射线验证精确组件/实例，再读取原生接触队列证明持续阻挡和倒车脱离。最终 `d19c15bc…` 模块完成 280 条查询、8 项真实钢轨接触和5项新出生点驾驶/复位；源位置门限保持，World 与车辆参数身份保持。完整边界及证据见 docs/changes/2026-11-19-flanders-rails-spawn.md 和 artifacts/maps/wt-rails-spawn-20261119/release-summary.json。


## 手动观察、角方向瞄准与地图方向（2026-11-19）

手动相机的 Orbit 角与云台保持的单位世界方向分别存储。C 进入捕获实际 aim_camera 前向，C 持续直接用角方向求解云台；射线命中点随当前枪口更新，仅用于显示。C 释放、Shift 退出做一次镜头重投影并保持角方向，新鼠标输入恢复实时瞄准。scope 每帧按 Orbit 角瞄准；C 中切 Shift 只切换返回模式。R 在处理当前 C 状态前清空旧保持，重新聚焦首帧丢弃陈旧鼠标量和 Shift 边沿。

GetGimbalPlanarForwardRos 统一实际云台的 ROS 水平前向。ApplyManualInput 在 NudgeGimbal 后形成全向速度请求，MapOverview 的 VehicleForwardRos 使用同一结果，大小地图投影沿用原校准。ROS 底盘／云台所有权保持独立；固定弹道抬高点仅通过显式 bControlProbeActive 夹具覆盖进入。

CameraHeadingProbe 在真实 Chaos 世界 tick 中独立采样参考方向、云台方向、请求速度和实际地图帧，覆盖近目标平移、底盘转向、C/Shift 组合、失焦及复位。Windows 当前 77 项原生 / 54 PNG 和 12 项离线地图校准绑定 4b362baa… 模块，证据与边界见 docs/changes/2026-11-19-camera-heading.md。

## RMUC 源学校体育馆与地图边界（2026-11-20）

RMUC 双 ROS 席的 MID360 以整组四通道采样降载。名义发射时钟按车辆配置，ROS 桥按声明的组步长验证点数、真实发射偏移及通道顺序。 [双席与单席验收](../../docs/changes/2026-11-27-rmuc-dual-mid360-startup.md)

RMUC 比赛各车共用同一份场地柔性碰撞网格。MuJoCo 每次复制车辆后立即清理该次生成的场地副本。 [六车启动验证](../../docs/changes/2026-11-27-rmuc-3v3-startup.md)

RMUC 地图路点为前哨停转状态提供优先观察位。仿真行为树在前哨任务中根据独立识别观测确认是否切换到车辆追击，视觉类别继续使用原识别链路。 [停转选位与行为交接](../../docs/changes/2026-11-27-rmuc-outpost-visibility.md)

场地导出按蓝方 CAD 实例匹配堡垒、补给区、中央高台和飞镖站外侧的标线，并共用蓝方道路材质。派生平台和站体网格沿用同一映射。 [资产与实拍](../../docs/changes/2026-11-26-rmuc-blue-ground-stripes.md)

哨兵贴纸独立渲染为带灯条避空和四孔的工字形平面网格，并随原装甲平面移动。 [实现与验收](../../docs/changes/2026-11-25-rmuc-sentry-i-profile-sticker.md)

红蓝飞镖站独立消耗弹量。UE 飞行动画进入虚拟检测体积后由裁判确认，场点观测屏蔽压制期间的读卡效果。 [比赛飞镖实现](../../docs/changes/2026-11-25-rmuc-dart-match.md) 权威命中奖励在裁判结算，遮挡与按席经验由操作席读取。 [命中奖励](../../docs/changes/2026-11-27-rmuc-dart-rewards.md)

发射站的固定结构、导轨盖板和抽拉滑台由原场地 CAD 拆分。盖板以原 CAD 全开为零位，独立于发射架的摆位配置。笃行发射架可见总成按原 CAD 尺寸加载为共享静态网格，安装垫块与原 CAD 的灰黑端罩同属滑台，虚拟飞镖从模型本地出站点开始。准备阶段允许抽拉滑台并在开赛前收回。 [运动、资产和验证](../../docs/changes/2026-11-26-rmuc-dart-station-guide-and-tray.md)

`RMUCDartVisual` 采样世界空间曳光。`RMUCDartGameplay` 将靶头位姿同步到原 CAD 的显示网格、查询网格和虚拟检测，裁判确认后触发场景命中效果。 [动效验收](../../docs/changes/2026-11-25-rmuc-dart-presentation.md) 绿灯、外壳和滑块归入同一 CAD 总成，点光挂载到活动视觉网格。 [滑台验证](../../docs/changes/2026-11-25-rmuc-dart-carriage.md) 导轨下方支撑台面及支架属于固定基地网格。 [固定支撑](../../docs/changes/2026-11-26-rmuc-dart-fixed-rail-support.md) 展开外壳携带原 CAD 紧固件，显示用滑靴与导向座连接固定内架。 [展开支撑](../../docs/changes/2026-11-26-rmuc-base-open-supports.md) 绿灯聚光灯由原 CAD 透镜的中心与法向确定。 [照明验证](../../docs/changes/2026-11-25-rmuc-base-guide-light.md) 飞镖落地姿态读取 CAD 碰撞面，独立的连续网格光带随时间回收。 [贴面验证](../../docs/changes/2026-11-26-rmuc-dart-ribbon-landing.md)

轴心两面的 R 标志使用独立材质槽，随裁判亮靶、完成和到期状态改变发光。 [实现与验证](../../docs/changes/2026-11-25-rmuc-rune-hub-lights.md)

转盘中性底色与裁判控制的红蓝灯分别绑定到原 CAD 材质槽。固定轴心标志由导入器统一重映射为中性灰。 [转盘外观与验收](../../docs/changes/2026-11-25-rmuc-rune-rotor-colors.md)

RMUC 能量机关由红蓝双面裁判状态驱动 CAD 转子与独立发光条，游戏 HUD 的 `T` 和选手端网关复用裁判的阶段和机会校验。本机网关以 RMUC 2027 MQTT／Protobuf 发布阵营状态，通过会话与回合标识转发已验证步兵席命令。 [实现与验证](../../docs/changes/2026-11-25-rmuc-rune-client-and-lights.md)

`ArenaShell` 将 `rmsim_campus_gym_v1` 分派给 `CampusGym`。当前布局加载独立 `SchoolGymFidelity` 命名空间的 23 个 native mesh / 57 实例，保留原馆看台、窗户和钢构，26 棵场外树与教学楼提供实际视差。新增座椅为 0。源馆两个篮球架移除，其余几何完整保留；馆体中心对齐原篮球运动区，整体竖向移动使源地板承托 RMUC 原底面。场馆及场外装饰使用无碰撞实例网格，原比赛 CAD 和车辆接触几何独立保持。

`CampusGym::Transform` 根据实际保存 mesh bounds、配置中心/尺寸及 ROS→UE 转换生成实例。源还原布局用 native bounds 重建单位缩放位置，随后统一加竖向支撑偏移。当前 `visual_floor_z_m=-0.3009999990463257`；地板、场外物件及灯位总上移 0.13559999942779544 m，树根与草地相对变换保持。源码 probe 读取每块玻璃的实际世界下端，核对与支撑面的关系。

连续边界显式区分历史 `preserve_validated_arena_shell_v1` 与活动 `outside_source_bounds_v1`。当前 `outside_clearance_m=0`、`source_bounds_outer_face`，四面 0.48 m 厚碰撞墙从 RMUC 原外沿向场外展开，Z=-1.25～4.80 m；相对前一 1 mm 配置仅平移内侧边界。每片玻璃、框架和关闭门的厚度向场外放置，四角墙重叠 0.48 m。视觉基座延伸到馆地板，上端保持，物理墙高度保持。固定装饰门无通行/交互状态。当前场外视觉不参与导航和 LiDAR 查询。

体育馆在当前地图世界内配置 12 盏面积灯、曝光、Lumen GI/反射及独立天空材质。照明使用 8K pure-sky 源生成的 cubemap；可见远景使用另一个完整 8K 景观源及独立背景材质。清透玻璃无额外 refraction，原玻璃两面/透明分类按源材质解释。其他地图保留各自路径。`GymCameraPosition/Target/Fov` 与 `GymProbeOutput` 提供真实场内/接触视角和边界检查；截图工具同时记录实际配置、模块、图像 SHA。

比赛弹丸使用 `RMSimProjectile` 专用查询通道。地板和赛场结构使用已绑定的 CAD 碰撞，玻璃、门框及五金使用其可见实例的查询形状。转子、闸门与基地活动装甲的弹丸查询随各自网格运动。裁判将阻挡接触与有效装甲命中分别处理。`RMUCPhysicalAudio` 从底盘实测运动和裁判发射/碰撞事件生成空间音效。[原生验证](../../docs/changes/2026-11-24-rmuc-projectiles-and-physical-audio.md)

`FRMUCFieldRules` 分别维护前哨塔体所属方、阵地控制方和控制阶段。`StepFieldObserver` 把前哨增益点及中央高地的合格激活信号送入裁判；HUD 和遥测读取同一状态。`RMUCFieldDeviceVisuals` 用塔体归属和存活状态更新装甲颜色，能量机关的 CAD 涂装与靶灯发光分别驱动。[规则与验证](../../docs/changes/2026-11-24-rmuc-outpost-and-rune-feedback.md)

源准备使用 `restore_school_source_materials.py`、`prepare_school_gym_fidelity.py`、`prepare_school_tree_fidelity.py`、`assemble_school_fidelity.py`；原生导入复用基础 transport 与 `school_fidelity_materials.py`。`verify_school_fidelity_geometry.py` 从保存资产 LODResources[0] 读取三角位置、UV0、材质和绕序，以 2 ULP 逐面核验；显式零面积/重复面和 UE 固定 UV 代表合并由独立证据闭合。Nanite 完整 fallback 与 cluster 编码范围分列。`activate_school_fidelity.py` 要求完整几何报告通过；外景和地面支撑调整分别有输入/输出 SHA 与回滚快照。

`capture_school_gym.py` 每个视角执行 96 条边界扫掠，并检查 314 个实际墙/装饰构件在源外沿之外、四角闭合及全部玻璃底面接触；`probe_school_gpu_visible.py` 单独观察真实可见窗口和 GPU 日志。原生几何、人工图像复核和持续 GPU 运行按各次实际模块分开记载。当前来源、复现、已验结果见 `docs/changes/2026-11-19-school-gym-fidelity.md` 与 `docs/changes/2026-11-20-school-floor-contact.md`；历史 CC0 校园馆工具及旧边界 fixture 保留原记录。

## 2026-11-19：WT 任务对象与地图界面

- `prepare_wt_mission_objects.py` 展开原任务 object groups 并烘焙完整仿射变换；任务作者仅拥有 `RMSim.WTMissionObjects.Zeppelin` 演员。`wt_mission_supplements.py` 为基础导入提供先验证、保存后恢复和最终 World 哈希更新。
- `MapOverview` 统一图像、方向、位置和拾取投影；`ManualMapControl` 统一 M、Alt、缩放、拖动与本地标记。实际云台拥有箭头方向，观察相机拥有扇区。
- `MapFontRenderer` 使用原 FontFace 字节和引擎 FreeType，缓存源字号／hinting／glow／gamma 的文字纹理。UI 资源经完整 Editor 导入，等待异步资源完成后验证。
- 地图 probe 捕获实际 Canvas HUD；飞艇 probe 使用真实 Chaos 驱动验证原碰撞、持续受阻和倒车。证据：`artifacts/maps/wt-zeppelin-minimap-20261119/release-summary.json`。

## 2026-11-20：MuJoCo 原生动力学后端

当前 AIM / RMUC2027 增加原生 MuJoCo 4.0.1 后端。FMujocoVehicleDynamics 持有唯一 mjModel/mjData；VehicleMujocoPhysics 将源车辆包、现有驾驶/电机/云台接口接入该后端，并在完成子步后更新 UE 可视和 QueryOnly 组件。MuJoCo 车辆的 Chaos 模拟体数为 0。位置采用 (x,-y,z)×100 转换，轴向量采用 (-x,+y,-z)，ROS 时间读取 MuJoCo d->time。

源模型包含 249 个车辆凸壳、14 组固定场地三角面和四面场地边界；源碰撞网格保持原拓扑。车辆质量、驱动配置和 2000/140 悬挂来自现有包。完整静态场地使用独立 4.0.1+rmsim.static-world-flex.3 运行库；缓存只覆盖严格固定在 world 的三角面，碰撞/求解结果与官方版本逐态对照一致。上游修改保存于 third_party/patches/mujoco/，生成模型和运行库由可复现脚本构建。

标准入口 Play-ManualVehicle.cmd 与 Play-Navigation.cmd 直接采用 MuJoCo；独立 Play-MuJoCo*.cmd 已移除。启动先核验源资产、车辆配置、活动地图物理身份、模型和部署 DLL。常规车辆采用 1 ms 原生步长、5 ms UE 世界更新与 60 Hz 视口；原始电机模式使用 1 ms 世界步长。43 项驾驶/物理参数和云台速度读取原 UE 相同配置，总质量 25.6 kg、重力 9.8 m/s²；按原 UE 线性/角阻尼及实际材质 Average 摩擦规则映射接触。

Native 实时节拍以当前仿真 epoch 的起点和实际 solver 时间维护固定的墙钟目标时刻。普通迟到帧保留原目标，完整推进物理与传感器后继续追赶；领先于目标时刻时才等待。初始化、motion generation 改变或 solver 时间回退时重新建立锚点。50 ms 为限频告警阈值，迟到时长与真实锚点次数分别记录。

手动和导航的运动数值统一到车辆 drive-profile.json：前进/倒车/横移 5/2.5/4 m/s，加速/减速/制动 2/4/7 m/s²，jerk 6/20 m/s³，普通 yaw 上限 π/2 rad/s、角加减速 3/5 rad/s²。NavigationRunner 从原 sentry YAML 读取算法配置，并以同一车辆源生成运动限值和 SHA 身份。原 fake_vel_transform 先将 navigation_frame 指令转到 base_link，再进入原 velocity_smoother；已有 Runner 执行 Twist/TwistStamped 类型适配，公共 cmd_vel 保留仿真时间。VehicleDrive 对两种输入复用连续速度/方向整形状态。明确的零速目标经合法性检查后立即发布到共同车辆制动路径，smoother 剩余尾段在停止锁存期间归零；严格更新的合法非零目标恢复正常输出，disconnect/stale/epoch/reset 清理适配器。运行门禁直接比较实际节点参数与车辆端全部 27 项控制数值。

MID-360 v3 使用 200,000 个 5 μs 发射槽/秒、4 通道、默认 10 Hz 完整组帧和有界发送队列。每束姿态从原生 1 ms 历史节点插值，射线查询采用当前 UE 场景快照；游戏线程调度、线程池批量求交、游戏线程光学/材质处理。IMU 以整数采样索引保持 200 Hz，真实测点速度在 t±1 ms 的支撑窗口计算比力。传感器安装原点与坐标轴按官方机械定义修正，具体近似和标定状态见 docs/sensors/mid360-physical-model.md。适用范围和真实验收见 docs/guides/mujoco-integration.md、docs/changes/2026-11-20-mujoco-integration.md。


## 2026-11-20：UE5、MuJoCo 与 RViz 三窗口导航

标准 `Play-Navigation.cmd` 由 `navigation_console.py` 管理 UE5、ROS 桥、Point-LIO、AMCL/Nav2 和 RViz。默认省去独立的 MuJoCo 显示进程、显示依赖检查和专用 UDP 链路。显式开启观察窗时，专用环境先加载经过身份校验的完整模型并绑定本机接收端，再启动原生求解与导航。观察窗收到首个匹配原生状态，且导航定位就绪后打开 RViz。各子进程、状态和输入身份写入同一会话目录，关闭 RViz 或已启用的观察窗、执行 Stop-Navigation.cmd 均由控制台收尾。

`FMujocoVehicleDynamics` 的可选私有 publisher 在现有 Step 最终状态验证后，以 30 Hz 仿真时间槽向 127.0.0.1 非阻塞单向 UDP 发送完整 qpos、qvel、act、ctrl 和全部 body 位姿。快照携带模型与物理身份、会话 token、重置 generation、单调 sequence 和原生时间；未请求显示时不创建 socket 或采样。原动力学、控制和传感器调用顺序保持，显示端没有控制回传接口。

`tools/mujoco/live_viewer.py` 使用官方 MuJoCo 4.0.1 的低层 mjv/mjr 与 GLFW 显示同一份经过校验的模型：249 个源车辆凸壳、14 组固定场地三角面和四面边界完整保留。每个快照只更新位置派生和显示数据，并核对全部 16 个 body 的位置和四元数；静态 flex 显示缓冲在严格 world 固定约束下复用。显示端不进行动力学积分、碰撞或前向动力学求解；UE 内定制运行库保持唯一车辆动力学来源。定位与导航继续只消费原传感器和估计结果。

鼠标与 F／Home 仅操作 MuJoCo 观察相机。数据中断时保留最后一个有效原生画面并显示 stale 状态，旧代数、乱序、身份不符和非有限数据均拒绝。真实窗口、导航移动、复位和生命周期的具体结果以 `docs/verification/2026-11-20-three-window-navigation.json` 与对应会话记录为准。

Windows 启动器将所属 UE PID 交给 MuJoCo 观察窗口和 RViz 覆盖检测插件。前台不透明 UE 全屏窗口完全覆盖相应窗口时，MuJoCo 暂停 OpenGL 绘制，RViz 停止主 OGRE 渲染目标的自动绘制。置顶及可见窗口继续绘制，RViz 在露出时请求刷新。观察窗口接收 UDP 快照，RViz 继续处理 ROS 消息、TF 和视图更新。同画质全屏 100 FPS 待验证。开源线程模型及 GPU 样本见 `docs/research/mujoco-ue5-performance-20261120.md`。

## 2026-11-20：独立原生求解与 UE 快照

标准实时 MuJoCo 路径的 `FMujocoVehicleDynamics::FDedicated` 持有唯一运行的求解线程。每批 5 ms 推进原生 1 ms 子步，以单调墙钟累计截止时间。游戏线程发布整批执行器控制及墙钟租约，消费完整车体/关节/时间快照。复位等待旧线程加入后清理历史，再按新代数重建。原生 LK 1 kHz 模式仍使用显式同步步进。

游戏线程消费历史并用真实 5 ms 车体/云台姿态回放 LiDAR 场景查询，TF 按严格递增原生时间发布，最后恢复最新视觉姿态。对固定 RMUC 原几何作查询，使用 1 ms 原生姿态历史计算双 IMU 与射线发射姿态。视口采用独立时钟。Windows 功能证据已通过，严格分段/滚动实时和全屏 100 FPS 待验证。见 `docs/changes/2026-11-20-mujoco-dedicated-thread.md`。

## 2026-11-20：RGB 异步传感器读回

ROS RGB 使用同一 UE 场景相机。游戏线程记录采集时的物理时间和相机相对位姿，渲染线程按捕获顺序提交 GPU staging copy，并在 fence 完成后按行跨度转换为 RGB8。游戏线程分段发送头部、原有 RGB8 缓冲和雷达块。读回帧持有到发完或取消，ROS 桥用缓冲视图读取像素。三个槽位有界复用，积压时计数，断线或复位后丢弃旧连接帧。UDP 观察、状态包、IMU 和 LiDAR 的时钟独立。ROS 图像、内参与相机光学 TF 共用原采集时间；关闭 RGB 的导航模式继续发布内参与 TF。

## 2026-11-21：主视口渲染比例

Windows 底层导航启动器默认保留原生分辨率，可显式传入 `-RenderScale` 选择主视口内部采样比例。RMUC 的 `Play-Navigation.cmd` 和手动、训练快捷入口默认传入 50% 与 120 Hz 目标。`navigation_console.py` 将请求写入会话来源和状态，并通过 UE 的 `r.ScreenPercentage` 启动选项生效。输出由视口尺寸决定，仿真和 ROS 发布配置与该比例独立。原生对照见 `docs/changes/2026-11-21-ue-render-tuning.md`。

RMUC 场馆的 `presentation_assets` 可配置玻璃、天空与太阳云影。云影材质与天空共享云密度函数和游戏时间，`CampusGym.cpp` 从当前太阳组件向材质写入投影方向。入口装饰墙仍由场馆 layout 提供且关闭碰撞、导航。 [场馆立面与天气](../../docs/changes/2026-11-21-school-weather.md)

RMUC 的正常模式共享 `assets/maps/rmuc2027/map.json` 和 `arena-shell.json`。导航从规范场景生成会话文件，套用路线包的出生点，并检查规范和路线两份场景的几何与栅格匹配。Windows 的 MuJoCo、Chaos 导航均逐字节快照规范场馆；Windows 和 Linux 手动从规范路径直接加载。 [同源验证](../../docs/changes/2026-11-21-rmuc-shared-venue.md)

## 2026-11-21：底盘指令的碰撞检查

Omni PID 在导航坐标系生成平移方向，使用估计的 base_stabilized 姿态摆放底盘足迹，并在局部代价地图中检查指令扫掠、制动位移及横向惯性。局部地图使用 navigation_frame。ROS 桥将有效导航速度转换为固定里程计方向，通过估计底盘偏航和关节角驱动云台。车速为零或速度输入暂缺时维持最近有效方向，估计偏航投影最长 0.50 秒。关节反馈按最短角差报告跨 ±π 的速度。车包和更新周期决定停车预测参数。

独立 MPPI 配置可在固定导航系下提供平移候选，原全向控制器仍核验候选车体限值、停止扫掠及转弯净空。小陀螺与云台由各自控制链处理。默认入口继续使用原跟踪方向及控制配置。

## 2026-11-21：导航速度生成

RMUC 仿真使用 10 m 滚动窗口及原 5 m 地形观测半径，候选停车位移仍经车身完整扫掠和观测范围约束。Omni PID 平移阻尼采用实测沿目标方向的速度，安全前视点在局部地图分辨率内细化。曲率限速比例连续演变，弯前与终点收速从车辆参数计算。

## 2026-11-22：坡面识别与角前速度

选定 sentry_ws 配置可为 terrain_analysis 和 terrain_analysis_ext 指定最大可行坡度。节点用 odom 系中的低表面点拟合局部支持面。接受同一实测坡面连续相接的高侧与低侧平台，核对高度变化和拟合残差。接近支持面的点为地面，其他点沿用原高度和体素标记路径。Omni PID 对浅弯设置连续进入速度，静态体素代价和车身制动扫掠约束巡航。

## 2026-11-25 RMUC 装甲图案与局部灯光

装甲外观依附原车体或前哨转子。局部灯光只消费裁判和比赛装置状态。哨兵雷达使用原始材质。能量机关的灯臂分段控制局部直接光。 [变更与验收](../../docs/changes/2026-11-25-rmuc-armor-and-match-lighting.md)

## 2026-11-25 RMUC 比赛自主哨兵

比赛自动接入以实际 ROS 席位、出生点和地图启动 Point-LIO、Nav2、行为与视觉适配。行为等待导航动作回执并发出站姿与云台扫视请求。UE 裁判确认站姿和发射，小陀螺走导航系速度接口和停车旋转净空检查。视觉配对图像、内参及采集时云台角度，识别后才取得短时瞄准许可。 [运行证据](../../docs/changes/2026-11-25-rmuc-sim-autonomy-repair.md)

## 2026-11-25 RMUC 哨兵原地复活

比赛裁判的读条与金币复活共用 `RespawnCompetitor`。该入口恢复血量和相关裁判状态。车体位姿与传感器时序由持续运行的物理求解器提供。 [复活验证](../../docs/changes/2026-11-25-rmuc-sentry-in-place-revival.md)

## 2026-11-25 RMUC 自动哨兵跟踪与驻守

自动比赛通过绑定的车辆资料启用导航云台跟随和 75° 相机。实际相机与云台姿态按采集时刻关联。连续确认的可见装甲取得跟踪控制和发射请求权限，裁判结算发射间隔、热量、弹量和命中。当前高地目标回执决定驻守，已控制或已重建归属的前哨状态决定推进。 [Windows 验收](../../docs/changes/2026-11-25-rmuc-sentry-combat-tracking.md)

## 2026-11-25 RMUC 驻守扫视和俯仰稳定

驻守无锁定时由行为节点根据当前世界朝向扫视，失锁附近短时复扫。导航保持原航向跟随，并以实测世界俯仰和关节俯仰的差补偿转动底盘。MuJoCo 世界方向伺服使用底盘角速度前馈。Windows ROS 入口先加载当前 Python 环境的 Fast DDS RMW，再初始化消息接口。 [验证](../../docs/changes/2026-11-25-rmuc-search-and-pitch-stability.md)

## 2026-11-25 RMUC 装甲命中音效

比赛弹丸先结算命中类别。计分装甲命中统一播放命中点的金属板录音，其他碰撞按照材质选音。操控侧裁判命中确认声走原 HUD 通路。 [音效验证](../../docs/changes/2026-11-25-rmuc-armor-impact-audio.md)
