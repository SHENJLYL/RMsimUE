# 战争雷霆弗兰德斯地图

弗兰德斯包从本机战争雷霆原图 `avg_vlaanderen.bin` 生成，使用原始地形、场景物件布局和对应碰撞资源。运行入口已接入 RMSim 的车辆包与真实接触物理，默认 RMUC2027 保持原有入口。当前工作位于分支 `feat/wt-vlaanderen-20261117`、工作区 `D:\simulator-wt-vlaanderen`。截至 2026-11-18，前一轮主场景、88 个材质资源、真实接触往返与复位已通过独立原生验证；本轮继续加入原 Land class 背景、原底图 UI、水面与 13 层源草规则。源草包、13 层原生草图、两视点同步流送和独立保存重载已通过；104 个材质资源、当前保存场景、两地图接触回归和实际草区 GUI 均已通过，详见“验证证据”。

## 启动与切换

在当前独立工作区运行：

```powershell
Set-Location D:\simulator-wt-vlaanderen
.\Play-WarThunder.cmd
```

通用入口也接受地图描述和出生点：

```powershell
.\Play-ManualVehicle.cmd .\assets\maps\wt_vlaanderen\map.json default
```

`Play-WarThunder.cmd` 使用已准备好的 AIM 车辆和 `ContactPhysics`，固定传入 `-GraphicsRhi D3D11 -SingleShaderWorker`；实际引擎参数为 `-d3d11 -NoShaderWorker`。Flanders 入口另传 `-ForceDPCVars=r.Streaming.PoolSize=4096`，将本机 16 GB 显存配置的常规纹理池设为 4096 MiB；其他地图保持各自默认预算。WASD、鼠标云台/视角、制动、复位等操作由共用控制链处理，见[手动驾驶指南](manual-controls.md)。`default` 出生点为 ROS 坐标 `[1028, -1020, -1.3805752992630005]` 米、偏航 `0` 弧度，来源于[离线候选记录](../../assets/maps/wt_vlaanderen/spawn-candidates.json)。

右下角小地图跟随车辆显示 512 米近区，按 **M** 展开或收回全幅原图；车位、朝向、出生点和比例尺的说明见[小地图与 M 键大地图](map-overview.md)。底图已用三处原水面、赛马场看台和马厩独立标定：ROS 左上 `[0,-2048]`、右上 `[2048,-2048]`、左下 `[0,0]`，默认出生点对应 1024 像素原图的 `[514,514]`。地图资料和校准证据由 `overview.json` 绑定。源标定、原生 UI 纹理和最终 GUI 已通过；实际 M 四次展开/收起、行驶中的车位更新、Q 旋转后的朝向更新及独立出生标记均有截图和输入记录。

无地图参数时，通用入口启动 RMUC2027：

```powershell
.\Play-ManualVehicle.cmd
```

单独预检：

```powershell
powershell.exe -NoProfile -File .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -MapFile .\assets\maps\wt_vlaanderen\map.json -SpawnId default -GraphicsRhi D3D11 -SingleShaderWorker -CheckOnly
```

`Run-ImportLab.ps1`、`Start-PreparedVehicle.ps1` 和 `Test-ManualControls.ps1` 均接受 `-GraphicsRhi Default/D3D11/D3D12`，默认 `Default` 沿用项目 RHI 设置；`-SingleShaderWorker` 传入引擎的 `-NoShaderWorker`。通用 Play 入口保持默认设置。

移除 `-CheckOnly` 后启动窗口。预检检查配置、出生点、保存关卡文件和同工程活动进程。`Start-PreparedVehicle` 与 `Import-WTScene` 使用规范化项目路径 SHA256 对应的共享互斥，启动入口检查同工程的 Editor、Editor-Cmd 和构建进程；不同工作树按各自项目路径独立检查。`AllowMultiple` 允许同工程多个 `-game` 预览，同时保留导入、编辑器和构建排他检查。`CheckOnly` 执行同样的互斥和进程预检。引擎加载时进一步检查来源标签、场景组件和碰撞状态；缺少 `World.umap` 时，入口提示显式导入。

## 完整重跑：源数据、主场景、草图烘焙和原生验证

以下命令使用已构建的 RMSim Editor 模块。全量地形、布局、模型与碰撞的新包准备命令见[来源记录](../../assets/maps/wt_vlaanderen/SOURCE.md)。第 2–3c 步面向刚完成基础准备的包副本，依次恢复旧式叶片 half4、绑定原几何 proof、恢复贴花 position.w，并在验证单 FBX 后重新绑定两份 proof。将第 1 步的 `$wtPackage` 指向该新包；包 ID 保持 `wt_vlaanderen`。

日常使用直接运行 `Play-WarThunder.cmd`，它加载已准备的关卡与车辆。以下步骤用于重建数据或 UE 资产。当前 `assets/maps/wt_vlaanderen` 已有叶片、opacity、Land/UI 和源草包；重新导入依次运行第 4 步主场景导入、第 4a 步草作者与烘焙，再执行第 5 步独立门禁。主导入重建 Terrain，草输出须随后重新安装。opacity 适配器检测到 `source_vertex_opacity` 时会拒绝再次添加 UV；重做源准备时使用新的基础包，或在独立副本中配齐 `decal-opacity-01/adapt/before/` 的 FBX、manifest 和原 geometry proof。

依次执行下面的步骤。导入、原生场景审查和车辆运行各使用一个自有 UE 进程；前一步退出后再开始下一步。本工程已有预览窗口应先关闭。其他工作树保留独立状态。每次选择全新的证据目录，脚本会保留此前结果。

### 1. 设置本机路径

```powershell
$wtRoot = 'D:\simulator-wt-vlaanderen'
$wtPython = 'C:\Users\SHEN\AppData\Local\Programs\Python\Python314\python.exe'
$wtBlender = 'E:\blender\blender.exe'
$wtEngine = 'E:\simulator-tools\UE_5.8'
$wtSource = 'D:\simulator\artifacts\maps\wt_existing\avg_vlaanderen'
$wtDae = 'D:\simulator\third_party\dagor_asset_explorer\src\dae'
$wtGame = 'D:\steamlaptop\steamapps\common\War Thunder'
$wtPackage = Join-Path $wtRoot 'assets/maps/wt_vlaanderen'
$wtStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$wtLeaf = Join-Path $wtRoot ('artifacts/maps/wt_existing/leaf-adaptation-' + $wtStamp)
$wtOpacity = Join-Path $wtRoot ('artifacts/maps/wt-scene/decal-opacity-' + $wtStamp)
$wtSceneSource = Join-Path $wtPackage 'scene-source.json'
$wtOodle = Join-Path $wtEngine 'Engine/Binaries/DotNET/UnrealBuildTool/runtimes/win-x64/native/oo2core.dll'
Set-Location $wtRoot
```

`$wtSource`、`$wtDae` 和 `$wtGame` 是本轮固定的只读输入；生成内容写入独立工作树。CPython 使用 `-B`，避免在原解码器目录写入字节码缓存。

### 2. 恢复源 half4 并独立审计

```powershell
& $wtPython -B .\tools\maps\extract_wt_leaf_payload.py --source-root $wtSource --dae-root $wtDae --game-root $wtGame --oodle-library $wtOodle --output (Join-Path $wtLeaf 'raw-payload')
if ($LASTEXITCODE -ne 0) { throw 'Leaf payload recovery failed.' }

& $wtPython -B .\tools\maps\verify_wt_leaf_payload.py --payload-root (Join-Path $wtLeaf 'raw-payload') --scene-root $wtPackage --output (Join-Path $wtLeaf 'independent-source-verification.json')
if ($LASTEXITCODE -ne 0) { throw 'Independent leaf source audit failed.' }
```

提取器保留原 typed declaration 和 draw 原字节，恢复 `delta.xyz`、`windNo` 与 packed normal。独立审计直接按 stride 24 的 offset 0/8/12/20 回读 position、UV0、half4 和法线字节，并核对原 NPZ、源布局及 RMI。两份报告分别为 `raw-payload/raw-payload-verification.json` 和 `independent-source-verification.json`。

### 3. 局部重导三 FBX

```powershell
& $wtBlender --background --python-exit-code 1 --python .\tools\maps\prepare_wt_leaf_adapter.py -- --source-root $wtSource --output $wtPackage --payload-root (Join-Path $wtLeaf 'raw-payload') --evidence-root $wtLeaf
if ($LASTEXITCODE -ne 0) { throw 'Leaf reference-card export failed.' }
```

该步骤先将原三 FBX、`scene-source.json` 和历史 `preparation-verification.json` 保存到本轮 `before/`，在 `staging/` 导出并经 Blender 逐面回读后，再更新三个 FBX 及其 manifest 记录。输出 `verification.json` 串联原 GRP/模型、raw payload、独立审计、旧新 FBX 和旧新 manifest 哈希。历史 preparation 报告保留原样。

三模型实际参考几何 `bounds_m`、完整朝向包络 `billboard_bounds_m` 和 `leaf_adapter` 写入各自 mesh record；其余视觉、碰撞和实例文件保持。

### 3a. 审计原始贴花共点面

`world_decal_000` 保留原始 87,864 个三角形和 FBX 几何。原 99 个 landmesh NPZ 中有 34 个三角形包含严格重合的顶点；另有 1 个三个顶点各不相同的共线三角形。UE 的精确目标为 87,830：只按绑定证据扣除前述 34 面，共线面保留在目标中。

```powershell
$wtDecalProof = Join-Path $wtRoot ('artifacts/maps/wt-scene/decal-proof-' + $wtStamp)
& $wtBlender --background --factory-startup --python-exit-code 1 --python .\tools\maps\audit_wt_degenerate_faces.py -- --scene-source (Join-Path $wtPackage 'scene-source.json') --output-dir $wtDecalProof
if ($LASTEXITCODE -ne 0) { throw 'Original decal geometry audit failed.' }
```

脚本生成完整 `source-proof.json`（schema `wt_source_degenerate_face_audit_v1`）和紧凑 `verification.json`，记录每个原 NPZ 的 SHA、34 面原坐标和面索引、完整源/FBX 几何及材质对应、FBX 轴/单位/模型变换、恒等 RMI，以及规范与实际模型变换下 float32 量化新增折叠均为 0。本次通过的证据位于 `artifacts/maps/wt-scene-20261118/decal-proof-01/`；这里的量化分析与后续 UE 原生计数各自记录。

当前包将完整 proof 放在 `geometry-audits/world_decal_000.json`，mesh record 以 `geometry_audit_file` 和 `geometry_audit_sha256` 绑定它。重新发布 proof 时，同步更新这两个字段；源 `triangles: 87864` 保留。`wt_mesh_geometry.py` 严格核对 proof、源身份和 FBX 的哈希及完整面证据；导入和保存重载均要求原生总数精确等于 87,830，并按源稳定材质 ID 核对 35 个材质的 `原始面数 − 已证实共点面数`。

新基础包首次绑定 proof 时运行下面的窄发布步骤；已有绑定会显式停止。它核对源与 FBX 身份，备份 manifest，再复制已通过的完整证据和设置两个绑定字段：

```powershell
$wtBindProof = @'
from pathlib import Path
import hashlib, json, shutil, sys
manifest, proof_file = map(Path, sys.argv[1:])
source = json.loads(manifest.read_text(encoding='utf-8-sig'))
proof = json.loads(proof_file.read_text(encoding='utf-8-sig'))
mesh = next(m for m in source['meshes'] if m['id'] == 'world_decal_000')
assert 'geometry_audit_file' not in mesh, 'Use a fresh base package; proof is already bound.'
assert proof['status'] == 'passed' and proof['mesh_id'] == mesh['id']
assert proof['source_sha256'] == source['source_sha256']
assert proof['fbx_sha256'] == mesh['sha256'] == hashlib.sha256((manifest.parent/mesh['file']).read_bytes()).hexdigest()
assert proof['source_triangles'] == mesh['triangles'] == 87864
assert proof['strict_coincident'] == 34 and proof['expected_native_triangles'] == 87830
backup = proof_file.parent/'before-bind-scene-source.json'
assert not backup.exists(), 'Preserve the previous evidence directory.'
shutil.copy2(manifest, backup)
relative = 'geometry-audits/world_decal_000.json'
target = manifest.parent/relative
assert not target.exists(), 'Proof target already exists.'
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(proof_file, target)
mesh['geometry_audit_file'] = relative
mesh['geometry_audit_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
manifest.write_text(json.dumps(source, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
'@
& $wtPython -B -c $wtBindProof $wtSceneSource (Join-Path $wtDecalProof 'source-proof.json')
if ($LASTEXITCODE -ne 0) { throw 'Initial source geometry proof binding failed.' }
```

### 3b. 从原始 short4n 恢复贴花 opacity

```powershell
& $wtPython -B .\tools\maps\extract_wt_decal_opacity.py --source-root $wtSource --dae-root $wtDae --oodle-library $wtOodle --output (Join-Path $wtOpacity 'raw')
if ($LASTEXITCODE -ne 0) { throw 'Original decal opacity recovery failed.' }
```

输出为 `raw/source-opacity.npz` 和 `raw/verification.json`。提取器按原 typed declaration 回读 position.w，核对 99 个原 decal NPZ 的坐标、索引和材质 ID；本轮确认 85,418 个源顶点、87,864 面和零位置误差。源 int16 w 有 6,353 个不同值，转换规则为 `max(int16_w/32767, -1)`、float32。本轮原始数据范围为 0–1。

### 3c. 只给 world_decal_000 追加 UV1，并重验 geometry proof

```powershell
& $wtBlender --background --factory-startup --python-exit-code 1 --python .\tools\maps\prepare_wt_decal_opacity.py -- --scene-source $wtSceneSource --raw-report (Join-Path $wtOpacity 'raw/verification.json') --evidence-root (Join-Path $wtOpacity 'adapt')
if ($LASTEXITCODE -ne 0) { throw 'Single-FBX opacity adaptation failed.' }
$wtOpacityReport = Get-Content -LiteralPath (Join-Path $wtOpacity 'adapt/verification.json') -Raw | ConvertFrom-Json
if ($wtOpacityReport.status -ne 'passed' -or $wtOpacityReport.uv1_max_abs_error -ne 0) { throw 'Opacity adaptation evidence failed.' }
```

适配器先备份旧 FBX、manifest 和 geometry proof。它通过 Blender 的官方二进制 parser/writer 添加 UV1.x；FBX V 固定为 1，对应 UE UV1.y=0。原有 FBX 元素、几何、UV0、变换和材质槽逐项校验保持一致，263,592 个 polygon corners 的 source w→UV1.x 误差为 0。原两处重复 draw 的顶点拆分保留，FBX 控制点仍为 85,424。

随后适配器在 staging 中调用独立 `audit_wt_degenerate_faces.audit`，重新验证全量源/FBX 几何和 35 个材质的面数；仍须是原 87,864 / 严格共点 34 / 原生目标 87,830。全部通过后发布单个新 FBX、`geometry-audits/world_decal_000-opacity.json`、重绑定的 `geometry-audits/world_decal_000.json` 与 manifest。mesh 新增的 `source_vertex_opacity` 明确声明 UV1.x、full precision、`raw_verification_file` 和 SHA256。`adapt/verification.json` 绑定整条证据链；完整 raw/proof 和 before 文件留本地，历史 preparation 报告保留。

本轮实际通过记录位于 `artifacts/maps/wt-scene-20261118/decal-opacity-01/`；后续导入使用已经发布的 manifest 和两份 proof。

### 3d. 发布原 Land class 与原底图

新基础包先从原 level 的 lmap 读取五个 Land class、1,024 个 cell 的 class/atlas 记录和原颜色纹理。发布器增加 `source_landclass_surface`，保留既有几何、材质 ID 及其他 scene 字段，并在证据目录备份旧 manifest。当前包已经发布时跳过本步；重新准备使用尚无 `landclass/` 的独立基础包。

```powershell
$wtLand = Join-Path $wtRoot ('artifacts/maps/wt-scene/landclass-' + $wtStamp)
$wtOriginalMap = Join-Path $wtGame 'levels/avg_vlaanderen.bin'
& $wtPython -B .\tools\maps\extract_wt_landclass.py --map $wtOriginalMap --game-root $wtGame --dae-root $wtDae --oodle-library $wtOodle --output (Join-Path $wtLand 'extracted')
if ($LASTEXITCODE -ne 0) { throw 'Original global landclass extraction failed.' }

& $wtPython -B .\tools\maps\prepare_wt_landclass.py --scene-source $wtSceneSource --extracted (Join-Path $wtLand 'extracted') --evidence-root (Join-Path $wtLand 'published')
if ($LASTEXITCODE -ne 0) { throw 'Verified landclass publication failed.' }
```

这一步输出八张纹理：五张原 class 颜色、两张无损 cell 索引图和原 448 页权重 atlas。原权重按 `last=max(0,1-sum(previous))` 读取，保留源 BC1 量化后的混合值。草准备复用 class 1 的实际覆盖与 HQ 地表颜色。原 Terrain 宏观颜色、biome 索引采用源负 V 朝向，detail UV 保持源正 XZ。

新包的底图按已验证标定准备：

```powershell
$wtOverview = Join-Path $wtRoot ('artifacts/maps/wt-scene/overview-' + $wtStamp)
$wtOverviewCalibration = Join-Path $wtRoot 'artifacts/maps/wt-scene-20261118/map-overview-01/georef-02/verification.json'
& $wtPython -B .\tools\maps\prepare_wt_overview.py --package $wtPackage --calibration $wtOverviewCalibration --calibration-sha256 ff43453d9b85949747969a6fa0b01c702c0468c5236b3b9a4a22abc4590ba33d --dae-root $wtDae --oodle-library $wtOodle --output $wtOverview
if ($LASTEXITCODE -ne 0) { throw 'Original map overview preparation failed.' }
```

入口重读原 DXP 与 ACES 的地图边界，生成 `overview.json`、原 PNG 与标定副本；前置要求是包内正确的 `map.json` 与 scene 身份。需要重新标定时，执行[小地图重建指南](map-overview.md)中的完整 georef 步骤。

### 3e. 准备原图草层的独立源包

这一步面向新的基础包，要求 `$wtPackage/foliage` 尚不存在，且包已包含原 Land class 数据。当前工作区的源草包已准备完成，可直接进入第 4 步。Land 的源发布与原底图准备先完成；底图的完整提取、标定和 `prepare_wt_overview.py` 命令见[小地图重建指南](map-overview.md)。

下面使用已经从原 ACES/GRP 解码并保存证据的 Flanders 配置与 landclass 记录，重新只读提取游戏里的七个 RndGrass 模型及其独立 alpha。新输出使用本轮时间戳目录。

```powershell
$wtFoliage = Join-Path $wtRoot ('artifacts/maps/wt-scene/foliage-' + $wtStamp)
$wtLevelConfig = Join-Path $wtRoot 'artifacts/maps/wt-scene-20261118/level-config-02/decoded/levels/avg_vlaanderen.blk.json'
$wtConfigProof = Join-Path $wtRoot 'artifacts/maps/wt-scene-20261118/level-config-02/decoded/verification.json'
$wtFoliageLandclass = Join-Path $wtRoot 'artifacts/maps/wt-scene-20261118/foliage-01/landclasses/avg_vlaanderen_detailed_biome.json'

& $wtPython -B .\tools\maps\extract_wt_foliage.py --level-config $wtLevelConfig --config-verification $wtConfigProof --landclass-json $wtFoliageLandclass --texture-index (Join-Path $wtSource 'textures-03/result.json') --game-root $wtGame --dae-root $wtDae --oodle-library $wtOodle --output (Join-Path $wtFoliage 'source')
if ($LASTEXITCODE -ne 0) { throw 'Original grass source extraction failed.' }

& $wtBlender --background --factory-startup --python-exit-code 1 --python .\tools\maps\prepare_wt_foliage_meshes.py -- --source (Join-Path $wtFoliage 'source/foliage-source.json') --output (Join-Path $wtFoliage 'meshes')
if ($LASTEXITCODE -ne 0) { throw 'Original grass FBX preparation failed.' }

& $wtPython -B .\tools\maps\prepare_wt_foliage.py --source (Join-Path $wtFoliage 'source/foliage-source.json') --meshes (Join-Path $wtFoliage 'meshes/mesh-preparation.json') --landclass (Join-Path $wtPackage 'landclass/landclass.json') --output (Join-Path $wtPackage 'foliage') --evidence (Join-Path $wtFoliage 'prepared')
if ($LASTEXITCODE -ne 0) { throw 'Source grass weight package preparation failed.' }
```

输出 `foliage/foliage.json` 绑定七个 FBX、七张 RGB mask、七张单独的 `*_tex_m_a` 遮罩、HQ 原地表颜色和四张 4096×4096 RGBA 权重图，承载全部 13 个源层。七模型共 56 个原三角形；权重覆盖完整 2,048 米核心区，像素大小 0.5 米，UE WorldXY 厘米到 UV 为 `(X/204800,1-Y/204800)`。源草密度按 Dagor 候选格与顶点数上限换算为 UE 每 100 m² 候选数，保留原比例与颜色端点。

这一源包提供规则与权重，运行时使用 Landscape Grass 流送。原 microdetail 移除位、按高度混合的 biome 边界、grass-decal 覆写、另一套 `small_grass` billboard、原随机种子、风与多 LOD 过渡另行适配。具体颜色公式、原 alpha 来源和验证范围见[材质适配说明](../research/wt-material-adapters.md#source-grass-layers-and-streamed-population)。

### 4. 显式全图导入

```powershell
$wtImport = Join-Path $wtRoot ('artifacts/maps/wt-scene/import-' + $wtStamp)
powershell.exe -NoProfile -File .\tools\unreal\Import-WTScene.ps1 -EngineRoot $wtEngine -SourceFile (Join-Path $wtPackage 'scene-source.json') -EvidenceDirectory $wtImport
if ($LASTEXITCODE -ne 0) { throw 'Full scene import failed.' }
```

也可以单独运行默认入口：

```powershell
powershell.exe -NoProfile -File .\tools\unreal\Import-WTScene.ps1 -EngineRoot E:\simulator-tools\UE_5.8
```

导入包装脚本保存独立 `execution.json` 和 `engine.log`，默认超时 7200 秒。导入器重新检查当前全部 1,365 个 FBX 的来源哈希，并核对各网格实际三角形数量、包围盒和稳定材质身份；重复的同一材质槽按源 ID 绑定。叶片网格使用五个全精度 UV 通道，先以裸几何校验参考 bounds，再设置覆盖所有无风朝向的 bounds extension。`world_decal_000` 则必须保留两通道全精度 UV，并通过 source opacity/geometry 两份 hash-bound proof 校验。

续跑使用新的证据目录。源 FBX、导入版本、材质身份、碰撞策略、叶片契约/包络、source opacity 与 geometry proof 绑定、保存资产哈希一致时，网格可从缓存复用；原生数量、UV、材质分配和包围盒仍接受检查。

当前材质作者为 VERSION 6，覆盖主场景 983 个源材质 ID，包含原 Land class 表/权重、水面合同，以及 34 个 HeightDecal 和 25 个经实际 T0/T1 像素与 hash 审计的普通树遮罩适配。底图由包内 `overview.json` 在同次主导入时导入；源草在后续第 4a 步安装。`world_decal_000` 写入 `WT_GroundDecalRVT`，由 Landscape 和外部 landmesh 采样；该源网格的 MainPass 为 Never，其他场景 Decal 继续使用原 Surface 透明路径。项目需启用 `r.VirtualTextures=1`；项目配置按 DXT5/BC5/DXT5、264 px 含边界物理页匹配 256 MiB 固定池。原生审计读回该配置；运行阶段记录同步请求矩形覆盖及随后两个完成帧，并通过实际截图检查可见结果。

成功后保存：

- 关卡资源：`/Game/RMSim/Maps/wt_vlaanderen/World`。
- 本地关卡：`unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/World.umap`。
- 包内报告：`assets/maps/wt_vlaanderen/ue-scene-import-report.json`。
- 本次详细报告：`artifacts/maps/wt_existing/scene-import-<UTC>/report.json`。

状态 `imported_pending_runtime_validation` 表示关卡保存完成，下一步检查保存后的场景与车辆。包装脚本核对真实退出码、报告完成时间、manifest SHA 和保存关卡 SHA。

### 4a. 安装源草并烘焙原生草图

主导入使用 NullRHI 完成主场景、Land 与底图 UI 资源。每次主导入成功并退出后，执行独立的渲染作者入口：

```powershell
$wtGrassAuthor = Join-Path $wtRoot ('artifacts/maps/wt-scene/grass-author-' + $wtStamp)
powershell.exe -NoProfile -File .\tools\unreal\Author-WTGrass.ps1 -EngineRoot $wtEngine -PackageDirectory $wtPackage -EvidenceDirectory $wtGrassAuthor -ShaderWorkers 4 -TimeoutSeconds 1200
if ($LASTEXITCODE -ne 0) { throw 'Native grass authoring or weight baking failed.' }
$wtGrassAuthorReport = Get-Content -LiteralPath (Join-Path $wtGrassAuthor 'verification.json') -Raw | ConvertFrom-Json
if ($wtGrassAuthorReport.status -ne 'passed') { throw 'Native grass author report failed.' }
```

该入口使用 `D3D11 + AllowCommandletRendering`，当前 Flanders 纹理池参数为 4096 MiB。`-ShaderWorkers` 接受 1–4，默认 4；2–4 通过本次进程的 `-ini:Engine` 参数控制 shader worker 数量，选 1 时传入 `-NoShaderWorker`。引擎和项目 INI 文件保持原样，执行报告记录请求数量，实际 worker 与编译阶段由本次日志确认。它安装一个草父材质、13 个颜色实例、七个源网格和 13 个 GrassType，在既有 Terrain 上追加 LandscapeGrassOutput，再同步构建草图与预览区流送实例。草实例不参与车辆碰撞，源车辆和地形几何保持原数据。

作者逐层核对保存前的非零权重、模型、尺度、密度及取整到 UE 厘米的 cull 距离。成功后保存 World，并在主 `ue-scene-import-report.json` 的 `postprocess` 中追加草步骤与 World 前后 SHA；此前报告和本次 `World-before-grass.umap` 保留证据链。包装器保存 `execution.json`、`engine.log` 和 `verification.json`，使用同工程共享互斥。本轮 `grass-native-07-resume` 已完成并退出 0，保存了 13 层草图；1,089 个组件有效、986 个组件有非零权重、过期草图为 0。两处预览视点同步更新后创建 2,237 个 Grass 组件、52,984 个真实 render instances，计数来自 `UGrassInstancedStaticMeshComponent::GetNumRenderInstances`。草组件随视点生成；World 持久保存各组件的 GrassData，保存重载由第 5 步独立检查。

草作者已经保存完整 Foliage/Terrain、随后编译或烘焙中断时，可在自有 UE 进程退出后使用严格恢复入口，并选择新的证据目录：

```powershell
$wtGrassResumeStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$wtGrassResume = Join-Path $wtRoot ('artifacts/maps/wt-scene/grass-resume-' + $wtGrassResumeStamp)
powershell.exe -NoProfile -File .\tools\unreal\Author-WTGrass.ps1 -EngineRoot $wtEngine -PackageDirectory $wtPackage -EvidenceDirectory $wtGrassResume -ShaderWorkers 4 -ResumeAuthoredAssets -TimeoutSeconds 1200
if ($LASTEXITCODE -ne 0) { throw 'Resumed native grass baking failed.' }
$wtGrassResumeReport = Get-Content -LiteralPath (Join-Path $wtGrassResume 'verification.json') -Raw | ConvertFrom-Json
if ($wtGrassResumeReport.status -ne 'passed') { throw 'Resumed grass author report failed.' }
```

恢复先校验来源 level、foliage definition 和 importer SHA，以及已保存的七个模型、四张权重纹理、13 个 GrassType/材质绑定、Terrain 唯一草输出与逐层通道连接；通过后复用这些资源继续原生编译和烘焙，并检查 Foliage/Terrain 文件 SHA。主场景重新导入或源定义变化后，执行普通 author 流程以安装当前草图。每轮保留实际 execution 退出状态；发生超时时，仍为 `running` 的 Python 报告在紧凑摘要中记为 `interrupted`。烘焙成功、保存重载和游戏草区画面各由后续独立证据确认。

### 4b. 已保存旧场景的水面独立更新（按需）

当前材质作者 VERSION 6 的主导入会消费已绑定的源水面合同。使用该版本完成第 4 步时，水面已随主材质安装，可直接进入第 5 步。对早于 VERSION 6 的已保存场景补水面时，在草作者退出后运行独立更新；它只更新原水面的实例与专用父材质：

```powershell
$wtProject = Join-Path $wtRoot 'unreal/RMSim/RMSim.uproject'
$wtEditorCmd = Join-Path $wtEngine 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
$wtWaterUpdate = Join-Path $wtRoot ('artifacts/maps/wt-scene/water-update-' + $wtStamp)
$wtWaterScript = Join-Path $wtRoot 'tools/unreal/update_wt_water.py'
& $wtEditorCmd $wtProject -run=pythonscript "-script=$wtWaterScript" "-SceneSource=$wtSceneSource" "-WaterUpdateOutput=$wtWaterUpdate" "-AbsLog=$wtWaterUpdate-engine.log" -AllowCommandletRendering -d3d11 -NoShaderWorker -unattended -nop4 -NoSplash -nosound
if ($LASTEXITCODE -ne 0) { throw 'Saved-scene water update failed.' }
$wtWaterReport = Get-Content -LiteralPath (Join-Path $wtWaterUpdate 'verification.json') -Raw | ConvertFrom-Json
if ($wtWaterReport.status -ne 'passed') { throw 'Water update report failed.' }
```

### 5. 重载保存关卡并做独立原生审查

```powershell
$wtAudit = Join-Path $wtRoot ('artifacts/maps/wt-scene/scene-audit-' + $wtStamp)
$wtAuditLog = $wtAudit + '-engine.log'
$wtProject = Join-Path $wtRoot 'unreal/RMSim/RMSim.uproject'
$wtEditorCmd = Join-Path $wtEngine 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
$wtAuditScript = Join-Path $wtRoot 'tools/unreal/verify_wt_scene.py'
$wtSceneSource = Join-Path $wtPackage 'scene-source.json'
& $wtEditorCmd $wtProject -run=pythonscript "-script=$wtAuditScript" "-SceneSource=$wtSceneSource" "-SceneAuditOutput=$wtAudit" "-AbsLog=$wtAuditLog" -NullRHI -unattended -nop4 -NoSplash -nosound
if ($LASTEXITCODE -ne 0) { throw 'Native scene audit process failed.' }
$wtAuditReport = Get-Content -LiteralPath (Join-Path $wtAudit 'verification.json') -Raw | ConvertFrom-Json
if ($wtAuditReport.status -ne 'passed' -or $wtAuditReport.complete -ne $true) { throw 'Native scene audit report failed.' }
```

审查脚本使用 `SceneSource` 和 `SceneAuditOutput` 参数。它加载已经保存的 World，逐组核对来源标签、726 个视觉组、对应碰撞组、35,922 个视觉实例计数和材质绑定；从实际 HISM 引用的每个唯一网格调用 `DescribeMapMesh`，逐个核对保存后 LOD0 总三角数和 section 覆盖，世界贴花另核对已绑定 proof 的 35 个材质分配。同时核对 source opacity 证据、两通道全精度 UV、RVT asset/volume/writer 和物理池配置。随后检查 Landscape 组件、原高度样本、四处实际高度射线和一个原始道具碰撞射线，并确认检查前后 `World.umap`、proof 绑定网格资产、FBX、proof 和 manifest 哈希一致。`native-scene-03` 已实际通过 21 项检查。

该报告分别保存 `vehicle_driving_verified`、`rendered_appearance_verified` 字段；驾驶和画面观察由后续步骤提供证据。

先重载并核对草的已保存数据。独立脚本比较 13 层的实际权重、模型、尺度、密度与 cull；再把出生区和三个原草区的原生四角权重与源 PNG 双线性采样逐层比较。原生值允许 1 byte 量化误差，出生区要求零、三个草区各有非零层。

```powershell
$wtGrassAudit = Join-Path $wtRoot ('artifacts/maps/wt-scene/grass-reload-' + $wtStamp)
$wtGrassFixture = $wtGrassAudit + '-source-samples.json'
& $wtPython -B .\tools\maps\prepare_wt_grass_audit_samples.py --package $wtPackage --output $wtGrassFixture
if ($LASTEXITCODE -ne 0) { throw 'Source grass sample expectations failed.' }

$wtGrassAuditScript = Join-Path $wtRoot 'tools/unreal/verify_wt_grass.py'
$wtGrassAuditLog = $wtGrassAudit + '-engine.log'
& $wtEditorCmd $wtProject -run=pythonscript "-script=$wtGrassAuditScript" "-MapPackage=$wtPackage" "-GrassSampleFixture=$wtGrassFixture" "-GrassAuditOutput=$wtGrassAudit" "-AbsLog=$wtGrassAuditLog" -NullRHI -unattended -nop4 -NoSplash -nosound
if ($LASTEXITCODE -ne 0) { throw 'Saved grass reload audit process failed.' }
$wtGrassAuditReport = Get-Content -LiteralPath (Join-Path $wtGrassAudit 'verification.json') -Raw | ConvertFrom-Json
if ($wtGrassAuditReport.status -ne 'passed') { throw 'Saved grass reload audit report failed.' }
```

草重载脚本只读取保存数据和源合同，不重新生成草图或保存 UE 资源。它同时检查七个网格的三角数、UV、包围盒和源哈希，以及 World、草资源、Terrain、源描述的前后 SHA。完整记录为 `verification.json`，紧凑结果为 `summary.json`。本轮 `grass-reload-01` 已在独立进程通过全部 27 项检查并退出 0，58 个已保存文件 SHA 保持一致。四处源位置的非零层数量依次为 0、4、8、4，原生四角权重与源 PNG 的最大差为 1 byte，插值最大差为 0.25 byte。初始流送数量由第 4a 步的原生作者记录，游戏中的可见植被由第 6 步画面验证。

在真实 RHI 下检查已保存材质图及本轮受影响的材质实例：

```powershell
$wtMaterials = Join-Path $wtRoot ('artifacts/maps/wt-scene/material-audit-' + $wtStamp)
$wtMaterialLog = $wtMaterials + '-engine.log'
$wtMaterialScript = Join-Path $wtRoot 'tools/unreal/verify_wt_materials.py'
& $wtEditorCmd $wtProject -run=pythonscript "-script=$wtMaterialScript" "-SceneSource=$wtSceneSource" "-MaterialAuditOutput=$wtMaterials" "-AbsLog=$wtMaterialLog" -AllowCommandletRendering -d3d11 -NoShaderWorker -unattended -nop4 -NoSplash -nosound
if ($LASTEXITCODE -ne 0) { throw 'Native material audit process failed.' }
$wtMaterialReport = Get-Content -LiteralPath (Join-Path $wtMaterials 'verification.json') -Raw | ConvertFrom-Json
if ($wtMaterialReport.status -ne 'passed' -or $wtMaterialReport.complete -ne $true) { throw 'Native material audit report failed.' }
```

历史 `native-materials-06` 已验证 7 个父图（含 Terrain）、2 个 Leaves、34 个 HeightDecal、1 个 Land、1 个原 river Decal 与 43 个普通 tree 实例，共 88 个资源。最新门禁还检查新增水面与草父图、13 个草颜色实例及其源纹理/参数绑定，实际资源数量以本次报告为准。脚本在 D3D11 下完成目标 shader 编译并核对保存资产 SHA 前后相同；当前 native-materials-08-final 已完成 104 个资源的真实原生编译；草、水与更新 Land 的外观由 gui-final-acceptance 的实际游戏画面记录。

### 6. 真实接触往返与共用复位

```powershell
$wtDrive = Join-Path $wtRoot ('artifacts/maps/wt-scene/contact-reset-' + $wtStamp)
powershell.exe -NoProfile -File .\tools\unreal\Test-ManualControls.ps1 -EngineRoot $wtEngine -MapFile (Join-Path $wtPackage 'map.json') -SpawnId default -Contact -ContactReset -ContactDistanceCm 340 -ContactSpeedMps 1 -GraphicsRhi D3D11 -SingleShaderWorker -EvidenceDirectory $wtDrive
if ($LASTEXITCODE -ne 0) { throw 'Contact driving/reset verification failed.' }
```

该组合使用真实世界 tick 和轮电机命令，检查出生稳定、3.4 米前进、制动、返程及共用复位路径，预期五项检查和八张 PNG。`ContactReset` 要求 `Contact`，与 `ContactIntegration`、`WithTraining` 互斥。报告包括 `control-result.json`、包装验证和 `runtime.log`；包装验证记录 `requested_rhi` 与 `single_shaderworker`。截图阶段为 `contact-rest`、`contact-end`、`contact-return`、`contact-reset`，每阶段 overview/aim 两张。

随后用 `Play-WarThunder.cmd` 打开可交互窗口，检查出生区、建筑、地表、植被、M 地图切换和车辆实际接地画面。该 prepared 入口直接读取已保存成果。

源草权重在默认出生点附近 10 米内为零。最近可验草区约在 ROS `(1074.25,-1011.75)`，距出生点约 46.7 米，原地形高度 `-0.344426642` 米；花草区为 `(893.75,-1029.25,2.488624399)` 米，郁金香区为 `(1019.25,-1217.75,1.907072557)` 米。画面检查可驾驶至实际草区，或只调整观察相机；默认出生点和车辆物理参数保持当前合同。源位置及邻域权重证据在 `foliage-01/field-samples.json`，最终游戏截图需记录对应源包、World 和模块 SHA。

## 地图包和运行时约定

[map.json](../../assets/maps/wt_vlaanderen/map.json) 使用 `kind: rm_scene_map`，`level_asset` 必须精确等于 `/Game/RMSim/Maps/<id>/World`。启动脚本和 C++ 加载器均校验这一对应关系。

WorldSettings 保存来源标签：

```text
RMSimMapId=wt_vlaanderen
RMSimSourceSha256=427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f
```

运行时检查当前 world package、来源标签、Landscape、视觉 HISM 和原始碰撞 HISM。碰撞组件注册就绪后，以源地面支撑检查初始完整车辆位置，再初始化现有车辆接触物理。视觉网格与碰撞网格使用独立组件。运行报告记录关卡名称、来源哈希、Landscape/实例组件数与碰撞就绪状态；ROS 桥将类型报告为 `rm_scene_map`。模块包含 shader、资源 streaming 与 RVT 的渲染就绪检查，最终 contact 报告应核对 `scene_render_readiness`。模块身份以本次执行报告记录的 SHA 为准；`flanders-contact-03-rvt` 使用的历史模块 SHA 为 `eceebaa1634a2df9c6314556efcc0190f5c5b4b926ef13299669ca461d68de76`。本轮小地图和草回读 helper 的构建记录单独保留。

地图描述管理来源、关卡和出生点；车辆几何、悬挂、驱动与传感器配置位于车辆包。

## 来源数据、坐标与朝向叶片

数据来源为[来源记录](../../assets/maps/wt_vlaanderen/SOURCE.md)、[历史准备验证](../../assets/maps/wt_vlaanderen/preparation-verification.json)、[碰撞覆盖记录](../../assets/maps/wt_vlaanderen/collision-coverage.json)和[本轮叶片适配](../../artifacts/maps/wt_existing/leaf-adaptation-20261117-01/verification.json)。

| 内容 | 已准备的数据 |
|---|---|
| 原图存储物件 | 45,675 个 RIGz 实例、659 类模型 |
| 实例矩阵 | 35,825 个普通旋转/缩放实例；9,850 个剪切或反射实例按原矩阵烘焙成批 |
| 视觉与碰撞网格 | 726 个视觉 FBX、639 个碰撞 FBX；历史 preparation 检查了原始准备批的 1,365 个文件，三叶片及单贴花 FBX 的后续变化由各自适配证据绑定 |
| 精细地形 | 原始 4096 × 4096 uint16 高度样本、0.5 米间距、约 2048 米范围；原始样本字节一致 |
| 外部背景 | 原 landmesh 与 SCN 静态几何，背景约 32 千米宽 |
| 原碰撞资源 | 匹配 616 个资源，其中 13 个由来源声明为非物理；43 类视觉资源缺少匹配碰撞资源，清单保留在包内 |

源物件数与 UE HISM 实例数采用各自口径：剪切/反射物件在烘焙批内保留原几何和变换，多个原物件可共享一个已烘焙网格实例。

```text
Dagor (x, y-up, z) -> ROS (x, -z, y) 米
ROS (x, y, z)     -> UE  (100x, -100y, 100z) 厘米
Dagor 原坐标       -> UE  (100x, 100z, 100y) 厘米
```

转换保留原点、尺度和源高度量化。历史准备验证中 35,825 个序列化实例的最大角点误差为 0.5341 毫米。

旧式 `rendinst_facing_leaves` 在源文件中以四个同中心顶点和 half4 偏移表示一张叶片。三模型的源半精度四分量已恢复，采用源码对应的无风参考卡片，保留 UV3/UV4 给材质按相机朝向重建：

| 模型 | 参考几何总三角形 | 叶片三角形 | 原始实例 |
|---|---:|---:|---:|
| `kh_gol_euraspen_fall_03_rendinst` | 104 | 62 | 593 |
| `kh_gol_liveoak_03_rendinst` | 84 | 80 | 1171 |
| `kh_gol_tree_simple_a` | 64 | 40 | 605 |
| 合计 | 252 | 182（91 张卡片） | 2369 |

本轮原 NPZ 位置误差为 0，2369 条源实例与 RMI 逐元素匹配，scale 全为正且等轴。Blender 实际回读五个 UV 通道，UV 及 raw4D 重建误差为 0；最大角点误差为 `4.291534 × 10⁻⁶ m`。原树干、贴图 UV、材质身份和实例文件保留。对应 UE 材质的原生构建和画面结果按独立证据记录。

## 导航接续

本轮覆盖原图关卡加载、碰撞与车辆驾驶。导航接续需要为该关卡建立并验证对应的占用图、地图元数据、定位和路线；`Play-Navigation.cmd` 继续使用已配置的 RMUC 场景。

现有 `rm_map_tools` 的占用图导出面向 `rm_flat_map` 的平地 box 几何。弗兰德斯的高度场、建筑和坡地导航数据需从本关卡实际几何与碰撞准备，并与新的地图身份匹配。现有 sentry_ws 导航参数和算法接入继续在导航工作树维护。

## 画面适配项

材质使用原图解码贴图与源 UV，详细公式和来源见[材质说明](../research/wt-material-adapters.md)。

| 项目 | 当前实现与后续内容 |
|---|---|
| 建筑、道具、分层材质 | 原贴图与源 UV/法线适配；Perlin 空间调制、调色板及湿润效果继续补充 |
| 精细地表 | 原宏观颜色、索引和分组微细节，读取 source ground-decal RVT；双层 flowmap 混合继续补充 |
| 外部地形与水面 | Land 读取五个原 class 颜色与 1,024 cell 的原 atlas 权重并接收 RVT；Water 使用原水面几何及 level 全局参数。当前源合同、具体公式与各自原生状态见材质说明 |
| 普通植被 | 43 项内容审计中 25 个材质采用源 T1.r coverage、保留原 atest；其余 18 项维持原路径 |
| 地表草层 | 七个真实源网格、13 层、四张全核心区权重图、原 RGB mask 与独立 alpha；原生草图、两视点 52,984 实例及保存重载已通过，实际驶入草区后，草与粉白花实例已在 GUI 中清晰显示。microdetail、grass-decal 覆写、small_grass billboard、风及原随机位置另行适配 |
| 旧式朝向叶片 | 三类源 half4、参考几何、五通道 UV 及无风相机 WPO；原 T0 alpha 保留，风/pivot 动画继续补充 |
| 地表贴花 | 34 个 HeightDecal 使用原 short4n.w 和源高度混合公式；RVT 投影到接收地表，保留源顶点位置与碰撞 |
| 其他场景 Decal | 保留源图和 UV 的 Surface 透明路径；玻璃、室内、自发光等材质族继续适配 |
| 光照和动态内容 | saved-scene 室外天空及渲染就绪流程已实现；原图光照后处理、破坏与程序生成树木布局另行跟踪 |

RVT 使用 `Mask=128/255` 的清屏校正恢复 coverage，按 `RVTColor+BaseColor*(1-coverage)` 合成预乘颜色。法线在世界空间处理；UE packed Z 只保留符号，因此以去预乘后的 XY 和原生采样符号重建 Z。独立源码/数值复核见 `rvt-composition-review-01/verification.json`。原生材质资源门禁与最终画面观察分别记录。

## 验证证据

`import-06` → `native-scene-03` → `native-materials-06` → `flanders-contact-03-rvt` 保存了加入本轮 Land/UI/Water/草之前的完整原生检查点。后续新增源包与原生检查分别记录，最终 GUI 和草区截图绑定本次实际 World、源包与模块。

| 验证项 | 当前状态与证据 |
|---|---|
| 历史源资源准备 | 通过；`assets/maps/wt_vlaanderen/preparation-verification.json` 保持原样 |
| 源 half4 独立恢复与三 FBX | 通过；`artifacts/maps/wt_existing/leaf-adaptation-20261117-01/verification.json`；91 卡片、182 叶片三角、2369 原实例 |
| 原始 position.w / 单贴花 FBX / 重绑定 proof | 通过；`artifacts/maps/wt-scene-20261118/decal-opacity-01/adapt/verification.json`；263,592 corners 零误差，几何和 UV0 保持 |
| 普通树 split alpha | 43 项审计中 25 项满足内容/hash 条件；`artifacts/maps/wt-scene-20261118/tree-split-alpha-01/verification.json` |
| 原草资源与权重准备 | 七 FBX / 56 面、14 原 mask/alpha、四张 packed 权重、13 非空层及 pure contract 通过；`artifacts/maps/wt-scene-20261118/foliage-01/prepared-01/verification.json` |
| 草作者中断记录 04 / 05 | 两轮 execution 均在 1200 秒超时后终止自有 UE；Python 报告保留原 `running`，紧凑摘要记 `interrupted`。证据为 `grass-native-04/summary.json` 与 `grass-native-05/summary.json` |
| 草原生作者与同步流送 07 | 通过，真实 exit 0 / 34.224 s；13 层、1,089 valid / 986 weighted 组件、2,237 群组、52,984 render instances，World 已保存；`artifacts/maps/wt-scene-20261118/grass-native-07-resume/summary.json` |
| 草独立保存重载 02 | 27 项通过，真实 exit 0 / 12.067 s；四点 × 13 层权重、七个网格、58 文件 SHA 保持，模块 1bf4126e…；`artifacts/maps/wt-scene-20261118/grass-reload-02-final/summary.json` |
| 当前材质与接触回归 | `native-materials-08-final`：104 资源原生编译通过；`native-scene-04-final`：21 项通过；`flanders-contact-04-final` / `rmuc-regression-03-final`：各 5 项、8 PNG，通过。四项报告均绑定模块 d79cf620…，各目录 `summary.json` 保存证据 |
| RVT 合成独立复核 | 修正 packed normal Z 符号恢复后通过；12 组法线/coverage 与颜色、清屏、重叠数值检查；`artifacts/maps/wt-scene-20261118/rvt-composition-review-01/verification.json` |
| 保存关卡启动配置 | 11 项通过；`artifacts/maps/wt_existing/scene-launch-20261117-01/verification.json` |
| 导入与预览共享互斥 | 19 项脚本检查，含模拟进程列表和真实跨线程具名 mutex；`artifacts/maps/wt_existing/prepared-launch-20261118-01/verification.json` |
| RHI 入口参数与转发 | 27 项脚本检查，拦截进程边界；`artifacts/maps/wt_existing/graphics-rhi-20261118-01/verification.json` |
| 二进制 FBX 共点预查 | 639 个 collider 与其余 4 个 world 批共 1,934,088 面，严格共点为 0；`artifacts/maps/wt-scene-20261118/fbx-preflight-01/verification.json`；贴花另按 bound proof 核对 |
| 历史 readiness/RVT 模块 | 原生构建退出 0；contact-03 使用 module SHA `eceebaa1634a2df9c6314556efcc0190f5c5b4b926ef13299669ca461d68de76`；本轮 UI/草 helper 按新构建记录验收 |
| 完整原图导入 06 | 退出 0，175.87 s；`artifacts/maps/wt-scene-20261118/import-06/execution.json`，包内状态 `imported_pending_runtime_validation` |
| 保存场景独立检查 03 | 21 项通过，退出 0，12.47 s；`artifacts/maps/wt-scene-20261118/native-scene-03/summary.json` |
| 保存材质原生检查 06 | D3D11 / NoShaderWorker，88 资源、25 个 split mask 核对通过，退出 0，270.73 s；`artifacts/maps/wt-scene-20261118/native-materials-06/summary.json` 与 `verification.json` |
| 历史接触往返及复位 03 | 五项检查、67 个物理样本和八张 PNG 通过，退出 0、67.55 s；`artifacts/maps/wt-scene-20261118/flanders-contact-03-rvt/summary.json` 绑定完整报告与图片 SHA |
| 当前 Land/UI/Water/草版本 GUI | `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`：模块 d79cf620…、PID 43760，8 图 / 12 次真实 OS 动作 / 16 个驾驶输入边沿 / M 四次切换通过。实际驶至 ROS `(1075.232351,-1012.656026)`，草与粉白花、绿褐地表可见；Q 旋转后两图箭头同步由右转向下，出生标记独立 |

本轮草保存与独立重载共同绑定的资产：

- scene manifest SHA256：`91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27`。
- foliage definition SHA256：`8fa36662c1ecf48028284b735ad86336fd0e235c22e7ddeb3ccf16872a8f733e`。
- World SHA256：`20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25`。
- module SHA256：`c44d2c079aeaa597be3a5a5076787f89dce1f261ca77c29da26fba08cb263e18`。
- `grass-reload-01/verification.json` SHA256：`718b8f5d58b90924d8a2e9f0b53258203736e15d9b3c71e70ebc11a861fed2b9`。

上述历史检查点的保存资产：

- manifest SHA256：`ac0afa8a40d90feee51bc53d02b41aa1b33c10b9f3d2a574b0e7d7be1f18c7d9`。
- World SHA256：`720931dad262197929863d553c2388c3de18131277498749ef31faa201f12253`。
- native scene 03 确认 726 / 642 个视觉/碰撞 HISM 组、35,922 / 20,033 个 placements；1,365 个唯一网格的 source/native 三角形总数为 13,478,813 / 13,478,779，差值精确等于 proof 绑定的 34 个共点面。
- 四处 Landscape 射线的最大原样本误差为 `0.00603515446 cm`，原 kiosk 碰撞实例 item 2 命中。该检查同时读回 RVT 配置、贴花两通道全精度 UV 及 source opacity 绑定。

前一版本 `flanders-contact-02-render-ready` 已通过五项真实接触/往返/复位检查、八张 PNG 和渲染就绪门禁，室外天空已显示；其截图用于定位树 coverage 与地表贴花合成问题。`flanders-contact-03-rvt` 前行 340.284 cm（目标 340 cm），前行漂移 −0.070 cm / 4.401 s，刹车速度 0；返程剩余 9.477 cm、漂移 −0.025 cm；共用复位计数 1、回出生误差 0.0133 cm、速度 0。实际 rest、reset overview 和 end aim 图已检查：旧浮动地表遮挡消失，复位车辆完整，树冠 cutout 在行驶后持续生效。当前新增 Land/UI/Water/草使用新的原生报告和 GUI 画面验收。

`flanders-contact-03-rvt` 的 readiness 用时 30.626 s，编译队列 66→0、streaming 0，并完成两个稳定帧。RVT 同步请求用时约 0.00095 s，请求矩形覆盖计数为 88 tiles，之后记录两个完成帧。报告中的 88 tiles 表示请求覆盖，GPU 实际生产页数本轮未计量。历史 `import-04`、`native-scene-02`、`native-materials-05` 以及保留的失败报告继续作为来源修复过程的证据。

原始重复材质槽和共点叶片的失败报告位于 `artifacts/maps/wt_existing/scene-import-20261117T141621Z/report.json` 与 `scene-import-20261117T142619Z/report.json`。原始贴花 full proof、截图、日志和 dense runtime 报告留本地，compact summary 绑定其 SHA。
