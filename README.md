# RM Simulator

UE5 + 原生 ROS 2 RoboMaster 仿真工程。当前已有 Windows AIM 哨兵接触物理驾驶、射击训练和原控制/传感器链路；长期目标保留 Windows/Ubuntu 原生同源算法、通用车辆包、可换地图、高画质和联机权威比赛。

[当前进度](docs/progress/current.md) · [长期目标](.codestable/goal.md) · [最新变更](docs/changes/2026-11-17.md) · [仓库与本地资源](docs/guides/repository.md)

## 本机直接运行

双击 **Play-ManualVehicle.cmd** 驾驶，双击 **Play-Training.cmd** 进行静态靶射击训练。默认进入 RMUC2027 来源场地，使用本机 `E:\simulator-tools\UE_5.8` 和已导入资源；其他引擎目录可设置 `UE_ROOT`。

| 输入 | 行为 |
|---|---|
| W/S/A/D | 镜头相对前后/横向平移 |
| Q/E；G；Z/X | 普通模式旋转；切换小陀螺；调整自转速度 |
| Ctrl；Space；R | 慢行；加强制动；复位 |
| 鼠标、C | 瞄准；按住C自由观察 |
| Shift、右键、滚轮 | 瞄准镜；临时放大；镜头距离/倍率 |
| 左键 | 训练模式开火 |

松键主动制动。启动入口会阻止重复游戏实例与构建期间启动。[操作说明](docs/guides/manual-controls.md) · [训练规则](docs/guides/training-combat.md)

## 最新实证：悬挂与接触驾驶

AIM 为原尺度15视觉部件/13刚体的四舵轮接触样例。当前悬挂每模块 **2000 N/m、140 N·s/m**；上一轮1800/130，质量、预载、行程、轮胎凸包、摩擦与控制增益保持不变。

同模块原生对照：100N附加载荷压缩15.620→14.447mm，卸载回弹峰值1.085→0.774mm，冲量后反弹0.213→0.029mm。新配置两次专项结果一致。当前模块/参数完成92项原生回归与48PNG，覆盖操控、小陀螺、红蓝波浪往返、坡面制动、射击/复位、ROS速度桥与原LK电流环；另有两次悬挂专项各4项/6PNG。

[悬挂参数、实测与回退](docs/guides/suspension.md) · [机器验收汇总](artifacts/control/suspension-round2-20261117/release-summary.json)

## 算法、地图与车辆扩展

Windows 原生 ROS 已接入速度指令、原LK电流/反馈、高层云台/发射、相机、IMU、LiDAR与原Point-LIO。当前2000/140悬挂下，原LK/Point-LIO联合11项通过。最新已完成两轮RMUC断线重连与自主往返各14项、平地实际R复位恢复17项，合计45项导航检查；两轮实际去程误差0.1871/0.1830m、返程0.0902/0.1026m，沿用0.25m门限。Windows导航进程使用经校验的127.0.0.1 UDP配置，解决已复现的重启消息中断；原失败证据保留。[导航指南](docs/guides/native-navigation.md) · [恢复验收](artifacts/navigation/reconnect-tf-20261117/release-summary.json)

地图支持包入口切换。WT CDK示例：`Play-ManualVehicle.cmd assets\maps\wt_cdk_crop\map.json hillside`。弗兰德斯原图资源已准备，完整UE场景运行待验收。[地图说明](docs/guides/map-packages.md)

车辆包v1提供校验、受限URDF和规范化导出；AIM采用已运行验证的mesh-rig资产路径。任意新车的完整惯量/关节/传感器即插即用仍在后续范围。[车辆资源](docs/guides/aim-sentry-assets.md) · [构建与导入](docs/guides/unreal-import-lab.md)

原生Ubuntu、实车动力学/传感器标定、完整视觉/Hub/行为树、正式裁判、多机器人权威联机与完整原地图/画质验收继续保留。[Ubuntu准备](docs/guides/ubuntu-offline.md) · [长期规划](.codestable/roadmap/rm-simulator/rm-simulator-roadmap.md)

## 仓库内容

| 路径 | 内容 |
|---|---|
| `.codestable/`、`docs/` | 目标、架构、需求、问题、进度、变更与指南 |
| `unreal/RMSim/Source`、`Config` | UE运行时源码和配置 |
| `ros2_ws/src`、`src/` | 原生ROS工作包及车辆/地图工具 |
| `assets/`、`schemas/` | 小型车辆/地图描述、控制/传感器参数与协议 |
| `tools/`、`tests/` | 导出、构建、验证、集成与离线测试 |
| `artifacts/`、`third_party/patches` | 精简真实验证/来源记录及上游适配补丁 |

大型模型/贴图、导入后的UE Content、编译/安装目录、缓存和完整运行轨迹保留本地，不进入Git。全新克隆需要按来源记录准备合法可用的资源与本机依赖，再构建/导入。源码与精简证据已发布至用户指定的公开仓库 [SHENJLYL/RMsimUE](https://github.com/SHENJLYL/RMsimUE)，main及回退标签均已核对远程提交。
