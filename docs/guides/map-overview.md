# 小地图与 M 键大地图

弗兰德斯驾驶界面使用战争雷霆原始 `avg_vlaanderen_tankmap` 底图。右下角显示跟随车辆的近区地图，按 **M** 展开全幅地图，再按 **M** 返回近区视图。

## 驾驶时查看地图

在仓库根目录启动：

```powershell
.\Play-WarThunder.cmd
```

点击游戏窗口获得键盘焦点后即可使用 M。地图操作沿用当前游戏窗口，WASD、Q/E、鼠标瞄准、滚轮相机、制动和复位继续由[手动驾驶控制](manual-controls.md)处理。

| 显示或操作 | 含义 |
| --- | --- |
| 右下角小地图 | 默认 512 × 512 米的跟车视野；车辆居中，背景随位置移动 |
| M | 在小地图和全幅原始底图之间切换 |
| 青色三角形 | 车辆当前位置与车头方向 |
| 金色菱形 | 当前选定的出生点 |
| X / Y | 车辆在项目 ROS 坐标系中的实际位置，单位为米 |
| +X / +Y 箭头 | 源坐标轴在底图上的方向 |
| 比例尺 | 当前显示范围对应的实际距离 |

大地图开启时显示地图名、车辆位置、方向、出生点和图例。失去窗口焦点会重置 M 的按下边沿，恢复焦点的第一帧同步当前按键状态。小地图的源图外区域显示空底；全幅地图用边缘位置标示界外车辆，坐标继续显示实际数值。

底图覆盖 2048 × 2048 米核心区；保存关卡同时包含约 32 千米的外围场景。地图车位和朝向取自本机车辆姿态，独立用于 HUD 绘制；ROS 定位、传感器与控制链保持各自的数据源。

## 已验证的坐标标定

原 `aces.vromfs.bin` 中 `levels/avg_vlaanderen.blk` 的根参数给出 `tankMapCoord0=[0,0]`、`tankMapCoord1=[2048,2048]`，二维分量对应 Dagor 的 X/Z。源几何转换使用 `ROS=(Dagor X,-Dagor Z,Dagor Y)`。

原图方向通过三处独立水面几何、赛马场看台和马厩核验。八种转置/镜像候选中，匹配方向是 X 保持、图像 V 反向。三组颜色阈值均选择同一方向；原水面覆盖区域与可见蓝色水像素的主阈值命中率为 46.50%，第二候选为 2.206%。该比例用于判别原图朝向；原始水面多边形包含岸边。

| 图片边界 | 对应 ROS 坐标，米 |
| --- | --- |
| 左上，UV(0,0) | `[0,-2048]` |
| 右上，UV(1,0) | `[2048,-2048]` |
| 左下，UV(0,1) | `[0,0]` |

因此连续纹理坐标为：

```text
u = ROS_X / 2048
v = 1 + ROS_Y / 2048
```

1024 × 1024 原图每像素对应 2 米。默认出生点 ROS `[1028,-1020]` 对应 UV `[0.501953125,0.501953125]`，连续图像坐标为 `[514,514]`。图上 +X 向右、+Y 向下。三个角点表示图片的连续外边界，HUD 按该定义计算车辆位置、朝向和比例尺。

完整证据见[五处地标和八方向核验](../../artifacts/maps/wt-scene-20261118/map-overview-01/georef-02/verification.json)，SHA256 为 `ff43453d9b85949747969a6fa0b01c702c0468c5236b3b9a4a22abc4590ba33d`。

## 准备文件与原生纹理

地图包的 UI 文件如下：

| 文件 | 作用 |
| --- | --- |
| `assets/maps/wt_vlaanderen/overview.json` | HUD 读取的地图身份、纹理路径、三角点标定和近区范围 |
| `assets/maps/wt_vlaanderen/overview/avg_vlaanderen_tankmap.png` | 从原 `locations_maps.dxp.bin` 解码的 1024 像素原图 |
| `assets/maps/wt_vlaanderen/overview/calibration.json` | 校准证据的完整副本，由 SHA256 绑定 |
| `/Game/RMSim/Maps/wt_vlaanderen/UI/WT_MapOverview` | UE 显示用的独立 Texture2D |

原 PNG 的 SHA256 为 `f4375346e0b6f267e0b16ddb48fb1fad4e9864000530ac2a0a267babbeb5c851`。HUD 定义的 SHA256 为 `1a9c1233bb6758abb54800dfb3fe8bc49a91a43645cf63cc11c3cde4d2ac730b`。

`tools/maps/prepare_wt_overview.py` 会重读原 DXP 包、VROMFS、共享名称表、字典和 BLK，检查原包、源条目、地图范围及校准证据，再生成三个 UI 文件。它要求重新解码出的 PNG 与已校准原图逐字一致。`scene-source.json`、模型、碰撞和关卡由各自的地图准备流程维护。

本机重建示例，在仓库根目录执行：

```powershell
$wtRoot = (Get-Location).Path
$wtPackage = Join-Path $wtRoot 'assets/maps/wt_vlaanderen'
$wtUiSource = Join-Path $wtRoot 'artifacts/maps/wt-scene-20261118/map-overview-01'
$wtUiProof = Join-Path $wtUiSource 'georef-02/verification.json'
$wtUiProofSha = 'ff43453d9b85949747969a6fa0b01c702c0468c5236b3b9a4a22abc4590ba33d'
$wtUiOutput = Join-Path $wtRoot ('artifacts/maps/wt-overview/prepare-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
python -B .\tools\maps\prepare_wt_overview.py --package $wtPackage --calibration $wtUiProof --calibration-sha256 $wtUiProofSha --dae-root D:\simulator\third_party\dagor_asset_explorer\src\dae --oodle-library E:\simulator-tools\UE_5.8\Engine\Binaries\DotNET\UnrealBuildTool\runtimes\win-x64\native\oo2core.dll --output $wtUiOutput
if ($LASTEXITCODE -ne 0) { throw 'Overview source preparation failed.' }
```

完整地图导入已调用 `tools/unreal/wt_overview.py` 的 `import_overview(package, folder)`。该模块先验证地图、图像和校准身份，再将纹理设置为 UI 组、RGBA8、sRGB、无 mip、NeverStream、Clamp 和双线性过滤，保存并读回设置、原生尺寸和来源标签；导入报告中记录保存资产的 SHA256。整图导入命令见[弗兰德斯地图指南](war-thunder-flanders.md)。

模块也提供独立 UE Python 入口 `-OverviewPackage=<包路径> -OverviewReport=<新报告路径>`，供维护流程单独导入这一张 UI 纹理。`validate_overview(package, folder)` 可在普通 Python 中检查 UI 包与证据绑定。

## 验收与诊断

源图标定、原资源重建和解析器检查已通过，UI 纹理在 `import-07` 中通过原生导入、属性和 1024 × 1024 尺寸检查。首次游戏加载会等待异步纹理编译完成后显示底图。

2026-11-18 已通过真实游戏窗口验收：从 `Play-WarThunder.cmd` 启动，在实际获得焦点的 UE 窗口完成 12 次操作，并审阅 8 张原生截图。M 两轮展开和收起均成功；W/A 驾驶后，小地图持续显示车辆位置，出生点保持独立。车辆由 ROS `[1028,-1020]` 驶至稳定位置 `[1075.232351,-1012.656026]` 米，HUD 显示 `1075.2 / -1012.7`；Q 转向后，小地图和大地图的车辆箭头均朝下。最终收起大地图，车辆停在可见的草丛与粉、白色花草中。

[真实窗口验收记录](../../artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json) 绑定 8 张截图、12 份输入执行记录、16 个驾驶按下/松开边沿、4 条原生 M 切换日志以及当前模块和地图文件。最后稳定姿态换算得到 UV `[0.5250157965,0.5055390497]`，与原生 HUD 日志一致。输入证据只在驾驶按键边沿更新，截图按各自拍摄时间解读。

维护时可按以下步骤复核实际游戏窗口：

1. 启动弗兰德斯并等待场景准备完成，检查右下角底图、车辆三角形、出生点、坐标和比例尺。
2. 按 M 检查全幅原图，再按 M 检查近区视图恢复。日志应分别出现 `MAP_OVERVIEW_TOGGLE expanded=1` 与 `expanded=0`。
3. 驾驶和转动车辆，观察背景移动与车头箭头；出生点菱形保持在标定后的世界位置。
4. 切走窗口再返回，确认按键焦点恢复后 M 开关正常。

加载成功时记录 `MAP_OVERVIEW_READY`，包含源身份、图片尺寸、三角点基向量、近区范围和实际车位 UV。`MAP_OVERVIEW_IMPORTED` 记录纹理的原生导入结果。配置或资产不完整时，HUD 显示 `Map image unavailable`，日志中的 `MAP_OVERVIEW_UNAVAILABLE` 给出具体原因。

源与准备证据：

- [地理标定](../../artifacts/maps/wt-scene-20261118/map-overview-01/georef-02/verification.json)：五处地标、八方向候选、三组阈值、原始坐标字节。
- [原始资源重建](../../artifacts/maps/wt-scene-20261118/map-overview-01/prepare-02/verification.json)：修订解析器后重建通过，三个 UI 文件字节均保持一致。
- [解析器审阅](../../artifacts/maps/wt-scene-20261118/map-overview-01/parser-review-01/verification.json)：23 项二进制输入与边界检查通过，55 个原 BLK 的重解结果与已有 JSON 逐项相等。

解析器对普通 VROMFS 路径使用 UTF-8，保留原始 `FF ? n m` 共享名称表键；对压缩内容限制解码大小并检查完整帧。BLK 的 `color` 值保持原 E3DCOLOR 的 **B、G、R、A** 字节顺序。对应官方 Dagor 源版本为 `9639942de5a8e5b01d91977f60ed82cda22d5f41`，具体字段和源文件哈希存于审阅记录。

## 二进制解析回归

已有 23 个 VROMFS/BLK 夹具已集中到 [test_wt_binary_formats.py](../../tests/map_tools/test_wt_binary_formats.py)，覆盖 UTF-8 名称、保留二进制键、原始 Point2/BGRA 字节、字段类型、越界、截断和压缩输出上限。使用本次源提取所用的 CPython 3.14 环境运行：

```powershell
python -B -m unittest discover -s tests/map_tools -p test_wt_binary_formats.py -v
```

缺少标准库 `compression.zstd` 的 Python 环境会明确跳过这 23 项，常规测试发现可继续进行；解析器验收使用实际执行的 3.14 结果。此次迁移保持全部测试函数与夹具内容一致，3.14 下 23 项通过，依赖缺失分支的 23 项跳过也已检查：[迁移验证](../../artifacts/maps/wt-scene-20261118/map-overview-01/binary-fixtures-01/verification.json)。
