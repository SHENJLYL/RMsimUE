# 2026-11-18：统一工程与弗兰德斯细节修复

状态：本次目录统一、补充碰撞、材质与地面细节、草地分布和地图界面已完成原生检查及所列实景验收。正式入口 `D:/simulator/Play-WarThunder.cmd` 使用统一工程，最终画面证据绑定 V9 材质与重新烘焙的草地缓存。

## 一个工程、一套运行参数

当前运行工程为 `D:/simulator/unreal/RMSim/RMSim.uproject`。`Play-WarThunder.cmd`、`Play-ManualVehicle.cmd`、`Play-Training.cmd` 与 `Play-Navigation.cmd` 从同一根目录进入模拟器，按各自 MapFile 选择地图。

原地图工作树已移入 `D:/simulator/artifacts/archive/wt-vlaanderen-before-unified-20261118`。合并使用主工作树和地图工作树的实际未提交内容做三方比较；回滚快照、逐文件哈希、合并计划及资源复制记录保存在 `artifacts/integration/unified-20261118`。原导航、手动驾驶及并行场景工作保留各自来源。

车辆运行参数统一读取 [drive-profile.json](../../assets/vehicles/aim_sentry/drive-profile.json)：前进 5、倒车 2.5、横移 4、轮速上限 6.5 m/s；起步、松键减速、刹车分别为 2、4、7 m/s²；悬挂 2000 N/m、阻尼 140 N·s/m、预载 50 N、行程 65 mm。文件 SHA256 为 `fd1ece7218f9465f94090e31853cc191006427608e467e058a8081aa234f6a87`。自治导航继续从所选 sentry_ws YAML 读取其导航参数。

C 长按独立环绕观察，WASD 使用真实云台水平朝向；Shift 退出清理瞄准状态。最新组合模块的 6 项控制检查中，平地实际速度为 4.9777 m/s，制动归零；C 环绕偏角 90.55° 时驾驶与云台方向点积为 1，Shift 退出视角漂移为 0。[控制证据](../../artifacts/maps/wt-refine-20261118/controls-shared-03/summary.json)。

## 铁路枕木与路肩碰撞

新增生成碰撞覆盖 18,454 个原实例：14,349 个主铁路枕木、37 个另一类横枕，以及 4,068 个路肩实例。保留源变换和非均匀缩放；15,558 个刚性变换实例使用共享形状，2,896 个完整仿射实例按 cell 烘焙。

10 个共享主形状共 872 个三角形；资产合计 203 个，空间索引 917 个 cell。适合单凸体的两种路肩保留凸体，其余使用共享三角 BVH。真实枕木缝隙和弯路肩内凹空间保留。原源碰撞清单与生成补充分别记录。

运行组件按当前查询范围加载周边碰撞，额外保留 32 m 余量；行进 8 m 后刷新，并固定保留本会话出生点的碰撞。GameMode 的 PostPhysics 更新为下一物理帧准备范围。手动会话和启用 LiDAR 的会话分别读取相应范围。

| 原生检查 | 实际结果 |
|---|---|
| 保存资产 | 203 个逐项通过，206 个保护文件前后不变 |
| 几何查询 | 128 条 Visibility / RMSimLidar simple / complex 查询通过 |
| 空隙 | 枕木缝隙与弯路肩内部 8 次无命中检查通过 |
| 命中精度 | 最大源/原生命中差约 0.0198741 mm |
| 手动默认出生 | 18 cells、327 bodies / shapes；首次组件初始化 0.787995 s |
| 跨范围驾驶与复位 | 实际 W 驾驶 10.579605 m，无恢复计数增加；随后 R 回到本会话出生点 |

查询、范围切换与实际游戏的模块和缓存条件分别记录。该碰撞 GUI 会话最终 22 cells / 512 bodies，峰值 555；累计值包含更早的外部驾驶。完整生成、资产、查询与运行依据见 [碰撞指南](../guides/war-thunder-generated-collision.md) 和 [证据索引](../../artifacts/maps/wt-refine-20261118/collision-evidence/summary.json)。

## 材质、法线与地面清晰度

按源混合方式区分 157 个 Modulate、39 个 Diffuse 和 1 个仅法线的模型贴花。Modulate 使用线性数据与中性灰乘法，仅法线贴花使用独立通道；源深度淡出与相机方向偏移参与贴花叠放。车站实景原灰色矩形底已消除，源破损与熏黑形状保留。

地表接入 65 个地类及 23 张 1024² 原始细节图组成的纹理数组，保留 11 级 mip 与原始地类索引。另接入 12 个原始微细节槽位、11 张唯一 512² 贴图，槽 11 保留与槽 4 相同的引用。源 UV 尺度约 0.51 次/m，对应约 1.9608 m 周期、3.83 mm/源纹素。最终采样在地表贴花合成后保留微细节；画面结果由下述实景确认。

34 个 HeightDecal 的 `_r` 反射贴图按原 `.agrb` 通道恢复 smoothness、AO、reflectance 和 microdetail ID。无独立原法线的分支使用 diffuse alpha 局部梯度，并增加明确的 UE 适配参数 `HeightNormalStrength=0.2`，减小碎石过强的明暗斜纹。原始系数、颜色、ID、微细节与几何保持。

坡面规则格线来自对混合高度求导时带入地类权重和随机旋转变化。现在每个邻项固定纹理 slice、rotation 和 mip，先对连续 UV 求局部法线，再按原最终 pow4 权重混合。3,003 个源像素样点的颜色逐位一致，36 条真实分类边界的法线连续性检查通过。此路径明确记录为 UE 使用原 alpha 的法线适配。

最终 Ground40 保存通过：4 个父材质与 36 个实例，34 个 HeightNormalStrength 的真实读回一致，原 64 张 Height 源纹理和 26 个草输出节点保持。完整独立重载验证 307 个唯一资产、12 个父材质编译无错误、所有保存文件 hash 保持。Land / Terrain 使用 16 / 8 个 sampler。

实景分别检查 `green_slope` 与正式默认出生点：原约 0.5 m 的规则格线及碎石强黑斜纹块已消失，草坡底纹、站台灰石、路肩和碎石细节连续。原浅色碎石颜色保留。最终 80 帧真实 W 行驶记录中实际抽查 6 帧，所见碎石、枕木与站台未出现原黑白跳变；车辆在铁路边接触停止。

依据见 [材质适配研究](../research/wt-material-adapters.md)、[地表保存](../../artifacts/maps/wt-refine-20261118/ground-normal-native-01/summary.json)、[最终完整重载](../../artifacts/maps/wt-refine-20261118/materials-reload-04/summary.json) 与 [原始微细节准备](../../artifacts/maps/wt-refine-20261118/ground-microdetails-prepare-01/summary.json)。

## 草地分布与变化

增强包包含 10 个模型、16 层，加入原小草资源的短草与两种高草，并保留花簇和蕨类变化。三种新草使用原倾角、底部向上法线、packed normal / 透光 / smoothness 和颜色端点；短草与高草密度为 200 / 150 / 120 每 100 m²。缩小后的花簇与草层共同组成近景。

分布参考地类和原道路、铁路、水面、建筑轮廓；16 层在硬地排除区域的非零覆盖为 0。六个原始坐标样点的铁路、出生点和硬路边为 0 权重，三处绿地分别有 4 / 7 / 4 层有效权重。装饰草保持无碰撞。

最终材质更新后真实重新烘焙并保存 1,089 个 Landscape 组件；72 份 Foliage 与 Terrain 作者资产在烘焙前后保持。两处作者预览视点产生 536 个 HISM、109,108 个 render instances，该数量是当前生成调用下的渲染 buffer 快照。独立 72 项重载逐组件比较保存与当前 generation hash，1,089 个组件全部 valid/fresh，mismatch 为 0。

最终坡地实景可见短草、高草、花簇和蕨类，覆盖较近站区域更密，铁路主面保持干净。来源、生成与重载步骤见 [草地指南](../guides/war-thunder-grass.md)、[最终重新烘焙](../../artifacts/maps/wt-refine-20261118/grass-final-rebake-01/summary.json) 与 [72 项独立重载](../../artifacts/maps/wt-refine-20261118/grass-final-reload-01/summary.json)。

## 地图界面、控制与最终交接

右下角小地图随真实车辆位置更新，包含朝向、出生点、坐标与比例尺，近车视域为 512 m。M 开关完整 2,048 m 核心地图；最终正式入口会话已实际打开、关闭大地图，并用 R 恢复默认出生点。地图显示消费 Rig 位姿，自治导航沿用其定位与规划链。

| 检查 | 最新实际结果 | 证据 |
|---|---|---|
| 原场景几何与高度 | 21 项；1,365 个网格、35,922 / 20,033 个视觉 / 源碰撞 placements | [场景重载](../../artifacts/maps/wt-refine-20261118/scene-reload-03/summary.json) |
| 共用 C / Shift / 速度 | 6 项、8 PNG；实际 4.9777 m/s、制动归零 | [控制](../../artifacts/maps/wt-refine-20261118/controls-shared-03/summary.json) |
| 弗兰德斯驾驶 / 复位 | 5 项、8 PNG；前进 340.040 cm，复位水平误差 0.0136 cm | [弗兰德斯](../../artifacts/maps/wt-refine-20261118/contact-flanders-03/summary.json) |
| RMUC 驾驶 / 复位 | 5 项、8 PNG；前进 340.446 cm，复位水平误差 0.0176 cm | [RMUC](../../artifacts/maps/wt-refine-20261118/contact-rmuc-03/summary.json) |
| 最终 GUI | 坡地 / 默认碎石、80 帧 W、M 开关及实际 R | [实景与交互](../../artifacts/maps/wt-refine-20261118/gui-final-visual-audit-01/summary.json) |

最终材质、草地、场景重载及上述控制 03 使用模块 `37daaf0efd01868a11c094b7b91481b222141400a679595dfd199575a3c14b0e`。最终保存 World 为 `3311399568469e48d8580f1b41897ad5899321b51617638dd2c541e875ef1066`。控制 03 在材质最终烘焙前完成；随后修改限定于显示材质和草缓存，源几何由最终场景重载再次核对。碰撞专项和历史视觉检查各自保留实际模块及 World 身份。

最终正式入口窗口已恢复到默认出生点，HUD 显示 0.0 km/h，小地图显示，保留供用户试驾。日志按运行中快照保存；实际前台画面、按键、位姿与文件身份记录在最终 GUI 摘要。主分支发布继续采用项目原有导航和实时性能门禁。
