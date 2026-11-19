# 2026-11-20 — 源馆地板承托 RMUC 场地与零净距玻璃围墙

## 当前结果与范围

按用户要求，馆体地板已与 RMUC 真实底面接触，玻璃内面直接接比赛场地外沿。修改落在源馆/场外场景的统一高度及围墙配置中，原 RMUC CAD、车辆 rig、drive profile 和导航数据保持。当前仍是 23 个 native mesh、57 个布局实例、26 棵树，原看台保留、额外新增座椅为 0。

CPU 几何与三个原生视角的边界/玻璃底面检查通过。南侧、角部和窗外三张实际图像已直接复核：地板与场地连接处没有悬空缝，外景清晰度保持。最新可见 GPU/交互窗口连续观察 150.02 s，通过并保留给用户试驾。2026-11-19 外景与 152.88 s GPU 记录保留于 [源场馆还原记录](2026-11-19-school-gym-fidelity.md)，各自绑定原模块和布局身份。

## 真实支撑面测量

源 RMUC `visual/floor.fbx` SHA-256 为 `7728a38a4a452fb45aa930a9ed4f43a523fe95c358077f8d254e19646fdfeeed`。CPU Blender 读取实际三角面和世界顶点后得到：

| 项目 | 实际值 |
|---|---:|
| RMUC 最低承托面 | Z = −0.3009999990463257 m |
| 同一最低面三角数 | 14 |
| 最低面投影面积 | 435.2569666862364 m² |
| 底面规则网格 | 493 点中 478 点命中原 floor；478 点全部处于同一最低平面 |
| 长边附近顶面 | 约 −0.21051359 m |
| 长边边缘厚度 | 约 0.09048641 m |
| 短边中段/中央隆起最大高度 | 近 Z = 0 m，对应厚度约 0.301 m |
| 原源馆运动地板顶面 | Z = −0.4365999984741211 m |
| 修正前真实竖向间隙 | 0.13559999942779544 m |
| 应用的馆体统一上移 | +0.13559999942779544 m |

15 个未命中的网格点位于原 floor 自带缺口，包含中心点；原场地其余独立构件保持。支撑判断来自大面积真实底面，未采用孤立最低顶点。BVH 射线浮点误差小于 1.2×10⁻⁷ m。

源馆 `RMSchool_0_0/0_1/0_3` 的真实地板三角形按保存 native bounds 与活动实例变换计算，wood、red、Tile26、PAVER16 地面共面。1 μm 平面精度下，源地板覆盖完整 RMUC 包围区域 **448.087567974944 m²，未覆盖面积 0**；Z = −0.335 m 的源门槛与比赛 footprint 交集面积为 0。

上移后又检查全部 19 个源馆网格、881,688 个三角面。比赛 XY 内侧 1 μm、Z = 支撑面上方 1 μm 到 4.5 m 的体积中，三角包围盒候选 **0**，实际侵入 **0**。未裁源馆面、未另加地板或座椅。

CPU 证据：

- `artifacts/fixes/school-view-fidelity-20261119/field-floor-contact-01/rmuc-floor.json`；Blender PID 24860 正常退出。
- `artifacts/fixes/school-floor-contact-20261120/source-floor-audit-01/report.json`：实际源地板、native 变换、三角覆盖并集与门槛交集。
- `artifacts/fixes/school-view-fidelity-20261119/field-floor-contact-01/support-lift-intersections.json`；Python PID 44600，exit 0，4.82 s。每个 OBJ SHA 与已通过的 native 导入源身份一致。

## 应用变更

`tools/maps/apply_school_floor_support.py` 将活动布局中的全部馆体、场外地面、树木、建筑、家具及室内灯统一增加上述 Z 偏移。XY、尺寸、旋转、实例数和相互接触关系保持；原几何/贴图/native 资产无需重新导入。实际应用及前后字节身份在 `floor-support-apply-01/report.json`，回滚文件保存在同目录。

`arena-shell.json` 使用：

| 配置 | 当前值 |
|---|---|
| `physics_contract` | `outside_source_bounds_v1` |
| `visual_alignment` | `source_bounds_outer_face` |
| `outside_clearance_m` | 0 |
| `visual_floor_z_m` | −0.3009999990463257 |
| 碰撞墙厚度 | 0.48 m |
| 碰撞墙下/上端 | −1.25 / 4.80 m |
| 装饰门 | 四扇固定关闭，连续墙覆盖门区 |
| 各侧框架厚度 | 朝场外展开 |

相对前一 1 mm 配置，四面物理边界的内表面向场地移动 1 mm，最终接源外沿，墙体仍全部在场外。原比赛场地碰撞、导航包和车辆物理参数未修改。角部墙体各方向重叠 0.48 m；门把手和铰链保持场外位置。

`CampusGym.cpp` 新增可选 `visual_floor_z_m`，默认 −0.21 m 兼容旧布局。地板相连构件只下延下端，上端保持：玻璃和底轨到 −0.301 m，立柱底部继续埋入馆地板 1 cm。视觉基座调整独立于物理墙的下端/上端。

## 原生验证与身份

Win64 Development Editor 构建通过，exit 0，19.23 s。模块 SHA-256：

```text
ed5fd48edf3a5e6b5c3d8bf533c01c18170b0aa3d578d8a6707a7d67b7097459
```

当前 arena SHA-256：

```text
5c026a16b87267f374e3755a0fa8497b8e524b6f3ec74793eb20c9607ddbec02
```

当前 layout SHA-256：

```text
64bfb32766056f33a4b706c2656472f3d2d7f954187128cd2b9cc37a91710300
```

`native-contact-01` 捕获 `FloorContactSouth`、`FloorContactCorner`、`FieldEndWindow` 三个真实场内视角。每个视角实际检查：

| 检查 | 结果 |
|---|---:|
| 四边、门区和四角物理扫掠 | 96 / 96 |
| 独立边界构件包围盒 | 314 |
| 最小边界净距 | −1.7763568394002505×10⁻¹⁵ m |
| 既有数值容差 | 1×10⁻⁶ m |
| 所有玻璃实际下端 | −0.30099999904632585 m |
| 玻璃下端与目标地板误差 | 约 1.7×10⁻¹⁶ m |
| 四角最小墙体重叠 | 0.4800000000000004 m |
| native 网格 / 布局实例 | 23 / 57 |

`all_glass_bases_meet_support_floor=true`、`all_boundary_parts_outside_source_bounds=true`，三个视角报告状态均 passed。既有 1 μm 数值容差保持。记录路径：`artifacts/fixes/school-floor-contact-20261120/native-contact-01/`。

## 最终视觉与 GPU

已直接查看 `FloorContactSouth`、`FloorContactCorner` 与 `FieldEndWindow` 三张原生图像。南侧和角部显示 RMUC 场地实际落在馆地板上、玻璃基座直接贴边；窗外树木与远景保持清楚。

`tools/unreal/observe_school_interactive.py` 已完成本版可见交互窗口 PID 52940 的 **150.02 s** 观察，状态 `passed_kept_open`，窗口保持运行供用户试驾。GPU timeout、GPU Crashed、TerminateOnGPUCrash、Aftermath dump、Non-Nanite marking queue overflow、Virtual Shadow Map pool overflow 六类警告计数均为 0。随后使用显式 Win32 函数类型发送 WM_NULL，约 **1.36 ms** 成功响应，`hung=false`。

实际记录为 `visible-current-01/report.json` 与 `window-response.json`。`tools/maps/record_school_floor_contact.py` 已生成本阶段 `release-summary.json`，状态 `accepted`，SHA-256 为 `fb44694fde58661be3ca184b67ab173b7cd66ecb24c782cb20392faf128984b4`。该汇总绑定当前 arena、layout、模块、CPU 源面审计、原生三图与可见运行身份。

完整原始图像、日志、CPU 三角详证和回滚数据保留本机，精简验收随源码/配置提交。

源看台与原灰色屋架保持。原篮球记分牌三维字模镜像按独立遗留项记录，本次工作围绕真实地板接触、围墙贴边及保持场内外景完成。
