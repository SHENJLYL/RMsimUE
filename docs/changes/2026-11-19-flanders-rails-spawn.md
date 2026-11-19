# 2026-11-19 弗兰德斯钢轨实体与真实战雷出生点

弗兰德斯的全部 34 个原始钢轨对象已加入实体碰撞。默认出生点改为原战雷 Dom 任务 Team A 第二坦克出生区的 `t1_tankSpawn02_zone01`，正式入口继续使用 `Play-WarThunder.cmd`。

## 钢轨来源与实体生成

遗漏来自原图的资源分组：钢轨以材质 `railroad_rails_tile_a` 合并在 `world_static_000` 中，源 SCol 未覆盖这 34 个 SCN 对象。此前生成清单覆盖 14,349 个枕木、37 个横枕和 4,068 个路肩。

准备器现在从 `static-02/scene.json` 及对应 NPZ 中选择全部 160,336 个原钢轨三角形。每个 SCN 对象使用一个中心局部坐标的静态 BVH，按原世界位置生成单实例 RMI；运行时继续使用既有 QueryAndPhysics / BlockAll、同步空间加载和出生锚点机制。钢轨保持坚硬、固定的实体。

新增来源契约固定原 SCN、34 个 NPZ、原世界视觉 FBX 和 identity RMI 的 SHA256。原视觉的 109 个重复 draw 面按原导入器拆点规则复现，随后逐三角集合与 7 个材质分配独立比较。原有 203 个碰撞网格的几何保持一致；补充总量为 237 个网格资产、951 个 cell、18,488 个源实例。

长钢轨 FBX 导出的 `-90.00000933466734°` 节点旋转会产生可测横向偏差。新导出仅将钢轨的已知正交节点规范为精确 `-90°`，逐元素指纹证明其余 FBX 内容保持。原生边界 0.01 cm 与射线 0.05 cm 门限保持；实际最大边界误差为 0.00390625 cm，最大射线位置误差为 0.001987410103339471 cm。

## 实际任务出生点

来源为本机只读的 `War Thunder/mis.vromfs.bin`，经过以下任务链核查：

- `vlaanderen_dom.blk` → `template_vlaanderen_dom_battlearea.blk` → `vlaanderen_dom_template.blk`。
- `areas/t1_tankSpawn02_zone01` 属于 `dom_t1_tank_spawn_02_arcade` 和 `dom_t1_tank_spawn_02_hardcore`。
- 通用 `dom_template.blk` 将这些组注册为 `team=A`、`tags/tank=true` 的 `missionMarkAsRespawnPoint`。

| 配置 | 值 |
|---|---|
| 默认 ID / 队伍 | `default` / `blue`，对应原 Team A |
| 原出生区中心，Dagor 米 | `[439.0, 1.0499999523162842, 148.3000030517578]` |
| RMsim 地面支撑点，ROS 米 | `[439.0, -148.3000030517578, -3.467716648308154]` |
| 航向 | `-2.0472659359862844 rad`，由原区域第一基向量派生 |

水平坐标保持原始值，高度垂直投影到原 HM2 精细地形。投影距离约 4.5177 m，仍在原约 10 m 半径出生 Sphere 内。`GroundMetres` 表示地面支撑点，车辆包既有离地偏移由原创建路径应用一次。完整任务注册、源包身份、坐标转换、地形与派生航向记录在 `assets/maps/wt_vlaanderen/spawn-source.json`。

## 原生验收

最终模块 SHA256：`d19c15bcf6a4eddfe77af506dfb15bc8eed72b4bb8fdea6199fc5a2750d70e52`。World 保持 `3311399568469e48d8580f1b41897ad5899321b51617638dd2c541e875ef1066`。

| 范围 | 结果 |
|---|---|
| Windows UE 5.8.4 构建 | 退出码 0 |
| 保存资产重载 | 237 个源绑定资产通过，前后受保护文件一致 |
| 原始几何对应查询 | 280 项通过，含 12 次空隙查询；覆盖 34 个轨顶、轨侧、成对轨顶、双轨空隙、枕木空隙与弧形路肩空隙 |
| 范围管理 | 5 个移动/返回阶段及 40 m、200 m 两档量程通过 |
| 真实钢轨接触 | 8 项通过，8 PNG；指定钢轨实例记录 908 次正冲量/前向阻挡接触，接触车体 `Contact_body`，峰值约 6.4629 N·s |
| 顶住、制动与倒退 | 0.35 m/s 指令持续 2.5 s，前进量约 -0.000823 cm；倒退脱离 70.4814 cm，钢轨实例及变换保持 |
| 新出生点驾驶及复位 | 5 项通过，8 PNG；前进 340.792 cm、漂移 -0.397 cm，制动速度 0，复位误差 0.1214 cm |
| 正式入口 | `Play-WarThunder.cmd` 返回 0；默认出生、生成碰撞和车辆物理就绪，交接进程 PID 8180 保持运行 |

钢轨测试只在显式 `-ContactProbe -RailContactProbe` 下执行，使用原车轮电机、约束和 Chaos 接触；没有姿态或速度覆盖。世界通道射线同时检查 blocking、组件和精确实例身份。当前 AIM 5 m/s 手动配置及 2000 N/m、140 N·s/m 悬挂保持。

上述驾驶/复位由原生验证入口执行；本轮未以代理发送 OS 键盘输入。实际检查了钢轨受阻、倒退和新出生点复位的原生画面。正式窗口保持用户当前焦点，前台被其他应用覆盖的桌面截图未作为游戏画面证据。

## 重现与回滚记录

精简验收记录：`artifacts/maps/wt-rails-spawn-20261119/release-summary.json`。完整记录保留在同目录：

- `rail-preparation-03`、`source-probes-02`、`rail-author-02`。
- `rail-native-audit-03-final`、`native-rail-contact-02`、`native-spawn-01`、`formal-entry`。
- `baseline` 保留修改前源码、地图配置、生成碰撞和 UE 派生资产；两次 canonical 替换也保留旧目录。

原生车辆接触可用 `tools/unreal/Test-WTRailContact.ps1 -FixtureFile artifacts/maps/wt-rails-spawn-20261119/rail-contact-fixture-source.json -EvidenceDirectory <新的 artifacts 子目录>` 重现。普通出生点回归继续使用 `Test-ManualControls.ps1 -Contact -ContactReset`，MapFile 指向正式弗兰德斯 map。

准备阶段的重复 draw 校验失败、旧 FBX 轴尾差导致的 author 失败、随之缺资产的第一次 query 失败、组件射线 overlap-all 语义导致的第一轮探针失败全部保留。最终批次逐一核对子进程退出码和原生报告，最终构建、重载、接触及出生点报告绑定相同模块。

最终执行 6,842 项输入哈希比较，包含两套测试的运行依赖与 fixture 副本。正式出生来源文件仅在验收后补写 `support.native_validation`，原坐标、来源身份及运行地图字节保持，并同时记录原测试快照和完成注释后的 SHA。大型源资产、派生网格、密集报告与截图保持本地；本轮验收范围为 Windows 原生。
