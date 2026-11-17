# 当前进度

更新：2026-11-16，Asia/Shanghai。Windows优先；Ubuntu只做同源离线适配。长期goal保持active，完整sentry_ws/比赛/联机尚未完成。

## 最新修复：舵轮自转平稳性与停车

原轮胎弧形胎面凸包已替换错误的全宽圆柱，导出/导入/缓存按源几何哈希管理；停车不再逐帧强制唤醒刚体。最终profile与本轮before一致，质量/摩擦/弹簧/阻尼/PI未改，原模型与12个用户控制器源文件未改。

完整自转窗口速度误差RMS0.01016m/s（旧0.04261）；20°坡实际下坡松键5.356cm停住，保持与外力唤醒通过。当前同版本92项/48PNG原生检查通过，含红蓝波浪、训练/复位、ROS11和原LK21。证据artifacts/control/gyro-ripple-20261116/release-summary.json；详细修复与失败尝试见gyro-precision-ripple issue。

最终2m/s普通/自转改向368.4/380.0ms，0.6m/s139.5ms，静止舵向96.3ms；这是引擎内响应，不是OS键鼠到屏幕延迟。默认RMUC手动窗口PID29224已恢复，实际观察243FPS/静止/gyro off；旧diagnostics_probe.exe安全弹窗仍遮挡，真实键盘试驾未验收。GUI状态见同证据目录gui-status.json。长期goal保持active；下一步继续新轮胎下Point-LIO/Nav2联合、RMUC波浪定位精度和恢复流程。

## 最新完成：LiDAR 与原 Point-LIO

当前推进native-navigation：10包原生构建，18程序/301DLL局部部署；诊断库DLL可见性、地形frame和初始位姿协方差问题已修复。测试场地原AMCL由0.72m先验误差收敛，原Theta*/Omni PID实际绕障到达[5.5,1.0]，末端约0.20m，轨迹/错图保护通过。公开目标脚本已实跑；取消后约6cm停车。旧LK/Point-LIO联合11、瞄准20、手动19项/12PNG回归通过。RMUC默认区域定位/短路线已分项通过，波浪导航精度、失联/实际R恢复和启动包装完整验收仍待完成，feature保持in-progress。进度证据artifacts/navigation/checkpoint-summary.json，指南docs/guides/native-navigation.md。

实际R测试因Windows安全中心的diagnostics_probe.exe权限弹窗无法由当前窗口工具操作，等待300秒后超时；前10项保留为分项成功，R未标通过。已请用户点击“取消”，未放行防火墙权限，后续诊断显式限制本机ROS通信。此UI步骤待用户操作，不等于整个长期目标无法推进。

此前RMUC已保留560×322、0.05m/128层无截断几何采样与candidate-03通行栅格，仍是候选。rmuc-01定位4项、rmuc-02短路线7项通过（末端0.1457m）；rmuc-03波浪目标实际误差0.2827m超过0.25m门限，未返程；rmuc-04诊断因观测超时失败，轨迹留存。当前优先处理用户控制反馈，不将这些候选标为完整RMUC导航通过。

实际UE滚动射线、原Livox CustomMsg/PointCloud2、原始IMU与原Point-LIO已经原生联跑。支持车辆包安装配置、自动外参、真实逐点时间、有限缓存及会话复位。原估计器不订阅真值；真值仅作为独立评估。

- 默认物理场景与真实R：13项通过；不同安装位置/朝向：11项。
- 默认RMUC场地：10项；原LK电流控制器+传感器+原Point-LIO同场：11项。原LK联合路线约1.39m，初始姿态对齐后IMU点末端误差约4.4mm。
- 压力3项，自身遮挡/近墙/盲区各3项，非法配置2项、原参数错误退出、CDR5种尺寸通过。
- 既有IMU11、ROS底盘11、瞄准射击20、TCP19、手动19项及12PNG回归通过。

本次数值只适用于未标定的理想射线/噪声模型和测试路线，不是实车精度。扫描图样不是Mid-360光学复刻；1ms物理帧内射线共享时刻，未虚构5µs物理状态。固定1ms通常约0.3倍墙钟，默认手动不开这些传感器。

证据：[机器汇总](../../artifacts/sensors/lidar/release-summary.json) · [验收](../../.codestable/features/2026-11-16-lidar-pointlio/lidar-pointlio-acceptance.md) · [启动指南](../guides/lidar-pointlio.md)。原节点/消息来源与补丁在artifacts/sources/point_lio、third_party及ros2_ws/src分开保存。

LiDAR阶段GUI证据保留于artifacts/sensors/lidar/gui-restored.json；当前预览与回归以本页顶部最新记录为准，历史模块的通过项不自动算新轮胎版本通过。

## 当前可玩与算法接口

- Play-ManualVehicle.cmd / Play-Training.cmd默认打开RMUC2027，原尺度AIM约503.850×501.985×419.192mm，轮半径42.5mm。15视觉部件、13真实刚体、4悬挂/4舵向/4轮轴约束。
- 普通/小陀螺均WASD镜头相对平移；Q/E普通旋转，G开关自转，Z/X调速，Ctrl慢行，松键主动制动，Space更强制动；鼠标瞄准/俯仰，C自由观察，Shift瞄准镜，R复位。
- 已修车辆放大、波浪假阻挡、轮速死区、改向/提速耦合及多实例长帧。红蓝波浪往返、坡向抓地、真实键盘映射有历史专项证据；最新模块只将实际重跑的检查记作本轮通过。用户手感持续反馈，未声称OS端到端延迟标定。
- 单车训练具备真实弹丸扫掠、靶HP、弹量/热量、射速、倒计时、胜利与R重置；是演示训练规则，非完整官方裁判。
- ROS支持TwistStamped速度控制、原LK电流/反馈闭环、高层自瞄/扫描/发射原消息、相机/CameraInfo/TF/Chaos时钟、raw IMU、LiDAR及原Point-LIO局部定位。
- Point-LIO本身拒绝时间倒退，runner按新session/epoch重新启动原进程/地图；自动生成外参必须用ROS double数组，参数读取错误立即退出。原输出当前仍是单车全局话题，尚无多车隔离。
- 速度/电流/瞄准有效期独立，断线/过期/复位会制动并丢旧命令；250ms执行租约不因传感器负载放宽。loopback TCP不是联机协议。

## 地图与模型边界

RMUC2027静态场地来自官方V1.0.0及同源转换包，来源/哈希在assets/maps/rmuc2027/SOURCE.md；场地Z-up不得再套普通glTF旋转。

WT CDK512m高度场只是历史地形示例。用户指定的弗兰德斯原图已完成659种模型、917贴图/数据图、616碰撞资源解码，准备726视觉/639碰撞FBX与45675物件；完整UE原图导入及实际驾驶仍未验收，不能用CDK裁剪代替。

车辆包v1离线校验/受限URDF与两个示例已有证据；当前真实物理运行是AIM专用mesh-rig路径，不代表任意新车惯量/关节/传感器全部即插即用或已经实车校准。

## 尚未完成

- 同源完整sentry_ws：视觉识别、Hub/原云台电机环、地图重定位、Nav2、行为树及完整自主哨兵。
- LiDAR真实扫描/材质反射、传感器带宽/温漂/误差标定；动力学实车标定与完整场地路线能力。
- 官方赛季裁判、动态多机器人对抗、权威网络服务器及多车话题/身份隔离。
- 原生Ubuntu编译/运行；当前只是同一源码、脚本与离线依赖准备，不采用WSL验收替代。
- 原战雷完整地图运行；进一步真实光影/画质验收。

## 环境与长期记录

项目D:/simulator；UE5.8.4在E:/simulator-tools/UE_5.8，须显式EngineRoot。原模型E:/WarThunderModding、原控制器E:/27UL_Sentry_chassis_controllers-main只读。原生Humble为RoboStack Python3.14，不混用官方Python3.10扩展。

CodeStable约束在.codestable/attention.md，当前结构在architecture，规划在roadmap，逐次变更在docs/changes。旧阶段详细进度已完整保存在[归档](archive/2026-11-16-before-lidar-pointlio.md)，不将其中“当时未完成”的语句当作当前状态。
