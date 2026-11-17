# 当前进度

更新：2026-11-17，Asia/Shanghai。长期目标 active，完整要求见 `.codestable/goal.md`。当前优先 Windows 原生验收，保留 Ubuntu 同源、通用车辆包、可换地图、高画质、正式裁判和权威联机目标。

## GitHub 与当前悬挂

用户指定公开仓库 `SHENJLYL/RMsimUE`。origin 已配置，原本地main `8d85457` 及 `baseline/2026-11-17-pre-suspension`、`release/2026-11-17-suspension` 两个标签已实际推送；GitHub连接独立确认远程SHA。源码、配置、上游声明/补丁和精简证据入库，大资源和生成产物留本地。上传证据：`artifacts/repository/rmsimue-publish-20261117/verification.json`。

用户要求进一步提高悬挂后，AIM当前默认 **每模块2000 N/m、140 N·s/m**，上一轮1800/130。本轮只改这两个运行参数：预载50N、行程65mm、质量、轮胎源凸包、摩擦、舵向/电机增益与原生休眠不变。

同一UE模块下的100N载荷/卸载/4N·s冲击对照：附加载荷压缩15.6195→14.4467mm；卸载回弹峰值1.08465→0.77350mm；卸载稳定带时间0.295→0.250s；冲击反弹0.21319→0.02890mm。新配置重复两次结果一致，所有原验收门限保留。

**92项原生回归、48PNG全部通过**：坡面10、手动19、红蓝波浪各5、训练/复位10、高速5、自转稳定6、ROS11、原LK21。两次新悬挂专项各4项/6PNG，另有旧参数同模块对照。离线48项中46通过、2项Windows符号链接权限跳过。精简汇总已逐项检查真实结果及模块/profile/rig哈希：`artifacts/control/suspension-round2-20261117/release-summary.json`。

更硬的未通过候选和降低休眠阈值的失败实验均保留，后者已撤回。C++最终只增加悬挂样本 `chassis_awake` 字段。细节见 `.codestable/issues/2026-11-17-suspension-round2/suspension-round2.md`。实车标定与用户键鼠手感单独验收。

## 定位与导航接续

新2000/140悬挂下，原LK电流控制器、真实IMU/LiDAR、原Livox消息和原Point-LIO在RMUC同场联合 **11项通过**。短路线IMU点经一次初始姿态对齐的末端误差约6.15mm，评测真值独立。证据：`artifacts/navigation/suspension-round2-lio-lk-20261117/ros/verification.json`。

**RMUC波浪路线自主往返9项通过**，证据 `artifacts/navigation/rmuc-07-suspension-roundtrip/ros/verification.json`：原Point-LIO/AMCL/Nav2完成去程与返程，独立实际终点误差0.167615m和0.117199m。

**主动导航断线、重连及恢复后的往返14项通过**，证据 `artifacts/navigation/rmuc-09-bounded-recovery/ros/verification.json`：只终止本测试持有的bridge进程树；导航停止后在新session、相同epoch重建一次；重新定位误差0.124716m；实际停车距离0.085634m；1仿真秒内无旧目标重放/漂移；随后新目标去程和返程误差0.222153m、0.114775m。

首次 `rmuc-06` 因监督器等不到生命周期状态查询回复超时而失败。新增只读GetState/is_active查询2秒超时清理重试，rmuc-09实际记录3次查询超时后恢复；状态转换和导航目标不会由此重发。7项查询单元测试通过，最终离线全套55项中53通过、2项符号链接权限跳过；实际colcon安装与源码哈希一致。一个初始Y误填的调用已标aborted_input_error并保留，未计入成功。

最终重复 `rmuc-12-final-recovery` 在重连后的扫描TF链阶段超时：6个先行检查通过，整组failed。原Point-LIO已初始化、AMCL已active并收到地图，但pointcloud_to_laserscan因坐标变换不可用持续丢弃队列，未能播种初值。**自主往返已验收，重复会话恢复保持in-progress**。问题与下一诊断入口见 `.codestable/issues/2026-11-17-reconnect-tf-chain/reconnect-tf-chain.md`。

另修复Windows metadata UTF-8 BOM兼容，3项配置测试和rmuc-11错图保护2项通过，身份校验保持。统一成功/失败检查点：`artifacts/navigation/suspension-round2-20261117/checkpoint-summary.json`。

上述路线保持原0.25m真实终点门限，未改AMCL/规划器/控制器算法或输入真值。旧 `rmuc-05` 的0.254827m失败保留。下一技术优先项为重复重连时的TF/时钟/订阅链诊断，随后补实际R复位、更多路线和多机器人隔离。

## 已有可运行内容

`Play-ManualVehicle.cmd` / `Play-Training.cmd` 默认RMUC2027和原尺度AIM：约503.850×501.985×419.192mm，轮半径42.5mm；15视觉部件、13刚体、四悬挂/四舵向/四轮轴约束。WASD镜头相对平移，Q/E旋转，G自转，Z/X转速，Ctrl慢行，Space制动，R复位；鼠标瞄准、C自由观察、Shift瞄准镜。

训练包含真实弹丸扫掠、靶HP、弹量/热量、射速、倒计时和复位。原生ROS已接入速度、原LK电流反馈、高层云台/发射、相机、IMU、LiDAR和原Point-LIO；有效期、断线与会话复位分项跟踪。

RMUC2027和WT CDK地形已有运行记录；弗兰德斯原图资源准备完成，完整场景运行待验收。受限URDF工具和两个离线车辆示例已通过，AIM mesh-rig真实导入运行已落地；通用新车的惯量、关节和传感器运行扩展继续推进。

## 后续范围与环境

后续推进完整导航/恢复、自主视觉/Hub/云台电机环/行为树、传感器和动力学校准、正式赛季规则、多机器人权威联网、原生Ubuntu运行，以及原战雷完整地图/画质验收。

工作区 `D:/simulator`，UE5.8.4 `E:/simulator-tools/UE_5.8`，Windows Humble使用RoboStack Python3.14。外部原模型与控制器目录只读。实际运行参数从车辆包drive-profile读取，导入rig内联配置为历史快照。

旧进度保存在 `docs/progress/archive/2026-11-17-before-suspension-round2.md`。测试期间已关闭此前PID55380手动窗口；最终预览交接另行追加实际观察记录。

## 当前预览交接

本轮测试进程全部退出后，通过标准单实例入口恢复手动RMUC窗口PID48844。实际窗口截图已观察到RMUC2027场地、AIM车辆、253FPS、0km/h、小陀螺off；当前profile为2000/140，模块/profile哈希与悬挂验收一致。窗口留给用户试驾，未将截图观察计作键鼠手感或延迟验收。证据 `artifacts/control/suspension-round2-20261117/gui-preview/verification.json`，截图保留本地。
