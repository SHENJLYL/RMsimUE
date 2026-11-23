# 弗兰德斯枕木与路肩碰撞补充包

本补充包为原弗兰德斯地图中缺少可用碰撞的 **14,349 个铁路枕木、37 个 rail_crosstie、4,068 个路肩实例**建立静态接触与射线查询几何。几何取自已核验的原视觉网格，保留原位置、完整三轴缩放和 full-affine 变换。

生产工程为 `D:\simulator`。数据位于 `assets/maps/wt_vlaanderen/generated-collision`，保存资产位于 `/Game/RMSim/Maps/wt_vlaanderen/GeneratedCollision/Meshes`。主场景继续使用原 `scene-source.json` 和 WorldSettings 来源标签。

## 范围与原始来源分类

原提取覆盖清单中，这批资源分别落在 `intentionally_nonphysical` 或 `missing_collision_resource` 项下。原碰撞覆盖状态继续保留在源清单。新数据明确归类为**由源视觉几何生成的静态碰撞**，单独报告实例、active bodies 和 shapes。

| 源资源 | 原 RMI 实例 | full-affine 实例 | 合计 |
|---|---:|---:|---:|
| russia_rails_sleeper_a | 14,349 | 0 | 14,349 |
| rail_crosstie | 0 | 37 | 37 |
| pavement_border_a | 149 | 864 | 1,013 |
| pavement_border_a_secondlayer | 171 | 163 | 334 |
| pavement_border_a_white_secondlayer | 324 | 190 | 514 |
| pavement_border_b | 18 | 633 | 651 |
| pavement_border_b_secondlayer | 163 | 131 | 294 |
| pavement_border_b_white_secondlayer | 264 | 159 | 423 |
| pavement_border_c | 102 | 253 | 355 |
| pavement_border_d | 2 | 240 | 242 |
| pavement_border_e | 10 | 216 | 226 |
| pavement_border_g | 4 | 0 | 4 |
| sicily_pavement_border_a | 1 | 3 | 4 |
| sicily_pavement_border_b | 1 | 7 | 8 |
| **总计** | **15,558** | **2,896** | **18,454** |

准备结果为 **10 份共享基础几何、872 个基础三角形、193 份 affine cell 网格、203 个网格资产、917 条 cell 记录**。193 个 affine cell 共 177,977 个三角形，203 个资产合计 178,849 个三角形。

全图数据共有 **15,751 个物理槽位**：15,558 个原 RMI 实例加 193 个 affine cell。运行时按空间范围激活其中的一部分。[准备摘要](../../artifacts/maps/wt-refine-20261118/prepared-collision-01/summary.json)记录完整数量与哈希；[独立审查](../../artifacts/maps/wt-refine-20261118/generated-collision-review-01/verification.json)核对全量原行、affine 矩阵、坐标轴和 cell bounds。

## 几何与物理表示

| 资源组 | 共享表示 | 接触轮廓 |
|---|---|---|
| 主枕木 | 原 314 个三角形的静态 triangle BVH | 保留木体、金属件轮廓与间隙 |
| rail_crosstie、普通及弯曲路肩 | 源三角形静态 BVH | 保留弯段内部空区和物体间隙 |
| 两种 white_secondlayer、两种 sicily 路肩 | 原 8 个角点建立单凸体，共用两份 convex 资产 | 凸体闭合底面，顶部和侧面由原角点限定 |
| full-affine 实例 | 按 32 m cell 合并为 193 个 triangle BVH | 完整剪切、旋转、缩放和位置烘入局部顶点 |

每个激活的原 RMI 实例使用一个 native body、一个 native shape；每个 affine cell 使用一个 body、一个 shape。三角形资产采用 `CTF_UseComplexAsSimple` 和双面碰撞，凸体采用 `CTF_UseSimpleAsComplex`。原生作者逐资产核对 cooked 数据、三角数、单 shape 及 bounds。`UWTGeneratedCollisionData` 绑定 FBX SHA256、配方 SHA256、模式和源三角数。

坐标遵循现有场景：Dagor 米制 `(x,y,z) → ROS (x,-z,y)`，随后 `ROS (x,y,z) → UE (100x,-100y,100z)` 厘米制。RMI 保留原 float32 行、四元数和三轴 scale；full-affine 使用完整原矩阵。binary FBX 的每个三角形均与转换后的源数据对照，affine 局部 float32 最大位置误差为 **0.001907 mm**。

实现：[准备脚本](../../tools/maps/prepare_wt_collision.py)、[原生作者](../../tools/unreal/author_wt_collision.py)、[MapImportLibrary](../../unreal/RMSim/Source/RMSim/MapImportLibrary.cpp)。

## 运行时 cell 管理

[WTGeneratedCollisionComponent](../../unreal/RMSim/Source/RMSim/WTGeneratedCollisionComponent.cpp) 在车辆初始放置后、启用接触物理前预热。GameMode 常规 Tick 为 **TG_PostPhysics**；`Advance` 在该阶段同步完成 cell 切换，为下一物理帧准备碰撞。

1. 启用 `-RosLidar` 时读取实际 `LidarProfile`，或从 `MeshRigFile` 同目录读取 `sensors/lidar.json`。当前 profile 原量程为 40 m；再加上传感器安装偏移长度及车辆局部视觉 bounds 半对角线。
2. 加载半径为 `max(16 m, 量程与安装包络) + 32 m`。未启用 RosLidar 时为 48 m。
3. 保留半径为加载半径加 32 m。车辆平面位移达到 8 m 时刷新，使用 cell 完整世界 AABB 与车辆的平面距离判定。
4. 先同步创建新范围内全部 body，再释放超过保留半径的旧 cell。逐 body 检查有效物理句柄和实际 shape 数。
5. 初始选定出生位置同时作为固定锚点。远移、R 复位和现有出界恢复流程保留该锚点周围的碰撞。

32 m 预加载与 8 m 刷新阈值留出 24 m 的额外空间余量。运行时 HISM 带 `RMSimGeneratedCollision` 标签，使用 `BlockAll / QueryAndPhysics`，隐藏渲染并关闭 UE 导航影响。`SceneMap` 单独报告补充组件；自动导航的全路线与稳定 1× 验收沿用独立门槛。

日志 `WT_GENERATED_COLLISION_READY` 记录出生范围实际 body/shape、初始化耗时和来源；`WT_GENERATED_COLLISION_FINAL` 记录退出前 active/peak bodies、cell 创建/释放数和最大刷新耗时。

## 完整重建与验收

以下使用当前机器已核验路径。先结束本工程预览，将已有 `generated-collision` 目录完整移入本地归档。准备脚本要求新的输出与证据目录。

~~~powershell
$wtRoot = 'D:\simulator'
$wtEngine = 'E:\simulator-tools\UE_5.8'
$wtPython = 'C:\Users\SHEN\AppData\Local\Programs\Python\Python314\python.exe'
$wtPackage = Join-Path $wtRoot 'assets/maps/wt_vlaanderen'
$wtEvidence = Join-Path $wtRoot (
  'artifacts/maps/wt-refine-20261118/collision-rebuild-' +
  (Get-Date -Format 'yyyyMMdd-HHmmss'))
~~~

### 1. 几何和空间数据

~~~powershell
& 'E:\blender\blender.exe' `
  --background --factory-startup --threads 2 --python-exit-code 1 `
  --python "$wtRoot\tools\maps\prepare_wt_collision.py" -- `
  --scene-source "$wtPackage\scene-source.json" `
  --source-root "$wtRoot\artifacts\maps\wt_existing\avg_vlaanderen" `
  --output "$wtPackage\generated-collision" `
  --evidence-root "$wtEvidence\prepared"
~~~

准备读取原 `models-all-07`、原视觉 FBX/RMI 和 `affine-bake-map.json`，只写补充目录与新证据。主 `scene-source.json` 保持原字节。

### 2. 独立源射线和范围 fixture

~~~powershell
& $wtPython -B "$wtRoot\tools\maps\prepare_wt_collision_probes.py" `
  --scene-source "$wtPackage\scene-source.json" `
  --source-root "$wtRoot\artifacts\maps\wt_existing\avg_vlaanderen" `
  --output "$wtEvidence\source-probes"
~~~

此步骤从原 NPZ、原 RMI 和 full-affine 独立计算源命中，覆盖 14 类资源表面、两个真实空区及范围阶段的 40 m 查询线段。

### 3. 原生资产作者

项目需已编译包含当前 MapImportLibrary 与生成碰撞组件的模块。

~~~powershell
& "$wtRoot\tools\unreal\Run-WTNativeScript.ps1" `
  -EngineRoot $wtEngine `
  -PythonScript "$wtRoot\tools\unreal\author_wt_collision.py" `
  -ReportArgument CollisionReport `
  -EvidenceDirectory "$wtEvidence\native-author"
~~~

默认 NullRHI 即可。作者仅保存 `GeneratedCollision/Meshes` 下的独立资产，检查 World 和源清单前后哈希。已有资产在其原生身份、三角数、单 shape 和 bounds 全匹配时复用。

### 4. 独立重载、源射线与 streaming

~~~powershell
& "$wtRoot\tools\unreal\Run-WTNativeScript.ps1" `
  -EngineRoot $wtEngine `
  -PythonScript "$wtRoot\tools\unreal\verify_wt_collision.py" `
  -ReportArgument CollisionAudit `
  -EvidenceDirectory "$wtEvidence\native-reload" `
  -ExtraArguments @("-CollisionFixtures=$wtEvidence\source-probes\fixtures.json")
~~~

门禁在新进程加载保存资产和 World，使用临时审计 actor 检查两种通道的 simple/complex 查询、源表面、空隙、范围切换、出生锚点与 40/200 m 实际 body/shape 数，并核对 206 个保护文件前后字节。

UE 5.8 实际通道名为 `ECC_RM_SIM_LIDAR`，CollisionChannel 值为 14、TraceTypeQuery 值为 2。响应枚举脚本名为 `CollisionResponseType`，`ECR_BLOCK` 值为 2。验证器先核对 native name/value/display_name。可用[只读枚举自省脚本](../../tools/unreal/inspect_wt_collision_enums.py)检查实际导出。

### 5. 默认出生位置真实接触回归

~~~powershell
& "$wtRoot\tools\unreal\Test-ManualControls.ps1" `
  -EngineRoot $wtEngine `
  -MapFile "$wtPackage\map.json" `
  -EvidenceDirectory "$wtEvidence\contact-reset" `
  -Contact -ContactReset -ContactDistanceCm 340 -ContactSpeedMps 1 `
  -GraphicsRhi D3D11 -SingleShaderWorker
~~~

当前默认出生地面坐标为 ROS `[1028,-1020,-1.3805753] m`。原 340 cm +X 中心线离新增表面最近的资源为枕木 row 2237：源位置 `[1031.593994,-1021.281189,-1.334452] m`，最近水平投影距离 **0.271629 m**，源 Z 范围 `[-1.445474,-1.202714] m`。

[源 probe 摘要](../../artifacts/maps/wt-refine-20261118/collision-probes-02/summary.json)保留该关系。本轮真实 `contact-flanders-01` 已完成 5 项物理检查及 8 张截图：前行 340.681 cm、横向漂移 −0.073 cm、用时 4.085 s；制动后速度为 0；返程剩余 9.675 cm、漂移 −0.007 cm；共用 R 复位一次，距出生目标 0.0134 cm，速度为 0。默认出生位置及原路线保持，运行期间输入文件哈希一致。

实际游戏 READY/FINAL 日志均为 **18 cells、327 native bodies、327 shapes、486 个源实例**。本回归未启用 RosLidar，实际加载/保留半径为 48/80 m；首次组件初始化 **0.787995 s**，同步物理创建峰值 **3.683802 ms**。3.4 m 路线内 `activation_passes=1`、创建 18、释放 0 个 cell。真实 GUI 的跨 8 m 行驶与 R 复位已完成，独立记录见下方 GUI 证据。

[游戏碰撞摘要](../../artifacts/maps/wt-refine-20261118/contact-flanders-01/collision-runtime/summary.json)保留实际日志和 5 项检查。Control-result 记录生成碰撞 ready 及 18/327 计数；四轮的实际接触对象为 `LandscapeHeightfieldCollisionComponent_544`，验证了该激活范围内的车辆路线和复位。新增几何的具体命中身份见下方独立 source/query 门禁。

## 已通过的原生 query 与流送结果

`collision-reload-03` 实际运行 **UE PID 42236、exit 0、13.9944 s**，模块 SHA256 为 `cd1c286269fc5d411084d6e23d0b861d142082adbd593680edda7294dfcec90c`。203 个保存资产通过检查；**128 条 native query 全通过**，覆盖全部 14 类资源及范围阶段。Visibility/RMSimLidar 各执行 simple/complex；两个真实空区共 8 次查询均保持无命中。源与原生命中位置最大误差 **0.00198741 cm（0.0198741 mm）**。

| 实测审计阶段 | active cells | native bodies/shapes | active source instances |
|---|---:|---:|---:|
| 40 m 查询量程，初始出生范围 | 28 | 498 / 498 | 791 |
| 移动 7.99 m，沿用初始 cell | 28 | 498 / 498 | 791 |
| 移动 8.01 m，执行刷新 | 28 | 498 / 498 | 791 |
| 远移 160 m，含常驻出生范围 | 45 | 1,152 / 1,152 | 1,445 |
| 返回出生点，保留滞回范围 | 31 | 623 / 623 | 916 |
| 200 m 查询量程，出生范围 | 82 | 1,838 / 1,838 | 2,431 |

160 m 阶段再次查询出生枕木，四种组合均通过；返回时实际释放 14 个 cell。完整 40 m 审计累计 20 次 activation，创建 251、释放 214 个 cell，峰值 1,152 bodies；累计最大同步刷新 **5.8661 ms**。200 m 初始加载的同步刷新为 **15.9408 ms**。

组件加载合同及资产引用用时分别为 113.744 ms（40 m）、121.150 ms（200 m）；这是本次验证进程先核验 203 资产后的测量。真实游戏启动从 READY/FINAL 日志另行记录初始化成本。上表数量来自实际创建的 native body/shape；全图 15,751 槽位为可加载数据总量。

World、主清单、补充清单和 203 个资产共 **206 个保护文件**前后哈希一致。原生作者使用 `2aee857b…` 模块，重载使用 `cd1c2862…`；各轮保留实际 execution 身份。

## 已完成的真实 GUI 跨阈值与复位

本轮 UE PID **47912** 使用 `cd1c2862…` 模块与观察用 `wall_baseline` 出生点。Win32 SendInput 记录、前台窗口身份及收到的按键边沿共同绑定两次操作：W 实际按住 **4502 ms**，随后 R 实际按住 **153 ms**。

80 帧记录中，第 11 帧保留 W 按下时的实时位姿；第 44 帧保留 W 释放后的位姿。车辆从 UE `(107523.171748, 101265.618600, -29.968296) cm` 移至 `(108580.965525, 101284.401249, 54.897267) cm`，水平位移 **10.579605 m**，超过 8 m 刷新阈值，恢复次数维持 3。操作前复制的 live-input 文件记录的是 09:02 的上一事件，摘要同时保留该文件与本次 W press 的实时起点。

真实 R 使恢复次数从 3 增至 4，回到本次选择的 `wall_baseline` 出生范围。释放后位置为 UE `(107523.230622, 101265.615877, -30.356783) cm`，距地图出生坐标的水平误差 **0.045841 cm**、三维误差 **0.428531 cm**；相对先前已停稳快照的三维误差为 **0.008753 cm**，此时记录速度为 **0.299951 cm/s**。复位后的窗口、live-input 和截图已绑定保存。

READY 记录初始 **12 cells / 264 bodies / 264 shapes**；退出前 FINAL 为 **22 cells / 512 bodies / 512 shapes**，`physics_ready=true`、出生范围固定锚点有效。整段会话峰值 **555 bodies**，累计 **39 次 activation、创建 36 / 释放 14 cells**，最大同步刷新 **2.291497 ms**，首次资产加载 **0.720741 s**。这些累计值覆盖该 UE 会话中更早的外部驾驶与本次两次操作；W/R 各自的输入及位姿变化独立列出。会话通过 `CloseMainWindow` 正常退出，exit 0。

[GUI 碰撞摘要](../../artifacts/maps/wt-refine-20261118/gui-collision/summary.json)绑定 80 张帧图、R 后截图、两次 SendInput execution、按键计数差和完整 READY/FINAL 日志。该记录的验收范围是实际驾驶跨阈值、复位与碰撞就绪；最终全画面由独立画面验收记录。

## 证据与提交边界

- [准备摘要](../../artifacts/maps/wt-refine-20261118/prepared-collision-01/summary.json)：源数量、FBX 对照与 affine 精度。
- [独立源变换审查](../../artifacts/maps/wt-refine-20261118/generated-collision-review-01/verification.json)：全量原行、矩阵、bounds 与坐标轴。
- [原生资产作者](../../artifacts/maps/wt-refine-20261118/collision-native-01/summary.json)：203 个单 shape 保存资产及原生 bounds。
- [独立原生查询和范围审计](../../artifacts/maps/wt-refine-20261118/collision-reload-03/summary.json)：128 条 query、void、LiDAR、anchor 与实际开销。
- [真实 GUI 跨阈值与复位](../../artifacts/maps/wt-refine-20261118/gui-collision/summary.json)：W/R 输入、10.58 m 实际位移、出生锚点返回与整段会话开销。
- [碰撞证据索引](../../artifacts/maps/wt-refine-20261118/collision-evidence/summary.json)：各轮状态、模块和完整证据哈希。

随功能源码提交的最小配方为 `assets/maps/wt_vlaanderen/generated-collision/recipe.json`。规范 JSON SHA256 为 `3b39c35a1eead7481744e423d0950b14b99b700e6c1d57a647ca90d0be7e8e07`，通过排序 key 和紧凑 JSON 计算。

完整 manifest、source-bindings、RMI、FBX、uasset 保留本地。作者完整报告位于指定 evidence 目录，没有向 package 写额外 import/cache 报告。提交证据采用紧凑摘要；完整逐资产报告、日志及载荷留作本机复现材料。
