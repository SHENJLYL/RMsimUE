# 2026-11-19 — 源学校体育馆还原与场内视野

## 当前状态

源体育馆与场外校园已导入独立的 `/Game/RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity` 命名空间，并通过源材质、导出几何和保存后的 native LOD0/fallback 几何核验。最终外景布局有 **23 个 native mesh、57 个布局实例、26 棵场外树，额外新增座椅为 0**。

2026-11-19 外景呈现阶段已完成原生三视角及人工图像复核，152.88 s 可见窗口 GPU 运行通过。汇总 `artifacts/fixes/school-view-fidelity-20261119/release-summary.json` 绑定该阶段的实际资源和模块。初始 `activation-01` 的 pending 状态保留为导入后的历史检查点。

2026-11-20 按用户追加要求将馆体地板接到 RMUC 真实底面，并把场外净距从 1 mm 收到 0；最新活动配置、图像与运行记录见 [地板承托与直接贴边](2026-11-20-school-floor-contact.md)。下文 1 mm 表格及 2026-11-19 GPU 身份按本阶段历史值保留。

[初版接入记录](2026-11-19-school-gym-sketchupbox.md) 保留旧座椅、裁面、资源数量和当时的验证身份。本次活动布局采用下述源还原与定位策略。

## 源模型、看台与材质

用户提供的 `Indoor Basketball Gym.skp` 保持只读，SHA-256：

```text
a40bbf0c69bace3b5ee1aed3795217e69c88c4c58ce71b191aff3e033e3502bc
```

完整保留源体育馆的原有看台、阶梯、钢构、栏杆和窗户；从派生模型移除两套篮球架。源馆保留 881,680 个三角面，篮球架移除 9,908 面，另保留 8 面赛事品牌图形，组成 881,688 面学校几何。旧额外添加的 144 组六连座椅和 72 段阶梯已退出活动布局。

OpenSKP 的初版转换遗漏了组件实例的继承材质。本次按 5,420 个源实例上下文恢复 3,892 项继承材质分配，保留 8 张源纹理与 48 种材质变体。原 GLB 的 POSITION、NORMAL、索引和节点变换保持字节一致；继承纹理的默认 UV 按源纹理尺寸修正。未绘制默认面采用中性白色，原 SKP 样式颜色未由该读取器暴露；已绘制表面的源材质继承独立保留。

来源证据：

- `assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity/source-material-audit.json`
- `assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity/source-fidelity-verification.json`
- `assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity/geometry-byte-verification.json`

这些 preparation 记录保留各自生成时的候选身份；正式活动 prepared 身份以下文最终导入与激活记录为准。

## 馆体平面对齐与玻璃边界（2026-11-19 阶段）

原馆篮球运动区中心位于源坐标 `(22.9023125, 24.6729248) m`。本次将该运动区中心对齐原 RMUC 场地；相对旧馆体位置，UE 平面位移为约 `(-0.0158752, -7.1650915) m`，ROS 平面位移为约 `(-0.0158752, +7.1650915) m`。源 RMUC 比赛场地、车辆、现有物理及导航数据保持。

源馆定位修正后，原看台位于比赛区之外。旧按面心剔除三角面的流程留下 101 个侵入面中心，在 121 条取样射线中有 16 条命中源看台残留；新位置的源几何核验在比赛区体积内找到 0 个三角形包围盒候选，121 条同类射线均无源馆侵入命中。该记录是 CPU 源几何核验，后续原生场内视角单独记录。

2026-11-19 外景验收时 `arena-shell.json` 的合同为：

| 项目 | 该阶段值 |
|---|---|
| 物理合同 | `outside_source_bounds_v1` |
| 视觉对齐 | `source_bounds_outer_face` |
| 玻璃内面到源场地外边缘净距 | 0.001 m |
| 碰撞墙厚度 | 0.48 m |
| 碰撞高度 | Z = −1.25 ～ 4.80 m |
| 可视玻璃顶部 | Z = 2.60 m |
| 装饰门 | 固定关闭、无开门交互 |
| 框架深度方向 | 朝场外 |

该阶段通过馆体平面对齐处理源看台覆盖，保留 1 mm 场外净距。2026-11-20 的后继版本使用 0 m 净距及实际地板支撑高度；原 `preserve_validated_arena_shell_v1` 的内缩 0.25 m 数值仍用于对应历史 fixture。

另修正 `RMSchool_2_1` 的导出 bounds 元数据：将旋转后局部 AABB 估计替换为实际导出 OBJ 顶点包围盒。实际 OBJ 最小 X 为 `-0.035949 m`，与 native `-3.594899892807007 cm` 一致。最终学校布局的中心和尺寸取 native 保存资产 bounds，以保持导入网格的原位置和单位缩放。

定位与包围盒证据：`artifacts/fixes/school-view-fidelity-20261119/geometry-audit-01/` 下的 `report.json`、`RMSchool_2_1-bounds.json` 和 `source-bounds-obj-01.json`。

## 场外环境与透明围墙

场外保留完整源树几何和 2K 源贴图。22 棵近树在根部锚点上放大到 1.5 倍，另有 4 棵远树，共 26 棵；四组教学楼朝远离场馆方向平移 18 m。实际草地三角面核验树根与地面接触。近树尺度和远树排列均为派生校园呈现配置，源树文件保持。

照明天空源为 Poly Haven CC0 的 `kloofendal_48d_partly_cloudy_puresky`，分辨率 8192×4096，源文件 SHA-256：

```text
30ff6bb1eec58c11b52254e89b5dc39aaf756dd332c9a14a4976d68f23feaa71
```

原生天空 cubemap 与清透玻璃材质位于 SchoolGymFidelity 独立命名空间。原生导入记录包含 cubemap 的 2048 面上限、玻璃 roughness 0.02、无 refraction 等实际保存属性。源 8K equirectangular 分辨率与 native cubemap 单面上限分别记录。

相关来源：

- `assets/maps/rmuc2027/arena/sketchupbox-school-gym/environment-fidelity/SOURCE.json`
- `assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity/tree-fidelity-source.json`
- `artifacts/fixes/school-view-fidelity-20261119/native-import-02/report.json`

可见远景另用同一环境的完整 `kloofendal_48d_partly_cloudy` 8K HDR，保留远处地形、绿植和建筑，源 SHA-256 为 `d4cbdbc4ff7ea71e7266ae57cdca4e38f3222029250664ec5f374dee6630a079`。新增 `M_SchoolLandscape` / `T_SchoolLandscape8K` 两个独立资产，原已逐面核验的 111 个 native 文件 SHA 全部保持。来源为 `environment-full-fidelity/SOURCE.json`，导入记录为 `landscape-native-01/report.json`。

最终光照配置为 sun 6000、sky 64、EV100 8.8、矩形灯 45000、背景亮度 512，屏幕比例 100%，无景深模糊。`FieldEndWindow` 可见树干、树冠、阴影及远景；`FieldSky` 可见窗外树冠、天空和云层，近楼对窗口的遮挡解除。原源馆灰色屋架保持其源材质，未按参考效果图另行改色。

## 保存后的原生几何核验

`native-geometry-02-all` 在独立 UE 进程中重新载入全部 23 个保存资产，读取 `StaticMesh RenderData.LODResources[0]` 的位置、UV0、材质槽和定向三角面。Nanite 开启的资产对应完整 fallback；普通透明几何对应常规 LOD0。Nanite cluster 编码结构保持独立核验范围。

完整 fallback 显式采用 `PERCENT_TRIANGLES`、fallback percent 1、fallback relative error 0、keep percent 1、trim relative error 0，并使用全精度 UV。UE 5.8 源码在此条件下直接保留原输入 fallback buffers，后续 Nanite cluster 位置编码不修改这些 buffers。

| 核验项 | 结果 |
|---|---:|
| 源三角面 | 1,959,036 |
| native LOD0/fallback 三角面 | 1,956,048 |
| 原 2 ULP 规则直接匹配 | 1,956,042 |
| 有明确源 representative 的 UV 合并匹配 | 6 面 / 10 corner |
| 已证明零面积或完全重复面差额 | 2,988 |
| 未知丢面 | 0 |
| 额外 native 面 | 0 |
| 当前 native 文件身份匹配 | 111 / 111 |

6 面差异均来自 `RMSchool_2_0`：位置、材质槽和绕序完全相同，native UV 精确对应更早且已匹配的源 corner，同位置、同源 normal，差值满足 UE `UVsEqual` 固定 `1/1024` 条件。CPU reconciliation 保留原 2 ULP 规则和原始失败证据，对这 10 个 corner 单独逐项证明。最终 source/native 计数全部闭合。

关键执行记录：

| 阶段 | 实际结果 |
|---|---|
| `native-import-02` | UE PID 21880，exit 0，232.656 s |
| `native-geometry-02-all` | UE PID 35020，exit 0，63.566 s；保存 22 个通过报告及 1 个 UV 合并差异报告 |
| `native-geometry-final` | CPU PID 15588，exit 0，1.83 s；23 mesh passed，未知差异 0 |

最终报告为 `artifacts/fixes/school-view-fidelity-20261119/native-geometry-final/report.json`，SHA-256：

```text
aaa0840e10b8b6073a03dc57074bbc02ad85f02a76aeff20167d1a7a80a15f19
```

已激活 prepared SHA-256：

```text
a6d29420c1b8e16bc036c17a700bc441ac1f679688e1f7061f571420dc6a0d12
```

原始失败 native 报告、逐面详证、源码行号与 SHA、包围盒误差和 Nanite 自动 fallback 简化诊断保留在同一证据根目录。原生读取完成后，CPU reconciliation 对全部源 OBJ、原 native 详证和当前 111 个 native 文件重新核对身份。

## 当前入口、复现与分发

活动清单为 `assets/maps/rmuc2027/arena-shell.json` → `arena/sketchupbox-school-gym/layout.json`，普通试驾入口保持 `Play-ManualVehicle.cmd`。新源馆流程使用 `restore_school_source_materials.py`、`prepare_school_gym_fidelity.py`、`assemble_school_fidelity.py`、`import_school_fidelity.py`、`verify_school_fidelity_assets.py`、`reconcile_school_fidelity_geometry.py` 与 `activate_school_fidelity.py`；各阶段以输入/输出 SHA 和保存的实际命令为复现依据。

SketchupBox 模型按随附 readme 保留来源署名及禁止单独出售/重新分发模型的边界。原始 SKP、GLB、OBJ、源纹理、native UE Content、完整日志和逐面稠密证据保留本机。Git 提交范围为源代码、配置、许可/来源说明和精简验证摘要；Poly Haven、ambientCG 及 DJI/RoboMaster 标识分别按各自来源和权利记录。

## 已完成的原生视觉与 GPU 记录

- `final-views-02` 的 `FieldEndWindow`、`FieldSky`、`BoundarySouth` 三张实际渲染图已直接查看；各视角 96/96 边界扫掠通过。图像、原生报告和人工观察绑定在 `visual-review-01/verification.json`。
- `visible-gpu-01/report.json`：实际可见窗口，UE PID 50148，运行 **152.88 s，exit 0，全部检查通过**。GPU timeout、TerminateOnGPUCrash、GPU Crashed、Aftermath dump、Non-Nanite marking queue overflow、Virtual Shadow Map pool overflow 六类计数均为 0。
- 该阶段 arena SHA `dbbdb75241c3b584819e98f450bc6b4d28341e8c72f21b62076bd0bf6e13041d`、layout SHA `8c70305d0631ffce6081b9bebdde38b94c63604790d7135e91f9fc25eedbe70e`、模块 SHA `96a1e532691590506254af97916dd42f048b9ef26d23b3667403b35b9baef102`。
- 精简汇总 `release-summary.json` SHA `938c0ed03aee999efd909549966393359b0069c6a0ce6e2ac6f82a9364918150`，范围为场内外景可见性和当时的场地水平边界。竖向支撑与 0 m 净距由 [2026-11-20 后继验收](2026-11-20-school-floor-contact.md) 单独闭合。
- 原篮球记分牌三维文字镜像作为独立遗留几何项保留；本阶段保持原源屋架和看台，未新增观众席。
