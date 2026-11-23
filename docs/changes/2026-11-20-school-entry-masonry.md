# 学校体育馆入口砖墙与连续远景

日期：2026-11-20。入口砖墙、连续远景与天空填充已完成 Windows 原生场景验收。

## 场景效果与尺寸

两侧体育馆入口采用现成 Grab3D **Brick Wall**，许可为 CC0-1.0。完整源模块保留砖体、压顶、两侧表面及原 PBR/UV；单模块原生导入维持 10,412 个三角形。原馆及校园的 23 个 native mesh、57 个实例保持，入口新增 1 个 mesh、24 个模块，活动场景合计 24 个 mesh、81 个实例。原有阶梯看台、树木、地块、灯位及 RMUC 场地继续使用既有布局。[源与材质证据](../../artifacts/fixes/school-entry-masonry-20261120/source-asset-diagnostics.json)、[原生导入](../../artifacts/fixes/school-entry-masonry-20261120/native-import-03/report.json)。

| 项目 | 活动配置 |
|---|---:|
| 砖墙布置 | 东、西入口各两段，每段 6 个完整模块 |
| 每段长度 | 15.3 m |
| 模块统一缩放 | 0.85 |
| 露出草地高度 | 约 1.7598 m |
| 墙厚 | 0.272 m |
| 中央入口净宽 | 19.2713 m |
| 墙内面到外侧台阶 | 约 4.2632 m |
| 地面埋入量 | 0.015 m |

四段墙位于 ROS X=-32.0158752 m / +31.9841248 m 的入口外侧，Y 范围分别为 [-17.7712915039,-2.4712915039] m 与 [16.8,32.1] m。原有两条入口铺装及路缘保持通畅；原 26 棵树的保守源三角形检查未发现墙体交叠。[布局与净距审计](../../artifacts/fixes/school-entry-masonry-20261120/static-placement-audit-01/report.json)。

## 天空与地面连续性

活动 `outdoor_background` 将天空球半径设为 8 km，并增加外边长 4 km 的四块地面环带。环带中央孔洞严格保留原 160×140 m 校园地面的范围，环带顶面接在真实草地 Z=-0.48440000057220454 m，厚 0.1 m 向下展开。新环带使用独立 `SchoolEntry/M_ExteriorDistantGround` 材质；背景几何采用 NoCollision、无导航影响、无投影阴影、无 Distance Field 光照影响。配置缺少该可选项时继续使用原背景行为。

远地面继承原 ExtGrass 最高平面两三角形的实际 native UV，拟合误差为 0。四个原地块分别采样同一源颜色与法线纹理，在源边界保持各自原相位，向外逐渐混合象限接缝，混合半宽最大 16 m。颜色在线性空间混合，法线解码后混合并在世界空间归一化，纹理坐标保持各自 affine 映射。原四地块、源纹理与入口砖墙资产保持；这次 native 更新实际只写入新远地面材质。[远地面 native 更新证据](../../artifacts/fixes/school-entry-masonry-20261120/ground-phase-native-01/report.json)。

活动天空填充强度为 512；曝光 EV100=8.8、太阳强度 6000、既有灯位与源材质参数保持。五幅最终原生图逐图核验后，远处地面硬接线已平滑，天空背景连续；原近景草地的大尺度纹理及局部接线保持其既有源表现。[视觉验收](../../artifacts/fixes/school-entry-masonry-20261120/visual-review.json)。

## 物理与导航身份

比赛场地、玻璃围墙、高平台锚点及车辆合同沿用已提交版本。物理边界身份保持：

`f416f6520940d821f62590e979fb2ade480e487822cb49667fafa2852ece126e`

新增入口墙与远景属于外部装饰，导航继续使用 `navigation-console-platform`。活动 manifest 已通过实际 installed helper 的 `prepare_scene` / `validate_localization_map` / `map_file_signatures` 预检，规划栅格、定位栅格和物理边界身份一致。[本轮导航预检](../../artifacts/fixes/school-entry-masonry-20261120/navigation-preflight-01/report.json)。

## 原生验证

源与布置快照 92/92 项通过，涵盖源声明、完整几何、原始像素、24 模块净距、原 23 个 mesh 及 57 个实例保持、导航与活动文件身份。[源与布置摘要](../../artifacts/fixes/school-entry-masonry-20261120/source-and-placement-summary.json)。

最终 `native-views-final-02` 包含 GroundSkySeam、EntryBrickDetail、EntryWestUpperDetail、EntryWestInside、DriverView 五视图，包装命令退出 0，运行 246.34 s。每幅分别通过 96/96 边界扫掠，确认 24 个 native mesh / 81 个实例、背景合同及既有高平台玻璃锚点；五幅 overview 图逐图完成视觉验收。[原生截图身份](../../artifacts/fixes/school-entry-masonry-20261120/native-views-final-02/report.json)、[逐图检查](../../artifacts/fixes/school-entry-masonry-20261120/visual-review.json)。

最终交互窗口 PID 35380 连续观察 60.03 s，7 次均为可见 1600×1000 窗口；六类 GPU 崩溃或资源溢出警告计数均为 0。WM_NULL 响应为 0.0000878 s，观察结束时窗口留开。[可见观察](../../artifacts/fixes/school-entry-masonry-20261120/interactive-observation-02/report.json)、[窗口响应](../../artifacts/fixes/school-entry-masonry-20261120/interactive-response-02/report.json)。

最终五视图与交互观察使用同一实际 DLL：

`27a87ddacd95d07938276d42cb610b1885175e86e69e906b6d037cf253b8ec2b`

本轮初始背景构建退出 0，记录模块为 `82d5a7017cacb27c0a41e186d9f48add5e4f19c9f309306b474eb62e32f2440c`；共享工程后续构建的最终运行身份如上分别绑定。前次视图及观察证据保留自身模块与文件 SHA。[初始构建](../../artifacts/fixes/school-entry-masonry-20261120/build-01/report.json)、[最终验收汇总](../../artifacts/fixes/school-entry-masonry-20261120/release-summary.json)。

## 文件与复现范围

源声明与 CC0 许可位于 `assets/maps/rmuc2027/arena/school-entry-brickwall/`。准备脚本为 `tools/maps/prepare_school_entry_brick_wall.py`，依赖同目录 `prepare_school_entry_wall.py` 的 GLB、哈希和表面完整性工具；UE 导入为 `tools/unreal/import_school_entry_assets.py`，活动配置脚本为 `tools/maps/configure_school_entry.py`。

代码、活动 JSON、许可与精简验收记录按 source、instrumentation、accepted scene 三组局部提交。原 GLB、纹理、UE Content、截图和稠密诊断留在本机证据目录。共享工程的其他任务及既有工作区变动各自保留。
