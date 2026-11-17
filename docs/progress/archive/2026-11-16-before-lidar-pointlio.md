# 当前进度

更新：2026-11-16，Asia/Shanghai。Windows原生原LK底盘、ROS高层云台/训练发射及原始六轴IMU切片已验收。Windows先做、Ubuntu离线准备。原长期goal保持active，完整sentry_ws/比赛/联机仍未完成。

## 最新接入进展

当前推进lidar-pointlio：原Point-LIO3099b9dd和原Livox a8230b02已原生构建，解析回放5项、实际UE激光7项、原节点联合9项有证据。真实射线200000/仿真秒、滚动扫描、原CustomMsg/PointCloud2与IMU已接通。复位管理首次测试发现外参矩阵整数数组类型错误，已改浮点，并修原节点吞掉配置错误继续运行的问题，正在复测。压力/复位及旧功能回归未完成，尚不标整个feature验收。依赖锁新增140包，无旧包替换。

**物理IMU已通过限定验收。** 实际安装点速度/角速度、SI比力、安装坐标、偏置/白噪声和独立采样已接通。最终12项含慢读者/重连/真实R，自由落体3项、坡面1项、帧率不足11项及非法配置拒绝通过；普通ROS11、云台射击20、手动19项/12PNG回归通过。汇总artifacts/sensors/imu/release-summary.json，指南docs/guides/imu-runtime.md。

输出仅六轴data_raw，orientation_covariance[0]=-1，无AHRS或真值定位TF。配置位于车辆包sensors/imu.json，默认200Hz，物理帧不足时计未采周期而不伪造样本。固定1ms约0.3倍墙钟，默认手动仍实时；Point-LIO只有SI参数预备件，LiDAR/定位闭环未运行。最新手动回归：静止舵向99.5ms、行进90°改向251.6ms，非OS端到端延迟。

最终默认RMUC手动训练窗口已恢复并实际R复位，观察232–241FPS、车速0、小陀螺关闭、弹量100/热量0，窗口留开；未启用ROS或IMU。GUI证据artifacts/sensors/imu/gui-observation.json。下方各阶段窗口/帧率记录属于当时验收。

**ROS云台/训练发射已通过限定验收。** 原AutoAimCommandMsg/GimbalCmd/Int32许可已接通，瞄准与底盘有效期独立；真实弹丸命中、扣弹、发热/冷却和评测隔离均已验证。最终普通21项、原LK联合20项、只观察4项通过；射击15项/10PNG、普通ROS11项、TCP19项、手动19项/12PNG回归通过。统一证据artifacts/control/ros-aim-fire/release-summary.json。

实际R只复位一次训练回合；最终普通RMUC手动窗口已R复位留开，观察235–245FPS。云台仍为有限转速运动学执行器；未声称原硬件云台电流环、动态碰撞、完整Hub或真实视觉识别已经运行。指南docs/guides/ros-aim-fire.md。

**原LK电流控制闭环已通过限定范围验收。** 用户原C++节点、完整custom_msgs与真实轮/舵反馈已接通，原PID/PowerLimiter实际运行。最终native-10为22项，含真实R、CLI、异常电流/会话与失联停车；同模块普通ROS11项、TCP19项、手动19项/12PNG通过。源12文件未改、原节点与UE/运行DLL哈希见artifacts/control/lk-motor-loop/release-summary.json。

电机模式固定1ms仿真步长，约0.28–0.33倍实时。平地前进1.165m时横偏约0.197mm，稳态舵角误差0.132度；这是未标定模型结果。普通手动保持原路径，行进改向256.7ms、静止舵轮96.5ms，最终RMUC窗口已R复位留开，观察212–240FPS。原LK波浪路线、完整sentry_ws、电气/实车标定、Ubuntu和联机未验收。详见docs/guides/sentry-lk-motor-loop.md。

UE接触车与原生Humble已真实互通：`ros2 run rmsim_bridge drive_demo`实际驱动车辆，发布同步Chaos `/clock`、1280×720 RGB8、CameraInfo与光学TF。指令失效/断线/复位会制动并丢弃旧命令。RosBridge只观察，RosControl拥有底盘；普通手动入口不开额外传感器。启动说明见docs/guides/ros2-native-bridge.md，机器证据见artifacts/ros2/native-release-summary.json。

上一阶段纯速度桥CLI门禁11项通过（独立demo前进0.352m）；TCP19项/106图像、只读4项、400ms停顿故障1项、实际R复位均有证据。其手动回归19项/12PNG、行进改向251.1ms、静止舵轮101.7ms属于当时模块；当前原LK接入及最新模块回归以上文为准。尚无完整OS输入延迟测量。下方旧45项保留波浪和抓地结果，不冒称新模块全部重跑。

当前ROS包括速度级与原LK电流级底盘、单相机、原始六轴IMU、高层云台/训练发射；LiDAR/AHRS、完整sentry_ws、实车标定、正式裁判与联机尚未完成，Ubuntu仍仅离线准备。观察相机缩放不改变传感器内参；此前Shift GUI切镜未确认，不能把复位后固定光学检查当成按键缩放验收。

验收后已恢复普通RMUC手动单实例，真实窗口显示241–243FPS，R键复位后静止、小陀螺关闭、弹量150。没有启用ROS控制或传感器采集，当前窗口留给用户试驾。

最新修正：用户已恢复goal active。按用户实车尺寸反馈，已查原始WT制作脚本及原毫米PLY，移除错误保留的2倍展示倍率。当前503.850×501.985×419.192mm，轮半径42.5mm，所有关节/炮口/相机同步缩半。中央坡道的大box误挡已改为7部件碰撞，原生前进1.6m通过；默认RMUC9项驾驶、14项射击检查通过。见scale-fix-20261115与vehicle-real-scale问题记录。

## 当前可玩功能

**最新控制切片已通过原生组合验收。** 15部件/13刚体独立舵轮，普通/小陀螺均镜头相对WASD；G开关自转、Z/X调速、Q/E普通旋转、Ctrl慢行、松键主动制动、鼠标瞄准/俯仰。最新45项/40PNG全部通过，模块与配置哈希一致，见artifacts/gameplay/gyro-control/release-summary.json。包含19项控制、最高4rad/s自转下红蓝波浪各3.4m往返、20度坡向抓地/停车、射击/实体墙/复位。

用户再次反馈滞后后补测了动态改向：0.6m/s行进90度改向由约967ms降到257ms；四舵轮原地到80度约101ms。去掉轮速死区，分开速度大小与方向约束，加入舵向速度前馈，关闭未使用离屏视图，快捷启动防重复实例。该时序为引擎内响应，最终用户手感仍需以最新窗口反馈为准。

最终窗口已重新打开并观察到215–229FPS；真实G/X/Z/R键验证通过，已R复位留在出生点。gui-verification.json/gui-input-evidence.json记录实际UI观察；没有宣称完整OS输入延迟测量。

车辆包drive-profile.json重启直接读取。本机E:/27UL_Sentry_chassis_controllers-main/27UL_Sentry_chassis_controllers-main的12源文件只读；早期数学适配验证900组便携案例，后续原LK ROS节点与电流反馈已单独接通，见上文。手动操作见docs/guides/swerve-gyro-controls.md。下述旧接触波浪成功属于此前11部件基线。

**波浪路修复已通过原生专项验证。** 已启用原尺度11部件/9刚体接触悬挂与轮驱动；修复电机方向、粗凸包填平凹槽和关节质量调节造成的异常支撑。红蓝两侧完整3.4m波浪路在1m/s及0.35m/s指令下均前进/制动/倒车通过（wave-native-09/10-slow）。8项物理操控、瞄准射击与实体墙阻挡通过（integration-02）。默认手动/训练启动器切换接触模式；全启动流水线和缺失碰撞自动重建通过。参数仍为演示值，独立舵轮转向、实车标定和全场所有路线尚未验收。详见docs/guides/contact-vehicle.md。

Play-Training.cmd已实际显示RMUC2027训练HUD及三个靶，真实鼠标触发发射/扣弹。左键发射，装甲命中扣HP，目标清零击倒；有弹量、热量/冷却、射速、倒计时、胜利/超时锁定和R重置。橙色框为预计弹道落点。

artifacts/gameplay/combat-03：14/14检查、10PNG、退出0；control-regression-01：18/18旧操控、10PNG。rmuc-training-01默认地图运行/截图。参数是明确演示值，未称官方RM赛季规则。ROS桥后续已单独实现；动态敌车、联网和完整裁判仍待实现。

## 原战雷地图状态（后续）

用户进一步指定直接使用东欧/弗兰德斯原地图。两图HM2地形与RIGz存储布局已解出。弗兰德斯659种模型、917贴图/数据图、616原碰撞资源已解码；assets/maps/wt_vlaanderen准备726视觉/639碰撞FBX，所有45675物件保留，离线核验通过。完整UE导入器和小场景API检查已完成，原图整体导入、专用材质效果和实际驾驶仍待验收。

旧解包器的32位/64位计数、未压缩MatVData、LOD dump长度与顶点声明适配已有实际回归。多UV/混合叶片顶点/原始材质索引与bounds是当前保真检查；记录在wt-existing-map feature和artifacts/maps/wt_existing。原先CDK裁剪只是历史测试，不能替代这次原图需求。

ROS2桥接已做消息/环境调查：TwistStamped接口仍成立，当前环境缺tf2_msgs。Python sidecar已在ros2_ws/src/rmsim_bridge实现并通过12项离线协议/socket检查；UE服务端和原生ROS端到端仍未完成。

## 已验证

1. UE5.8.4在E:\simulator-tools\UE_5.8，原生Win64 Development Editor构建通过。
2. 7个真实FBX部件、11材质导入通过。实际UE尺寸与源几何误差最大约6.32e-6cm。29个原始文件哈希未改变。
3. 最新运动/碰撞检查：artifacts/unreal/2026-11-15/probe-07-final-controls，9/9通过、6PNG可解码。
4. 最新操控检查：artifacts/unreal/2026-11-15/controls-04-aim-fix，18/18通过、10PNG可解码。它使用与真实键鼠共用的ApplyManualInput，覆盖油门、刹车、世界瞄准、自由观察、切镜、变倍、失焦和快速瞄准。
5. 实际GUI已显示完整车辆并观察到驾驶/转向；wt-input-aim-gap-before-fix.json保存真实输入事件及位姿，修复前后证据分别留存。
6. Windows原生ROS2 Humble双进程5/5消息、ament_python构建/执行通过，尚未接完整算法。
7. 离线车辆包工具26测试中24通过、2个Windows符号链接权限跳过；实际junction测试通过。
8. 平地map包两图各9项运动检查，真实geometry/spawn/region与占用导出一致；全车重叠出生在输入前拒绝。
9. RMUC2027静态场地14对mesh导入，默认入口实际GUI可见。terrain-final-02：7项与4PNG通过，出生地面倾斜约1.5°。
10. WT CDK 512m地形裁剪，同源运行时；terrain-final-03：hillside出生5.214°、7项与4PNG通过。完整游戏足球场/训练场bin尚未转换。
11. 地形改动后的flat-regression-after-terrain保持18/18操控、10PNG；最新转向保护仅作用地形路径。实际导入bounds与支撑法线有独立核对。

## 当前交付

- Play-ManualVehicle.cmd：本机直接打开RMUC2027手动样例；可传另一张map.json及出生点ID。
- 普通/小陀螺均WASD镜头相对平移，Q/E普通旋转，G切换自转，Z/X调速，Ctrl慢行，Space制动；鼠标瞄准、C自由观察、Shift瞄准镜、右键及滚轮变倍。
- 手动相机pivot固定在车顶上方，默认5m距离；视线不再被自身车体占据。
- MouseSensitivity=0.04度/原始计数，yaw540度/s、pitch360度/s，可在DefaultGame.ini修改；未标定为真实机构性能。
- 支持原生接触舵轮、旧运动学诊断、单车射击训练和可选ROS速度/相机桥。真实接触未实车校准，完整比赛规则、sentry算法与联机仍未完成。地图源/工具/证据按assets/maps、tools/maps、artifacts/maps及artifacts/sources分类。

## 本次用户反馈与修复

用户报告橙色炮口框不跟白色准星、有延迟且鼠标灵敏度低。controls-03-aim-gap-repro实际复现切回第三人称3.089893度偏差，退出2。

修复相机视差重新投影、鼠标重复缩放/平滑与低跟随速率后，controls-04-aim-fix切镜误差0.000001度；30度目标跳转经0.1秒固定仿真步后炮口误差0.000830度。该数值是引擎行为回归，不是端到端真实输入延迟测量。问题记录在.codestable/issues/2026-11-15-aim-reticle-parallax。

## 工程基线

源资产E:\WarThunderModding只读；本地导出位于assets/vehicles/aim_sentry。构建调用外部UE/VS需要沙箱外授权，不能把权限错误当代码错误。原生Humble为RoboStack Python3.14构建，不与apt Python3.10扩展混用。

已修复的历史问题：UE V5/V7构建设置、Role成员重名、相机frustum污染车体bounds、视觉轮转导致错误地面碰撞、PowerShell UTC误判stale、PNG线性/sRGB输出。详见每日变更。

## 下一步与未完成范围

- 继续根据实际试驾反馈调整手感；不要凭一次截图声称准确物理或最终画质。
- 将车辆/场地导入能力扩展到通用包、正确驱动与物理标定。
- 再连接同源sentry_ws、添加射击裁判和联机。
- 原生Ubuntu按最新指令后置；已准备同源地图/车辆重建脚本和docs/guides/ubuntu-offline.md，仅语法/预检，不记为双平台通过。

手动Windows首样例已有分项验收；整个长期目标保持active、未标完成。
