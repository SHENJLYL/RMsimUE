# RMUC 默认运行模式

Windows 中运行 `Play-Navigation.cmd`，进入 RMUC 2027 场地。启动器使用 UE、MuJoCo、ROS 2 和 RViz，按地图包选择导航配置，默认启用 5 rad/s 小陀螺。G 开关，Z 和 X 调整转速。启动器自动录制本地导航会话。

## 跨场计时

基准使用物理起点 (7.86, −6.78) 和目标 (−8.32, 6.16)。从 RViz 首帧显示地图和车辆坐标系到到达目标，墙钟 **37.82 秒**，物理仿真 **37.65 秒**。Nav2 动作发送到结束为 37.81 秒。普通启动使用地图默认出生点，计时按实际会话记录。

实测终点误差 0.231 米。逐姿态检查了 5,580 个带边距的车体轮廓，静态占用重叠为 0。三个平台目标的终点和净空也通过。跨场录包另有一次或多次画面帧间隔告警。

## 地图与动力学

默认平滑曲线期望净空为 0.84 米，局部和全局代价图膨胀半径均为 0.45 米。重新规划时检查整条路径。地形节点使用同帧扫描核对波浪路和坡面。底盘外壳接触响应采用 6 ms 时间常数和阻尼比 1。原始轮胎、悬挂和场地几何参与碰撞。

地图包内的 `navigation-release.json` 校验导航配置与两份地形参考文件。源代码、参数、重建脚本与紧凑验收记录随仓库分发。完整 rosbag、引擎构建产物和需另行取得的原始场馆模型保存在本地。Ubuntu 原生运行本次未验收。

地图准备步骤见 [地图包说明](../../assets/maps/rmuc2027/navigation-console-platform-support-v2/README.md)。逐项核验数据见 [默认模式实测记录](../verification/2026-11-23-rmuc-default.json)。

## 已准备的体育馆资产

场地网格按[地图导入指南](../guides/map-packages.md)准备。使用有权使用的本地 SchoolGymFidelity 包，在 UE Editor 中运行 `tools/unreal/import_school_fidelity.py`，传入 `-VenueAssetRoot=<bundle>` 和 `-VenueImportReport=<报告>`，随后运行 `tools/unreal/author_school_weather.py` 生成玻璃、天空和云影材质。砖墙使用 `tools/maps/prepare_school_entry_brick_wall.py` 及 `tools/unreal/import_school_entry_assets.py`。受限场馆模型和预生成 UE Content 需在本地准备。

## 版本清理

正式控制器移除了旧 MPPI 预测与方向仲裁、合并停车和命令租期预测实验，合并了重复的终点减速调用，净删 133 行。基准跨场、配套平台和唯一回滚已保留。9 个经单独确认的过时录包已清理，各自的日志和指标已保留。
