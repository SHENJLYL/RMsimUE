# 地图包与切换

默认地图 `rmuc2027` 使用 DJI RMUC2027 V1.0.0 CAD 来源静态场地，已在 Windows 运行。`wt_cdk_crop` 是 War Thunder 官方 CDK 高度场的 512×512 m 裁剪；`wt_vlaanderen` 是从本机弗兰德斯原图生成的完整保存关卡，当前 import06、原生场景 21 项、材质 88 资源与本版本接触往返/复位 5 项检查已通过，最终 GUI 画面交接继续跟进。另保留自建平地回归地图 `import_lab`（20×20 m）和 `warehouse_lanes`（24×16 m）。

来源、坐标和缺失内容见各包SOURCE.md及[来源审查](../research/map-source-audit.md)。RMUC当前没有比赛裁判/可动机构；WT裁剪没有建筑、植被和原地表贴图。

## 切换地图

从仓库根目录运行：

    Play-ManualVehicle.cmd assets\maps\warehouse_lanes\map.json

也可把map.json拖到Play-ManualVehicle.cmd上。不传地图时使用rmuc2027。已导入地图之间切换无需重新编译或导入车辆。

War Thunder地形斜坡试驾：

    Play-ManualVehicle.cmd assets\maps\wt_cdk_crop\map.json hillside

第二参数是出生点ID：RMUC有default/red，WT有default/hillside，均为预览出生点。

完整参数入口：

    pwsh -File tools/unreal/Run-ImportLab.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -InputType MeshRig -InputFile assets/vehicles/aim_sentry/ue_mesh_rig.json -MapFile assets/maps/warehouse_lanes/map.json -SpawnId blue

窗口HUD显示地图名称和所在区域。区域目前只有位置/类型/阵营语义与可见标记，没有补给或占点结算。

## 导入来源网格地图

mesh-source.json记录FBX、显示/碰撞分组、材质、哈希、出生点与来源；导入后生成rm_mesh_map类型map.json。数据使用米/ROS右手坐标，导入一次转换UE单位/方向。

    pwsh -File tools/unreal/Import-MeshMap.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -SourceFile assets/maps/rmuc2027/mesh-source.json

同一导入器也处理WT裁剪。完整Start-ManualVehicle.ps1和Linux.sh入口会导入选中包的mesh-source.json。[Ubuntu离线准备](ubuntu-offline.md)。

UE资源路径须在/Game/RMSim/Maps/内，碰撞网格使用complex-as-simple triangle collision。当前 region 数组为空。以下 `-Terrain` 记录属于历史运动学支撑检查，采用 2 cm 间隙、35°坡度和 5 cm 台阶限制。当前默认车辆入口使用真实 `ContactPhysics`；原图保存关卡的驾驶验证见下一节。

原生检查：

    pwsh -File tools/unreal/Test-ManualControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -Terrain -MapFile assets/maps/rmuc2027/map.json -MapOverview -EvidenceDirectory artifacts/maps/new-rmuc-check
    python tools/maps/verify_mesh_map.py assets/maps/rmuc2027 artifacts/maps/new-rmuc-check

7项检查使用真实网格碰撞与共用操控函数，输出4PNG、位姿/轮位支撑记录。证据目录须为新目录；可换WT地图并加-SpawnId hillside。

## 导入原图保存关卡

弗兰德斯原图包 `wt_vlaanderen` 使用 `rm_scene_map`，加载已经保存的 HISM / Landscape 关卡。地图包的三种入口分工如下：

| 类型 | 内容 | 入口处理 |
|---|---|---|
| `rm_flat_map` | 平地、box 障碍和区域 | 根据 map.json 创建场景 |
| `rm_mesh_map` | 配对的显示/碰撞网格 | 加载已导入的网格资产 |
| `rm_scene_map` | 原始实例布局、Landscape 和源碰撞 | 加载 map.json 指定的已保存 World |

显式导入弗兰德斯：

```powershell
powershell.exe -NoProfile -File .\tools\unreal\Import-WTScene.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -SourceFile .\assets\maps\wt_vlaanderen\scene-source.json -EvidenceDirectory .\artifacts\maps\flanders-import-new
```

导入成功后，专用快捷入口使用准备好的 AIM 车辆、真实 `ContactPhysics` 和本轮 D3D11 设置：

```powershell
.\Play-WarThunder.cmd
```

通用入口也接受地图文件和出生点：

```powershell
.\Play-ManualVehicle.cmd .\assets\maps\wt_vlaanderen\map.json default
```

专用入口固定 `-GraphicsRhi D3D11 -SingleShaderWorker`，引擎接收 `-d3d11 -NoShaderWorker`；通用 Play 入口沿用项目默认 RHI。Windows 的 `Run-ImportLab`、`Start-PreparedVehicle` 和 `Test-ManualControls` 均可显式选择 `Default/D3D11/D3D12`。原图启动要求先完成显式导入；缺少保存关卡时会提示运行 `Import-WTScene.ps1`。

`map.json` 中的 `level_asset` 必须精确等于 `/Game/RMSim/Maps/<id>/World`。运行时核对实际 World package，以及 WorldSettings 的 `RMSimMapId=<id>` 和 `RMSimSourceSha256=<sha256>`。Landscape 与原碰撞 HISM 注册并建立物理状态后，车辆才进行地面支撑检查和接触物理初始化。ROS 地图身份保留 `rm_scene_map`。本轮 `regions` 为空。

弗兰德斯 `default` 出生点为 ROS `[1028, -1020, -1.3805752992630005]` 米、偏航 `0`。此类场景的 `bounds_m` 包含原外部背景，地图身份、尺度和出生高度由包内来源信息约束。

截至 2026-11-18，`import-06` 已保存完整 World，退出 0、175.87 s；`native-scene-03` 的 21 项检查通过，确认 1365 个唯一网格、726 / 642 个视觉/碰撞 HISM 组、35922 / 20033 个 placements、source opacity/proof、两通道全精度 UV、RVT 原生配置、四处原高度射线和 kiosk 原碰撞命中。精简证据为 `artifacts/maps/wt-scene-20261118/native-scene-03/summary.json`。World SHA 为 `720931dad262197929863d553c2388c3de18131277498749ef31faa201f12253`。

VERSION 4 保留 983 个源材质身份，34 个 HeightDecal 使用原始 position.w；地表贴花通过 RVT 投影到 Landscape 和外部 landmesh，原几何位置保持。25 个普通树材质依据真实 T0/T1 内容与哈希使用独立 coverage。`native-materials-06` 已在 D3D11 / NoShaderWorker 下完成 88 个保存资源的编译和参数门禁，退出 0、270.73 s。

同版本 `flanders-contact-03-rvt` 已通过五项真实接触/往返/共用复位检查及八张 PNG，退出 0、67.55 s。前行 340.284 cm，返程剩余 9.477 cm，共用复位计数为 1、回出生误差 0.0133 cm、最终速度 0；ready gate 完成后才开始验证。已查看的 rest 图中，旧浮动地表遮挡消失，左侧树冠恢复 cutout 空隙。最终 reset 图与 GUI 观察结果由本轮交接记录补齐。

原始叶片 half4/五通道 UV、贴花 short4n.w/两通道 UV、geometry proof 重绑定、RVT/材质审查以及完整重跑命令见[弗兰德斯指南](war-thunder-flanders.md)和[材质适配说明](../research/wt-material-adapters.md)。当前保存场景接入证据来自 Windows，Ubuntu 原生运行另行验收。

## 编辑或新增平地地图

复制一个地图目录，修改其中的 map.json；描述使用米、弧度、ROS右手坐标X前Y左Z上。运行边界会转换UE坐标，不要在数据中提前翻转Y。

- floor：地面尺寸、高度、厚度和颜色。
- boundary：场地内侧围墙厚度及高度。
- obstacles：落地的box；支持绕Z旋转，不支持悬空障碍、楼层/斜坡。
- spawns：地面位置与朝向。离线检查点位置，运行时还会检查完整车辆是否碰撞。
- regions：矩形spawn/supply/capture区域，带red/blue/neutral阵营。它们不阻挡车体。
- inspection_obstacle_id：可选测试地标；使用固定碰撞回归时才需要。

验证方式：

    $env:PYTHONPATH = Join-Path (Get-Location) 'src'
    python -m rm_map_tools validate assets/maps/warehouse_lanes

Ubuntu可使用 `PYTHONPATH=src python3 -m rm_map_tools ...`；同源Linux运行脚本增加了 `--map FILE --spawn ID`，但还没有原生Ubuntu验收。

## 导出平地导航地图

    python -m rm_map_tools export assets/maps/warehouse_lanes --output artifacts/maps/my-new-export --resolution 0.05

输出目录必须不存在，防止覆盖旧证据。生成 map.pgm、map.yaml 与来源哈希metadata.json。图片最高行对应最高地图Y；黑色为墙/障碍，254为可通行格。旋转box按真实投影生成，不用它的轴对齐外框替代。

这是平地box地图的静态占用图，没有机器人膨胀、安全距离、定位精度或Point-LIO闭环保证。`rm_map_tools` 的占用导出面向 `rm_flat_map`；`rm_mesh_map` 和 `rm_scene_map` 的坡地、建筑及高度场导航数据需按实际几何与碰撞单独准备、校验。`Play-Navigation.cmd` 继续使用已配置的 RMUC 场景。

## 平地地图历史验证

- 两个平地地图均在同一个 UE5.8.4 运行时通过 9 项移动/碰撞/相机检查。
- 真实UE障碍包围盒、出生姿态、初始区域与导出栅格做了独立核对。
- 空障碍地图也能加载。
- “出生点中心空闲但完整车辆重叠”被引擎在开放输入前拒绝。

证据位于 artifacts/maps/，其中 warehouse-ready 为当前完整运行结果。`-MapOverview` 提供正交俯视图；`-MapReport <新JSON路径>` 保存实际运行几何，拒绝覆盖既有文件。