# RMUC 校园体育馆

本场景把原 RMUC2027 比赛场地放在完整的校园体育馆中央。体育馆、观众区和玻璃围栏通过地图扩展加载；原场地模型、比赛障碍、车辆尺度及悬挂参数保持原有来源。

## 空间和围栏

体育馆净尺寸为 **56 × 40 × 14 m**，地坪基准为 **Z = -0.21 m**。室内包含完整屋顶、钢屋架、暖木吸声吊片、高侧窗、混凝土墙面、木地板通道和成组灯具。四周看台有六排、864 个座位，配套楼梯通道、扶手及后方通道。场地与看台之间最小通行空间约 3.45 m。

比赛场地周边使用 **58 块固定玻璃板和 4 扇固定装饰门扇**，通过立柱、压条、分格接缝、把手和铰链表现拼装玻璃围墙及真实出入口的外观。按照当前使用要求，门扇始终关闭，场地边界连续阻挡车辆；没有开门按键或开门状态。

四面连续物理保护墙保留此前导航验收使用的几何：中心线按原地图边界内缩 0.25 m，厚 0.48 m，Z 范围为 -1.25～4.80 m。可见玻璃厚 12 mm、顶面为 Z=2.60 m，单块宽度上限 1.50 m，内面贴合保护墙的场内侧，门扇区域使用同一连续阻挡。装饰结构采用实例化网格，玻璃与视觉门缝不会产生可供车辆穿出的碰撞开口。玻璃外观透明度与激光雷达材质透射模型分别管理；当前雷达仍读取物理边界。

## 素材与配置

模型和材质来源均保留原始网址、许可证、下载哈希及派生说明：

| 用途 | 来源 | 处理 |
|---|---|---|
| 看台座椅 | [Innerscene 六座体育场座椅模块](https://www.innerscene.com/tools/library/3d-parts/stadium-seats-on-a-concrete-riser-row-of-6-000f8e6d)，CC0 | 去除源混凝土台阶，保留座椅与支架；和本场景连续踏面配合 |
| 木地板 | [ambientCG WoodFloor051](https://ambientcg.com/view?id=WoodFloor051)，CC0 | 1K PBR 纹理，按实际贴图尺寸铺设 |
| 混凝土 | [ambientCG Concrete046](https://ambientcg.com/view?id=Concrete046)，CC0 | 1K PBR 纹理 |
| 吸声木饰面 | [ambientCG Wood049](https://ambientcg.com/view?id=Wood049)，CC0 | 1K PBR 纹理 |
| 金属 | [ambientCG Metal050A](https://ambientcg.com/view?id=Metal050A)，CC0 | 1K PBR 纹理 |
| 环境光与窗外环境 | [Poly Haven Quadrangle Cloudy](https://polyhaven.com/a/quadrangle_cloudy)，CC0 | 1K HDR 环境图 |

体育馆结构布局为本项目生成的原创几何。配置入口为 `assets/maps/rmuc2027/arena-shell.json`；布局为 `assets/maps/rmuc2027/arena/campus-gym/layout.json`；素材来源记录和许可证位于同一素材目录。导入后的 UE 资产位于 `/Game/RMSim/Maps/rmuc2027/Arena/Campus`。

仓库保存实现源码、配置、选定的 CC0 源素材、来源说明和精简验收记录。下载缓存、生成的 UE Content、构建产物、完整日志及截图按忽略规则保留本地；可以用准备和导入脚本重建。源模型目录保持只读。

## 灯光与画面

当前运行时采用手动曝光 EV 8，12 盏面积灯，每盏设为 65,000 lm、5,000 K，其中两盏投射阴影。灯具位置与可见灯壳相配，钢屋架和吸声吊片形成室内层次。HDR 立方体贴图提供近似环境光，窗外使用有界的低分辨率校园庭院背景；实例化建筑、两盏阴影灯和 1K 素材控制场外美术的运行开销。

这些设置记录在当前场景实现和来源清单中。运行画面、帧率以及 ROS 负载分别通过原生截图和性能报告检查；照度与相机响应的物理标定待专项实测。

## 编译、准备和启动

以下命令在已安装 UE 5.8.4 的 Windows 工作区执行。**先编译工程，生成原生模块，再启动资源导入。** 新 checkout 的 UE 命令行编辑器需要先加载工程模块。

统一准备入口按顺序核验并准备 CC0 素材、生成布局、执行原生 UE 导入；任一步退出或报告失败即返回失败，每步输出及报告保留。输出目录必须使用新名字。

```powershell
Set-Location D:\simulator

& 'E:\simulator-tools\UE_5.8\Engine\Build\BatchFiles\Build.bat' `
  RMSimEditor Win64 Development `
  'D:\simulator\unreal\RMSim\RMSim.uproject' `
  -WaitMutex -NoHotReloadFromIDE

if ($LASTEXITCODE -ne 0) { throw 'RMSim 编译失败，请检查构建日志。' }

$gymRun = 'D:\simulator\artifacts\maps\campus-gym-rebuild-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
python tools/unreal/setup_campus_gym.py `
  --engine-root E:\simulator-tools\UE_5.8 `
  --report-dir $gymRun

if ($LASTEXITCODE -ne 0) { throw "体育馆导入失败，请检查 $gymRun\verification.json。" }
```

准备脚本自动复用通过固定哈希核验的下载缓存，缺失文件按来源下载；当前没有 `--offline` 开关。统一入口可用 `--project` 指定隔离预览工程，或用 `--repo-root` 指定源码目录。它只准备资源和原生导入，编译及启动由上述独立命令执行，地图的 `arena-shell.json` 选择不会被自动修改。

同一入口在 Linux 选择 `<engine-root>/Engine/Binaries/Linux/UnrealEditor-Cmd`。本功能原生验收记录当前来自 Windows；Linux 路径支持等待 Ubuntu 实机运行记录。

主工程集成、编译和导入完成后，现有手动驾驶及导航入口加载 RMUC2027 时会读取同一个体育馆扩展。也可以明确指定地图启动：

```powershell
& 'E:\simulator-tools\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe' `
  'D:\simulator\unreal\RMSim\RMSim.uproject' `
  '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode' `
  -game -ContactPhysics `
  '-MapFile=D:\simulator\assets\maps\rmuc2027\map.json' `
  '-MeshRigFile=D:\simulator\assets\vehicles\aim_sentry\ue_mesh_rig.json'
```

## 当前验收记录

准备与重复导入已验证：`import-07`、`setup-repeat-01` 均通过，包含 14 张纹理、15 个材质；座椅为 1,380 个三角形，包围盒约 290.8 × 40 × 75 cm。

主分支体育馆切片已通过 `publish-build-04` 编译。`publish-native-03` 的四个视角各通过 14 项检查，包含每次 96 个边界球形扫掠及严格 PNG 校验；当前工作区的 `workspace-native-02` 主视图与门视图也各通过 14 项检查和 96 次扫掠。独立助手已经打开实际 PNG 复核室内整体、玻璃接缝和固定门、屋架、木饰面及 HDR 庭院背景，画面检查通过。截图保存已改用 `GetRenderTargetImage` 与 `CompressImage`，消除旧接口写入多余缓冲区字节的问题。

恢复原围墙后的 `boundary-restored-01` 六项真实碰撞检查全部通过。车辆以 2 m/s 目标接近南侧墙，接近阶段峰值为 1.58424 m/s，行驶 65.955 cm 后实际命中。持续以 2 m/s 目标顶压 1.506 s，继续前移 0.883 cm，阶段末速 0.148 cm/s；底盘与车轮包络距原始场地矩形边缘的最小余量为 48.164 cm。随后倒车脱离 60.472 cm，最后速度为零，全程没有复位或位姿覆盖。

当前导航工作区最终 `realtime-04` 已通过 **14/14 动作检查及 32/32 时钟检查**，完整动作包含在同一个 90 秒窗口内。单点真实误差约 **7.01 cm**、两航点终点约 **6.49 cm**，取消后停车距离约 9.02 cm，后续漂移为零。总体实时比为 **1.000036×**，场景绘制约 **59.9974 Hz**；固定 10 秒实时比范围为 0.997591～1.001221，滑动 10 秒范围为 0.990140～1.010139，全部保持原门槛。两路 IMU 均约 200 Hz、LiDAR 为 200k 射线/秒和约 9.996 Hz 点云，记录中没有传感器丢失或重置。云台与目标的中位角差约 0.00642 rad，1,150 个样本。

本轮采用 640×360 视口、60 Hz 场景目标、0.005 s 固定物理步长和 `cameraHz=0`，生产 LiDAR 及 IMU 预算保持。退出码为 0，输入保持不变，清理错误为空。该导航结果属于当前工作区模块；主分支体育馆切片有独立的上述原生场景验证。

历史记录继续保留：`realtime-01` 的试验墙内面外移 0.31 m 改变雷达边界，动作误差 0.366829 m 超门槛，随后恢复原验收几何；`realtime-02` 在另一授权会话运行手动视角/gyro 测试时以退出码 3 主动互斥，未启动 UE/ROS；`realtime-03` 动作 14 项通过、时钟 30/32，固定及滑动 10 秒窗口失败。`realtime-04` 使用与 03 相同的代码、参数、素材和阈值完成全部检查，两轮结果并列归档。

完整证据入口为 `.codestable/features/2026-11-19-campus-gym/acceptance.md`，具体目录位于 `artifacts/maps/campus-gym-20261119`。本功能实现及记录随本次提交发布，提交身份与远端核对通过 Git 历史和最终 `publication-verification.json` 确认。原生 Ubuntu、1080p 性能、光度标定及其他撞击速度与角度属于后续覆盖范围。
