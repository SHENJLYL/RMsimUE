# 2026-11-19 弗兰德斯材质通道、地表接缝与默认清晰度

本轮修正分层建筑法线、地面反射通道、道路覆盖极性和草叶远距离遮罩，并将共用入口切换为 TAA 与 100% 内部渲染比例。Object 193 资产、Ground 40 目标、草 82 项、全量材料 307 资产和场景 22 项均已通过独立原生回读。最终三个静态视角中，道路边缘呈不规则羽化，地表微纹理、车辙和裂纹可见；原有矩形接缝在这些视角中消失。

最终同一 DLL、World 和 1,001 个输入文件的坡地对照中，TAA 100% 为 125.626 FPS，TSR 自动 83.4% 为 129.936 FPS。TAA 恢复完整内部渲染比例的实测 GPU 均值成本为 0.263 ms。这里使用本轮最终身份的对照；早前不同 DLL/World 的采样单独保留。

## 材质和源数据修正

- **分层模型法线。** 192 个原 Layered 实例按各自 UV0、UV2 的导数切线基处理旋转和镜像，在世界空间按源 RNM 公式组合后转回 UE tangent-space Normal。原颜色、alpha 和 UV 采样权重保持；原实例继续引用更新后的 Surface 父图。
- **道路及地表贴花。** 同 shader 的 CDK 地面纹理规则和官方 `texExp` 消费端共同确认 cooked `_r` 的 BARG 布局：R 为 smoothness，A 为 microdetail ID，粗糙度取 `1-R`。G reflectance、B AO 保留源记录。18 个已核原 RGB/alpha 轮廓的材质通过 source ID、d/r 名称及 PNG SHA 白名单采用 `height_above=1`；显式源参数优先，其余 16 个保留 0。Height normal strength 保持 0.2。
- **地表反射与微细节。** 23 个原反射图与原 albedo slice、旋转及混合权重绑定，并保留连续 `SampleGrad`。每个邻居先应用原 12 槽 self-color，再按源权重混合；离散 microdetail ID 按源最大邻居、最大层规则选择。后置微细节分别读取 LC 与 RVT 的 floor/next，用 coverage × valid 混合颜色、粗糙度和法线，共用原 12 个 slice，并复用相同 slice 的采样。
- **草叶原始 mip。** 10 个源 BC4 遮罩的 100 个原始 mip 保存为线性 R8 DDS，逐级绑定像素 SHA。UE 使用 `LeaveExistingMips`；独立回读检查 `UTexture.Source` 各级像素及实际 build settings。小草 0.5 阈值有公开源 shader 依据，普通 random-grass 的 0.5 记录为本次 UE cutout 适配。草分布、密度、尺度、模型和剔除范围保持本轮材质修正前的合同。
- **共用画质设置。** `unreal/RMSim/Config/DefaultEngine.ini` 使用 `r.AntiAliasingMethod=2`，并在 `[ConsoleVariables]` 设置 `r.ScreenPercentage=100`、`r.ScreenPercentage.Default.Desktop.Mode=0`。最终三个 TAA 采样均未传 CVarOverrides，原生日志和 unit 图确认共用设置生效。

源合同包括 `terrain-reflectance` 下 7 份 JSON 和 `foliage-enhanced/source-alpha-mips` 下 4 份 JSON，共 849,376 字节。它们按原字节 SHA256 相互绑定，由精确 `-text` 规则保留。原 PNG、DDS、FBX 与 UE 生成资产留在本地，通过准备脚本重建；提交包含代码和这 11 份必要合同。完整路径见[源合同候选清单](../../artifacts/maps/wt-visual-review-20261119/final-source-contract-candidates.json)。

## 原生验证

| 范围 | 已观测结果 | 紧凑证据 |
|---|---|---|
| Object 独立重载 | Surface 父图 + 192 Layered MI，共 193 资产、9 项通过；1,283 个受保护文件不变；UE 51360 exit 0，23.889 s | [Object reload](../../artifacts/maps/wt-visual-review-20261119/object-native-reload-01/summary.json) |
| Ground 独立重载 | 40 目标通过，1,921 个受保护文件不变；UE 37636 exit 0，124.605 s | [Ground reload](../../artifacts/maps/wt-visual-review-20261119/ground-native-reload-01/summary.json) |
| 草 author / bake | 10 模型、16 层、100 原 mip；969 HISM、200,098 个实际实例；UE 46120 exit 0，World 从 14e805… 更新为 ba531c… | [Grass author](../../artifacts/maps/wt-visual-review-20261119/grass-alpha-native-01/summary.json) |
| 草独立重载 | 82/82 通过，1,089 组件 grass map fresh、0 mismatch，90 个保存文件不变；UE 40372 exit 0，20.891 s | [Grass reload](../../artifacts/maps/wt-visual-review-20261119/grass-alpha-reload-01/summary.json) |
| 全量保存材料 | 307 资产通过，12 个父图编译零错误；307 个文件不变，10 草 DDS 与 96 个颜色端点核验；UE 2756 exit 0，152.595 s | [Full materials](../../artifacts/maps/wt-visual-review-20261119/full-material-reload-01/summary.json) |
| 场景独立重载 | 22 项通过，314 个 proof 文件不变；基础 1,368 个 HISM、1,365 个唯一网格，726/642 视觉/碰撞 HISM、35,922/20,033 原实例保持；任务补充按源核验 55 visual + 53 collision HISM、67/65 实例，108 网格 hash 和变换通过；UE 23056 exit 0，16.130 s | [Scene reload](../../artifacts/maps/wt-visual-review-20261119/scene-reload-03/summary.json) |
| 最终原生运动 | UE 38796 exit 0，34.559 s；4 项通过，51 个物理样本固定步长误差最大 8.69×10⁻¹⁰ s；19 张主视口图中含 5 张前进、6 张返程图，输入文件前后不变 | [Motion03](../../artifacts/maps/wt-visual-review-20261119/motion-final-default-03/summary.json) |

Object author 绑定 DLL `f178cc7a…`，Object 独立重载绑定 `4e38f728…`，两者 World 为 `14e80512…`。Ground 独立重载绑定 DLL `7269f297…` 与 World `14e80512…`；草重建后的全量材料、场景及最终视觉采样绑定 DLL `7269f297…`、World `ba531c13…`。每次运行均保留自己的前后身份和受保护文件记录。

## 最终静态视角与性能

最终四个采样采用 D3D11、单 viewport，输出 PNG 为 1600×1000。相机画面高度为 900 像素，窗口上下包含黑边。TAA 的实际内部画面为 1600×900；TSR 自动模式为 83.4%、1336×751。

统计窗口为 capture frame 1206–3600，共 2,395 帧，位于地图 ready 和截图操作完成之后。完整 frame 601–3600 的 3,000 帧统计同时保留，包含截图造成的停顿。平均 FPS 按总帧数除以总采样时间计算。

| 最终视角 / 设置 | 平均 FPS | 完整 post600 平均 FPS | Frame p50 / p95 / p99，ms | GPU 均值 / p95，ms |
|---|---:|---:|---|---|
| 默认道路，TAA 100% | 181.385 | 173.555 | 5.409 / 6.460 / 7.050 | 5.513 / 6.534 |
| 草坡，TAA 100% | 125.626 | 122.174 | 7.783 / 9.199 / 11.231 | 7.960 / 9.205 |
| 建筑 annex，TAA 100% | 185.461 | 177.392 | 5.319 / 6.281 / 6.928 | 5.391 / 6.312 |
| 同草坡，TSR 自动 83.4% | 129.936 | 125.557 | 7.494 / 9.125 / 10.780 | 7.697 / 9.191 |

同坡地两次运行的 1,001 个受监测输入文件 hash 一致。TAA 的 GPU 均值比 TSR 增加 0.263 ms，GPU p95 分别为 9.205 和 9.191 ms；Render 均值分别为 7.892 和 7.627 ms。清晰度选择及每项 p50/p95/p99 的原始绑定见[最终同身份对照](../../artifacts/maps/wt-visual-review-20261119/final-renderer-same-identity-summary.json)与[四视角采样汇总](../../artifacts/maps/wt-visual-review-20261119/final-visual-profile-summary.json)。

人工检查了三张最终 clean 图：默认道路的灰色铺装边缘呈不规则羽化，前景草地和石块微纹理清楚；坡地与灰色小路过渡柔和、草花颜色多样；annex 地面的车辙和裂纹可见，所查视角未见原矩形边界。三张静帧未见明显错位或新增黑白矩形。坡地图左下约 (170,760)–(350,740) 的亮黄绿水平草片保留为独立待查项。 [源诊断](../../artifacts/maps/wt-visual-review-20261119/foliage-audit-01/bright-fill-card-review-01/verification.json)将 grass_fill_a/layer12 列为强候选：该资源的宽低卡片和鲜绿原色均有记录，画面中具体 rendered HISM ID 尚未回读。[源 consumer 补充审查](../../artifacts/maps/wt-visual-review-20261119/foliage-audit-01/random-grass-consumer-review-01/verification.json)确认 randomGrassInstance 还包含 packed landNormal，完整 consumer 主体尚未取得；当前 mesh normal 与透光处理属于 UE 适配。本轮保留现有法线、颜色与尺度，后续使用目标法线对照验证。

### 已定位的绘制开销

现有 CSV 的坡地 RHI DrawCalls 中位数为 4,467，默认视角为 2,061。按原生 pass 分类：

| DrawCall 中位数 | 默认道路 | 草坡 |
|---|---:|---:|
| ShadowDepths | 494 | 1,929 |
| Basepass | 630 | 1,098 |
| Prepass | 438 | 758 |
| RenderVelocities | 192 | 340 |

坡地 GPU 均值为 7.960 ms：Basepass 1.185 ms、Prepass 0.835 ms、ShadowDepths 0.767 ms，Unaccounted 3.984 ms。默认视角 GPU 为 5.513 ms，Basepass、Prepass、ShadowDepths 三项的合计增量约 0.496 ms。CSV 的资源级归属尚未覆盖具体网格；草原生动态阴影已关闭。下一步性能定位可围绕静态物件的阴影提交、已证实存在的源 LOD 和未归属 GPU 时间开展，保持近景密度作为基准。

采样窗口内 VT ResidencyMipBias 为 0；RenderedPages 合计为默认 1、坡地 TAA 0、annex 0、坡地 TSR 2。此次 CSV 未提供 pending/resident page 数量。GPU pass 时间可能包含重叠 scope，报告按原字段保留。详见[现有 CSV 的 GPU 与绘制诊断](../../artifacts/maps/wt-visual-review-20261119/final-gpu-pass-diagnostics.json)。

## 正常入口与运动观察

正常入口继续为 `Play-WarThunder.cmd`。Prepared 启动链直接加载已保存 World，并检查地图身份和 World 文件。importer 指纹变化在这条路径上不会触发隐式全图重导；显式 `Import-WTScene.ps1` 后的 grass author/bake 仍为独立步骤。本次增量 author/reload 通过独立报告追加证据，历史全图导入报告保留原记录。

Motion03 绑定 DLL `3b35f486…`、World `ba531c13…`，UE 38796 exit 0，34.559 s；原生 4 项通过，51 个物理样本与固定 1/60 s 的最大差为 8.69×10⁻¹⁰ s。19 张主 viewport 图中包含 5 张前进、6 张返程图，输入文件前后保持。人工检查了八帧联系表以及 0008、0016 两张 1600×1000 原图，地表和道路边缘随前后运动连续，已查帧未见大块覆盖跳变、明显 UV 漂移或新增黑白底面。观察范围为这些已选帧。详见[Motion03 画面审阅](../../artifacts/maps/wt-visual-review-20261119/visual-review.json)。

正常入口最终窗口为 PID 11144，绑定 DLL `95f19c9f…`、World `ba531c13…`。人工检查实际前台 PNG，确认弗兰德斯地面、草地、伪装网、HUD 和小地图可见，窗口保留供继续驾驶；记录采用 SIGHT 视图。该记录验证公共入口与画面呈现，单 viewport 性能仍使用前述独立 CSV。对应[正常入口审阅](../../artifacts/maps/wt-visual-review-20261119/normal-entry-final-02/review.json)没有执行 OS 键盘回归。

Motion02 保留 UE 49848 exit 0、原报告 4 项和图片，但 wrapper 停止后共享 DLL 再次编译，严格 SHA 恢复门禁拒绝了该轮记录；未生成 recovered execution 或通过摘要。最终原生运动验收使用 Motion03。首个正常入口 PID 32056 在截图前已退出，保留[仅启动、未观察窗口的记录](../../artifacts/maps/wt-visual-review-20261119/normal-entry-final-01/launch-without-window-observation.json)。

本轮临时诊断地图 JSON 已原字节归档为 `visual-map-fixture.json`，包目录中的临时文件已移除；[清理证据](../../artifacts/maps/wt-visual-review-20261119/fixture-cleanup.json)核对 canonical map hash 未改。此前 1280×720、888×500 采样和跨 DLL/World 的对照保留为诊断历史；最终性能表使用同身份并明确实际分辨率的记录。

本轮证据总入口为[发布摘要](../../artifacts/maps/wt-visual-review-20261119/release-summary.json)与[最终 proof 索引](../../artifacts/maps/wt-visual-review-20261119/final-proof-index.json)。共享工作区中的操控、小地图、任务和其他外部改动按实际文件及运行身份保留；本轮提交范围由显式源文件清单和共享文件 hunk 候选确定。
