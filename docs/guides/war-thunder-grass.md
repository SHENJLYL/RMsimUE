# 弗兰德斯植被：短草、花簇与硬地排除

当前植被采用 **10 个模型、16 个 Landscape Grass 层**：保留 7 个原始花草模型及其 13 层来源关系，增加 3 个明确标记为 UE 增强的短草模型。连续短草覆盖合适的绿地，花簇沿原 biome 分类形成局部变化；道路、铁路主面、建筑和水面按原几何投影排除。车辆、地图 UI、ROS 和碰撞由统一的 `D:\simulator` 工程提供。

完整地图的准备入口见[弗兰德斯地图指南](war-thunder-flanders.md)。本文负责植被生成、独立来源约束与验收。运行时仍通过 `Play-WarThunder.cmd` 进入已准备的地图。

## 资产与来源

| 内容 | 路径或数量 | 作用 |
|---|---|---|
| 原植被包 | `assets/maps/wt_vlaanderen/foliage`，7 模型、13 层 | 保留原始模型、纹理、颜色端点及 biome 来源 |
| 增强植被包 | `assets/maps/wt_vlaanderen/foliage-enhanced`，10 模型、16 层 | 独立声明新的覆盖、尺度、密度和短草卡片 |
| 权重图 | 4 张 4096 × 4096 线性 RGBA PNG | 16 层各占一个通道，覆盖原 2048 m × 2048 m 核心 |
| 空间证据 | `enhancement-proof.json`、`native-sample-fixture.json` | 绑定原输入、硬地投影、六个独立采样点和 PNG SHA |
| UE 资产 | `/Game/RMSim/Maps/wt_vlaanderen/Foliage` | StaticMesh、材质、GrassType 与权重纹理 |
| 原生挂接 | `WT_Terrain` 的一个 `LandscapeGrassOutput` | 保存草权重，由 UE 按实际视角生成和剔除实例 |

原配置来自固定地图的 `randomGrass` 与 `small_grass`，原始 source level SHA 为 `427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f`。增强准备证据绑定每份源文件的实际 SHA；历史来源 JSON 中的归档路径保留其记录语义，执行依赖使用当前仓库的相对路径。

原 7 个模型为 `grass_blueberry_a`、`grass_daisies_a`、`grass_fill_a`、`grass_heather_a`、`grass_paigle_a`、`grass_simple_a`、`grass_tulip_b`。新增 `generic_grass_small_a`、`generic_grass_tall_a`、`generic_grass_tall_b` 使用各自原始 RGB 遮罩、配对 alpha 和 packed-normal 纹理，每个模型为 3 张卡片、6 个三角，三者合计 18 个三角。V3 按原 `vertical_angle_add/mul` 参数为各卡片加入不同倾斜，根部采用地表向上法线；每个卡片使用一次完整源图。三种模型的倾斜、长度、朝向和 UE 随机尺度分别记录在 manifest 中。

## 分布与可见尺度

新增层的参数如下，密度对应权重为 1 时每 100 m² 的目标实例数；实际生成还受权重与当前视角影响。

| 层 | 主要用途 | 高度范围 | 密度 / 100 m² | 开始淡出 → 结束剔除 |
|---|---|---:|---:|---:|
| `ue_short_grass_0` | 连续短草底层 | 11.38–22.75 cm | 200 | 70 → 110 m |
| `ue_short_grass_1` | 较高的局部变化 | 20.32–35.10 cm | 150 | 70 → 110 m |
| `ue_short_grass_2` | 第二种短草变化 | 16.26–28.08 cm | 120 | 70 → 110 m |

表中高度是沿模型局部地表法线的最高叶尖范围，包含原生随机 Z 缩放。卡片内部采用不同长度与倾角，形成错落的边界。原 13 层继续使用来源 biome 分类；花簇的 Z 比例范围为 0.35–0.65，XY 为 0.40–0.75，保留局部聚集与原颜色端点。填充草与细草作为次级变化。原层从 90 m 开始淡出，140 m 剔除。各层完整参数以 `foliage-enhanced/foliage.json` 为准。

短草资格结合原 biome 索引、Land class 混合和原高度场坡度。硬地遮罩直接读取原地面贴花、道路/轨道、建筑与水面几何并应用原放置变换；道路轨道增加 1 m 保护带，建筑水面增加 0.5 m，花簇再退让 1 m。16 张单层权重在全部已排除像素上的非零数必须为 0。

当前离线短草非零覆盖面积为 **2,626,811.0 m²**。这个值表示权重支持区域，实际可见实例由 UE Landscape Grass 生成。[V3 发布证据](../../artifacts/maps/wt-refine-20261118/grass-visual-publish-01/summary.json)绑定旧快照与新包：硬地排除 PNG 完全一致，16 个权重通道中的 15 个逐像素保持一致，连续短草通道增加空间密度变化。按全图权重积分计算的三种新草目标占比为 61.29%、21.52%、17.19%；实际视角的原生实例数单独记录。

空间方向固定为 ROS 米制：X 向源 +X，Y 为源 Dagor Z 的负值。权重 PNG 顶行对应源 Z = 2048 m、ROS Y = −2048 m。在 UE 厘米坐标中取 `u = X / 204800`、`v = 1 - Y / 204800`。每像素 0.5 m。六点 fixture 独立从原高度场与原三角投影求取预期，不使用保存后的 UE 草数据生成答案。

## 颜色与透明度

六个 RGB 颜色端点先按原 CPU 路径做 `pow(channel / 255, 2.2)`，alpha 保持原归一化值，再执行 RGB 遮罩混色。源依据为固定 Dagor 提交 `9639942de5a8e5b01d91977f60ed82cda22d5f41` 的 `prog/gameLibs/render/gpuGrass.cpp` 中 `srgb_color4` 上传，以及 `prog/daNetGame/shaders/grass_generate.dshl` 的 RGB mask 组合。`small_grass` 的 `*_tex_d` 在该路径中参与遮罩混色。

`wt_grass.source_color_linear()` 统一这一步转换，原生重载读取 16 层材质的全部六个颜色向量并核对误差。三种新草的原 DDSx 遮罩、alpha、normal 均设置 `GAMMA_EQ_1`，UE 按线性数据导入；96 个原颜色向量在 V3 中保持一致。源配对 alpha 取 R 通道，材质采用 Masked，裁剪值为显式 UE 适配值 1/3，并接入实例淡出。

V3 导入三张原 512² packed-normal 纹理：AG 为法线 XY，B 为透光，R 为 smoothness，依据 `grass_generate.dshl:821–826`。草父图以 B 调制 TwoSidedFoliage 的 SubsurfaceColor，并将 `1-R` 接入 UE Roughness。新增卡片的根部向上法线与原倾角来自 `grass_generate_inc.dshl:254–257,525–529`；世界空间法线的向上半球限制是显式 UE 适配，避免双面卡片根部翻向地面。旧 13 层保持常规双面法线分支。风动画继续作为独立适配项。

[视觉修正来源审查](../../artifacts/maps/wt-refine-20261118/grass-visual-source-review-01/verification.json)保留修正前截图、原源码与纹理 flags、FBX 法线读回、占比变化和各项适配边界。

## 从原资源重建

先按地图指南恢复原 `foliage`、Land class、高度场及 `avg_vlaanderen` 提取目录。运行下列步骤使用 Python 3.14、NumPy、Pillow、已有解码依赖和 Blender 5.1。所有输出目录都要求新建；重建已发布的增强包时，先在退出本项目 UE 后将旧增强目录完整归档，保留其来源与证据链。

以下使用当前已有的源配置快照。新提取可以将 `$wtGrassSource` 指向 `extract_wt_foliage.py` 输出的 `foliage-source.json`；其原地图身份仍须匹配。

```powershell
Set-Location D:\simulator
$wtGrassRepo = 'D:\simulator'
$wtGrassPackage = Join-Path $wtGrassRepo 'assets/maps/wt_vlaanderen'
$wtGrassSource = Join-Path $wtGrassRepo 'artifacts/maps/wt-refine-20261118/source-baseline/foliage-source.json'
$wtGrassRun = Join-Path $wtGrassRepo 'artifacts/maps/wt-grass-rebuild-YYYYMMDD-01'
$wtGrassPython = 'C:\Users\SHEN\AppData\Local\Programs\Python\Python314\python.exe'
$wtGrassBlender = 'E:\blender\blender.exe'
$wtGrassEngine = 'E:\simulator-tools\UE_5.8'
$wtGrassGame = 'D:\steamlaptop\steamapps\common\War Thunder'
$wtGrassDae = Join-Path $wtGrassRepo 'third_party/dagor_asset_explorer/src/dae'
$wtGrassOodle = Join-Path $wtGrassEngine 'Engine/Binaries/DotNET/UnrealBuildTool/runtimes/win-x64/native/oo2core.dll'

& $wtGrassPython -B .\tools\maps\extract_wt_small_grass.py `
  --source $wtGrassSource --game-root $wtGrassGame --dae-root $wtGrassDae `
  --oodle-library $wtGrassOodle --output (Join-Path $wtGrassRun 'small-source')

& $wtGrassBlender --background --factory-startup --python-exit-code 1 `
  --python .\tools\maps\prepare_wt_small_grass_meshes.py -- `
  --source (Join-Path $wtGrassRun 'small-source/small-grass-source.json') `
  --output (Join-Path $wtGrassRun 'small-meshes')

& $wtGrassPython -B .\tools\maps\prepare_wt_foliage_enhancement.py `
  --repo $wtGrassRepo --source $wtGrassSource `
  --small-source (Join-Path $wtGrassRun 'small-source/small-grass-source.json') `
  --small-meshes (Join-Path $wtGrassRun 'small-meshes/mesh-preparation.json') `
  --output (Join-Path $wtGrassPackage 'foliage-enhanced') `
  --evidence (Join-Path $wtGrassRun 'prepared')

& $wtGrassPython -B .\tools\maps\prepare_wt_grass_audit_samples.py `
  --package $wtGrassPackage `
  --output (Join-Path $wtGrassPackage 'foliage-enhanced/native-sample-fixture.json')
```

每条命令都须正常退出并生成 `status=passed` 的证据，再执行下一步。提取器输出 3 组遮罩/alpha/normal，共 9 张原图；增强生成器复制所需原模型与纹理，保留原包，将所有新增数据写入 `foliage-enhanced`。`validate_foliage()` 自动选择存在的增强包，核对 PNG、模型、原输入和增强 proof 的 SHA。

已有增强包的修正可先使用 `--staging` 写入独立候选目录，并用 `--baseline-definition` 指向不可变的旧快照；准备器要求硬地排除字节不变并沿用六个空间见证点。采样工具支持 `--foliage-base <候选目录>`，可在发布前完成来源核对和六点 fixture。发布时保存旧目录、核对完整旧 SHA 清单，再验证候选复制后的全部文件 SHA。

## UE author、烘焙与保存

顺序为地图主导入或材质增量更新 → 植被 author/烘焙 → 独立重载 → 材质与三视角检查。主地图的 NullRHI 导入完成后，草权重烘焙必须使用可渲染 RHI。

```powershell
.\tools\unreal\Author-WTGrass.ps1 `
  -EngineRoot $wtGrassEngine -PackageDirectory $wtGrassPackage `
  -EvidenceDirectory (Join-Path $wtGrassRun 'native-author') `
  -ShaderWorkers 4 -TimeoutSeconds 600
```

该入口检查同项目的 UE/构建占用并取得导入锁。它创建 10 个 mesh、16 个 GrassType 与材质、4 张权重纹理，挂接 GrassOutput，等待编译并烘焙全部 1,089 个 Landscape 组件。保存前要求 outdated = 0，每层都有实际非零 GrassData，mesh/尺度/密度/剔除距离匹配定义，所有装饰草关闭碰撞，预览区域有原生实例。

UE5.8 的草图同步 builder 可在延迟 grass shader 完成期间带着 Pending/Streaming/Rendering 返回。入口记录每次真实计数，最多进行 3 次 native build；待更新数量必须严格下降，额外续建预算为 300 秒，并由 owned-process 的外层 timeout 限定整个运行。最终验收条件保持完整。

若资产 author 已完成、报告是 `authored_pending_weight_bake`，可以在新证据目录使用 `-ResumeAuthoredAssets`。恢复入口要求原地图、定义和 `wt_grass.py` SHA 一致，并在 UE 内重新读取 GrassOutput 连接、10 个模型、16 层、纹理来源；保存前后检查全部 Foliage/Terrain 文件 SHA 一致。定义或导入器变更后应完整 author。

## 独立保存重载

单独的 NullRHI 进程读取已经保存的 World，检查 16 层、10 个模型、96 个源颜色向量及六个实际场地样点。V3 另读取两个 Custom 图的实际源码、世界空间法线设置、三张 512² 线性 clamp normal 资产和各层 `UseSourceNormal` 绑定。此阶段读取保存的 GrassData，不调用 BuildGrassMaps 或保存 UE 资产。

```powershell
& "$wtGrassEngine\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" `
  "$wtGrassRepo\unreal\RMSim\RMSim.uproject" `
  -run=pythonscript `
  "-script=$wtGrassRepo/tools/unreal/verify_wt_grass.py" `
  "-MapPackage=$wtGrassPackage" `
  "-GrassSampleFixture=$wtGrassPackage/foliage-enhanced/native-sample-fixture.json" `
  "-GrassAuditOutput=$wtGrassRun/native-reload/audit" `
  "-AbsLog=$wtGrassRun/native-reload.log" `
  -NullRHI -NoShaderWorker -unattended -nop4 -nosplash -nosound
```

由统一 runner 执行时同样使用以上参数，并记录 owned PID、实际退出码、模块 SHA 与期限。V3 完整原生报告为 `artifacts/maps/wt-refine-20261118/grass-visual-reload-01/audit/verification.json`；同目录 `summary.json` 保存原生摘要，父目录的[执行与重载合并摘要](../../artifacts/maps/wt-refine-20261118/grass-visual-reload-01/summary.json)另外绑定实际退出码、时间、日志和脚本 SHA。完整报告留在本地。

| 独立样点 | ROS X, Y / m | 预期 |
|---|---:|---|
| 原 GUI 铁路草簇位置 | 1075.232351, −1012.656026 | 源硬面，16 层全部为 0 |
| 默认出生点 | 1028, −1020 | 源硬面，16 层全部为 0 |
| 车站旁绿地 | 1084.75, −1029.75 | 草权重为正，原硬面 witness 为空 |
| 绿色坡地 | 1146.25, −942.25 | 草权重为正，原硬面 witness 为空 |
| 轨道路面边缘 | 1075.25, −1012.75 | 源硬面，16 层全部为 0 |
| 开阔草地 | 1163.25, −1192.25 | 草权重为正，原硬面 witness 为空 |

每点核对 Landscape 四角的实际 GrassData 字节与双线性插值，允许字节量化误差 1。实际场景观察还覆盖原铁路视角、车站旁绿地、远处绿坡/硬地边缘，确认近处草种、尺度、颜色和干净硬表面。

## 当前最终保存版本

[V9 表面修正后的真实重烘焙](../../artifacts/maps/wt-refine-20261118/grass-final-rebake-01/summary.json)已完成：UE PID 23208 退出 0，执行记录用时 **82.963949 秒**。入口采用 `ResumeAuthoredAssets`，第一次 native BuildGrassMaps 即完成，全部 **1,089 个组件有效且 fresh，mismatch / outdated 均为 0**。72 份已保存的 Foliage 和 Terrain 作者资产在烘焙前后、World 保存后保持原字节。

本轮两视点初始生成 **536 个 HISM、109,108 个渲染实例**，装饰草碰撞为 0。三种新增草分别为 66,212 / 21,869 / 17,421 个。V3 定义、16 层密度/尺度/剔除设置和全部层的权重汇总保持；与首次完整 author 的 115,911 相比，差值 6,803 全在 `small_a` 的初始渲染 buffer。旧 author 使用两次 Build/Update，本次 Resume 使用一次，保留两轮实际快照和调用条件；密度继续为 **200 / 150 / 120**。

[最终独立保存重载](../../artifacts/maps/wt-refine-20261118/grass-final-reload-01/summary.json)已通过 **72 项**：UE PID 42976 退出 0，用时 **12.8125819 秒**。逐组件读取的 1,089 对 saved/current generation hash 全部匹配；1,015 个组件有草权重，16 层、10 模型、96 个原颜色向量、三张原 normal 绑定通过。六点非零层仍为 **0 / 0 / 4 / 7 / 0 / 4**，最大四角误差 1 byte、插值误差 0.25 byte，76 份保存资产与源文件前后保持。

- 最终 World SHA256：`3311399568469e48d8580f1b41897ad5899321b51617638dd2c541e875ef1066`。
- 实际模块 SHA256：`37daaf0efd01868a11c094b7b91481b222141400a679595dfd199575a3c14b0e`。
- rebake 摘要 SHA256：`9eaf5fb33524577d94b9d85f62074cbe50678fe1e61104a5850e61b81109345b`。
- 72 项摘要 SHA256：`6904ba0903746a37c186f60a1ea446be184558617e32322976c65b402c29efb4`。

[最终 GUI 记录](../../artifacts/maps/wt-refine-20261118/gui-final-visual-audit-01/summary.json)绑定同一模块和 World。绿色坡地的规则格线已消失，草、花簇、蕨类的分布保持，画面内铁路硬面干净；默认碎石面连续。运动录制保留 80 张原 PNG 的完整 SHA，明确抽查其中 6 帧，车辆行至铁轨边停止。实际 M 展开/收回和 R 复位已核对输入、计数与位姿；默认 GUI 保留运行供试驾。坡地原图由 Root 查看，独立审查查看等比例预览；结论限定于记录中的可见区域。

## V3 首次 author 与修正前观察（历史）

[grass-visual-native-01](../../artifacts/maps/wt-refine-20261118/grass-visual-native-01/summary.json)已通过：UE PID 8820，exit 0，执行记录时间差 99.222058 秒，4 个 shader worker，完整 author。模块 SHA 为 `7a4edd9792accc9f50151e2bfe38e70785c679db2ce684046ecdf78e1083f987`，定义 SHA 为 `e6eb9cb72bc88d7fa4d4887f3d530fe79731fe51ef172fb4eb307531fec6b0bc`。

- 1,089 个组件全部有有效 GrassData，1,015 个组件有权重，16 层完整。既有续建守卫记录待更新数 1,054 → 0。
- 两个预览视角生成 536 个 HISM、115,911 个渲染实例，草碰撞组件数为 0。
- 三种新草分别为 73,015、21,869、17,421 个，占三者的 65.01%、19.47%、15.51%。原预览中的首种占比为 86.47%。
- 三个新卡片各保留 6 个三角，原生 bounds 最大误差为 0.000002325 cm，三张 packed-normal 资产已创建。
- 保存后 World SHA 为 `0a57103fcb58de77467bc9f75fcbd0019b6a2d62e687d0a6080e43e763597419`。原 scene-source SHA、7 个原模型与全部原颜色端点保持稳定。

V3 独立保存重载已通过 **71 项**：UE PID 18184 退出 0，用时 47.968831 秒，模块与 World 均和上面的 author 一致。16 层实际权重/设置、10 个 mesh、96 个源颜色向量、两个 Custom 法线图与三张 512² 原 normal 逐项匹配；最大颜色向量误差为 `2.6246772e-8`。原 13 层 `UseSourceNormal=0`，新增 3 层为 1，原 normal 资产按定义绑定。

六点非零层依次为 **0 / 0 / 4 / 7 / 0 / 4**；四角权重最大误差 1 byte，双线性插值最大误差 0.25 byte。76 份保存资产与源文件在原生只读检查前后保持，报告生成后再次按原 SHA 清单重算也一致。[完整重载摘要](../../artifacts/maps/wt-refine-20261118/grass-visual-reload-01/summary.json) SHA256 为 `42fc70b1975f5fec5ee3eb7c009815d36a55fc069401d32a2a6554f9954ee5bf`，绑定完整报告 SHA `37ceb857003382d2d62f071e121754561e2a499f94488ffb9b28a34e4cd2f15c`。

[完整材质 materials-reload-03](../../artifacts/maps/wt-refine-20261118/materials-reload-03/summary.json) 已通过 307 个唯一资源，UE PID 32780 退出 0、90.143116 秒；`scene-reload-02` 的 21 项也已通过，UE PID 38304 退出 0、15.457537 秒。两轮均绑定相同的 World `0a57103f…` 与模块 `7a4edd97…`，源清单和 World 前后保持。

[V3 两处草地实景摘要](../../artifacts/maps/wt-refine-20261118/gui-grass-v3-visual-audit-01/summary.json)已记录车站旁绿地和绿色坡地的原始 PNG、实际车位、Space 输入及正常退出。近景短草呈浅绿、带倾角，花簇尺度缩小且有多种颜色；坡地覆盖更密，可见短草、高草、花簇与蕨类混合，画面内铁路主面与硬路保持干净。两处分别为 PID 37804 / 22484，均正常退出 0，使用模块 `b9278d1a…` 与同一 World `0a57103f…`。Root 已查看两张 1616×1039 原图，独立审查查看了两张等比例预览；结论限定在这两处可见区域。新三层密度保持 **200 / 150 / 120**。

该修正前实景摘要 SHA256 为 `c21a9d7fe3bfa0fb8615949e7abce41c97908827a27f49f5626216db4a76f295`。原图保留当时的 LC1 格线与近地表外观；同位置修正后的最终结果见上节。

## 材质更新后的草图新鲜度

UE 的草图 generation hash 包含整个 Landscape 材质状态。更新 Terrain 表面图后，独立重载现增加逐组件读取 `GrassData.GenerationHash` 与引擎 `ComputeGrassMapGenerationHash()` 的比较：必须 **1,089 个组件全部有效且 fresh，mismatch = 0**；完整草门禁增为 72 项。[新增门禁来源与离线守卫](../../artifacts/maps/wt-refine-20261118/grass-freshness-gate-01/verification.json)保留旧 71 项结果的检查范围；最终 `grass-final-reload-01` 已实际通过这项逐组件验证。

若植被定义、原生模型、材质参数和 GrassOutput 闭包保持，仅 Terrain 表面重编，可使用专项恢复入口 `-ResumeAuthoredAssets` 重新执行真实草图烘焙。该入口核对 V3 importer/definition/source 哈希和全部模型/层合同，记录当前已保存资源，执行 native BuildGrassMaps 后保存 World，再由 72 项只读门禁确认新鲜度。新包的完整重建继续使用上文的全量 author。

## 分布增强基线与历史验收

2026-11-18 的 [grass-native-02 摘要](../../artifacts/maps/wt-refine-20261118/grass-native-02/summary.json)记录 UE PID 26964 正常退出，模块 SHA `cd1c286269fc5d411084d6e23d0b861d142082adbd593680edda7294dfcec90c`。新进程复用已保存资产，**第一次 native build 即完成**；本轮未触发额外续建。

- 1,089 / 1,089 个组件拥有有效 GrassData，outdated = 0；1,015 个组件含权重，16 层全部有非零权重。
- 两个实际预览视角生成 536 个草 HISM、122,955 个渲染实例。三个新增模型分别为 103,204、9,645、6,500 个。
- 69 份 Foliage/Terrain 资产在烘焙和 World 保存前后 SHA 完全一致；原 scene-source SHA 保持 `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27`。
- 保存后 World SHA 为 `1cb484b861b8ee2cc8f2463a294abe7165af680718077cebc3de37390ef1400c`。

实例数口径为 `UGrassInstancedStaticMeshComponent::GetNumRenderInstances` 的两个预览视角人口。[独立保存重载摘要](../../artifacts/maps/wt-refine-20261118/grass-reload-01/audit/summary.json)记录 UE PID 46516 正常退出，51 项检查全部通过，使用同一模块与上述保存后的 World。

- 16 层实际 GrassData、10 个 mesh 与 96 个源颜色向量逐项匹配；1,089 个组件的保存数据均有效。
- 原 GUI 铁路点、出生点、硬路边缘的实际保存权重全部为 0；车站旁绿地、绿色坡地、开阔草地分别有 4、7、4 层非零权重。
- 六点四角最大误差为 1 字节，双线性插值最大误差为 0.5 字节，均在既定量化门槛内。
- 73 份保存资产与源文件在只读重载前后 SHA 完全一致。

三视角实际渲染观察另行记录。此前 [grass-native-01 失败摘要](../../artifacts/maps/wt-refine-20261118/grass-native-01/summary.json)保留草图未完成与 World 未改变的事实；[续建守卫证据](../../artifacts/maps/wt-refine-20261118/grass-bake-recovery-01/verification.json)保留对应源码依据和错误路径检查。
