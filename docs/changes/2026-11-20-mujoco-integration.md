# 2026-11-20 标准入口接入 MuJoCo 与 MID-360 时序、测量模型

## 结果

标准手动入口 `Play-ManualVehicle.cmd`、导航入口 `Play-Navigation.cmd` 和复用手动入口的 `Play-Training.cmd` 现已采用同步原生 MuJoCo。车辆的底盘、悬挂、转向、轮轴和 yaw/pitch 云台在同一个求解器中推进，UE5 负责当前源场景的显示、相机、材质和传感器射线查询。旧 `Play-MuJoCo.cmd` 与 `Play-MuJoCo-Navigation.cmd` 已合并移除。

标准 RMUC2027 包使用 1 ms 动力学积分、5 ms UE 世界更新和 60 Hz 视口目标。MID-360 默认提供 200,000 发射槽/秒、10 Hz 完整点云帧，内部 IMU 为 200 Hz。源装配的约 45° 倾角、云台父链和原车体几何保持，传感器光学原点与轴向依据官方尺寸重新提取。

## 原始配置与输入身份

驾驶源为 `assets/vehicles/aim_sentry/drive-profile.json`，43 项控制/物理参数沿用现有值。按用户最新要求，RMSim 导航的速度、加速度和减速度从这一份手动驾驶源生成；所选 sentry YAML 继续提供路径控制、定位、避障和目标到达算法配置。

| 配置层 | 前进 / 后退 / 横移 | 其他关键值 |
|---|---|---|
| 手动驾驶源 | 5.0 / 2.5 / 4.0 m/s | 加速 2、减速 4、制动 7 m/s²；加速 jerk 6、减速 jerk 20 m/s³ |
| RMSim FollowPath | `v_linear_max=5.0`、`v_linear_min=-2.5` m/s | 角速度范围 ±π/2 rad/s，控制频率 20 Hz |
| RMSim velocity_smoother | max `[5,4,π/2]`、min `[-2.5,-4,-π/2]` | 顺序为车体 x/y/yaw；加速度 `[2,2,3]`、减速度 `[-4,-4,-5]`，20 Hz |
| 共同车辆指令整形 | 同一份前后/横移、速度和角速度参数 | 制动 7、jerk 6/20、方向向心约束 8、方向 jerk 70；同源车辆控制原生 13/13 项通过 |
| 未传车辆覆盖的独立 reality 源 | 控制器前进上限 1.0 m/s；smoother max `[1,1,3]`、min `[-1,-1,-3]` | 真机源文件保持；RMSim 内选择 reality 行为时仍采用统一车辆运动数值 |
| 悬挂 | 刚度 2000 N/m、阻尼 140 N·s/m | 预载 50 N、行程 65 mm |

标准 console 显式传入车辆 `drive_profile`；运行参数和状态记录它的 SHA-256、全部 27 项控制数值及限值映射。`fake_vel_transform` 在原 velocity_smoother 前将估计导航轴速度转换为 `base_link`，使前后/横移限值随车体朝向成立。Runner 内的两端消息适配连接 TwistStamped 与原 Humble Twist 接口；公共 `/<robot>/cmd_vel` 保持 TwistStamped、车体坐标与仿真时间。原节点、插件、速度超时和桥接 deadman 保持。

车辆端的手动输入和导航输入通过共同的速度、方向、角速度整形器；连续 20 Hz 导航消息保留上一帧加速度和 jerk 状态。明确的零目标在合法时间戳、坐标系及 session/epoch 检查后立即送达车辆制动路径，同时交给原 smoother；停止期间将 smoother 剩余非零尾段替换为零，收到时间戳严格更新的合法非零目标后恢复。适配器由实际消息驱动。断连、状态失效与 epoch 更新清除适配器状态。

同源配置及停止逻辑共 31 项合同通过，真实安装 ROS 环境中的 Twist/TwistStamped 回调检查 18/18 通过。`ros-bridge-build-03` 构建 exit 0，24 个实际安装 Python 模块与对应源文件逐字节一致。最终 build13 的 `native-shared-drive-02` 通过 13/13 项，覆盖 27 项实际控制值、各方向限幅、连续 20 Hz 指令下加速度与 jerk、角速度、车体坐标变换、零指令制动和 watchdog，最大行驶范围为 0.630 m。导航正式用例另行从运行节点查询实际 FollowPath、smoother 参数并验证同源身份。

| 原始文件 | SHA-256 |
|---|---|
| `assets/vehicles/aim_sentry/drive-profile.json` | `fd1ece7218f9465f94090e31853cc191006427608e467e058a8081aa234f6a87` |
| `ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml` | `be50ccce20d0d3d799079f76be5ff3cd620f1b6a4a19451750364c8cb4dfe778` |
| `ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml` | `e5bcf8243ceb57c3e83c73d48cc9051ea7a853e3110a1b5ed11610b35c9384d6` |

## 动力学、源场地和坐标

车辆使用 15 个源部件、249 个车辆凸壳与四组独立悬挂/转向/轮轴，配置质量合计 25.6 kg。上部车体与云台合计 20.2 kg；源阻尼、轮摩擦和驱动增益映射到 MuJoCo 力矩、阻尼及接触参数。重力采用实际 UE 世界的 9.8 m/s²。当前 RMUC 场地使用 14 组固定三角面，保留坡面、波浪、凹槽、孔洞及四面场地边界。

正式入口验证车辆源、场地身份、MJCF 和实际运行库。模型使用右手 SI 坐标，UE 表现采用厘米和对应的左手坐标转换。UE 车辆代理采用 QueryOnly，车辆的逐步动力学状态由 `mj_step` 统一产生；R 重置同时更新求解器、指令、云台目标和传感器 epoch。

正式模型清单 `assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json` 的 SHA-256 为 `f5583e06f7c337e37c16dd61d71e5998617084e170c191b2a6f5d4c1a741a204`；`model.xml` 为 `6dc6edb0d5f45b80cae3dc4073584d895450d10f826107e213138f64e689097a`。运行库沿用 MuJoCo 4.0.1+rmsim.static-world-flex.3；静态 world 三角面缓存与原版的对照依据见融合指南和 `docs/physics/mujoco-static-flex-cache.md`。

## MID-360：公开参数与实现

| 项目 | 公开依据或设备配置 | 当前实现 |
|---|---|---|
| 光学视场 | 水平 360°，垂直 −7°…52° | 设备本体坐标系内定义，随真实云台与源倾角运动 |
| 总点率、回波 | 200,000 点/秒，第一回波 | 每 5,000 ns 一个槽；无回波保留零点及原时间/line |
| 通道 | 官方驱动为 MID-360 设置 4，逐点 `i % 4` | `line=0..3` 为通道标签；线程按工作职责调度 |
| 组帧 | 官方驱动允许 5/10/20/50/100 Hz | 默认 10 Hz，每帧 20,000 槽，末槽 offset 为 99,995,000 ns |
| 光学原点、IMU 外参 | O 距底面 47 mm，+X 远离连接器；IMU 相对 O 为 `[0.011,0.02329,-0.04412]` m | 由 Blender 源几何和实际连接器重新提取，挂于 `yaw_link` |
| IMU | ICM40609，默认 200 Hz | 真实安装点速度在 t±1 ms 中央差分，输出 m/s² 与 rad/s |
| 测距 | 0.1 m 起；100 klx 条件下 40 m@10%、70 m@80% | 70 m 裁剪上界，加 NIR 反射率及入射角检出模型 |
| 误差指标 | 距离 1σ≤2 cm@10 m、≤3 cm@0.2 m，80% 目标/25°C；角度约 0.15° 1σ 上界 | 显式记录的未标定测量近似 |

每束的发射姿态由相邻 1 ms MuJoCo 状态进行位置插值和四元数 slerp，保留运动造成的扫描畸变。射线查询使用当时 UE 世界快照，按连续 32 束分块交给 UE 线程池，各块持有独立查询参数；材质判断与透明表面推进在游戏线程处理。点云完整帧队列容量为 8，溢出按完整旧帧计数。网络输出与画面刷新保持独立。

当前方向序列为 `spherical_fibonacci_approx`，可通过完整 5 µs 方向 CSV 进行记录回放。NIR 缺省反射率 0.1、两锚点检出模型、距离/角度高斯噪声、`round(150*ρ)` 漫反射强度均标记 `measurement_calibrated=false`。IMU ±16 g/±2000°/s 为显式配置假设，偏置/白噪声/量化默认关闭。完整棱镜轨迹、工厂校准、波束光斑、雨雾、多径、逆反射和动态障碍物连续历史属于后续测量模型范围。

资料固定到 Livox 官方规格、User Manual v1.2、Ethernet Protocol v1.4.12、driver2 v1.2.8 与 SDK2 v1.5.2。精确来源、许可、坐标和公式见 `docs/sensors/mid360-physical-model.md`。

## 已有功能证据

下列结果各自绑定报告中的模块与配置；不同构建的功能检查分别记录。

| 用例 | 已核结果 | 证据 |
|---|---|---|
| 原生 MuJoCo 车辆 | 9 项通过，exit 0；接触落地、前后/横移、正负 yaw、制动、云台与 R | `native-vehicle-01/verification.json` |
| 最终构建手动与相机 | 6/6；C 环视、Shift 进入/退出、5 m/s 指令与制动；实测 4.9202 m/s，8 张图；原生回放，未计为 OS 键盘输入验收 | `manual-camera-rmuc-03/verification.json`，build13 |
| 最终构建传感器与 ROS | TCP 23/23、ROS 14/14；200 Hz IMU、20,000 个 distinct 5 µs 时刻、零回波、TF/单位、断连与重连；4,096 束/8,192 次原生查询对照命中误差 0 cm、材质一致 | `native-sensors-09-final-query/verification.json`，build13 |
| 显式 NIR 配置 | TCP 23 项通过，保留配置身份 | `native-sensors-05-configured-nir/verification.json` |
| 红方波浪 | 4/4、exit 0；前进 340.388/340 cm，侧偏 1.385 cm，制动后 0.0328 cm/s；倒退余距 9.689 cm、侧偏 1.773 cm | `native-wave-red-01/verification.json`，模块 `d14ecfd1…50813` |
| 蓝方波浪 | 4/4 通过；前进 340.387/340 cm，侧偏 0.420 cm，制动后 0.0510 cm/s；倒退余距 9.717 cm、侧偏 1.659 cm | `native-wave-blue-01/verification.json`，build07 |
| 导航短目标与取消 | standard-01 和 profile-01 各 17/17；目标误差分别 0.0760 / 0.0654 m；取消后制动 0.1304 / 0.1055 m | 对应用例 `actions/verification.json` |
| 导航观察器修复 | profile-02 动作 17/17、clock 覆盖完整动作；性能门限未通过 | `navigation-profile-02`，统一驾驶参数前的配置 |
| 手动/导航同源配置与停止语义 | 31/31 合同、18/18 真实 ROS 消息回调通过；源字节保持，车体坐标、epoch 与立即制动合同通过 | `navigation-unified-drive-01`、`navigation-stop-intent-01` |
| 最终构建同源车辆控制 | 13/13；连续指令保留整形状态，实际控制值、车体限幅、加减速与 jerk 一致；行驶范围 0.630 m | `native-shared-drive-02/verification.json`，build13 |
| 实际碰撞包络与墙体 | 6/6 通过；真实源凸包最小墙面余量 −0.008936 cm，门限保持 −1 cm | `native-boundary-02/verification.json`，build09 |
| Windows 时钟状态发布 | 22/22 通过；临时占用恢复、持续占用有界失败、原窗口字节保持 | `clock-state-atomic-01/verification.json` |
| 离线接口与 profile | 38 项通过；含 6 项源坐标/再生成/schema/Point-LIO 外参 | `geometry-review-01/contracts-review2.json` |

证据根目录为 `artifacts/sensors/mid360-physical-20261120/`。各报告的 SHA-256、检查摘要与最终状态保存在本次 `release-summary.json`，同一精简记录入库于 `docs/verification/2026-11-20-mujoco-mid360.json`。

## 正式导航与实时验证状态

正式条件保持为：完整 60 s 窗；整体及每 1 s 滑动的 10 s 窗倍率均在 0.98…1.02；native Draw 60 Hz±5%；LiDAR 200k 槽/秒、IMU 200 Hz；同一 session 的完整动作 trace 被时钟窗口覆盖。Windows 自动用例使用完整场景的离屏渲染、原生 ROS/Point-LIO/AMCL/Nav2，RViz 关闭、RGB 流采用标准的 0 Hz；可见交互单独验证。

MuJoCo pacing 按实际求解时间累计绝对墙钟 deadline。正常迟到时保留欠下的时间并完整推进下一帧；提前时等待。初始化、motion generation 变化或求解器时间回退时重建时基，超过 50 ms 的迟到只记录限频诊断。原实现的普通超时重建时基会丢掉墙钟欠账；修复后的实际 C++ 策略通过 6 个场景，包括可追赶的 80 ms 尖峰和持续算力不足，未修改时间窗口、帧数或传感器输出。观察器的 Windows 原子文件替换对共享冲突执行最多 200 ms 有界重试；状态内容和窗口起止生成一次，相关合同 22/22 通过。

| 用例 | 动作 | 60 s 整体倍率 | 时钟检查 | 完整动作覆盖 |
|---|---|---|---|---|
| `navigation-profile-04`，build10 | 18/18；目标误差 0.082092 m，取消制动 0.181093 m | 0.983225× | 30/32 | 通过 |
| `navigation-profile-05`，build10 | 18/18；目标误差 0.090804 m，取消制动 0.175148 m | 0.976053× | 29/32 | 通过 |
| `navigation-standard-05`，build10 | 实际统一参数检查通过；短目标 45 s 超时 | 0.969459× | 29/32 | 通过 |
| `navigation-standard-06`，build11 | 17/18；目标误差 0.085483 m，取消制动 0.279477 m | 0.999809× | 30/32 | 通过 |
| `navigation-standard-07`，最终 build13 | 18/18；目标误差 0.131949 m，取消制动 0.169945 m，停车漂移 0.000067 m | 0.999841× | 30/32 | 通过 |

最终 `navigation-standard-07` 的实际统一参数、短目标、取消与停车共 18/18 项通过，动作完整位于同次 60 s 时钟窗口。整体倍率为 0.9998410526×，时钟 30/32 项通过；分段和滚动 10 s 窗口两项未通过，滚动倍率范围为 0.894489…1.069938×。本轮将标准入口、同源驾驶和已实测的功能记为完成，完整实时性能验收保留 `partial`，沿用原门限跟踪。

standard06 的公共指令在取消后 285 ms 仿真时间才达到全零，此前按 smoother 的 4 m/s² 普通减速输出，实际取消制动距离为 0.279477 m。即时停止锁存修复后，最终 standard07 的取消制动距离为 0.169945 m、停车漂移为 0.000067 m，均通过原 0.2 m / 0.02 m 门限。各轮地形姿态和速度分别保存，距离变化按实际用例记录。 `cancel-analysis-07/analysis.json` 进一步确认停止策略在实际运行中启用：首条公共精确零指令的 stamp 比取消时最新 solver 诊断时间晚 25 ms，从取消请求到该指令被观察到的墙钟间隔为 26.3207 ms；即时停止计数增加 1，残余输出抑制计数增加 4，取消后记录的 24 条公共指令全部为零。原始零输入的接收时刻未采样。

统一参数后曾出现末端约 0.05 m/s 指令下的短目标停滞。精确源三角面与实际车辆凸壳复核确认，该路线穿越约 70 mm 高的源波纹，失败尾段的底盘后缘与路面法向余量已进入 0.1 mm 接触层。成功用例中也出现底盘触地，完成情况依赖姿态及轮地载荷。源碰撞体、悬挂、驱动增益及原导航 approach 策略保持；现有成功记录用于证明对应运行，后续地形适应工作继续跟踪此工况。

历史失败与诊断均保留：standard01/02/03 的整体倍率分别为 0.890430×、0.947059×、0.963657×；profile02 动作 17/17；standard04 统一参数查询通过、短目标超时。profile03 在发送目标前遇到 Windows 状态文件替换冲突，原失败文件与原子替换修复证据独立保存。各运行绑定自己的模块、命令、源文件与结果。

边界探针改为投影实际源碰撞凸包顶点，保留同一 −1 cm 门限、碰撞控制阶段和旧 AABB 对照值。原 132 个凸包、2,232 个顶点和 104 个记录姿态证明，组件局部盒旋转后的外扩会高估越界。`native-boundary-02` 实际重新运行 6/6 项通过，真实最小余量约 −0.089 mm；没有修改碰撞体或动力学求解。

最终候选构建 build13 模块 SHA-256 为 `85ee653ec4a901cc3b65ff9f05104d6aadb056bc2da5aeab5fca79f963a00217`，编译 exit 0。`native-sensors-09-final-query` 已在该模块通过 TCP 23 项、ROS 14 项及 4,096 束命中/材质独立对照。短观测倍率 1.010921× 单独保存，正式时钟验收由 60 s 导航用例负责。共享驾驶 13/13 与手动回放 6/6 也分别绑定该模块。

build12 的静态接触身份查询缓存实验全程走原查询回退，未获得要求的 4,096 条缓存候选对照；`native-sensors-08-identity-cache` 因覆盖不足失败，其中 TCP 23、ROS 14 项通过。该实验已按精确 before SHA 从生产代码撤回，最终使用原有 UE 接触身份查询，保留所有失败与恢复记录。MuJoCo SDK 内已验证的固定 world 三角面缓存继续使用。

先前 build09 的共同控制 13 项、手动相机 6 项和边界 6 项，以及 build06/07 的红蓝波浪结果，仍各自绑定原始模块与输入。最终记录分别保存新运行和历史覆盖。

## 可见标准入口交接

通过零参数的 `Play-ManualVehicle.cmd` 实际启动标准手动模式，窗口 PID 10412，启动于 2026-11-20 16:35:10 Asia/Shanghai。运行参数为默认 RMUC 地图及出生点、原生 MuJoCo、5 ms 世界步与 60 Hz 视口目标；实际加载的 RMSim 和 MuJoCo DLL、模型、车辆源身份均与最终构建匹配。交接时有且只有这一个 UE 进程，窗口可见、前台、响应正常。

`manual-standard-handoff-01/verification.json` 保存 8 项启动/身份/窗口检查及实际图像审查。已查看第二张原始截图，车辆、源场地、体育馆和 HUD 正常显示。可见窗口保持供试驾；本交接未发送车辆/相机输入，6 项操控回归按前述原生回放单列。

## 适用范围与来源

当前 MuJoCo 模型覆盖 RMUC 静态场地。弗兰德斯专用入口沿用现有场景与破坏逻辑；动态植被、碎屑、实体移除和缓存失效映射按对应后端任务继续建设。Windows 原生结果、Ubuntu 原生结果与实车标定分别保存。

- 操作与重建：`docs/guides/mujoco-integration.md`。
- MID-360 详细模型：`docs/sensors/mid360-physical-model.md`。
- 官方规格：https://www.livoxtech.com/mid-360/specs
- 官方手册：https://terra-1-g.djicdn.com/851d20f7b9f64838a34cd02351370894/Livox/Livox_Mid-360_User_Manual_EN.pdf
- 官方驱动固定提交：https://github.com/Livox-SDK/livox_ros_driver2/tree/21445540f0d100dc86a7e6df312dd70bbdb4afdf
- 官方 SDK2 固定提交：https://github.com/Livox-SDK/Livox-SDK2/tree/c0796f04c143143899c87a773d9f6b7136453c0b
