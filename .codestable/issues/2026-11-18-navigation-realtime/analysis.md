---
doc_type: issue
status: in-progress
updated: 2026-11-18
timezone: Asia/Shanghai
baseline_main: 6872798
working_branch: feat/navigation-speed-20261117
evidence_cutoff: engine-build-02
---

# 原 sentry_ws 高速导航与标准实时运行

当前任务是让选定车辆的 sentry_ws 配置决定导航速度、平滑变速、路径跟踪和避障，并在完整传感器规格下实现标准 1× 仿真。当前源码已完成并行激光查询、真实时间节拍、扫描相位和速度输入职责调整；截至本记录，新的完整组合正在进入原生运行验收。

已验收 main 为 6872798。工作分支原提交 13f1961 之后还存在未提交的诊断试验和本次修复，本文件保存其来源与结果。此前 6 轮高速失败保留在[上一问题记录](../2026-11-17-navigation-source-realtime/sentry-nav-speed.md)和[原检查点](../../../artifacts/navigation/nav-speed-20261117/checkpoint-summary.json)。

## 当前约束

| 项目 | 保持的要求 |
|---|---|
| 导航上限 | 当前 AIM 源 ROS YAML 的前进上限 3.0 m/s；后退、横移及平滑器参数同样由所选 ROS 配置持有 |
| 参数所有权 | 原 sentry/Nav2 控制器、速度平滑器、规划器和代价地图继续运行；车辆可以提供自己的 NavigationSource |
| 激光 | 原位置、随 yaw_link 转动的 Mid-360；200000 射线/仿真秒，10 Hz；原自身遮挡与光学窗口 |
| IMU | 同一实际安装父节点，200 Hz，SI 单位与实际采样时间 |
| 时间 | 5 ms 同步游戏步、真实 Chaos 时钟；渲染目标 60 Hz；倍率来自单调墙钟与求解器时间的独立比较 |
| 车辆物理 | 每模块 2000 N/m 刚度、140 N·s/m 阻尼；质量、50 N 预载、65 mm 行程、源轮胎与真实接触保持 |
| 评测 | 真值用于独立测量；定位输入保持原 Point-LIO、AMCL 与真实模拟传感器 |
| 保护 | 输入有效期、断线停车、会话/epoch 隔离及导航不开火保持 |
| 验收范围 | Windows 原生优先；Ubuntu 原生、实车标定、全场与多车分别验收 |

3.0 m/s 是源配置的速度上限。路线实际峰值、加减速过程、地形和接近目标阶段另行测量。

## 已确认的问题与证据

### 1. 串行激光查询占满单线程时间预算

200k 射线运行的 runtime.log 中，RMSIM_LIDAR_TIMING 记录每 5.005 仿真秒的射线耗时：

| 既有运行 | 激光消耗的墙钟时间 |
|---|---:|
| rhi-profile-brake-01 | 5.28–5.56 s |
| nullrhi-optical200k-01 | 5.09–5.19 s |
| render60-lidar200k-01 | 5.14–6.21 s |

相机同期约消耗 0.25–0.27 s / 5 仿真秒。200k 激光在关闭渲染的诊断中已经占用超过一个完整墙钟秒/仿真秒；并行查询因此成为本次主要性能修复。

原始证据均位于 artifacts/navigation/nav-speed-20261117/<运行名>/runtime.log。完整日志留本机，运行来源和精简验证记录入库。

UE 5.8 本机源码已核对：WorldCollision.cpp 的 LineTraceSingleByChannel 进入 RaycastSingle；SceneQuery.cpp 的 FScopedSceneReadLock 覆盖场景查询及结果转换；ChaosInterfaceWrapper.cpp 使用求解器外部数据的 ReadLock/ReadUnlock。普通同步 PostPhysics 工作线程读取同一 GT 场景数据。

### 2. 固定时间步绕过 SetMaxFPS 的等待路径

既有 SimRealTimePace 只设置 SetMaxFPS(200)。本机 UE 5.8 UnrealEngine.cpp 的固定时间步分支直接设置逻辑增量，最大帧率等待位于另一条分支。paced-render60-lidar75k-01 日志中，部分 5.005 仿真秒只经过约 3.3 墙钟秒，呈现约 1.5× 的局部加速；该试验的短窗口平均值 0.953295× 同时包含负载变慢和局部过快。

本次以单调墙钟截止时间约束实际固定步执行。物理时钟继续随实际求解步推进；性能不足时保留真实落后量。HUD/遥测倍率使用时间窗口累计增量比值，取代瞬时倍率的平均。

### 3. 激光扫描边界每轮多等待一帧

Chaos 接收 float 步长。float(0.005) × 20 = 0.0999999977648 s，旧判断的 1 ns 容差不足；旧代码随后以当前物理时刻重新设置 FrameStart，重复形成 21 × 5 ms 周期，约 9.5238 Hz。

新增 Sensors/ScanCadence.h，以连接时的物理时钟起点和扫描序号决定截止时间，使用 1 µs 分组容差。FrameStart、FrameEnd、每个点的 offset_time 保留实际采样时间。长间隔只结束一个已有观测窗口；重置与重连重新建立相位。

### 4. 导航速度输入还经过手动输入整形

原 ROS 速度经过 Nav2 velocity_smoother 后，又进入车辆手动模式的加速度/jerk 整形。这个路径会再次改变上游已经平滑的轨迹和停止时序。

当前增加 external_shaped_velocity 输入模式：导航目标直接进入车辆物理执行层，轮组与真实接触继续决定实际运动；手动模式保留原输入整形。明确的全零命令调用车辆包的主动制动。当前组合的制动距离、路径偏差与恢复行为由新一轮原生测试确认。

### 5. Point-LIO 参数来源与运行快照

原接入初期读取 5 项滤波参数；随后诊断已增加原估计器的输入模式、饱和阈值、测量协方差与 ivox 分辨率。本次将读取改为启动时一次性校验、哈希与快照，后续状态发布和会话重建复用同一份值，避免每 100 ms 重读 YAML 和运行中途参数漂移。

原始加速度饱和阈值按源 acc_norm 转为 SI。Estimator.cpp 中 IMU 残差已经归一化，相关测量协方差保持源值。实际安装外参、传感器频率、话题和重力仍来自本车实际传感器接口。完整支持字段以 sentry_pointlio_source.py 为准。

高速终点偏差在不同诊断配置下表现不同，当前仍需在完整规格和新速度执行路径下复测。所有失败数据继续保留。

## 13f1961 之后已有的原生诊断

下表目录统一位于 artifacts/navigation/nav-speed-20261117。位置门限为 0.25 m；原取消检查停车距离门限为 0.20 m。单项、原 14 项 action gate 和严格实时 gate 分别记录。

| 运行目录 | 单点误差 m | 双航点终点误差 m | 取消停车 m | 已有结论 |
|---|---:|---:|---:|---|
| diagnostic5ms-01 | 0.835476 | — | — | 单点失败，action status 6 |
| diagnostic5ms-02 | 0.179319 | 0.184659 | 0.225312 | 到点通过，取消停车失败 |
| pointlio-source-cov5ms-01 | — | — | — | 栈就绪后 action 等待超时 |
| imu-input-01-console | 0.778766 | — | — | IMU input 诊断单点失败 |
| imu-output-voxel-01-console | 0.199490 | 0.200210 | 0.206616 | 到点通过，取消停车失败 |
| rhi-profile-brake-01 | 0.198109 | 0.202501 | 0.171745 | 200k 外部 YAML 组合的原 14 项 action 检查通过；实时仍低 |
| rhi-lidar100k-01 | 0.188655 | 0.206283 | 0.185715 | 100k 诊断的原 14 项通过 |
| rhi-lidar75k-01 | 0.186394 | 0.200911 | 0.194564 | 75k 诊断的原 14 项通过 |
| rhi-lidar50k-01 | 0.208653 | 0.259269 | — | 50k 双航点终点失败 |
| render60-lidar200k-01 | 0.184453 | 0.226242 | 0.211013 | 200k 到点通过，取消停车失败 |
| paced-render60-lidar75k-01 | 0.203971 | 0.201469 | 0.197800 | 75k 原 14 项通过；旧时间节拍存在局部超速 |

每行的直接来源为该目录 independent-observer/verification.json。部分旧检查的取消触发时刻接近起步，新的验证还需覆盖已有较高实际车速时的取消/停车。

| 运行目录 | 旧只读时钟观测倍率 | 观测范围 |
|---|---:|---|
| imu-input-01-console | 0.697465× | 短窗口 |
| pointlio-source-cov5ms-01 | 0.657197× | 短窗口 |
| nullrhi-diagnostic-02 | 0.903991× | 关闭渲染的隔离诊断 |
| rhi-profile-brake-01 | 0.546913× | 完整 200k 激光 |
| rhi-lidar100k-01 | 0.709561× | 降采样诊断 |
| rhi-lidar75k-01 | 0.935386× | 降采样诊断 |
| rhi-lidar50k-01 | 0.850014× | 降采样诊断 |
| render60-lidar200k-01 | 0.596900× | 完整 200k，旧 viewport 节流 |
| paced-render60-lidar75k-01 | 0.953295× | 降采样，旧 SetMaxFPS 节拍 |

倍率来自各目录 clock-01/verification.json；这些旧文件的 status=measured 表示观测完成。新的正式实时 gate 使用至少 60 s 和逐 10 s 窗口。

其他保留记录：

- nullrhi-diagnostic-01 因 UE 退出码 3 启动失败，后续加入 NullRHI 相机读回保护；NullRHI 保留为性能隔离入口。
- nullrhi-optical200k-01 保留实际 200k 耗时日志，未完成该目录的时钟/路线验收记录。
- optical-native-01/tcp/verification.json 为 9 项激光接口检查通过。
- paced-render30-lidar75k-01 只有会话状态及日志，未找到完整动作或时钟 verification；旧 status.json 的 ready 是当时状态。
- variants 中 150k 配置文件存在，未找到对应完整运行验收。每个诊断配置和原始失败均保留。

### 外部诊断配置来源

原 14 项通过的 rhi-profile-brake-01、100k、75k 及 paced75k 使用 variants/imu-output-voxel-01.yaml：use_imu_as_input=false、point_filter_num=4、体素 0.15 m。当前默认源 ROS YAML 仍为 point_filter_num=8、体素 0.2 m。两组配置在来源表中分别记录。

| 配置 | SHA-256 |
|---|---|
| variants/imu-input-01.yaml | 0192f20fafd050b1171797c920c755245aeefd3b67af29f00e0fde6ca4c758c7 |
| variants/imu-input-voxel-01.yaml | 286ffe433fb52aaa1b10c9200132b2e128379a675405bd328ba572d3e9641258 |
| variants/imu-output-voxel-01.yaml | a5972f12f46b8f527cbee2a6b2a47a993b1fb3d0c8bcafc271ce148019b32a63 |
| variants/mid360-100k-diagnostic.json | 1d0306c5aa76e640d6a59f4bfc392cbc1ad04c0c7a590982f747bbd72bc35edb |
| variants/mid360-75k-diagnostic.json | 0891b9a3b0451ff4f07693af1bdc0d48092e530586c6f19a4cede41bd3a31c3c |
| variants/mid360-50k-diagnostic.json | f2e68415096464617222f1e657fc5ec1791d634ba9a17e19f9dfd3cbf56e370d |
| variants/mid360-150k-diagnostic.json | 5c39309d2b0e2e78d9b3eca4a63a695c215a6c34a99e28abe7ea815b1f0b3fb3 |

正式传感器目标保持 200k。NullRHI、降采样和外部滤波变体继续作为诊断证据管理。

## 当前实现与验证状态

| 修改 | 当前状态 |
|---|---|
| 分阶段 ParallelFor 激光查询 | 已编译；游戏线程准备射线并处理材质/噪声/序列化，工作线程仅查询；16 次光学处理与自身遮挡保持 |
| 串行查询对照 | LidarSerialQuery 可独立选择原串行路径 |
| 并行一致性校验 | VerifyLidarQueries 比较前 4096 条射线的全部光学查询；命中对象/面/距离与撞击点一致性门限 1e-4 cm；失败立即终止；原生结果待运行 |
| 相位固定扫描 | 生产 ScanCadence 的 MSVC 36 项检查通过；真实 ROS 发布频率待运行 |
| 真实时间节拍 | 单调墙钟截止时间与实际固定物理步；完整负载下 1× 验收待运行 |
| 独立渲染节拍 | RMSimGameEngine 在 FSceneViewport 构造前选择持久场景纹理；RMSimViewportClient 以相位截止时间调度 60 Hz，并发布实际 Draw 次数 |
| 上游平滑速度接入 | external_shaped_velocity 模式已编译；真实轮组/到点/取消/恢复回归待运行 |
| Point-LIO 配置快照 | 已实现并有离线契约检查；新完整组合待运行 |
| 严格观测器 | 要求完整时间窗口、会话连续、真实扫描/IMU计数、掉样计数及实际场景渲染遥测 |

当前已观察到的验证：

- [offline-01](../../../artifacts/navigation/realtime-20261118/offline-01/verification.json)：143 项，141 通过，2 项 Windows 符号链接权限跳过，失败/错误均为 0。
- [engine-build-01](../../../artifacts/navigation/realtime-20261118/engine-build-01/verification.json)：Win64 Development Editor 构建退出 0，164.77 s；模块 SHA 6bbfc81f5f06792c3c8c9defeb30a0e3c6960b12af4402231a6678ebe7f935ee。
- [engine-build-02](../../../artifacts/navigation/realtime-20261118/engine-build-02/verification.json)：加入扫描相位、持久场景目标和遥测后的构建退出 0，24.06 s；模块 SHA 1527d6f52fce3a9a0fa27e54654860b4d0a07ea527c4209953fa1f91372f558c。
- [scan-cadence-native-01](../../../artifacts/navigation/realtime-20261118/scan-cadence-native-01/verification.json)：MSVC /std:c++20 /W4 /WX /O2 编译/执行均退出 0；36 项检查覆盖真实 float 5 ms、7/10/30 Hz、抖动、非零起点、间隔、重连和 Livox 偏移边界。10 Hz 案例 100 仿真秒测得 9.999499 Hz；此为生产调度器的独立原生测试。

各次修改前的源码快照与 SHA 分别保存于 artifacts/navigation/realtime-20261118/baseline-before-*。CodeStable 当前保持 in-progress，新的完整原生组合没有提前标记通过。

## 下一验收顺序

1. 完整 200k 激光下运行 VerifyLidarQueries，检查串/并行命中一致性，保留日志与模块哈希。
2. 默认源 sentry 配置下，以完整渲染运行单点、往返双航点及较高实际速度取消；真实终点门限仍为 0.25 m。
3. 在导航运动与传感器负载持续期间，完成至少 60 s 实时观测：整体及每 10 s 窗口 0.98–1.02×；实际场景 Draw 57–63 Hz；真实激光 200k/10 Hz、IMU 200 Hz 和零掉样/重置计数。
4. 重跑手动、瞄准、真实 Mid-360、ROS 速度和原 LK 兼容检查，覆盖实际 R/断线后的会话重建。
5. 按新结果更新当前进度、架构、变更记录与验收汇总，再决定正式发布。已有通过与失败继续保留，当前工作分支可提交保存。

原生 Ubuntu、多车、完整裁判/权威联机、完整车辆包即插即用及实车物理/传感器标定继续沿长期 goal 推进。

## 2026-11-18 后续原生诊断：rmuc-03 / rmuc-04

rmuc-03 使用 build03（关闭四处无消费者 overlap 查询）、collision small batch=1、原200k射线/200Hz IMU、60Hz场景目标与实时节拍。运动 SyncBodies 从约1.4ms降至0.08–0.13ms，窄阶段碰撞仍有2.49ms；每个5ms帧执行两个物理子步。单点导航失败：19.31s局部定位先偏离，30.15s实际车体才倾斜超过30°。首次明显偏离时车速约0.237m/s，roll/pitch为−3.50°/+1.88°。此证据排除了把本轮根因归为先高速翻车。低频里程计角速度预测增加了实际云台追赶脉冲，已回退至快照SHA01e5e1af…；预测实现和六项离线测试保存在rejected-follow-prediction，未计入现役验收。

rmuc-03后期独立clock曾报告0.999524×，其窗口在action结束150.40s后才开始。该记录属于静止测量，不能计入运动验收。measure_console_clock.py现增加每1s起点的滚动10s窗及原子measurement-state；协调器等待measuring后才发动作。汇总器要求clock墙钟/仿真双区间完整覆盖动作trace，并核对DefaultEngine.ini身份。

rmuc-04把MaxSubstepDeltaTime改为0.005，保持bSubstepping、16最大子步、全部几何/CCD/32-8迭代、2000/140悬挂、200Hz IMU和20万射线。CSV原生确认15000个热身后帧均执行一个真实5ms物理步。运动31.195665墙钟秒推进31.199999仿真秒，倍率1.000139；913个滚动10s动作窗口为0.991222–1.009197。完整90sclock倍率0.999966993，全部原时间与滚动时间门限通过。

rmuc-04原14项联合检查全部通过：单点误差0.228708783m，两航点终点误差0.231876227m，未遗漏航点；取消后行驶0.085482040m，后续漂移0；云台跟随中位误差0.018474646rad，云台/安装点云/IMU持续输出。当前构建和配置尚待完整兼容性回归。

同一90s窗口的场景Draw平均57.926Hz，最低滚动10s约56.105Hz，未达到既定57–63Hz门限；该窗口后段处于静止，不能把动作段平均58Hz替代完整绘制验收。

新增PointLIO原生回调计数证明：rmuc-04至sim37.415s实际接收4487条IMU，按200Hz时间戳推算缺2575条，约36.5%；重复/倒退均为0。仿真传感器自身计数保持完整。正在把common.imu_qos_depth接入源YAML，默认5、选定simulation配置试128；先只改变PointLIO订阅端，保持发布端及滤波算法以隔离队列影响。

原生构建记录：engine-build-03 DLL e113f608…；bridge-build-04完整源码/安装一致，恢复原跟随；lio-build-03新增只读计数exe cb9760df…。前两次PointLIO构建分别因未初始化MSVC标准头环境及cmd引号失败，均保留；同进程调用vcvars64.bat后原生构建通过。bridge-build-03曾因回退源文件mtime早于安装文件导致未复制，哈希门禁发现后触碰源mtime并在bridge-build-04重新构建通过。

此时状态为in_progress：真实1×时间与本轮完整路线已有证据，后续需在补齐收包/绘制修复的最终同配置下重复路线、最高速、可见RViz与接触/旧功能回归，再形成发布汇总。原始报告均在artifacts/navigation/realtime-20261118对应目录，失败记录保持。

## 精度与短时实时性复核（2026-11-18，后续工作中）

所选 Simulation 源恢复到点容差的控制权：适配层固定 0.10 m 仅用于 legacy；当前源选择 0.05 m。完整 AMCL/scan 可选块从所选车辆 YAML 读取，旧源缺块时沿用原 reference。修改前后实际配置逐项比较，除 XY 容差 0.10→0.05 m，其余数值相同；13 项来源与控制契约检查通过。源 SHA 为 `d8e12b1f0637ea3a4086362ea21a46d2c34a2551888a3a70d19dad53f08d26d9`，bridge-build-07 退出 0。

新增只读 rich observer 保存实际 Path 端点、AMCL/odom 完整姿态、TF 与原索引 LaserScan。原 14 个检查调用及 0.25 m/取消门限保持，独立 precision 报告另外计算 0.10 m 目标。真值只进入评估。

| 会话 | 原导航 | 单点/末航点实际误差 m | 90 s 时钟 | 说明 |
|---|---:|---:|---|---|
| rmuc-09 | 失败 | 0.186709 / 0.285641 | 32/32，平均 0.999865484 | 默认无 CSV；容差仍被适配层固定 0.10 |
| rmuc-10 | 14/14 | 0.160071 / 0.192976 | 31/32，平均 1.000033150 | 源容差 0.05；滚动窗最大 1.029675149，独立高频复核也失败 |
| rmuc-11-highqos | 未测 | — | 未测 | 自有命令路径 guard 在应用策略前拒绝；已清理，保留失败记录 |
| rmuc-12-highqos | 14/14 | 0.145636 / 0.141310 | 30/32，平均 1.000088893 | 仅自有 UE HighQoS+保留计时器精度；滚动 0.974508741–1.027232664，未采纳为生产改动 |
| rmuc-13-cpuset | 14/14 | 0.158774 / 0.144367 | 32/32，平均 0.999999613 | 动态性能核 CPU Sets，滚动 0.987762426–1.015691736，Draw 59.972724 Hz；正接入正式入口并等待复验 |

上述四轮已完成的 precision 10 cm 诊断均失败，不能记为达到 10 cm。rmuc-10 原生 PointLIO 收样 19/19，8012 份应收/收到/接受一致，无缺口、重复或倒退；startup serial/parallel oracle 4096 rays / 8010 scene queries / 最大误差 0 cm。

### 已排除与已确认

- rmuc-10 全部 74 条规划路径末点符合 5 cm 栅格中心，最终 `[9.425000349,6.375000215]` 相对请求 `[9.45,6.4]` 偏移 35.354940 mm。精确 Nav2 1.1.20 backtrace 已重复目标格中心，不能据 linearInterpolation 未追加末点就添加重复点；没有额外丢失末段的运行证据。
- scan 均为 base_stabilized，同 stamp 动态 TF 与 odom 的姿态补偿一致；没有发现重复安装外参。剩余倾斜的平面/完整三维投影端点差最大 7.15 mm，不能解释十余厘米偏差。
- rmuc-10 同时间戳 AMCL−实际位置：单点 `[+0.065162,-0.104415]` m（模 0.123080），末航点 `[+0.070965,-0.193280]` m（模 0.205896）。车辆最终零速度指令后的 XY 位移约 2.901 mm；原平滑器减速段保留。
- 复现实际 Nav2 map_lib.dll 距离场和 1.1.20 likelihood_field 评分，125 帧严格同 stamp、2244 条实际有限选中 beam。估计姿态分数高于独立实际姿态：单点 35/39 帧，航点 53/81 帧。最后单点 22 条 beam 在偏移姿态下全部落入占据区域，距占据格均为 0。当前可通行性 PGM 的填充区域存在宽评分平台。
- 原 scan 每帧 720 方向，均值约 138.9 条有限 range；原 max_beams=100 固定每 7 个索引抽取后，实际有限 beam 均值约 19.7 条。没有通过重新采样制造额外观测。

### CPU Sets 实验边界

通过当前 Windows GetSystemCpuSetInformation 动态识别性能类，选用本机 8 个性能核的 CPU Set ID；没有写固定逻辑核编号、硬 affinity mask、实时优先级或机器电源策略。只操作经项目路径、会话日志路径和活体 handle 核对的自有 UE；该进程退出即结束策略。当前生产启动流程尚在接入，rmuc-13 不能直接充当用户入口完整验收。

实现依据为 Microsoft 的 [CPU Sets](https://learn.microsoft.com/en-us/windows/win32/procthread/cpu-sets)、[GetSystemCpuSetInformation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemcpusetinformation) 和 [SetProcessDefaultCpuSets](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessdefaultcpusets)。HighQoS 对照依据 [SetProcessInformation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation)，实际结果未证明改善，因此未纳入默认实现。

下一步：生成与实际几何/扫描语义匹配的独立定位地图候选，保留导航可通行地图和原避障链；用原观测离线评分筛选后原生复验。随后完成当前入口的实时性、导航、手动/悬挂兼容和可视化验收，再合入用户指定 GitHub。所有旧失败记录保留。
