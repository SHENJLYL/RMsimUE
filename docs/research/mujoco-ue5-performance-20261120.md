# MuJoCo、UE5 与 RViz 三窗口性能

当前 RMUC 样例在 UE 进程内用 MuJoCo 4.0.1 求解，UE 直接使用已完成物理步的车体和关节姿态。单独的 MuJoCo 窗口接收 30 Hz 只读快照，负责查看碰撞模型。RViz 接收 ROS 2 定位、点云和路径。完整三窗口启动、立体场景、车辆和原导航链均有原生运行记录。

## 开源实现可以复用的部分

| 实现 | 源码中核实的做法 | 对 RMSim 的意义 |
|---|---|---|
| UnrealRoboticsLab，`efbc5e27` | 独立 MuJoCo 物理线程固定步进。每步生成一份完整快照，UE 游戏线程在同一次访问中消费同一个 `FrameId`。传输层可选 ZMQ 或本机共享内存，相机数据有独立通道。 | 借用单写者、完整快照和独立物理时钟。RMSim 当前 5 ms 外层步进仍绑定 UE 更新，异步化需要一起处理传感器时间、车体碰撞代理、复位和命令时效。 |
| MuJoCo 官方 `simulate`，`4ed093a9` | 用 `std::thread` 单独启动物理循环。渲染由主线程运行，物理按实际时间追赶，并控制每一轮追赶的墙钟预算。 | 可复用“追赶物理步”和“选择展示帧”的区分。保留 RMSim 的绝对时钟与延迟统计，避免渲染忙时丢失物理步。 |
| oneclicklabs 原型，`d2cb5a8a` | 在 UE Actor `Tick` 中按 `DeltaTime` 运行多次 `mj_step`，随后一次更新画面。 | 代码路径短，但物理工作仍留在游戏线程。可作固定子步对照，无法单独解决 UE 线程争用。 |

源码：[URLab 物理线程与快照](https://github.com/URLab-Sim/UnrealRoboticsLab/blob/efbc5e272a51dbe641c7c078406bd3c076b05823/docs/concepts/architecture.md) · [URLab 传输与线程](https://github.com/URLab-Sim/UnrealRoboticsLab/blob/efbc5e272a51dbe641c7c078406bd3c076b05823/docs/concepts/networking.md) · [MuJoCo 官方物理与渲染循环](https://github.com/google-deepmind/mujoco/blob/4ed093a9f06b170bd1564942fa37e1bb907d93e3/simulate/main.cc) · [oneclicklabs 步进源码](https://github.com/oneclicklabs/MuJoCo-Unreal-Engine-Plugin/blob/d2cb5a8a8f9154e670c15fa7eb0944f5a218a92a/Plugins/MuJoCoUE/Source/MuJoCoUE/Private/MuJoCoSimulation.cpp)

对 RMSim 来说，先验收本机传输，再决定是否换协议。当前 30 Hz 的小状态包在一轮覆盖测试中连续接受 6,198 个序号，缺号和拒收均为 0。读者故意合并过期显示帧，与 UDP 序号缺口是两项不同指标。只有长期或高频测量出现传输尾延迟、缺号，或启用高带宽 RGB 流时，才有证据优先试 URLab 的共享内存方案。环形缓冲必须报告读者落后和覆盖数，重置后必须区分 generation。

## 同画质实测与已落地修改

- RTX 5080 Laptop，2560×1440 全屏，原生屏幕比例 100%，现有体育馆、Lumen、车辆和传感器均使用原配置。初始 120 Hz 请求的 60 秒观察为 19.49 次 UE 场景 Draw 每秒、0.99990× 仿真。窗口焦点有变化，报告保留为条件未通过。历史同分辨率 60 Hz 目标有约 36.58 次 Draw 每秒的记录。源代码已具备约 1× 的物理时钟，场景绘制和物理步进需要分别测量。
- MuJoCo 观察进程原先在 UE 全屏遮挡时继续提交约 322 万三角面。现在确认所属 UE 窗口为前台不透明无标题窗、完全覆盖该观察窗口后，跳过 OpenGL 绘制和交换。模型、GL 缓存、相机和物理仍使用原来数据，事件循环和接收线程继续工作。多屏未覆盖、部分可见、不确定或切回时继续显示。
- 相同程序的两次 60 秒采集均完成约 1×。另一个会话有 42 个五秒间隔报告处于覆盖暂停。取实际相邻两个采样点都为全屏 UE 前台的片段，绘制开关前后分别有 38.0 和 28.0 秒，MuJoCo 窗口绘制 23.04 与 0.25 次每秒，整卡功耗中位数 97.85 与 80.97 W。两次整段均有焦点变化，片段只用作功耗与覆盖诊断。UE Draw 为 20.57 与 14.71 Hz，本次没有测得 UE 升帧。恢复可见的采样中，MuJoCo 同一会话继续绘制，序号仍连续。
- UE 原生 CSV 将 `DrawCall/Basepass` 大于零作为实际场景工作的分类依据。反射异步从 0 调到 1，样本区间对齐 Draw 的 `GPUTime` 中位数分别约 18.15、18.40 ms，恢复默认后约 18.47 ms。RHI 队列扩大至四个外层帧时约 19.29 ms。两项均不作为默认优化。GPU 计时的读取与提交存在异步延迟，`ProfileGPU` 首次采到的是两个未画场景的帧，保留失败范围并另补实际 scene pass 捕获。
- 补测连续采样找到了 6 个完整场景帧，实际图形队列 17.21–20.14 ms，中位 17.86 ms。各 pass 中位包含透明 3.50 ms、延迟光照 2.53 ms、阴影 2.34 ms、后处理 1.90 ms、基础通道 1.18 ms。范围是单机静止视角的诊断区间，包含耗时可能重叠，不直接相加。`gpu-passes-system-cpu-01/graphics-pass-summary.json` 保留逐帧数据，基础通道的已测成本不足以解释全部差额。
- 新增 `unreceived_snapshot_sequences`，统计首包前和相邻有效包之间的序号缺口，已有 `accepted_snapshots`、`rejected_snapshots` 和不同序号上屏数可联合检查。它不能观测会话末尾发送后永未收到的包。正式前台导航期间需要长时间、运动和复位压力测试。100 FPS 指新场景帧每秒至少 100 次，同时观察显示呈现和仿真倍率。当前仍未达标。

## 布网核查

原 AIM 视觉包共 1,260,698 个三角面。车体约 39.6 万、云台偏航组 47.8 万、俯仰组 17.2 万。优先核查这些部件的重复壳体、退化三角及完全封闭的内部件。云台有雷达玻璃、亚克力和传感器材质，需分别验证轮廓、阴影、反射和传感器查询。体育馆已有精确去重及退化删除证据，约 196 万个保存的 LOD0/fallback 面。后续网格试验应使用独立渲染副本，逐个验证焊接顶点的 UV/法线/材质槽、固定视角和移动序列。碰撞与 LiDAR 查询资产按原身份保留。

只读扫描固定原 Blender 模型的这三个部件，在 1,046,738 个直接三角化面中找到 178 个确切零面积面和 1,618 个三顶点位置相同的候选，合计约 0.17%。直接三角化比正式导出清单多 343 面，记录了逐部件差异。忽略 winding 的重合位置统计不能直接决定可删性，尤其是玻璃和光学件。此次未将候选写入正式网格。证据 `aim-face-audit.json`。

物理包已有 1,608,500 个场地碰撞面及在物理 SDK 中生效的静态碰撞缓存。旧平面消融超过 1 微米误差门槛，凸分割只消去 464 面，不能作为直接删除物理面或推广同画质的依据。有关 GPU 排查方法与独立 Lumen 预算，参见 [Epic Lumen Performance Guide](https://dev.epicgames.com/documentation/en-us/unreal-engine/lumen-performance-guide-for-unreal-engine)。

## 下一项实现的验证边界

先把纯数值 MuJoCo 步进从 UE 外层 Tick 分离，工作线程独占 `mjData`，发布带 `sequence、generation、solver_time` 的完整快照。UE 仅在游戏线程访问 UObject 和绘制，按物理时间消耗姿态历史，维持 1 ms 动力学、5 ms 世界观测、200 Hz IMU、200k LiDAR 发射槽/秒。传输缓冲用有界队列，分别计数求解步、传感器样本、已发/已收序号和呈现帧。每次复位的第一个新姿态和第一组新传感器必须归同代。先复跑原路线和波浪接触，再做全屏 60 秒及滚动窗。GPU 路线同时依据 pass 计时处理重复提交和精确布网，继续以 100 FPS 为目标。

证据目录 `artifacts/performance/three-window-fullscreen-20261120`。最终标准入口在默认 60 Hz 目标下运行至就绪并正常退出，接受 1,228 条连续序号，903 次观察窗交换，有一次覆盖暂停间隔报告。最终版通过 42 项显示/协议测试、53 项启动合同，以及三组真实 GL 色彩和深度逐像素对照。该短程入口回归与 FPS 调查分别记录。正式显示呈现工具的 ETW 启动因现有 Windows 权限被拒，采用原生 Draw 及 GPU CSV 量化本轮进展。跨平台原生验收另外记录。 [专项汇总](../../artifacts/performance/three-window-fullscreen-20261120/verification.json)
