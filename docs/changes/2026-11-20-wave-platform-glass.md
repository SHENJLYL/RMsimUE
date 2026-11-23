# 两侧长玻璃墙对齐波浪通道高平台

日期：2026-11-20，Asia/Shanghai。用户要求：左右长玻璃墙紧挨波浪通道所在高平台的两侧。本次已在活动 RMUC2027 场景完成。

## 实际效果

两面长墙沿 X 方向保持连续，玻璃内表面改为贴合原波浪平台的最外侧轮廓。墙脚继续落在馆地板，短墙长度和四个转角随两侧新边线收拢。

| 侧面 | 实际 UE 平台边缘，ROS Y / m | 相对上一版向内移动 / m |
|---|---:|---:|
| 红方波浪平台侧 | 7.522841186523437 | 0.478724746704102 |
| 蓝方波浪平台侧 | -7.522840576171875 | 0.478725357055664 |

原 RMUC 外底板仍为 Y=±8.001565933227539 m；其两条低边带保留在新墙外。活动 `physics_contract` 为 `outside_platform_bounds_v1`，`perimeter.target_bounds_m` 单独声明高平台边界，原 `bounds_m` 继续绑定原 CAD。`outside_source_bounds_v1` 和历史 inset 合同保留原行为。

源平台外侧面有 1.5° 外倾，最外轮廓位于约 Z=0.9024 m 的高端头。竖直玻璃贴合该最外轮廓，主平台低顶边与竖墙之间约 23.56 mm，源斜侧面各高度保持原形。玻璃、立柱、门饰及碰撞体均朝目标边界外侧展开，平台与波浪通道完整保留。

源馆、外景、灯位与地板高度本次保持。馆地板顶面和 RMUC 底面仍为 Z=-0.3009999990463257 m，玻璃底端也在该平面。额外新增看台和座位为 0；原源材质、清晰窗户、23 个 native mesh / 57 个实例 / 26 棵树保持。AIM 源模型、5 m/s 手动设置和 2000 N/m、140 N·s/m 悬挂保持。

## 几何依据

源 `preview.blend` 实际三角形与 visual/collision FBX 均已读取。源 red_road / blue_road 最外坐标为 7.522842884063721 / -7.5228424072265625 m。首次加载使用这些源坐标，在原 1 μm 核验下暴露了 FBX→UE 导入浮点偏差。

随后直接读取四个已保存 UE 资产的 LOD0 顶点和 `GetBoundingBox`。两种表示、visual 与 collision 的外极值一致；采用表中实际 native 坐标后，锚点误差为 0。原 1 μm 门限保持，初次失败及源到 native 的 1.70/1.83 μm 转换差保留在证据中。运行时同时核对两个平台的真实 visual/collision 组件 world bounds，避免仅由配置自证对齐。

## 原生验证

最终 Win64 Development Editor 编译退出 0，模块 SHA-256：

`d1f1a51b8ab26e495fd3009ff21cfed54b4780e1485a75634b3160fded634496`

- 红侧、蓝侧与全场三幅 D3D12 / SM6 / Lumen 实际图像已查看。每幅各有 96/96 边界扫掠通过、314 个边界构件核验；同一几何在三个视角重复检查，数量分开记录。
- 两侧各 21 块长墙玻璃的实际内面均匹配目标边线，最大数值差约 1.78×10^-15 m。四个碰撞墙的内面同时对齐；角部重叠 0.48 m。
- 玻璃底端实测 -0.30099999904632585 m，与馆地板的差约 1.7×10^-16 m。
- 南侧真实轮驱动的静置、接近碰墙、持续顶压、制动、倒退和最终停止共六项通过。接近阶段峰值 1.6042 m/s，确认接触前行进 66.8844 cm，持续驱动 1.5003 s，倒车脱离 60.0072 cm；没有运行中位姿覆盖或复位。
- 物理测试使用 NullRHI，渲染由上述三幅图独立验证。底盘/车轮 AABB 对原 CAD 边界最小余量 46.9098 cm；对新目标墙平面的最小余量 -0.962728 cm，满足原生既有 -1 cm 数值门限。该 AABB 指标与真实网格接触分开记录。
- 正式手动入口当前可见窗口 PID 41096，连续观察 60.02 s，六类 GPU 崩溃/资源溢出警告均为 0，窗口保持打开。

## 导航与可复建性

规划栅格、定位栅格及表面 provenance 统一生成到 `navigation-console-platform`，活动 manifest 指向该包。实际 installed helper 和正式 `prepare_scene` / `validate_localization_map` 已通过。原 CAD 导航包与旧 outside 包共 17 个文件保持。

物理边界身份：`f416f6520940d821f62590e979fb2ade480e487822cb49667fafa2852ece126e`。

14 项离线测试通过，包含旧合同输出等价、非法目标、真实栅格、过期边界、精确复建以及平台合同误选无边界旧包的拒绝。后者在修改前 guard 上实际失败、修改后通过。边界导出 recipe 继续绑定源几何；两个 scene.json 使用精确路径 `.gitattributes -text` 保存原 CRLF 字节，避免 checkout 改写造成身份失配。

本次完成导航数据同步和启动预检；新边界下完整 Nav2 路线和 Ubuntu 原生运行按各自任务验收。

## 证据与复现

主记录：`artifacts/fixes/school-wave-platform-walls-20261120/release-summary.json`。

源几何和原生边缘在 `source-platform-audit-01` / `native-edge-audit-01`；最终图像和逐视角命令在 `native-views-02`；真实接触命令与原始状态在 `physical-boundary-01`；导航与合同在 `contract-tests-02`、`navigation-generation-02`、`navigation-preflight-03`；交互窗口在 `visible-final-01`。保留 `native-views-01` 的严格核验失败。

```powershell
python tools/unreal/capture_school_gym.py --output <新证据目录> --views WavePlatformNorth WavePlatformSouth WaveWallOverview --capture-delay 30 --timeout 600
```

日常运行仍使用根目录 `Play-ManualVehicle.cmd`。原 SKP、贴图、FBX、UE Content、日志、PNG 和稠密几何保持本机；源码、配置、变更与精简记录进入本地提交。
