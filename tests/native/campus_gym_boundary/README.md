# 校园体育馆：真实车辆封闭边界验证 fixture

本目录保存一次可复现的原生 Chaos 行驶测试：车辆从场内起步，通过轮电机接近南侧玻璃围墙对应的连续保护碰撞体，持续向墙驱动、制动，再倒车脱离。测试使用真实碰撞和原有车辆控制接口，运行中不修改车辆位姿。

**本 fixture 适用于当前 unified 工作树的车辆接触观测 API。它未集成到 clean main，不能把本目录或其历史结果视为 clean main 已支持这项实车测试。** clean main 的 `test_campus_gym.py` 验证场景、材质、固定关闭门和 96 个边界扫掠点，实际车辆行驶证据由本 fixture 单独提供。

## 文件与接口

| 文件 | 用途 |
|---|---|
| `CampusGymBoundaryProbe.cpp` | 六阶段原生车辆接触测试及原始样本、命中记录 |
| `integration.json` | `ImportLabGameMode.h` 与 `ContactProbe.cpp` 的最小接入片段 |
| `run_boundary.py` | 显式选择 UE 项目、准备独立场景副本、运行及保存证据 |

车辆代码须提供 `GetRecentHitAgainst`、`GetFoliageContactBounds`、`GetMotionGeneration`、`GetContactState`、`GetCommandOriginVelocity` 和 `SetContactDrive`。游戏模式须包含 `TickCampusGymBoundaryProbe` 及其 state，并在 `TickContactProbe` 中对 `-CampusGymBoundaryProbe` 做可选分派。复现 wrapper 在启动前检查关键声明与分派是否存在。

`integration.json` 描述接入位置，使用时应合并到当前源码，保留已有的其他 probe 分派。模块需要先构建，并将校园体育馆资源导入所选项目。wrapper 使用现有二进制，不承担编译或导入。

## 复现

等待其他 UE 预览、导航、编译或性能测量结束。在本目录执行，例如对已经构建并导入资源的隔离项目：

```powershell
python .\run_boundary.py `
  --engine-root 'E:\simulator-tools\UE_5.8' `
  --project 'D:\simulator\artifacts\maps\campus-gym-20261119\preview-project\unreal\RMSim\RMSim.uproject' `
  --map-file 'D:\simulator\assets\maps\rmuc2027\map.json' `
  --mesh-rig-file 'D:\simulator\assets\vehicles\aim_sentry\ue_mesh_rig.json' `
  --arena-manifest 'D:\simulator\artifacts\maps\campus-gym-20261119\preview-scene\arena-shell.json' `
  --output 'D:\simulator\artifacts\maps\campus-gym-20261119\boundary-reproduce-01'
```

每次使用全新 `--output`。`--arena-manifest` 可省略：wrapper 会先检查输入地图旁的 `arena-shell.json`，再按运行时约定向上查找项目的 RMUC manifest。默认超时为 180 个墙钟秒，可用 `--timeout` 设置 30–600 秒。

wrapper 自动完成以下准备与记录：

1. 检查是否已有 UE 进程；存在时保存失败原因并退出，由该进程的所有者处理后再重试。
2. 在输出目录中复制地图、校园 manifest 和 layout。只向地图副本加入出生点 `gym_south_guard`：ROS `(-11, -6.6, 0)`、yaw `-π/2`；原始地图保持原样。
3. 对所选项目的模块、模块索引、源码、配置、车辆 rig、manifest/layout 和已导入校园资源保存运行前 SHA-256。
4. 启动该项目的 `-ContactProbe -CampusGymBoundaryProbe`。超时或中断时只清理本 wrapper 创建的 UE 进程树，保留日志和已有证据。
5. 检查六个具名原生物理检查、精确墙组件命中、motion generation、不重置、逐 tick 场内余量、倒车脱离、96 个扫掠点及 PNG 文件，并比较运行后的输入 SHA-256。

程序退出码 `0` 表示本次 wrapper 所列检查全部通过；`2` 表示失败或前置条件未满足。`verification.json` 保存具体原因。脚本提供 Windows/Linux 可执行文件选择；本目录记录的实际运行平台为 Windows / UE 5.8.4，Linux 运行仍需独立验证。

## 实际行为与通过条件

初始出生点通过正常 Spawn/SnapToTerrain 初始化。之后由真实轮电机命令驱动，无 teleport、位姿覆盖或出界重置。

| 原生检查 | 行为及条件 |
|---|---|
| `physical_settle` | 制动静置 3 s；速度小于 0.15 m/s、车辆直立且尚未碰墙 |
| `actual_closed_perimeter_contact` | 目标速度最高 2 m/s；实际接近峰值大于 0.5 m/s、前进超过 0.5 m，取得精确南墙组件的真实命中 |
| `closed_glass_boundary_blocks_continued_drive` | 碰墙后继续施加驱动至少 1.5 s；验证持续新接触、低速保持及有限前进量 |
| `physical_brake_at_boundary` | 原生轮刹保持 2 s；速度低于 0.1 m/s |
| `physical_reverse_releases_wall_contact` | 以最高 0.75 m/s 目标速度倒车至少 0.6 m，并解除近期墙接触 |
| `final_stop_inside_original_field` | 再制动 2 s，车辆停止，motion generation 不变、恢复计数为 0 |

路径误差会用于小幅转向修正，2 m/s 是目标速度上限。报告中的 `peak_approach_actual_speed_m_s` 是接近阶段测得的峰值，不能用作接触瞬间的碰撞速度。

命中证据通过墙组件指针和近接触点匹配建立身份，来源为 `OnComponentHit`。`recent_exact_wall_contact_frames` 是满足近期接触条件的帧数；去重后的观测也属于每车体最近命中的采样记录，均不等于全部求解器接触事件计数。

### 场内余量的具体范围

原生字段 `minimum_vehicle_envelope_boundary_margin_cm` 实际来自 `GetFoliageContactBounds()`：**底盘 `ContactChassis` 与各车轮 Bounds 的联合 AABB**，对照原始 CAD 地图的 XY 矩形边界，每个游戏 tick 检查一次。该值不包含全部云台、枪管、舵机等部件，也不是车辆与玻璃之间的距离。现有 native fixture 允许最多 1 cm 的数值越界余量；wrapper 保持更严格的 **最小值必须 ≥0 cm**。

96 个球体扫掠覆盖四侧、角部和关闭门位置。它们查询时忽略原始 CAD，以隔离连续保护墙。实际车辆行驶测试保留原始 CAD 碰撞。南侧这一条路线验证真实驱动接触，其余位置由静态扫掠提供边界覆盖证据。

该 fixture 会持续渲染捕获视图，包含截图准备阶段，适合物理接触验证。实时倍率、导航精度和正常手动模式性能由独立的测量流程验证。

## 已审查的恢复后结果

实际证据目录：

```text
D:\simulator\artifacts\maps\campus-gym-20261119\boundary-restored-01
```

该历史运行由 `gym_work/run_boundary.py` 的前一版专用 launcher 执行；本目录的新通用 wrapper 完成静态语法与命令行检查，尚未额外启动 UE。历史结果不作为新 wrapper 已原生运行的证据。

| 项目 | 真实记录 |
|---|---:|
| 原生六项物理检查 | 6/6 通过 |
| 模块 SHA-256 | `9bd12d192bab3e985ba18527093db8cbab16a84b85a4b2dbfc2d40268f342a5e` |
| 接近阶段实际峰值 | 1.5842432698 m/s |
| 首次确认接触前行驶距离 | 65.955 cm |
| 碰墙后继续驱动时长 | 1.5060333014 s |
| 继续驱动时额外前进 | 0.8831305454 cm |
| 继续驱动阶段末速度 | 0.148 cm/s |
| 原生轮刹后速度 | 0 cm/s |
| 底盘与车轮包络距原始场地边缘的最小余量 | 48.1641087210 cm |
| 倒车脱离距离 | 60.4721829305 cm |
| 最大横向漂移 | 0.5669697079 cm |
| 最大车体上升量 | 0.2504228007 cm |
| motion generation | 0 → 0 |
| 恢复计数 / 位姿覆盖 | 0 / 无 |

实际命中部件包含 `Contact_yaw` 与 `Contact_body`，命中对象为同一个 `arena_guard_south` 组件。边界原生报告同时记录 96/96 扫掠通过、4 道连续碰撞墙、4 个固定关闭门扇、无开门交互；保护墙的 inset/厚度/顶部为 0.25/0.48/4.8 m，视觉玻璃顶部为 2.6 m。

据此可以记录：**在这条南侧测试路线上，车辆通过真实轮电机和 Chaos 碰撞被关闭边界阻挡，持续驱动后仍位于场内，并能倒车脱离、再次停止，全程无重置或运行中位姿覆盖。** 全场高速、所有角度及其他车辆仍应各自保留实际验证记录。
