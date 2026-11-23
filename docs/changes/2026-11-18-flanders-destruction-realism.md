# 弗兰德斯破坏行为：源几何、局部接触与受保护结构

日期：2026-11-18。活动工程：`D:/simulator`。分支：`feat/navigation-speed-20261117`。

本次修正把硬物破坏收敛到 **19 个薄木板组合实例**，并将原来的 **1,479 个硬物候选恢复为受保护结构**。4,647 个软植被实例改为车辆经过部位的叶片局部压倒。碎片直接来自源网格，运动由真实接触方向、当次冲量及 Chaos 产生。

最终运行版本为 **build-06**，模块 SHA256 以 `b9278d1a` 开头。Forward、Reverse、Protected、Pressure、Foliage、Geometry 六个用例均在这一模块下重新完整通过，另有弗兰德斯驾驶/复位 **5 项回归通过**，共保留 **92 张原生 PNG**。最终绑定报告为 `artifacts/maps/wt-destruction-fix-20261118/release-verification.json`。build-05 的结果作为前一检查点保留。历史实现与当时的证据见 [首次破坏实现记录](2026-11-18-flanders-destruction.md)。

## 1. 按实际源资源限定破坏范围

### 薄木板白名单

运行时仅允许精确资源名 `prop_wood_fence_h_plank`，对应源碰撞名 `col_prop_wood_fence_h_plank`，共 **19 个 placement**。名称近似匹配、未知变体和整组木建筑均不进入此白名单。

源 FBX 连通几何核对表明，每个实例包含两块独立竖板。每块约宽 8.3 cm、厚 3.6 cm、高 1.176 m；两块板合计实体体积约 0.004783 m³。破坏选择实际接触的一块板，在接触高度形成切口，保留另一块完整板与被击中板的下桩。

相邻的 `prop_wood_fence_h` 是完整粗木围栏：约 1.904 × 0.160 × 1.176 m，含三根厚约 8–11 cm 的横梁及两块竖板。整体实体体积约 0.058171 m³，按实现使用的 550 kg/m³ 密度估算约 32 kg。该资源的 **969 个实例全部受保护**。

### 从旧候选中移出的结构

| 源资源范围 | 实例数 | 保护依据 |
|---|---:|---|
| `prop_wood_fence_h` | 969 | 带粗横梁的完整固定围栏 |
| `prop_sicily_villa_railing_{a,c,f,g}` | 119 | 源材质与几何对应石材/灰泥栏杆 |
| `prop_sicily_fence_cafe_{a,b,c,d}` | 177 | 完整固定围栏组件 |
| `prop_sicily_gardens_wood_planks_{a,b,c}` | 25 | 大型木结构整体 |
| `prop_wood_debris_{a,b,e,f}` | 123 | 多米尺度的成组木料 |
| `prop_wood_fence_m_pole` | 55 | 固定结构立柱 |
| `prop_wood_fence_n_gate_double_a`、`prop_wood_fence_o_gate_double_a`、`prop_wood_gates_{b,d}` | 11 | 固定门及门框结构 |
| **合计** | **1,479** | 原 1,498 个硬候选中保留 19 个薄板组合 |

运行日志 `protected_wood_and_structures=1879` 使用更宽的受保护集合，包含上述 **1,479 个旧候选**与 **400 个原先未列为候选的结构实例**。这两个统计口径分别保留，避免把 1,879 全部计作本次移出的破坏候选。

完整精确名称和源实例数由 `prepare_wt_destruction_geometry.py` 输出到 `negative-policy-cases.json`。砖石、金属构件、建筑、路肩、轨道、枕木及固定木结构保持各自硬碰撞。

### 软植被范围

| 精确源资源 | 实例数 | 行为 |
|---|---:|---|
| `prop_tree_poplar_bush_medium_a/b` | 3,115 | 局部叶片压倒，保留木质材质槽的可视几何 |
| `prop_tree_tilia_bush_a/b` | 1,289 | 局部叶片压倒，保留经过范围之外的叶片 |
| `prop_bush_flowers_a/b/c` | 145 | 局部叶片压倒，保留木质材质槽的可视几何 |
| `prop_flower_bed_b_flowers_a`、`prop_flower_bed_c_flowers_a` | 98 | 局部花叶压倒，实体花坛底座保持原碰撞 |
| **合计** | **4,647** | 9 种精确资源，非阻挡植被 |

原先 4,404 个 `tree_*_bush`、145 个花灌木与 98 个花坛花卉的计数保持一致。车辆沿实际运动轨迹扫描自身接触范围，选中高度及距离满足条件的完整叶片卡片；剩余三角形继续按原位置、原尺寸显示。对 poplar 和花灌木，已经核对为木质部分的材质槽 0 留在静态余部中。

局部叶片采用零初速的阻尼弯曲响应，沿车辆运动方向下压，根部位置保持，法线和切线随弯曲更新。前 2 秒完成稳定下伏，局部压倒叶片在 6 秒时回收；其余叶片与木质余部继续保留。局部视觉替换不增加植被硬阻挡。

## 2. 真实接触与破坏触发

接触由 `AVehicleRig::DispatchPhysicsCollisionHit` 从 Chaos 物理事件采集。事件直接记录 `OtherInfo.BodyIndex`，以 **碰撞组件 + 精确实例号**定位目标，同时保留实际接触点、朝障碍物的单位法线、法向冲量、时间、序号及运动代数。原 UE 冲量单位 kg·cm/s 除以 100 转换为 N·s。

碰前速度采用质心线速度估计：

```text
碰前 COM 线速度 = 当前物理线速度 - MyInfo.DeltaVelocity
```

UE 事件来源中 `DeltaVelocity = V - PreV`，因此该估计能够取回碰撞求解前的质心平移速度。法向速度取该速度在实际接触法线上的分量；这里记录的是 COM 平移估计，接触点因角速度产生的附加速度另属更完整的接触动力学模型。

事件经过有限环形队列与新鲜度检查，破坏端使用最近 0.10 秒的有效接触。重置后的旧运动代数事件失效。实例号、原碰撞体、源视觉和源碰撞变换、接触面位置及源连通构件均须匹配，才允许进入局部破坏。

薄板参数位于 `unreal/RMSim/Config/DefaultGame.ini` 的 `[RMSim.Destruction]`：

| 参数 | 当前值 | 用途 |
|---|---:|---|
| `BoardImpactMps` | 2.5 m/s | 真实法向碰前速度门槛 |
| `BoardImpulseNs` | 6 N·s | 当次真实冲量门槛 |
| `BoardPushNewtons` | 150 N | 连续顶压的法向平均力门槛 |
| `BoardPushSeconds` | 2.5 s | 连续有效顶压时长 |

连续顶压要求同一白名单实例的真实侧碰、朝接触法线的驱动指令至少 0.5 m/s、车体速度低于 0.6 m/s，以及足够法向平均力。有效样本不足则清零，超过 0.10 秒的接触间断清除计时。累计压力只决定是否失效；碎片动力使用当前碰撞事件的冲量。

build-06 使用相邻有效接触事件的时间差计算平均力，保持 150 N / 2.5 s 阈值。首个样本、超过 0.10 s 的间断或接触法线点积小于 0.7 时重新建立基准；同面同时间戳样本不增加时间，迟到样本不回退基准。每帧的松开、反推或实际速度达到 0.6 m/s 会清除旧累计；先处理已排队接触，再执行过期清理。原生 pressure-02 在有效连续受力 2.501 s 后触发，随后碎片清理完成。

## 3. 源几何断裂与 Chaos 运动

`WTBreakableGeometry` 读取源 StaticMesh 的 LOD0 三角形，保留材质槽、UV0–3、顶点颜色、法线与切线。经源顶点连通关系识别独立板材后，接触点选中具体构件，按局部平面裁切并封闭切口。所有外表面继续使用源顶点属性，切口属性由交线插值获得。

当前启用的薄板路径只切断实际受击板材。另一块板及下桩创建为静态余部并保持实体碰撞；碎片采用源裁切几何的凸碰撞体、按实体体积估计的质量与 Chaos 重力。原 HISM 的该实例在替代几何成功创建后局部隐藏，精确原碰撞 Body 关闭；相邻实例的变换与 BodyIndex 保持。

碎片冲量沿当次接触法线的水平分量施加，作用点来自实际接触点在碎片包围盒内的最近位置。碎片转动由偏心冲量和后续静态环境接触产生。实现将可转移动量限制为当次法向冲量的一部分，并按碰前法向速度限制碎片离开速度；不做随机 kick、额外向上抛射、随机角速度或动画缩放。压力累计量不会成为发射动量。

动态碎片使用 CCD 和阻尼，与静态地形和物件碰撞，当前对 PhysicsBody 通道采用忽略策略。碎片保持源尺寸，8 秒后回收；局部叶片与碎片均采用延迟销毁。原 HISM 的零尺度操作只承担替代成功后的实例隐藏，动画对象及其运动保持原尺寸。

源几何不可读、拓扑/尺寸与已核对构件不符、接触位置无法对应构件，或替代创建失败时，原硬物继续保持。当前运行时白名单保护整段粗围栏；相关通用几何代码的存在不会自动启用该资源的破坏。

## 4. 资源准备与源文件保护

新增 `tools/unreal/prepare_wt_destruction_geometry.py`，通过既有 `Run-WTNativeScript.ps1` 执行。脚本按 `scene-source.json` 的实际资产路径，仅为 **11 个视觉 StaticMesh** 设置 `allow_cpu_access=True`：

- 两个木资源：`prop_wood_fence_h`、`prop_wood_fence_h_plank`。
- 上表九个软植被资源。

粗围栏保留 CPU 可读性供源几何检查；运行时破坏策略继续保护其 969 个实例。原导入脚本同步使用这组精确名称，使后续导入保持相同 CPU 可读策略。

准备脚本记录属性前后值及资产身份，检查拓扑、UV/布局和材质槽，保存 UE 派生资产。源 FBX、RMI、源碰撞网格、材质资产、原始 `World.umap` 和坐标轴转换保持只读；没有重新生成几何或重新导入源 FBX。

`cpu-geometry-03/audit/verification.json` 已记录：

- `status=passed`、`assets_checked=11`、`assets_changed=3`；另外 8 个资产的 CPU 标志已由前两次准备保存。
- `source_fbx_instances_materials_world_unchanged=true`。
- `reimported=false`、`world_saved=false`、`geometry_rebuilt_by_script=false`。
- 边界比较按 float32 的 origin、extent 和端点运算传播舍入误差；拓扑与材质槽继续严格比较。离线检查接受实际保存造成的舍入差异，并拒绝 0.001 cm 的合成整体平移。

## 5. 原生 Windows 验证

证据根目录：`artifacts/maps/wt-destruction-fix-20261118/`。build-05 的 UBT 结果为 `Succeeded`，模块身份：

```text
UnrealEditor-RMSim.dll SHA256
7a4edd9792accc9f50151e2bfe38e70785c679db2ce684046ecdf78e1083f987
```

测试入口为 `tools/unreal/Test-WTDestruction.ps1`。每个用例使用独立的新证据目录，复制规范地图的临时出生点夹具及必需的 `generated-collision`、`overview.json`；源地图保持只读。脚本保留项目活动进程/互斥保护，只在超时情况下终止自己启动的进程，记录命令、原生退出码、日志、PNG、模块/源码/车辆配置/源 World 及运行依赖的前后哈希。

以下六个原生用例均为 build-05，原生退出码 0，最终报告 `status=passed`，前后受保护输入哈希相同：

| 用例 | 当前结果 | 验证目标与证据 |
|---|---|---|
| 构建 build-05 | **通过** | UBT `Result: Succeeded`，上述模块 SHA256 |
| CPU 几何准备 cpu-geometry-03 | **通过** | 11 个资产检查，源文件保护检查通过 |
| Forward / forward-03 | **完整通过** | 精确薄板实例 3、真实接触、正确初始运动方向、8 个时间阶段及 16 PNG、碎片清理、退出码 0、前后输入哈希相同 |
| Reverse / reverse-01 | **完整通过** | 同一薄板实例 3；真实法向速度 3.805394 m/s、冲量 72.596359 N·s；1 碎片沿接触方向运动，反向初始数 0，最终活动数 0；16 PNG |
| Protected / protected-01 | **完整通过** | 粗围栏 h 实例 833；12.002 s 中记录 2,648 次目标接触事件，破坏数 0；4 PNG |
| Pressure / pressure-01 | **完整通过（build-05）** | 1.2 m/s 指令；有效连续顶压 2.508 s、触发力 284.44 N；局部破坏 1 次，碎片清理完成；16 PNG |
| Foliage / foliage-01 | **完整通过** | tilia_b 实例 91；1 个局部叶片事件，触及 1 / 保留 179 个三角形；车体平面位移 247.274009 cm，硬物破坏 0，最终活动数 0；16 PNG |
| Geometry / geometry-02 | **完整通过** | 主动源几何身份/负例策略与生命周期检查；hard=1、live=0、8 阶段、16 PNG；按几何探针范围单独统计 |
| build-06 最终复验 | **完整通过** | 六项破坏用例及驾驶/复位 5 项全部重跑；见下方最终模块表 |

### Forward 已完成数据

原始证据：`forward-03/verification.json` 与 `forward-03/engine.log`。

- 运行时间：2026-11-18 10:11:01.882–10:11:45.436 UTC，包装脚本耗时 **43.555 s**。
- 请求速度 **4.5 m/s**；实际碰前法向速度 **3.816204 m/s**。
- 实际目标：`col_prop_wood_fence_h_plank`，实例 **3**；目标接触事件 **2** 次。
- 单个接触事件的最大法向冲量 **78.629534 N·s**。同帧两次事件合计约 **84.408 N·s**，对应局部破坏事件日志的聚合值。
- 生成碎片 **1** 个；第一物理采样速度沿接触方向投影 **134.427672 cm/s**，反向初始碎片数 **0**。
- 破坏后主动制动并观察 8.5 秒；最终活动碎片 **0**。
- `before`、`post_010`、`post_030`、`post_060`、`post_120`、`post_250`、`post_450`、`post_830` 共 **8 个阶段、16 张可解码 PNG**。
- `WT_DESTRUCTION_DRIVE_PASSED`、原生 `exit_code=0`、最终 `status=passed`；2,075 个受保护输入哈希的变化数为 **0**。

复现示例使用新的证据目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\simulator\tools\unreal\Test-WTDestruction.ps1 -ProbeCase Forward -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory D:\simulator\artifacts\maps\wt-destruction-fix-20261118\forward-next -TimeoutSeconds 240
```

原生启动包含 `-RenderOffScreen -unattended -ContactPhysics -d3d11 -NoShaderWorker`，正向用例传入 `-DestructionDriveFast -DestructionDriveSpeed=4.5 -DestructionExpectMesh=col_prop_wood_fence_h_plank -DestructionExpectedInstance=3`。完整实际命令以各次 verification 的 `command`/`arguments` 为准。

### build-06 最终模块与复验

最终模块 SHA256：`b9278d1a8dab8cdcd27b3fa5b9e295c05fb042a4ea1bae7c6ab762b503af84b8`。UBT 编译退出 0，`Result: Succeeded`，原生编译执行 13.58 s。与 build-05 相比，仅修正 `FPressure` 状态和连续受力的时间/接触面处理，源几何裁切与动画实现保持。

以下各项均绑定最终模块，原生退出 0，输入前后哈希一致：

| 用例目录 | 实际结果 |
|---|---|
| `pressure-02` | 764 条精确目标接触事件；最大碰前法向速度 1.160031 m/s；有效连续顶压 2.501 s 后断裂，触发样本推力 350.45 N；碎片首个速度沿接触方向 7.271483 cm/s；最终活动数 0；16 PNG |
| `forward-04` | 法向速度 3.818158 m/s、最大单事件冲量 76.071805 N·s；1 个源几何碎片，初速度沿接触方向 134.552176 cm/s；反向初始数 0、最终活动数 0；16 PNG |
| `reverse-02` | 从相反方向碰撞同一薄板实例 3，法向速度 3.813311 m/s；碎片沿本次接触方向 147.773932 cm/s；反向初始数 0、最终活动数 0；16 PNG |
| `protected-02` | 约 4.983240 m/s 碰前法向速度；12.008770 s 内 2,658 条目标接触事件，粗围栏 h 实例 833 的破坏数 0；4 PNG |
| `foliage-02` | 实际车体平面位移 247.579187 cm；tilia_b 实例 91 局部触及 1 / 保留 179 个三角形；硬物破坏 0，延迟清理后活动数 0；16 PNG |
| `geometry-03` | 源几何局部断裂、相邻实例碰撞保留、9 项保护名称检查及延迟清理通过；按几何探针范围统计，16 PNG |
| `contact-regression-01` | 原生物理稳定、前进、制动、倒车和无训练模式复位 5/5 通过；8 PNG |

六项破坏用例共 84 PNG，加驾驶回归 8 PNG，共 92 PNG。最终汇总再次核对 2,075 个唯一受保护输入的当前 SHA256，并绑定每份原始验证报告。汇总状态 `passed`，完整入口 `release-verification.json`，源码快照包括运行时、配置、构建依赖、导入准备与测试脚本。

人工图像检查读取了正向碰撞前/0.3/1.2/4.5 s、反向 1.2 s、植被碰撞前/1.2 s，以及最终 build-06 植被 8.3 s 的原生截图。薄板保留源外形与纹理，受击部位独立运动、未受击部分保持；植被主体保留，局部叶片延迟回收。粗围栏原源碰撞采用覆盖梁间空隙的整体近似碰撞体，此处保留为受保护静态结构。

## 6. 保留的失败与诊断证据

| 证据 | 实际原因与处理 |
|---|---|
| `cpu-geometry/audit`（CPU-01） | 首个资产保存后，bounds 浮点运算出现微小舍入差异，严格逐值比较触发失败；拓扑、UV/布局和材质绑定未变。失败报告及已保存资产的基线保留。 |
| `cpu-geometry-02/audit`（CPU-02） | 按最终端点 ULP 判断，在接近零的下界发生 origin−extent 消减时错误拒绝；tilia_a 的 loZ 约变化 1.31×10⁻⁶ cm。比较改为 origin/extent/端点的 float32 舍入误差传播，并保留逐项误差预算。 |
| `build-01*`、`build-02*` | `DestructionProbe.cpp` 的 `UE_LOG` 宏置于无花括号的 if/else，产生 C2181 非法 else；修复控制块后重新构建。两次原始日志完整保留。 |
| `geometry-01` | 临时地图夹具缺少按 map 目录相对解析的 `generated-collision/manifest.json` 及 RMI 依赖，启动日志为 `IMPORT_LAB_FAILED generated collision JSON missing or beyond size limit`。最终报告同时检出运行期间 `ImportLabGameMode.h` 变化。夹具现复制并哈希完整 generated-collision 与 overview.json；本次证据保持 failed。 |
| `forward-01`、`forward-02` | 原生均写出 PASS，包装脚本在最终 JSON 序列化阶段卡顿；共同根因为 Windows PowerShell 5 的 `Get-Content` 字符串附带 PSProvider/PSDrive 扩展元数据，`ConvertTo-Json -Depth 30` 递归展开。两次标记 incomplete，保留原记录与日志；forward-02 已捕获原生 exit 0，forward-01 的最终退出码未完整保存。 |

forward-01 排查时曾怀疑无界 `WaitForExit()`，随后改为有限等待与显式退出状态读取。这项调整作为进程管理健壮化保留；两次卡顿的已确认共同根因统一记录为最终报告的扩展字符串递归。

日志读取现使用 `IO.File.ReadAllText/ReadAllLines` 返回普通字符串，并输出 `native_exit`、`validation_passed`、`finalizing_report` 阶段。独立有界离线验证对完整报告重哈希 2,075 个输入、读取 19 条日志、解码 16 PNG 后，约 1.95 MB 的 JSON 在 **39 ms** 内完成序列化，整个检查 **5.81 s**，工作集 **138.2 MB**；检查没有写回历史证据。随后 forward-03 完成整个包装流程。

## 7. 当前覆盖边界

- 另一块板与下桩保持静态实体；当前余部尚未进入二次破坏流程。
- 当前用例在首次破坏后制动观察，**尚无本实现的破坏后穿越门验收**。旧实现中整段围栏删除后的穿越记录保留为历史版本证据。
- Foliage 使用非阻挡局部叶片事件及实际车体位移核对；硬物用例要求真实精确目标接触。
- 源几何探针、CPU 属性准备、真实接触驾驶和截图各自记录。build-05 保留历史模块身份；build-06 的全部六项用例与驾驶回归绑定最终模块。
- 本轮为原生 Windows UE5.8.4。Ubuntu 未运行。
- 原 FBX/RMI/源材质和保存 World 保持只读；本次破坏状态只存在当前运行会话，重新加载地图恢复源实例。
