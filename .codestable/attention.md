# Attention

默认 RMUC 模式对应从 RViz 就绪起的 37.82 秒实测。保留 0.84 米期望净空和 0.45 米膨胀半径，按当前地图包恢复支持数据。

本文件是 CodeStable 技能启动必读的项目注意事项入口。


## 2026-11-20：MuJoCo 后端约定

- 最新驾驶数值口径：手动与导航以同一 drive-profile.json 为准，前进/倒车/横移 5/2.5/4 m/s，加速/减速/制动 2/4/7 m/s²，jerk 6/20 m/s³，普通 yaw 上限 π/2 rad/s，角加减速 3/5 rad/s²。导航使用生成的同源限值和共同车辆整形器，连续消息保留整形状态。原 fake_vel_transform 先将 navigation_frame 目标转为 base_link，再通过原 Nav2 velocity_smoother；上游 sentry YAML 保存来源，标准仿真显式传入车辆 drive_profile。实际节点参数、源 SHA 与车辆端控制值需在运行验收中一致。
- 标准 Play-ManualVehicle.cmd / Play-Navigation.cmd 已按用户最新要求直接采用 MuJoCo，Play-Training.cmd 复用标准手动入口；独立的 Play-MuJoCo.cmd / Play-MuJoCo-Navigation.cmd 已移除。正式模型为当前 RMUC2027 全场，启动执行严格源文件、模型和地图身份检查。弗兰德斯专用场景的动态实体尚需单独迁移。
- 完整场地需要 4.0.1+rmsim.static-world-flex.3；仅官方 4.0.1 DLL 无法替代该已验证运行库。检查 staged DLL 与 SDK build manifest 的 SHA256，头文件使用 c-api/include 防止 Filament 与 UE Math 路径重名。
- 模型保持 249 个源车辆凸壳、14 组原三角面，禁止用整场凸包、包围盒、平面或轮胎简化来替代正式接触。2000 N/m、140 N·s/m 悬挂及真实尺度保持；惯量/摩擦仍以 calibrated=false 标识。
- 运行时由 mj_step 单独驱动；UE 车辆代理只提供查询。普通运动只能经执行器。初始化和 R 明确复位允许设定位姿，并清理传感器历史与 ROS epoch。
- 云台初始角必须同时设置初始关节状态和伺服目标。接触 normal 从 MuJoCo geom0→geom1 的 frame 按车辆 Side 取向；禁止用整个 body 质心猜测组合凸壳的接触方向。
- 主交互保持 1 ms MuJoCo / 5 ms UE 世界 / 60 Hz 视口。Mid-360 v3 每 5 μs 发射一个槽，基于真实 1 ms native 位姿节点做平移线性插值与四元数 slerp；环境和自体遮挡网格使用当前 UE 世界快照。200 Hz IMU 使用实际测点速度的 t±1 ms 中心差分，integration_dt_s=0.002，采样时间戳仍为 t；需要后支撑可用后才交付。扫描光机轨迹、NIR 材料响应与噪声模型保留未标定声明。
- 正式启动对模型和实际 MapFile 做预检；导航运行场景旁须放置同身份 arena-shell.json。模型的源码/配置绑定发生变化时通过 Prepare-MujocoModel.ps1 重建，旧包归档。静态缓存的直接几何修改须显式失效。
- Windows 原生证据、原版/缓存等价证据与 Ubuntu 验收分别记录。弗兰德斯动态破坏和碎屑尚需建立对应原生实体映射。精简证据为 artifacts/integration/mujoco-ue-20261120/release-summary.json。

## 2026-11-20：入口外景边界

活动入口采用 `school-entry-brickwall` 的 Grab3D CC0 完整源模块，24 个装饰实例形成四段 15.3 m 砖墙，保留 19.2713 m 开口和约 4.2632 m 台阶净距。原馆及外景 23 mesh / 57 实例保持。`outdoor_background` 以半径 8 km 天空和四块无碰撞、无导航、无阴影/DF影响的地面环带围住原 160×140 m 地块；草地真实顶面 Z=-0.48440000057220454 m。新远地面材质在源边缘保留原相位，外侧最大 16 m 半宽混合四象限采样；原地块与源贴图保持，原近景纹理及局部接线按源保留。

活动 sky_intensity=512，比赛场地/玻璃/车辆与 `navigation-console-platform` 的 f416f652…126e 物理身份保持。最终五视图每幅 96/96 扫掠和背景/玻璃合同、92/92 源布置检查、60.03 s 可见窗口观察通过；最终运行 DLL 27a87dda…8ec2b，六类 GPU/资源警告 0。具体身份及前次证据在 `artifacts/fixes/school-entry-masonry-20261120/release-summary.json`。共享工程的其他任务进程和 WIP 分别保留。

## 2026-11-20 最新：波浪平台长玻璃墙

- 用户指定长墙紧挨波浪通道所在高平台。活动 `outside_platform_bounds_v1` 以实际 native `visual/collision_blue_road` 的 Y=-7.522840576171875 m、`visual/collision_red_road` 的 Y=7.522841186523437 m 为边线；原 CAD `bounds_m` 保持，独立 `target_bounds_m` 供围墙和导航共用。
- 两侧各内移约 47.87 cm，短墙长度和角部同步，玻璃与碰撞体内面均对齐。原底板低边带保留在墙外；勿再次用整体 floor Y=±8.001565933 m 覆盖平台定位。源外侧面外倾 1.5°，竖墙贴高端头外轮廓，低顶边约 23.56 mm 间隔。
- 源到 UE 导入有 1.70/1.83 μm 坐标差；应读取实际 native 资产位置。原 1 μm 核验门限保持，首次失败已存。运行时同时校验源身份、目标边界和实际平台 visual/collision bounds；source/target 的 outside 报告保持各自意义。
- 玻璃底端及馆地板接触 Z=-0.3009999990463257 m 保持；原材质、场馆、外景、23 mesh / 57 实例 / 26 树和额外 0 座位保持。新导航包 `navigation-console-platform` 已同步栅格及生产预检，两种 outside 合同都须有 runtime_boundary 声明。
- 当前证据：三视角各 96/96 扫掠、314 构件；真实车辆接触六项；离线14项；可见窗口60.02 s，六类GPU警告0。物理底盘/车轮对target平面最小AABB余量 -0.962728 cm，使用既有 -1 cm 数值门限；原CAD边界严格非负检查为46.9098 cm，二者分别报告。
- 记录见 `docs/changes/2026-11-20-wave-platform-glass.md` 和 `artifacts/fixes/school-wave-platform-walls-20261120/release-summary.json`；后文旧 source-envelope 合同为前一版历史身份。完整 Nav2 路线、Ubuntu 与实车标定保留各自验收。

## 2026-11-19 弗兰德斯地表材质验收

- Ground40、Grass82、全量307材质和Scene22已完成保存资产的原生重载；草的完整author更新World为`ba531c13f4a40c662d7bb361ba5aa669832ad9240aaddb42a7fe282f66814be0`。Object193独立重载记录单列。原地图、几何与碰撞清单保持；Scene检查按已绑定的55可视/53碰撞任务分组核对飞艇，未知附加组件仍失败。
- `terrain-reflectance`与`source-alpha-mips`来源合同按原字节SHA关联，勿自动转换JSON换行。地表_r通道为R smoothness/G reflectance/B AO/A microdetail ID；18项height polarity适配有source ID与PNG哈希白名单。GrassV4保留100个原BC4 mip的解码像素和16层分布；完整Terrain更新后必须重新author/bake草，再独立verify。
- 默认渲染使用有效的`r.AntiAliasingMethod=2`、`r.ScreenPercentage=100`、`r.ScreenPercentage.Default.Desktop.Mode=0`。最终7269/ba531静态同身份测试的草坡为TAA100 125.626FPS、原TSR83.4 129.936FPS，GPU差0.26277ms；旧4e模块的+5.95%只属历史比较。性能采样使用单主视口，ContactProbe移动截图单独验收。
- 本轮固定画面已人工观察，完整来源、失败历史、实际身份与后续局部草叶照明/源LOD事项见`docs/changes/2026-11-19-flanders-visual.md`及`artifacts/maps/wt-visual-review-20261119/`。共享工程有并行更新时，以每份execution实际DLL/World身份为准，禁止跨模块计算材质总提速。

## 项目碎片知识

<!-- cs-note managed: 项目 owner 已在本次对话明确授权记录长期约束。 -->

### 编译与构建

- 用户要求 Windows 和 Ubuntu 原生环境使用同一套 `sentry_ws` 源码测试；不采用 WSL，不把双系统运行不同算法称为同源验证。
- 2026-11-15最新优先级：先在Windows实现/实测，Ubuntu预先离线适配。当前不等待Ubuntu/SSH，不把脚本语法检查记成原生Ubuntu验收。
- 已选主路线为 UE5 + ROS 2。上游 `27UL_Sentry_ws` 的已检查版本使用 Humble；完整 Windows 依赖闭包和原生构建尚未验证。
- 车辆包 v1 标准库工具、受限 URDF 解析和两个示例已有 scoped 验证；当前推进用户优先要求的 AIM Sentry 手动样例。工具/资源检查通过不代表 UE 或 ROS 2 闭环可运行。
- 2026-11-15 已确认 UE 5.8.4 Installed Build：`E:\simulator-tools\UE_5.8`，CL 58902052。`LauncherInstalled.dat` 仍空，脚本须显式传 `-EngineRoot`，不要因此判定引擎未安装。
- UE 5.8 当前 targets 使用 `BuildSettingsVersion.V7`；初次 V5 构建冲突及局部变量 `Role` 隐藏成员的 C4458 已修复。2026-11-15 原生 Win64 Development Editor 构建退出 0，`artifacts/unreal/build.log` 为 `Result: Succeeded`，已产生 `unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll`。这不等于资产导入或场景运行通过。

### 运行与本地起服务

- 用户要求联机比赛对抗、真实光影与传感器属性、比赛逻辑、可替换地图和新车辆扩展。
- 比赛权威状态由服务器持有；机器人算法实例只能消费其允许获得的传感器/裁判信息。网络协议和联机实现仍属 roadmap。
- Windows 原生 Humble 通信/包构建、UE 模块构建、实际资产导入及引擎内运动学 probe 已通过。最新基线 `artifacts/unreal/2026-11-15/probe-07-final-controls`：9/9运动检查；`controls-04-aim-fix`：18/18控制检查、10张PNG解码，包装脚本退出0；真实 GUI 已显示模型。没有真实哨兵算法闭环或原生双平台验收。
- 用户优先使用 `E:\WarThunderModding\war_thunder_sentry\aim_sentry.blend` 做手动样例；2026-11-15 最新要求是 War Thunder 式移动和视角，Windows手动样例已分项验收，用户准星/灵敏度反馈已回归修复。源目录只读，导出包位于 `assets/vehicles/aim_sentry`；自动车辆仍在后续范围。

### 测试

- 记录实际命令、退出状态、环境与证据路径。源码审查、格式校验、构建通过、运行通过和实车校准分别记录。
- 不能用仿真真值定位替代 Point-LIO 后宣称同一定位算法已经验收；真值可作为独立评测数据。
- probe 通过证明引擎内调用/碰撞查询/变换与截图链路，不证明完整键盘操作验收或真实物理标定；早期CUA输入被guard打断；后续真实窗口已记录W/S/A/D/C/Shift/滚轮与位姿变化，共用控制路径有独立回归，不夸大为所有OS输入压力测试。

### 命令与脚本陷阱

- 上游最新导航的 `cmd_vel` 输出为 `TwistStamped`，原深北莫 Gazebo 接口为 `Twist`；接入前核对类型和坐标系。
- 上游 Point-LIO 依赖 Livox 消息包，而该驱动构建绑定 Linux `.so`；跳过整个驱动包会破坏消息依赖。证据见 `docs/research/source-audit.md`。
- 本机 UE 构建需要进入工作区外的引擎目录；本轮 sandbox 直接 UBT 调用访问 `Engine/Source` 被拒，已通过批准的 sandbox 外构建继续。不要把此访问限制误记为项目编译错误，也不要全局放宽文件权限。
- 本机原生 Humble 是 RoboStack 的 Python 3.14 构建；不能混用官方 Python 3.10 的 rclpy 扩展/DLL。Windows colcon 的 ament_python 源目录和 build 目录须在同一盘符。
- Ubuntu 同源入口 `tools/unreal/Start-ManualVehicle.sh` 已提供；目前只有 Windows 上 Git Bash 语法/预检证据，不能算原生 Ubuntu 构建或运行通过。
- 车辆 bounds 不得包含相机 frustum；视觉轮子自转的 AABB 不得驱动碰撞地面判断，现使用独立轮碰撞 proxy。两项均由真实 probe 暴露并修复。
- PowerShell 反序列化得到的 UTC DateTime 不要再次 Parse 成无时区时间，否则曾减去 8 小时并把新 probe 误判为 stale；probe-04/05 已验证包装脚本修复。
- PNG 捕获须保持正确的 sRGB RenderTarget 格式；本轮已修复亮度并调整 studio 灯光，属于视觉输出修正，不是传感器或材料物理标定。

### 路径与目录约定

- 项目工作区：`D:\simulator`。`.codestable/architecture/` 只记录现状；计划放在 `.codestable/roadmap/`。
- 车辆包、地图包的静态资产不执行任意插件或外部脚本；资产路径由导入器校验。

### 环境变量与凭证

- 未使用的 AndroidFileServer 插件已禁用，其自动生成的本地 token 已移除；不在代码、文档或证据摘要中记录 token 值。

### 其他

- 授权来源：2026-11-13 用户要求持续自主实现并长期记录；2026-11-15 用户确认安装完成并要求继续。可在既定范围内继续可逆实现及验证，不以逐阶段重复确认阻塞。
- 上述授权不等于用户逐篇审阅了自动生成的方案。文档中的 `last_reviewed` 表示维护者检查日期；未单独审阅的文件注明 `user_review: not_requested_under_standing_authorization`。
- 任何功能完成状态必须有实际证据；未安装工具、缺少依赖或尚未运行时保持未验收状态。
- 旧 Epic MSI 1925/1603 安装阻碍属于 2026-11-13 历史状态；2026-11-15 已发现实际引擎，当前不再等待安装或重复请求登录。引擎文件存在仍不等于项目编译、导入或运行通过。
- 用户已将原goal恢复active；继续原目标，不新建替代目标，不把手动首样例通过当整个目标完成。

- 当前手动相机从炮口/第三人称pivot分别投向同一世界目标，切镜不得直接复用旧镜头角度。鼠标读raw counts，敏感度/FOV只各应用一次；参数在DefaultGame.ini。
- 用户指定RMUC2027作为第一张地图，现默认assets/maps/rmuc2027/map.json；输入来自DJI V1.0.0与hxyulin同源转换包，来源/哈希在包内SOURCE.md。官方CAD的Z-up声明必须保留，不能再按普通glTF做Y-up旋转。
- 地图分rm_flat_map和rm_mesh_map。前者支持box占用导出；后者使用独立triangle collision和轮位地形跟随，尚无语义比赛区域/坡地导航导出。不能把两者混称导航闭环。
- WT第二地图只是官方CDK location_sample的512m高度场裁剪；真实足球场/训练场bin仍未转换。原游戏和CDK目录只读，权利/来源单独记录。
- TerrainPose在physics bodies就绪后才落车；缺支撑/过大台阶/碰撞即拒绝。倾斜底盘瞄准须变换到车体局部系；转向扫掠覆盖中间姿态。当前5cm台阶、35°坡度和2cm间隙是运动学预览参数，不是轮胎标定。
- 地图最终证据：rmuc2027/terrain-final-02、wt_cdk_crop/terrain-final-03，各7项与4PNG；flat-regression-after-terrain为18/18控制。最终构建已重新打开，实际GUI观察到RMUC2027 HUD、车辆和场地，窗口留给用户试驾。
- 用户随后明确要求直接用东欧/弗兰德斯等现成战雷地图，并授权解包查资源/源码；当前优先wt-existing-map，不能以CDK裁剪代替。两个DBLD原图已定位，优先转换较小的弗兰德斯。原游戏目录只读。
- 原图解码证据在artifacts/maps/wt_existing。地图HM2精细地形与外部lmap背景不是同一个数据块；目前已解出前者和RIGz存储布局，SCN/外部landmesh/程序生成内容需单独核对。
- 解包器源码固定于third_party/dagor_asset_explorer提交1f0d1e04，当前进行了Python3.14/headless/新版计数/原始顶点声明等适配。只加载本机UE的Epic Oodle库，不运行下载的daKernel/dae_intrinsics DLL。工具补丁须持续保存。
- 原图模型不能按“通道数量+步长”猜布局；保留所有UV与材质索引，旧树木可在同一buffer混合叶片/树干布局。每个导出几何须比较资源自带bounds，有限值本身不足以证明正确。
- 最新优先级：用户要求地图并行时先完成仿真功能。Play-Training.cmd已实际运行，14项训练检查/18项旧操控回归通过；单车静态靶训练已交付，完整裁判、ROS接入和联机仍未实现。不得因地图漫长而停下功能工作。
- TrainingRange拥有训练时间、弹热/弹量、目标HP、扫掠弹丸与epoch。发射按下一枪/热量恢复事件切分仿真子步，不能按每渲染帧最多一枪。失焦仍推进时间，但首帧重新聚焦不触发开火。训练橙框是弹道预测；非训练仍为射线。
- 弗兰德斯assets/maps/wt_vlaanderen已准备726视觉/639碰撞FBX；原存储45675物件全部保留，含9850剪切矩阵烘焙。preparation-verification.json通过，但完整UE场景尚未导入验收。材质917输出、原碰撞616资源均有真实来源；专用着色及程序生成植被局限见SOURCE/材质报告。
- UE MapImportLibrary已编译，原生tiny场景API检查确实保存测试umap。完整导入器tools/unreal/import_wt_scene.py可继续，但运行时savedlevel接入仍待做，不能把资产准备/接口检查说成完整原图可玩。
- 用户再次“请继续”授权工作；goal工具当前仍报告paused且无resume能力，不新建替代目标，也不把界面状态阻塞已授权工作。
- 用户随后已将goal恢复active。当前必须使用真实尺度AIM：约0.503850×0.501985×0.419192m，轮半径0.0425m。源WT linear_scale=2必须在export_aim_sentry.py去除，网格/关节/炮口/相机一起规范化，不能再以展示倍率运行机器人。
- 2026-11-15用户报告“RMUC几乎不能移动”。默认出生原生9项行驶通过；中央12°坡道整车box有确证假阻挡，已改为7部件稳定box查询并实际行驶1.6m。更陡坡折/低矮通道未全路线验收。证据artifacts/assets/scale-fix-20261115；原数据只读，旧导出在before。
- 后续用户明确波浪路仍不能走，不能把中央坡道通过作为问题全解决。波浪69.9mm起伏/42.29°局部面触发旧35°门限；轮径支撑和真实前悬接触也需要处理。现在推进contact-vehicle：真实Chaos车体/四升降模块/四轮/约束，而不是继续抬车或关闭碰撞。
- 资产已拆11部件：fixedbody只保留body+linear_guides，4carrier/carriage/caps模块和4wheelchildren。保持原尺寸/材质/1260698三角形，中立轮心误差0。悬挂65.333mm几何参考不等于实测弹簧/行程。此时C++接触动力学尚在集成，尚未验收波浪路线，不要启动旧7部件映射覆盖新包。
- ROS sidecar已在ros2_ws/src/rmsim_bridge落地，12项离线协议/socket测试通过，但UE服务端和原生ROS收发未完成。用户当前优先级是波浪通行修复；其他功能保留，不替代此问题。
- 波浪修复最终更新：wave-native-09与10-slow中红蓝各3.4m完整往返通过（1/0.35m/s），integration-02共8项/10PNG通过。此前01–08失败保留作诊断，不能继续读成最新阻塞。根因包含电机符号、body16凸包凹槽误填35.7mm，以及关节MassConditioning与真实接触质量不一致；现body128凸包、投影/质量调节均关，弹簧预载/刚度显式可配。
- 默认Play-ManualVehicle/Play-Training与Windows/Linux全启动入口现为ContactPhysics。11视觉部件、9独立刚体；几何尺度不变，不强制车体位姿穿障碍。旧运动学须显式KinematicPreview。ensure_vehicle_collision.py跨平台检查/重建缺失碰撞，派生资产/绑定含哈希且保存旧rig。
- 这是未标定的接触样例：固定朝前轮组差速转向，源舵轮独立舵向和云台动态碰撞尚未实现；不称为全场所有路线或实车全部物理能力通过。自定义出生点的训练靶可能越界；wave入口使用纯驾驶，训练默认出生点单独验证。
- 最新活动工作变更：用户在试驾后要求平滑加速、更快转向、松键主动制动、小陀螺转速映射与崎岖路面方向正确。已确认普通/小陀螺均为镜头相对WASD平移，G切自转，Z/X调速，Q/E普通模式旋转，鼠标瞄准俯仰。正在推进swerve-gyro-control；不要把旧11部件接触验收外推到新15部件全部通过。
- 新15部件/13刚体已导出、核验和导入；4转向carrier从滑动模块分离，中立尺寸/1260698三角形/源哈希不变。VehicleDrive.cpp管理SI速度整形、四舵轮和舵向速度前馈；drive-profile.json在车辆包内。swerve-integration-01有10项/12PNG原生成功；gyro flat-01前进/松键/横移通过但小陀螺侧漂38cm失败，flat-02-feedforward正在复测。粗糙地形小陀螺和坡向抓地还没验收。
- 用户提供了本机控制器E:/27UL_Sentry_chassis_controllers-main/27UL_Sentry_chassis_controllers-main（嵌套一层）。只读，12文件哈希见artifacts/sources/sentry_chassis_local。Control/SentrySwerveKinematics.h已适配其LK向量/32768编码器最短路径，修复int16跨界/负模；独立MSVC检查通过。源chassis_radius默认0.40m与原尺度轮心约0.194m不同，不照搬。完整ROS/custom_msgs/PID电流模型尚未接入。
- 试驾曾驶出地图坠落；已增加R重置所有物理部件及地图最低点以下5m自动恢复，integration-03-recovery旧11部件版本10项通过，新15部件integration-01也通过。原生路线probe禁用自动恢复，不能借重生掩盖路线失败。
- 2026-11-16当前控制基线：release-controls-01为19项/12PNG；release-waves-precision在Ctrl+W/S、0.35m/s与4rad/s下红蓝3.4m往返各5项通过；release-traction6项、release-integration10项。合计45项/40PNG、同一模块与配置哈希；release-summary.json是当前机器证据入口。
- 最新手感修正已量化：行进90度改向由966.7ms降到256.7ms；静止四轮到80度100.8ms。速度大小/方向分开限幅（0.8m/s²起步、3m/s²松键、6m/s²Space、4m/s²改向），G/Z/X、Ctrl慢行、Q/E旋转均已实现。默认关闭车体速度外环，轮速PI保持坡上零速。45项只证明当前演示参数与覆盖路线，不声称实车/OS全链路延迟校准。
- 手动窗口关闭两路未用离屏逐帧渲染、HUD显示FPS。单实例实测约7ms帧间隔；曾因试驾+验证并行出现183–300ms长帧。Play入口经Start-PreparedVehicle.ps1拒绝重复实例/编译期间启动；开发多实例需显式AllowMultiple。不要再用多个同项目游戏实例互相争用GPU做延迟结论。
- drive-profile.json为车辆包内启动时读取的配置；ue_mesh_rig.json中的内联值/哈希是导入快照，运行值以实际profile为准。adapters/sentry_lk.yaml仅为原LK节点的离线预备参数，不代表完整ROS电机消息桥已工作。
- 最终GUI已观察：RMUC最新单实例215–229FPS，真实G/X/Z/R键与HUD转速联动，R后出生点静止/gyro off/训练重置；gui-verification.json留证。不要把这些GUI键验证说成完整OS端到端延迟测量。源控制与车辆参数仍待实车标定。
- 2026-11-16后续ROS更新：UE RosBridgeComponent与原生rmsim_bridge已真实互通。RosBridge只观察、RosControl交出底盘；不开旗标无额外采集。物理Chaos时钟、1280×720 RGB8/CameraInfo/光学TF、TwistStamped驱动与250ms单调租约已验收。证据artifacts/ros2/native-release-summary.json；完整sentry_ws/原LK电流环、ROS云台/开火、LiDAR/IMU和Ubuntu仍未完成。
- ROS坐标为aim_sentry/base_link，m/s、rad/s、x前y左；禁止使用墙钟给TwistStamped打戳。R引起epoch/session和仿真时间重置；真值只在evaluation topic，不发布定位TF。仅loopback单连接，不能当联机比赛服务器。
- 新ROS模块下manual-regression/default仍为19项/12PNG，行进改向251.1ms、静止舵向101.7ms；旧控制45项为此前模块，不能称为最新模块全部重跑。相机array('B')优化只证明赋值局部耗时改善；同步GPU读回仍可能影响帧率。
- 原生ROS包修改后须colcon重新安装到ros2_ws/install_bridge；源码存在不代表ros2 run能找到新入口。pixi已补tf2_msgs/ros2run，实际CLI drive_demo已跑。Ubuntu脚本仅语法检查，依赖须另行离线准备。
- 后续原LK工作正在收尾：ros2_ws/src/chassis_controllers为用户12文件的工作副本，pristine在third_party/sentry_chassis_original；完整custom_msgs固定EcatV2_Master 7c1bd8a。原节点已通过原生电流/反馈运行，采用RosMotorControl固定1ms物理步长与feedback_driven PID，约0.3倍实时；默认手动仍原路径。native-08有21项含真实R，新增同一电机力矩上限后native-10复测中，尚待默认模式回归，不能先写全功能完成。
- Windows原节点从Codex启动继承MSIX身份，普通PATH不参与其静态DLL搜索；Build-SentryControllers.cmd已部署非系统DLL闭包到EXE旁，并带哈希清单。Python使用限定包目录的os.add_dll_directory。不得借此修改系统安全设置或全局DLL目录。原库构建成功不等于原节点运行成功，native-01至03保留诊断证据。
- 原LK最终更新：native-10-release22项含真实R/CLI/故障通过，安全停车轮矩限制2.255859375Nm；同最终模块普通ROS11项、TCP19项、手动19项/12PNG通过。统一证据artifacts/control/lk-motor-loop/release-summary.json。原LK固定1ms模拟约0.28–0.33倍速，不是墙钟1kHz；未做该原节点的波浪路线或实车电气标定。默认手动RMUC已重新打开并R复位，212–240FPS，静止舵向96.5ms/行进改向256.7ms为引擎内时序。
- 最新ros-aim-fire收尾中：原sp_msgs与pb_rm_interfaces完整包已构建；RosAimControl独立接管云台/训练发射，许可/过期/回合与底盘分开。原Hub的scan pitch为上正，AutoAim pitch为下正，适配已按固定源码记录。此高层直连不是原硬件云台/发射电机环，且没有运行视觉识别。21普通/20 LK联合/4只观察有证据；最终回归及恢复手动窗口尚待本轮收尾。
- ros-aim-fire最终更新：native-06普通21、native-04原LK联合20、native-05只观察4全部通过；射击15/10PNG、普通ROS11、TCP19、手动19/12PNG回归通过。证据artifacts/control/ros-aim-fire/release-summary.json。关节速度按真实角度差采样，发射事件使用帧前后炮口插值，R仅重置一次。最终默认RMUC手动窗口已恢复/复位，235–245FPS；云台动态物理/原硬件电机环、完整Hub/视觉识别和Ubuntu仍未完成。
- 物理IMU已限定验收：RosImu读取车辆包sensors/imu.json，真实点速度差减世界重力、轴向角速度转换、最多4通道。世界重力当前9.8m/s²，静止传感器X/Y不必为0，须按实际车体/安装姿态投影；坡面角不等于悬挂后车体角。raw orientation_covariance[0]=-1，无AHRS/真值定位TF。固定步长可用SimStepSeconds独立配置；未采周期和缓冲丢样显式记录。最终12项/自由落体3/坡面1/帧率不足11与非法配置拒绝通过；普通ROS11/瞄准20/手动19回归通过，见artifacts/sensors/imu/release-summary.json。Point-LIO仅有SI参数预备件，不是算法闭环。
- 最新LiDAR/Point-LIO更新：原源码3099b9dd、Livox a8230b02已原生运行；真实扫描/原消息/自动外参、复位13、旋转安装11、RMUC10、原LK联合11及压力/遮挡/既有功能回归通过，见artifacts/sensors/lidar/release-summary.json。理想均匀射线与常反射率未标定，不是Mid-360光学复刻；固定1ms约0.3倍墙钟，默认手动不启用传感器。
- RosLidar使用v2多段二进制，原v1无LiDAR/命令不变；sidecar重装须先source install_lio，入口Build-RosBridge.ps1。原Livox接口与Linux硬件SDK分离；不能把消息构建称为硬件驱动已移植。
- 原Point-LIO会丢弃倒退时间而不清地图，runner按session/epoch重启原节点。外参必须是float数组；原参数读取异常现退出2，不许继续半初始化估计。原CDR padding字节无语义，测试比较原解码后的全部字段。原输出仍单车全局话题，多车/完整导航/Ubuntu未完成。
- current.md现只保留当前状态；旧详细进度在docs/progress/archive/2026-11-16-before-lidar-pointlio.md。本attention较早的“尚未实现”条目是当时记录，最新具体证据及current优先。
- LiDAR收尾默认手动RMUC已恢复235–237FPS并R复位，gui-restored.json留证。一次沙箱Get-Process未看到已启动UE，但沙箱外按项目命令行匹配的CIM证明仍活着，随后CUA窗口出现；不可凭一次清单空结果就重复启动。
- native-navigation当前有实际平地绕障/AMCL/原插件到达、错图拒绝、公开目标脚本和取消停车证据；尚未完成RMUC导航/失联/R恢复，不能标feature/长期goal完成。原诊断库Updater缺DLL虚表可见性导致Windows访问异常，GenerateExportHeader补声明后通过；不能删诊断或误套旧mutex兼容宏。原terrain硬编码odom已参数化，原AMCL初值须非零协方差。
- 导航环境净新增107包、替换4个图像/压缩库，前后锁和回归已保留。原LK/Point-LIO联合11、瞄准20、手动19/12PNG通过，见artifacts/navigation/checkpoint-summary.json。源码原副本7仓库、工作包和补丁分存。
- 导航R测试被PickerHost的diagnostics_probe.exe防火墙弹窗挡住；工具未给可操作窗口ID，点击被目标窗口保护拦截，已请求用户点“取消”，不要重复请求或自行点允许。没有放行网络权限；夹具现明确ROS_LOCALHOST_ONLY=1/DOMAIN176。native-08等待R超时，已有目标/取消结果仅是分项成功。需要用户取消后重新补R用例，并在CUA观察到交接结果后调用completion_summary。
- 导航本轮末尾已重新启动默认手动预览PID13632，仅启动确认；GUI仍待系统弹窗取消后观察，不把旧235–237FPS记录当本轮实测。下一轮先核对实际进程，避免重复实例。

- 用户再次反馈滞后：2026-11-16本轮复现2m/s改向714.6ms/4rad自转732.8ms，方向参数10m/s²与100m/s³后358.5/361.4ms；0.6m/s149.9ms。51项/32PNG回归，证据artifacts/control/latency-20261116/summary.json。新高自转切慢行组合仍失败（0.35目标/0.428实际），独立gyro-precision-ripple；不要把spin-after整组写成通过。无效刚度/外环/1ms子步已恢复，仅保留两项方向参数修改。

- 本轮默认手动窗口PID35612已恢复，实际看到RMUC HUD235FPS/静止/gyro off；旧diagnostics_probe.exe安全弹窗仍遮挡，PickerHost仍无窗口ID，先前取消交接未结束。gui-status.json只记录显示观察，不是键盘验收；不要绕过窗口保护或重复启动实例。

- 2026-11-16最新gyro-precision-ripple已完成Windows分项验证：原轮胎弧形源凸包替代错误全宽圆柱，collision_vertices_m与几何哈希进入导出/导入/缓存；停车不再逐帧WakeAllRigidBodies。profile与本轮before逐字一致，所有试验调参/位置保持已撤回。92项/48PNG含ROS11/原LK21通过，见artifacts/control/gyro-ripple-20261116/release-summary.json。不要继续把旧spin-after失败当当前阻塞，但保留为历史复现。
- 当前TractionProbe是10项，包含更严格的静止准备、实际下坡松键、外力唤醒/恢复；原位移/速度门限未放宽。Chaos GetConstraintForce在UE5.8返回末子步冲量，新字段注明Nms；旧spring/axle字段名是历史兼容，不得直接作力/力矩标定。
- 当前预览PID29224已实际显示RMUC243FPS/静止/gyro off；旧diagnostics_probe安全弹窗仍覆盖且PickerHost无可操作ID。用户取消交接未结束，不重复请求或绕过窗口保护，CUA completion_summary仍应等交接实际结束。上一批Point-LIO/Nav2是旧几何证据，后续应在新轮胎下做联合回归。长期goal仍active。

## 2026-11-17 当前接续检查点

- 本聊天已再次明确完整长期goal，持久入口`.codestable/goal.md`和`AGENTS.md`。本地Git基线02e68c9，原始标签baseline/2026-11-17-pre-suspension；GitHub建仓受到平台安全检查阻止，本轮没有可调用GitHub写入工具，上传保持待完成，不记成已推送。
- AIM当前默认悬挂是每模块1800N/m、130N·s/m（旧1600/90）。仅修改车辆包两项；质量、预载50N、行程65mm、源轮胎、摩擦、控制增益、自然休眠不变。旧ue_mesh_rig内联值是导入快照，读取实际drive-profile.json，勿因旧内联值回退已验证参数。
- 当前模块92项/48PNG通过，覆盖坡面10/操控19/红蓝波浪各5/训练复位10/高速5/自转6/ROS11/原LK21；另有两次悬挂专项各4项/6PNG。100N加载卸载与4N·s冲量真实测量，回弹峰值分别降低58.95%/77.46%。汇总artifacts/control/suspension-20261117/release-summary.json；离线40通过、2权限跳过。
- 原配置与选定配置均重复两次；所有被拒绝的候选、旧严格稳定带失败和波浪失败保留，门限未改。2600/170不能恢复为默认，它曾在波浪返程真实接触受阻。更硬参数需重新测量与路线回归。
- 顺序回归在ROS11通过后曾被其自有UE服务退出延迟中断；Test-RosBridge.ps1现等待该进程真正退出并Dispose，不操作其他进程。随后以相同模块/参数哈希补齐LK21并确认无残留，汇总明确记录中断/续测，不描述成不间断单次运行。
- 新悬挂尚未重跑Point-LIO/Nav2联合、原生Ubuntu或实车标定，用户键鼠试驾手感待反馈。导航最新旧悬挂证据rmuc-05-new-tyres真实终点误差0.254827m，门限0.25m，仍失败；接续此点与恢复流程。新轮胎旧悬挂LK/LIO11已通过，不再当成未开始。

## 2026-11-17 第二轮当前基线

- 用户明确指定公开GitHub仓库SHENJLYL/RMsimUE，origin已设置、main 8d85457及两个首轮标签已实际推送，GitHub连接独立核对。前文等待私有建仓的条目是历史状态；以docs/progress/current.md最新提交记录为准。
- 当前AIM每模块2000N/m、140N·s/m，较1800/130继续提高；只有这两个运行参数变化。当前UE模块9b489d1e、profile d1db3392已完成92项/48PNG回归，两次悬挂专项各4项/6PNG；完整哈希在artifacts/control/suspension-round2-20261117/release-summary.json。自然睡眠、预载、行程、质量、轮胎、摩擦和电机/舵向控制保持原值。
- 降低睡眠阈值没消除2200/170的卸载高度偏差，试验代码和参数已撤回。最终VehicleContactPhysics源码与原基线一致，SuspensionProbe只新增chassis_awake诊断。不要把失败假设写成根因已修复。
- 新悬挂下原LK+真实传感器+原Point-LIO的RMUC联合11项通过；rmuc-07自主波浪往返9项，真实误差0.167615/0.117199m。rmuc-09在导航途中断开自有bridge，重连重建且无旧目标重放，随后往返14项通过；停车0.085634m，去回0.222153/0.114775m。门限仍0.25m，未输入定位真值。
- 新ReadOnlyServicePoll只用于GetState/is_active查询：2秒截止后移除旧pending并重试，原生rmuc-09实际记录3次查询超时恢复。不可用于生命周期转换或导航目标重发。sidecar修改后已真实colcon安装，原脚本在PS重定向stderr时误将colcon warning作终止错误，当前保留原日志并用原生子进程capture获得实际exit0。
- Windows metadata.json可能含UTF-8 BOM，navigation_config用utf-8-sig兼容；身份字段与错误JSON仍严格校验。3项配置测试与rmuc-11错图2项通过。实际R按键恢复、多车/全场/Ubuntu仍独立待验收。rmuc-12是最终配置下恢复往返复测，结果须查看实际verification，不预填通过。

- 最终rmuc-12恢复复测failed：重连后Point-LIO已初始化、AMCL已active并收到地图，但pointcloud_to_laserscan的TF过滤队列满，initial_pose_sent=false。当前须同时记录rmuc-09首轮14项通过与rmuc-12复测失败；自主往返已验收，重复恢复继续in-progress。诊断入口为2026-11-17-reconnect-tf-chain issue。不要扩大一次成功为稳定恢复，也不要忽略已完成的独立RMUC往返。
- 最终导航检查点 `artifacts/navigation/suspension-round2-20261117/checkpoint-summary.json` 已核对同一UE模块和车辆profile、各原始报告哈希。pixi激活实际ROS_DOMAIN_ID=71、LOCALHOST_ONLY=1，外层176会被覆盖；诊断须确认实际子进程域。没有改全局网络权限。

- 2026-11-17导航恢复最新：当前2000/140悬挂与UE模块不变；正式入口RMUC重连往返两轮各14项、平地实际OS R复位17项通过，统一证据artifacts/navigation/reconnect-tf-20261117/release-summary.json。旧rmuc-12与本轮四次默认传输失败均保留，不能继续作为最新未解决结论。
- Windows navigation入口在创建ROS context前验证包内127.0.0.1 UDP profile，只影响导航监督器及其子进程。ROS_LOCALHOST_ONLY=0仅出现在该经过校验的进程环境，XML白名单保持本机边界；不要误改为全局开放网络或删除校验。其他ROS模式保持原传输。Ubuntu实际运行、多车与全场路线另行验收。
- loam_interface/sensor_scan_generation TF查询现为零等待：缺安装变换丢弃当前观测、后续真实样本重试，避免仿真时钟停顿时回调挂起。传输修复与该独立阻塞修复分别留有对照。原生接口20项通过，离线92项中90通过/2权限跳过。
- 实际导航夹具在pixi激活后由probe_ros_topics设为ROS_DOMAIN_ID173，启动夹具180、重启夹具181；基础pixi默认71。诊断须读取实际环境。
- R输入仅针对确认PID/项目/证据路径且在前台的自有RMSim窗口。首次焦点失败未发送按键，正常AppActivate后重试发送一次R，ROS独立确认epoch/clock重置和导航重建；不操作其他程序的覆盖窗口。截图只留本地。
- 下一接续优先：RMUC更多出生点/实际R与路线覆盖、公开启动包装端到端验证，再推进多车隔离和完整自主决策。当前45项导航检查属于限定场景Windows验收；完整长期goal保持active。

## 2026-11-17 可视化导航当前基线

- Play-Navigation.cmd/Stop-Navigation.cmd已提供UE+RViz启动和自有进程清理；真实GUI单点4/航点6/取消3共13项通过，完整原action联合14与兼容回归70共84项。离线120通过、2权限跳过。当前证据sentry-console-20261117/release-summary.json。
- radar_mid360随yaw_link，光学中心零位base约[.198122,.073760,.361194]m，前倾45°；共装IMU按实际关节运动采样。源blend哈希不变，悬挂2000/140保持。专有扫描、反射和实车标定继续补充。
- Point-LIO的satu_acc在归一化前与原始加速度比较；ROS SI必须同步设置acc_norm=9.81和原3g阈值29.43。pointlio_units.py及6项单位测试固定契约，旧失败保留。
- Qt的TogglePattern曾只改勾选而未激活Nav2 Goal；测试采用QAction InvokePattern和实际OS地图鼠标，保持前台/PID/像素归属校验。读取实时相机投影，默认65px/m近车视图；GUI短路线与完整波浪action分别记录，不放宽25cm门限。
- 导航偏航整形参考原95c66c2，最大目标转速1rad/s、加速度2rad/s²、输入200ms过期。扫描/获准自瞄优先，导航不开火。完整硬件云台电流环/Hub/视觉继续独立跟踪。
- 新Mid360组合的实际R/断线恢复、Nav Through Poses按钮专项、更多出生点/全场、多车和原生Ubuntu保持后续验收。关闭RViz停止该会话；不得并行多个同项目UE做性能结论。大资源/截图/稠密轨迹保留本地。


## 2026-11-18 弗兰德斯地图当前检查点

- 用户明确要求继续 RMsimUE5 战雷原图接入；使用独立工作树 `D:/simulator-wt-vlaanderen` 与 `feat/wt-vlaanderen-20261117`，原 `D:/simulator` 导航工作树及其进程独立保留。feature 发布后，完整导航路线和稳定 1× 门槛仍决定 main 合并。
- 原图 source SHA `427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f`；历史 import-06 World SHA `720931dad262197929863d553c2388c3de18131277498749ef31faa201f12253`，manifest SHA `ac0afa8a40d90feee51bc53d02b41aa1b33c10b9f3d2a574b0e7d7be1f18c7d9`。完整 import-06 原生 exit 0；独立 native-scene-03 21 项、native-materials-06 88 个真实资产通过，日志 Error 0。
- 原生 1,365 网格/13,478,779 三角的差值由源 world_decal 34 个共点面逐材质证明；不能按宽泛容差接受未知丢面。保留 35,922 视觉 placements、20,033 碰撞 placements 和 1,089 Landscape 碰撞分量。完整坐标报告只留本地，以 summary SHA 绑定。
- 地表贴花恢复源 short4n.position.w 到 UV1.x，全精度 2UV；world_decal 保留几何，仅绘入 RVT，由 Landscape/背景 landmesh 合成。源 height 公式和原生保存图连接均严格检查。RVT 物理池专配 DXT5/BC5/DXT5、264px物理页、256 MiB；与普通纹理 streaming pool 分开。
- 普通树的 25 个 T1.r 分离遮罩实例按 7 对原 PNG 哈希限定；43 个实例包括 18 个保持原绑定的负例已真实核对。旧 facing-leaves 保留 5UV/half4、91 cards/182叶三角/2,369实例；源坐标与摄像机空间变换已独立证明。
- 历史基础模块 `eceebaa1634a2df9c6314556efcc0190f5c5b4b926ef13299669ca461d68de76` 下，flanders-contact-03-rvt 与 rmuc-regression-02-final 各 5 项、8 PNG、exit 0。弗兰德斯前进 340.284 cm/横漂 0.070 cm，共用复位距离 0.0133 cm；复位调用共享路径，OS键盘观察独立记录。该历史检查点使用当时的 AIM 2000/140 悬挂与车辆包参数；后续组合模块的车辆与碰撞行为按各次实际报告绑定。
- SceneRenderReadiness 以实际 asset/shader/streaming 计数等待；UE5.8 wanting-resource ID 真实可保持 0，freshness 来自 StreamAllResources 强制更新。RVT 88 为原生 allocation 请求矩形覆盖数量；同步调用完成后又等 2 个完成渲染帧。不得写成读到了 GPU 生产页计数。
- 原始浮地块/黑树卡和首帧未完成材质的截图均保留。最新静止、行驶后、复位图已看见完整车体和树冠透明间隙。原 Land 五类颜色/权重、水色和七网格/13 层草已接入并完成实测；microdetail、grass-decal、small_grass billboard、风动与原随机位置继续按 SOURCE 与材质报告跟踪；原碰撞缺失/非实体资源保持清单。
- 最新保存 World SHA `20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25`，manifest SHA `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27`。grass-native-07-resume 在 c44d2c… 模块烘焙七网格/13 层，两视点 52,984 个真实 render instances；grass-reload-02-final 与 water-native-02 在 1bf4126e… 模块分别 27 项通过和 exit 0 / 14.17 s。
- 后续 d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404 模块已通过 native-materials-08-final 的 104 资源、native-scene-04-final 的 21 项，以及 flanders-contact-04-final / rmuc-regression-03-final 各 5 项 / 8 PNG。GUI PID 43760 已实测绿褐地表、草与粉白花、小地图车辆标记和 M 大图；12 次真实 OS 动作、16 个驾驶输入边沿、4 次 M 切换、8 图绑定 `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`。最终车位 ROS `(1075.232351,-1012.656026)`，旋转后两图箭头同步，出生标记独立。
- 大资源/UE Content/日志/稠密报告与截图留本地。compact 证据在 `artifacts/maps/wt-scene-20261118`；本轮代码、复现步骤、架构和当前进度随 feature 发布。此前“完整场景尚未导入”的历史条目以本检查点及 current.md 为准。

## 2026-11-18 统一工程接续

当前活动工程统一为 D:/simulator；原 D:/simulator-wt-vlaanderen 已移动到 artifacts/archive/wt-vlaanderen-before-unified-20261118。运行入口与新命令从共同 uproject/车辆包/设置派生，历史报告中的旧绝对路径作为来源记录保留。回滚快照和三方合并逐文件哈希在 artifacts/integration/unified-20261118。

源碰撞覆盖与新生成补充分列：14,349 个主枕木、37 个横枕、4,068 个路肩；生成组件按活动范围加载，并保留出生点锚点。当前已通过203保存资产、128条原生查询，以及默认真实驾驶/复位与跨8m的实际W/R。几何查询与实际轮地接触各自表述。

草地当前为10模型16层，硬面排除权重与原色端点按来源验证。最终真实rebake后72项草重载通过，1089个组件的保存/当前generation hash全部匹配。最终307材质、21场景及共用控制6项/两图接触各5项通过。坡面LC格线与Height碎石强斜黑纹已在最终原图确认消除；源像素和UE alpha-normal适配分别留证，保留所有历史失败及旧通过记录。

最终显示与grass freshness模块37daaf0e…包含并行Arena/破坏工作；World为33113995…。最终GUI实际完成默认入口、坡面/碎石、80帧W、M开关、R回默认出生点，窗口保留试驾。源码提交按主工程WIP、地图合并、此次功能与外部工作归属生成候选，保留工作字节及main门禁。共享Markdown中的外部章节同样保留并独立归属。


## 2026-11-19 手动相机方向更新

C 环视保持实际云台单位世界方向，Shift scope 持续读取角方向；普通手动路径不得保留有限世界点形成追踪。C/Shift 返回第三人称可做一次投影，后续保持角方向直到新鼠标输入。WASD 和大小地图箭头在当帧云台更新后共用 GetGimbalPlanarForwardRos。固定弹道目标只属于显式控制 probe。当前 77 项原生和 12 项地图校准绑定 camera-heading-20261119/release-summary.json；旧测试只检查驾驶与云台一起同向，不能单独证明环视期间方向稳定。

## 2026-11-20 学校体育馆与实际场地接触

- `rmsim_campus_gym_v1` 是 RMUC2027 地图美术扩展；活动入口 `assets/maps/rmuc2027/arena-shell.json`。当前源馆采用 `SchoolGymFidelity` 流程，`setup_campus_gym.py` 对应历史 CC0 校园馆。
- 用户要求保留源看台、阶梯、窗户和源材质，额外新增座椅为 0。源馆按原篮球运动区中心对齐；两套篮球架之外的源几何完整保留。旧新增座椅、阶梯模块及按面心删除源面均为历史流程，后续准备不得重新引入。
- 当前 **23 mesh / 57 实例 / 26 棵树**。source→保存 LOD0/fallback 核验通过，6 面 UV 代表合并有逐 corner 源证明，基础 2 ULP 规则保持；Nanite cluster 编码结构独立列为未审范围。
- 地板实际接触使用 `visual_floor_z_m=-0.3009999990463257`；源馆及全部外景、灯位统一上移 `0.13559999942779544 m`。馆地板覆盖完整比赛 footprint，原 RMUC 实际底面落在其上。原 CAD、车辆及导航数据保持，源地板与看台不另裁面。后续核验须分别读取真实上下表面和水平边界，避免只凭包围盒或截图判断。
- 当前合同为 `physics_contract=outside_source_bounds_v1`、`visual_alignment=source_bounds_outer_face`、`outside_clearance_m=0`：玻璃内面直接接原场地外沿，框架向场外展开。相对前一版，四面连续碰撞墙向内移动 1 mm；厚度 0.48 m、碰撞 Z=-1.25～4.80 m、玻璃上端与永久关闭装饰门保持。玻璃和底轨延伸到地板，立柱下埋 1 cm。不得新增开门交互或车辆出场通道。
- 历史 `preserve_validated_arena_shell_v1` 内缩 0.25 m 以及前一源馆版本的 1 mm 场外净距保留各自 fixture/证据身份。当前 0 m 接触使用现有 1 μm 几何数值容差，禁止更改验收门限掩盖实际侵入。
- 外景采用完整三维树木/2K 贴图、独立 8K pure-sky 照明与完整 8K 景观背景。2026-11-19 三幅原生外景图已查看、152.88 s 可见 GPU 运行通过；2026-11-20 地面接触版本的三张实际图像复核及 150.02 s 可见运行通过，六类 GPU/溢出警告为 0，WM_NULL 可响应且窗口保持给用户；记录在 `docs/changes/2026-11-20-school-floor-contact.md`。原灰色屋架材质保留，原篮球记分牌三维文字镜像独立记录。
- SketchupBox 原 SKP、派生几何/源纹理及 UE Content 保持本机，随附来源署名与禁止重新分发边界保持；Poly Haven、ambientCG 和官方商标按各自来源记录。源码、配置、来源/许可与 compact 验证入库。
- 导航、车辆物理与跨平台验收保留各自模块/运行身份。旧校园馆历史证据在 `artifacts/maps/campus-gym-20261119/release-summary.json`；源保真阶段在 `artifacts/fixes/school-view-fidelity-20261119/`；最新接触阶段在 `artifacts/fixes/school-floor-contact-20261120/`。

### 2026-11-19：飞艇任务对象与地图原资源

- 弗兰德斯飞艇源于原任务 object groups；完整基础导入须先验证任务补充，再在保存及最后一次旧 UObject 读取后恢复，保持源 67 实例 / 65 碰撞和固定地标策略。
- HUD DrawLine 默认 Canvas 项忽略 alpha；原网格用真实 translucent 路径。sRGB 字节必须显式转换，避免 UI 变亮。
- tiny_text_hud 为 PT Sans reduced-height、1080p18px、forceAutoHint=no；fontFx64 转换为2.0。当前 MapFontRenderer 复用原字体字节，实现源 glow／coverage gamma 缓存。
- FontFace 导入需要完整 Editor／Slate；NullRHI commandlet 路径不适用。Texture 须等待 Editor.AsyncAssetCompilationFinishAll 后再验尺寸和保存；失败证据保留，重试使用新目录。

## 2026-11-19 校园体育馆基线

- `rmsim_campus_gym_v1` 是 RMUC2027 的地图美术扩展；原 `rmsim_arena_shell_v1` 分派与 legacy fixture 保留。入口 `assets/maps/rmuc2027/arena-shell.json`，重建入口 `tools/unreal/setup_campus_gym.py`。
- 用户要求玻璃门永久关闭且仅作装饰。不得新增 F 交互、开门状态或车辆出场通道。四面连续保护墙覆盖门区及四角。
- 物理/LiDAR 围墙保持既有验收几何：内缩 0.25 m、厚 0.48 m、Z=-1.25～4.80 m。可见玻璃顶面 Z=2.60 m、厚 12 mm，内表面对齐保护墙的场内侧。改变视觉样式不得无记录地移动定位边界。
- 源素材有固定哈希、CC0 来源及派生说明；新 UE Content 保持生成和忽略，现有 14 个 legacy 资产许可白名单未扩大。原源模型与导航真值边界保持。
- 验收对应具体模块与构建树；发布切片的场景结果与当前统一工作区的导航结果分开记录。基线、实际检查及失败记录见 `.codestable/features/2026-11-19-campus-gym/acceptance.md` 与 `artifacts/maps/campus-gym-20261119/release-summary.json`。
