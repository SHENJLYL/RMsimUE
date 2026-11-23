# 全屏观察窗口、RGB 读回与 RMUC 渲染排查

## 已接入的改动

- Windows 导航控制台把所属 UE 的 PID 交给原生 RViz 插件。插件只在该 UE 窗口位于前台、属于不透明无边框窗口且完全覆盖 RViz 时，暂停 RViz 主 OGRE 目标的自动绘制。ROS executor、显示对象、地图及 TF 继续更新。RViz 露出时请求一次新绘制。已由 `Prepare-NavigationConsole.cmd` 构建，旧部署可继续使用原 RViz，重新运行准备入口即可加载插件。 [RViz 11.2.29 更新循环](https://github.com/ros2/rviz/blob/11.2.29/rviz_common/src/rviz_common/visualization_manager.cpp#L365-L425)
- ROS RGB 改用 `FRHIGPUTextureReadback`。游戏线程提交现有场景相机，渲染线程按提交顺序复制到 staging 目标，fence 完成后按行读回。最多复用三帧，旧连接和复位前的帧不再交付。RGB8 图像、内参和光学 TF 使用采集时的物理时间戳及相对位姿。当前状态包维持最新仿真时钟，旧版 Python 协议已支持较早的相机时戳。`missed_captures` 记录超时或缓冲已满造成的采集缺口。

## 渲染诊断

`tools/unreal/measure_gym_render.py` 保存实际渲染帧的 GPU pass 和同机位截图。`--orbit` 用现有追尾镜头重复按帧环绕，保持同一仿真和资产，保存两处截图。`fence_glass`、`sun_shadows`、`rect_all` 或 `rect_1` 至 `rect_12` 可隔离指定对象，仅用于离屏测试。默认启动继续使用原材质、光照和阴影配置。

- 64 块边界玻璃无重复摆放，独立碰撞原样。显示材质为两面薄玻璃，带 Fresnel、透射与反射。2560×1440 隐藏边界玻璃后，固定镜头约 12.96% 的 RGB 通道绝对差大于 8/255，重复基线约 0.13%。移动镜头两处采样分别为 11.56% 和 24.20%。AIM 车辆的亚克力按资产清单 opacity=1 导入为不透明，雷达窗口和蓝色 Mid-360 玻璃分别使用 0.12 和 0.88。模型大部件既有重合面清单只记录候选，未验证光学件可删性。
- 实际场馆 12 盏矩形灯位置各异，均不投影。按 25 m 衰减球作上界，场地中心地面在 8 盏灯的球内，场地采样点最多落在 12 个球内。720p 隔离第 6 盏矩形灯和整组矩形灯均改变画面。关闭太阳阴影后阴影 pass 约 2.54→0.04 ms，约 69.90% 的 RGB 通道变化超过 8/255。保留太阳和矩形灯。
- 2560×1440 固定观察的太阳级联缓存约 2.59–2.75→1.06 ms。移动观察原阴影阶段约 2.57–2.73 ms，禁用滚动复用的缓存增至 5.35 ms，允许滚动复用仍增至 4.05 ms。级联裁切试验的 720p 结果落在重复基线波动内，默认配置保持原样。

## 验证和复现

- `tools/unreal/Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8` 原生编译。`tools/environment/Prepare-NavigationConsole.cmd` 构建并准备 RViz，导航启动合同在具备 CIM 读权限的原生进程中通过 53 项。
- `tools/integration/measure_fullscreen_observers.py <新目录>` 对照全屏三窗口继续绘制、遮挡停绘、仅 UE 可见三种模式。实际完全遮挡的两个连续区间内 RViz 累计推进 61、67 次更新，绘制均增加 0，露出后相邻区间分别推进 148、134 次绘制。MuJoCo 在停绘模式收到 1,510 条连续序号，缺口为 0。
- 前台稳定区间分别测得三窗口绘制全部开启 52.53 Draw/s（20.37 s），遮挡停绘 44.79 / 45.09 Draw/s（各约 4 s），仅 UE 53.77 Draw/s（6.06 s）。后两组发生焦点变化，未形成等长同条件的升帧结论。100 FPS 尚未达成，实测只有 UE Draw 计数，显示器呈现未纳入。
- `tools/mujoco/tests/Test-MujocoSensors.ps1 -EngineRoot E:/simulator-tools/UE_5.8 -EvidenceDirectory <新目录>` 原生渲染、TCP、ROS 和 LiDAR 串并行结果。最终复跑通过 TCP 24 项和 ROS 14 项，76 张 RGB 到状态包的仿真时间差为 35–70 ms，中位 50 ms。GPU 提交在游戏线程的均值为 0.20–0.22 ms，日志中的 `missed_total` 为 0。前一轮同模块复测 TCP 24 项通过，ROS 探针在一路 IMU 中观测到 40 ms 样本缺口，当轮 ROS 门禁失败。相机渲染和 GPU 传输仍消耗帧预算。

原始证据归档于 `artifacts/integration/render-and-sensors-20261120`。首轮离屏工具实际退回 1280×720，且 GPU 日志解析器未取到正确表列，原始运行全数保留。后续修正解析并验证了 2560×1440 实际截图。Windows D3D12 已验证，Ubuntu 原生及更高 RGB 帧率待测。 [原生结果摘要](../verification/2026-11-20-render-and-sensors.json)
