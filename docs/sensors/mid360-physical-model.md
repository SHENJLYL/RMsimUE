# MID-360 物理参数、坐标与数据链

更新：2026-11-20。配置：`assets/vehicles/aim_sentry/sensors/mid360.json` 和
`mid360-imu.json`。标准启动入口为 `Play-ManualVehicle.cmd`、`Play-Navigation.cmd`，
车辆动力学选择 MuJoCo native，模型清单为
`assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json`。

本模型分别记录公开器件指标、源装配几何、仿真时序和测量近似。
`calibrated=false` 与 `physical_model.measurement_calibrated=false` 进入运行状态。
完整棱镜轨迹、905 nm 材料响应和工厂校准参数需要实测数据。
版本 3 profile 约束见 [LiDAR schema](../../schemas/lidar-runtime-v3.schema.json) 和
[IMU schema](../../schemas/imu-runtime-v3.schema.json)。

## 1. 公开指标与配置

| 项目 | 公开依据 / 默认配置 | 仿真含义 |
| --- | --- | --- |
| 视场 | 水平 360°；垂直 −7° 到 +52° [S1] | 在设备自身坐标系定义，保留源装配倾角 |
| 点率、回波 | 200,000 点/秒，第一回波 [S1, S3] | 每 5,000 ns 一个发射槽；无回波保留槽位 |
| 线号 | 驱动给 MID-360 设置 4，逐点 `i % 4` [S4] | `line=0..3` 为通道标签；200k 为设备总点率 |
| 点云发布 | 默认 10 Hz；支持 5 / 10 / 20 / 50 / 100 Hz [S5] | 每帧分别 40,000 / 20,000 / 10,000 / 4,000 / 2,000 个槽 |
| 量程 | 0.1 m 起；40 m @10%、70 m @80%，100 klx [S1] | `max_range_m=70` 为裁剪上界；材料和入射角另行限制 |
| 距离误差 | 1σ ≤2 cm @10 m，≤3 cm @0.2 m；80% 目标、25°C [S1] | 用规格上界构造可复现噪声近似，分布需要标定 |
| 角误差 | 约 0.15° 的 1σ 上界 [S1] | 默认每角轴 0.15° 高斯近似，明确标为未标定 |
| IMU | ICM40609；默认 200 Hz [S1, S2] | 共用仿真时钟；SI 加速度和角速度 |
| IMU 满量程 | 假设 ±16 g、±2000°/s [S3] | 协议可选量程，实际固件配置需要识别 |
| 无回波 | 坐标 (0, 0, 0) [S2, S3] | 保留时间、线号；本项目同时写强度 0、tag 0 |

MID-360 使用非重复扫描。公开驱动的四个 `line` 标签没有定义四条固定俯仰角，
也没有对应四条独立软件线程。[S4]
`spherical_fibonacci` 是本项目的覆盖近似，状态标为 `spherical_fibonacci_approx`。
厂家完整随时间变化的光机扫描方向函数尚无公开可执行定义。

`frame_hz` 遵循官方驱动推荐的发布频率及 100 Hz 上限，表示点云组帧频率。[S5]
默认设备 IMU 为 200 Hz；较新协议还列出 500 / 100 / 50 Hz，
具体支持依赖型号和固件。[S3] 本 profile 保持 200 Hz。

## 2. 光学原点与源安装

官方坐标图以设备底面以上 **47 mm** 为光学原点，+Z 朝设备上方，
+X 背离侧面连接器。内置 IMU 在 LiDAR 坐标系的位置为
`[0.011, 0.02329, -0.04412] m`，两者坐标轴平行。[S2]

源 Blender 彩色镜罩的球面拟合中心距底面约 39.380395 mm。
提取器把光学原点放在原轴线上、底面以上 47 mm，
相对于球心沿 +Z 移动 7.619605 mm。
设备 +X 由独立连接器壳体和四颗面板螺钉确定：
螺钉中心平面法向经光学轴正交投影得到连接器朝外方向，其反向为 +X。
源 +Z 和约 45° 的安装姿态保持。

| 传感器 | `yaw_link` 内 xyz / m | rpy / rad |
| --- | --- | --- |
| LiDAR | [0.203510156093, 0.073759915267, 0.175951734002] | [−0.785399079323, −0.000000357300, −1.570754885674] |
| 内置 IMU | [0.188781535919, 0.062759287802, 0.128285686628] | 同上 |

数值由 `tools/assets/inspect_mid360_mount.py` 从源几何生成。
完整组件证据、轴矩阵、拟合残差、底面和源哈希保存在
[`mid360-source.json`](../../assets/vehicles/aim_sentry/sensors/mid360-source.json)。

- 源 `radar_mid360` 挂接 `bone_turret`，运行期 parent 为 `yaw_link`。
- 壳体底面至顶部 60.097166 mm，位于官方 60 ±0.5 mm 外形范围内。
- 光学表面 3,869 面、2,138 顶点；最大拟合残差 0.079282 mm。
- 独立连接器壳体 4,550 顶点；四颗面板螺钉各 2,308 顶点。
- 源 blend SHA-256：`4414ee2f57ef5604b8e9cd8cdf2b2386a424737271ed94c40abbad2e1ce18d37`。
- 源 rig_manifest SHA-256：`237ff1460a3bf14b819974757cbd461734ac38bdfb526931ee8e102a3709453f`。

提取器只读打开源文件，关闭源脚本执行，计算后核对 blend 和 rig_manifest 字节。
传感器坐标写入独立 profile。车体、云台、轮组与行驶配置继续使用既有来源。

真实 Point-LIO `parameters()` 由运行时两传感器 parent-relative pose 计算
`extrinsic_T=[-0.011,-0.02329,0.04412]`、`extrinsic_R=I`。
静态外参挂于 `yaw_link`，云台转动由动态关节 TF 提供。
坐标契约保持 `mount_schema=2`；测量 profile 升级为 v3。

## 3. 时间、组帧与线程

全部传感器使用仿真时钟。10 Hz 完整帧包含 20,000 个槽，
第一个槽相对时间为 0，最后一个为 99,995,000 ns。
`CustomMsg.timebase` 为帧起始时间，
`CustomPoint.offset_time` 为相对它的纳秒偏移。
`line` 按发射序号模 4 生成，零回波槽保持对应序号。

MuJoCo native 在 1 ms 子步提供真实机械状态。
光束在 5 µs 网格取发射时间，相邻 native pose 经位置线性插值、四元数 slerp，
保留车体平移和云台旋转导致的扫描运动畸变。
UE 场景射线使用当前世界快照。状态分别标为
`pose_time_model=native_substep_translation_lerp_quaternion_slerp`、
`scene_time_model=current_ue_world_snapshot`。
运动障碍物在一个 UE 世界步内的连续轨迹仍需场景历史或独立场景采样。

发射槽、MuJoCo 子步、IMU 采样和点云组帧采用各自的节拍，共用 MuJoCo 仿真时间与 epoch；画面按独立的墙钟绘制节拍调度。
点云以完整帧交付；队列默认为 8，溢出丢完整旧帧并更新统计。
ROS 输出保留既有 `<IfffBBBB` 20-byte 内部布局和 Livox CustomPoint CDR 对齐，
单位保持 m、ns、m/s²、rad/s。

官方 SDK2 分离探测、命令和数据 IO，ROS driver2 另有数据处理、点云发布、
IMU 发布线程。[S5, S6] 本项目按物理求解、UE 场景查询和 ROS/网络输出的工作边界执行；
UE 并行查询按连续 32 束分块，各块使用独立查询参数和作用域场景读锁，逐束保留原查询及内层读锁；材质判断和透明表面推进在游戏线程完成。
`line=0..3` 参与点属性，不参与线程调度。

MID-360 常见 Cartesian 单回波 UDP 包含 96 点，坐标 int32、单位 mm。[S3]
协议的 `time_interval` 描述首末点跨度，单位 100 ns；
本轮核对的 driver2 实际以点数 N 作除数，相关修正讨论见 [S7]。
本仿真直接按固定 5,000 ns 网格构造时标，桥接采用本项目 TCP/ROS 封装。
官方 LVX2 样例中相邻 96 点包起始时刻相隔 480,000 ns；
LVX2 未保留原始 UDP 的 `time_interval`，此样例用于核对总体点率。[S8]

## 4. 测量模型和假设

### NIR 材料与检出距离

`nir_material_reflectance` 按 UE 材质精确名称映射 905 nm 反射率。
缺省材料取 `nir_default_reflectance=0.1`，
并累计 `default_NIR_material_intersections`。
可见光 RGB 颜色不提供近红外标定。

材料反射率为 ρ、出射方向 d、单位表面法向 n；
开启入射模型取 `ρ_eff=ρ·max(0,(-d)·n)`，关闭取 `ρ_eff=ρ`。
两点约束的幂律模型为：

```text
k = log(70 / 40) / log(0.8 / 0.1) = 0.2691183073525347
R_detect = min(max_range_m, 40 * (rho_eff / 0.1)^k)
rho_eff <= 0 时 R_detect = 0
```

完整距离/反射率/入射角响应需要实测。
默认 10% 材料正入射下限于 40 m，80% 材料正入射下限于 70 m。
官方还指出垂直视场上部有效量程更短，完整角度响应曲线未数字化发布。[S2]
当前按上述标量公式求值。

### 距离、角度、强度

距离误差采用零均值高斯分布，标准差从
`0.03 m @0.2 m` 线性插值到 `0.02 m @10 m`，区间外保持端点值。
默认值取自规定试验条件下的规格上界；
实际分布、偏置、温漂和相关性需要标定。

报告方位角和俯仰角各添加默认 0.15° 标准差的零均值高斯误差。
噪声按 seed、全局发射序号、维度计算；
断连、无回波或调度变化保持后续同一槽的噪声一致。
场景检测使用 pencil ray。出射光斑、波束能量、混合像素、多径、
雨雾颗粒散射需要独立模型与测量。
0.1–0.2 m 实物距离值供参考；0.1–1 m 内细小、低反射目标的检出效果依赖目标。[S1, S2]

漫反射强度采用未标定的 `round(150*ρ)`，范围 0..150。
官方 151..255 表示逆反射；本模型的逆反射响应留给独立材料模型。[S2]
距离、入射角、饱和造成的强度变化需要标定。
旧顶层 `reflectivity` 保留供 v1/v2 兼容；v3 使用 NIR 结果。
默认 `tag=0`，当前仿真未产生雨雾、拖尾等置信类别。
零回波 xyz 和强度为 0，时间与通道保持。
自身不透明部件参与第一回波检测并遮挡后方，在量程和信号范围内正常返回自身表面；
透明镜窗按既有光学规则穿透。
`self_occluded_rays` 统计自身第一击中对后方的遮挡；当前未启用自体回波 mask，
因此 `rejection_counts.self_occluded` 为 0。盲区内自身击中归入 `below_min_range`。

### IMU

内置 IMU 采用其安装点的真实速度和姿态。
v3 在以 t 为中心的 5 ms 窗内差分 native 测点速度以计算比力，包含旋转偏心运动。
角速度由同一窗口两端的姿态差得到，避免把 1 ms 物理子步的振荡采成持续角速度。
采样时间仍为 t，等到 t+2.5 ms 的支持状态完成后发布。
满量程、量化、偏置、噪声均显式配置。
设备 profile 假设 ±16 g / ±2000°/s；偏置、白噪声和量化步长为 0，表示未加入该项。
底盘辅助 IMU 满量程为 0，保留既有无限幅行为。协议可选量程见 [S3]。

ROS 加速度保持 m/s²，Point-LIO 使用 `mapping.acc_norm=9.81`。
加速度饱和阈值根据所选源配置计算为 `source_satu_acc × 9.81 / source_acc_norm`；当前 simulation 源对应 **30 m/s²**。
驾驶能力限值由共同车辆 profile 生成，Point-LIO 的其余算法参数继续从所选 sentry YAML 读取。

## 5. 方向记录与理想对照

`mid360.direction_sequence_file` 默认为空。
提供完整方向 CSV 后，状态为 `recorded_direction_replay`，并记录文件 MD5。
表头固定：

```csv
offset_time_ns,azimuth_rad,elevation_rad,line
```

记录从 0 起，每行递增 5,000 ns，line 为行号模 4，行数为 4 的倍数；
角度遵守设备 FOV。运行时循环该有限方向记录。
真实零回波点只有零坐标，无法单靠它反求方向；
回放输入需要每个发射槽的方向观测或标定结果。

`mid360-ideal.json`、`mid360-imu-ideal.json` 使用修正后的机械坐标，
保留 v2 理想测量行为：LiDAR 为 40 m、无距离噪声，IMU 原偏置/噪声与无限幅。
旧 `lidar.json`、`imu.json` 字节不变。
实际选中配置由运行状态、profile 哈希和测试配置共同确认。

## 6. 再生成与验收

新测试环境安装项目的可选测试依赖（`jsonschema>=4`）：

```powershell
python -m pip install -e 'D:\simulator[test]'
```

源几何抽取和 profile 生成：

```powershell
& 'E:\blender\blender.exe' --background --factory-startup --python 'D:\simulator\tools\assets\inspect_mid360_mount.py' -- --output 'D:\simulator\artifacts\sensors\mid360-physical-20261120\geometry-review-01\mount.json' --profiles-dir 'D:\simulator\assets\vehicles\aim_sentry\sensors'
```

省略 `--profiles-dir` 时仅输出报告。随后用正式入口重建模型及来源封印：

```powershell
powershell.exe -NoProfile -File D:\simulator\tools\mujoco\Prepare-MujocoModel.ps1 -RebuildModel -GeometryDirectory D:\simulator\artifacts\integration\mujoco-ue-20261120\geometry-source-01
python D:\simulator\tests\control\test_mid360_physical_profiles.py -v
```

该测试覆盖公开坐标、源轴向、真实 Point-LIO 参数生成、schema、配置再生成和 SI。
Windows Blender 提取与这 6 项测试已执行。文件哈希、命令与测试证据保存在
`artifacts/sensors/mid360-physical-20261120/geometry-review-01`。
native 构建和实时 UE/MuJoCo/ROS 结果见相邻 integration 记录。

运行验收检查：物理 backend/模型封印；完整帧槽数及 5 µs 偏移；
零回波、4 个通道；IMU 200 Hz；云台运动时动态 TF、扫描畸变；
ROS 帧和 SI 单位；时标无回退；负载下溢出统计；
材料默认率、recording/model identity。
离线几何和配置测试与实际引擎记录共同构成验收证据。

## 7. 上游来源、版本与许可

本轮研究固定到以下公开来源。所列版本用于核对接口，
项目的既有 vendored 依赖继续由各自清单管理。

- **[S1] MID-360 产品规格**：<https://www.livoxtech.com/mid-360/specs>；
  [下载页](https://www.livoxtech.com/mid-360/downloads)。
- **[S2] User Manual v1.2，2024.04**：
  <https://terra-1-g.djicdn.com/851d20f7b9f64838a34cd02351370894/Livox/Livox_Mid-360_User_Manual_EN.pdf>。
  下载 SHA-256：`3f202c845330023d50eee086cc522918ec8b1279446c4286ea3d38722400cab2`。
  查阅坐标/输出、IMU、安装尺寸和规格页。
- **[S3] 官方 Ethernet Protocol v1.4.12，2026-09-21**：
  <https://livox-wiki-en.readthedocs.io/en/latest/tutorials/new_product/mid360/livox_eth_protocol_mid360.html>。
  文档覆盖 MID-360 家族型号，设备固件对各命令的支持需分别核实。
- **[S4] livox_ros_driver2 v1.2.8**，commit `21445540f0d100dc86a7e6df312dd70bbdb4afdf`：
  [comm.h 的 4 通道定义](https://github.com/Livox-SDK/livox_ros_driver2/blob/21445540f0d100dc86a7e6df312dd70bbdb4afdf/src/comm/comm.h#L82)，
  [pub_handler.cpp 时标处理](https://github.com/Livox-SDK/livox_ros_driver2/blob/21445540f0d100dc86a7e6df312dd70bbdb4afdf/src/comm/pub_handler.cpp#L125-L145)，
  [逐点处理](https://github.com/Livox-SDK/livox_ros_driver2/blob/21445540f0d100dc86a7e6df312dd70bbdb4afdf/src/comm/pub_handler.cpp#L370-L394)。
- **[S5] driver2 发布频率和线程**：
  [README](https://github.com/Livox-SDK/livox_ros_driver2/blob/21445540f0d100dc86a7e6df312dd70bbdb4afdf/README.md#L145-L154)，
  [ROS2 入口](https://github.com/Livox-SDK/livox_ros_driver2/blob/21445540f0d100dc86a7e6df312dd70bbdb4afdf/src/livox_ros_driver2.cpp#L127-L185)。
- **[S6] Livox-SDK2 v1.5.2**，commit `c0796f04c143143899c87a773d9f6b7136453c0b`：
  [device_manager.cpp](https://github.com/Livox-SDK/Livox-SDK2/blob/c0796f04c143143899c87a773d9f6b7136453c0b/sdk_core/device_manager.cpp)，
  [io_thread.cpp](https://github.com/Livox-SDK/Livox-SDK2/blob/c0796f04c143143899c87a773d9f6b7136453c0b/sdk_core/base/io_thread.cpp)。
- **[S7] driver2 时间间隔修正 PR #69**：<https://github.com/Livox-SDK/livox_ros_driver2/pull/69/files>。
- **[S8] 官方 LVX2 格式与样例**：
  <https://terra-1-g.djicdn.com/65c028cd298f4669a7f0e40e50ba1131/LVX2%20Specifications.pdf>；
  <https://terra-1-g.djicdn.com/65c028cd298f4669a7f0e40e50ba1131/Mid360/Indoor_sampledata.lvx2>。

许可依据为 [driver2 LICENSE.txt](https://github.com/Livox-SDK/livox_ros_driver2/blob/21445540f0d100dc86a7e6df312dd70bbdb4afdf/LICENSE.txt)
和 [SDK2 LICENSE.txt](https://github.com/Livox-SDK/Livox-SDK2/blob/c0796f04c143143899c87a773d9f6b7136453c0b/LICENSE.txt)。
所参考的 Livox 核心代码在对应 MIT 范围内，第三方部分有各自许可清单。
本页保存参数依据和实现选择；官方手册、样例与原始模型按原来源管理。
