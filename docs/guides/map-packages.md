# 地图包与切换

默认地图`rmuc2027`使用DJI RMUC2027 V1.0.0 CAD来源静态场地，已在Windows运行。第二种地形`wt_cdk_crop`是War Thunder官方CDK高度场的512×512m裁剪。另保留自建平地回归地图`import_lab`（20×20m）和`warehouse_lanes`（24×16m）。

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

UE资源路径须在/Game/RMSim/Maps/内，碰撞网格使用complex-as-simple triangle collision。当前region数组为空，没有比赛区域绑定。车辆支持地形运动学跟随：2cm间隙、35°坡度和5cm台阶限制，不是实际轮地动力学。

原生检查：

    pwsh -File tools/unreal/Test-ManualControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -Terrain -MapFile assets/maps/rmuc2027/map.json -MapOverview -EvidenceDirectory artifacts/maps/new-rmuc-check
    python tools/maps/verify_mesh_map.py assets/maps/rmuc2027 artifacts/maps/new-rmuc-check

7项检查使用真实网格碰撞与共用操控函数，输出4PNG、位姿/轮位支撑记录。证据目录须为新目录；可换WT地图并加-SpawnId hillside。

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

这是平地box地图的静态占用图，没有机器人膨胀、安全距离、定位精度或Point-LIO闭环保证。rm_map_tools当前不接受rm_mesh_map；RMUC/WT坡地不能套用平地占用导出并宣称导航一致。

## 实际验证

- 两地图均在同一个UE5.8.4运行时通过9项移动/碰撞/相机检查。
- 真实UE障碍包围盒、出生姿态、初始区域与导出栅格做了独立核对。
- 空障碍地图也能加载。
- “出生点中心空闲但完整车辆重叠”被引擎在开放输入前拒绝。

证据位于 artifacts/maps/，其中 warehouse-ready 为当前完整运行结果。`-MapOverview` 提供正交俯视图；`-MapReport <新JSON路径>` 保存实际运行几何，拒绝覆盖既有文件。
