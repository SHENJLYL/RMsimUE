# RM Simulator

RMUC 默认入口使用 32.4 秒稳态 MPC 配置。Windows 使用 `Play-Navigation.cmd` 或 `Play-Simulator.cmd` 中的内置导航模式。[当前版本与验证](docs/changes/2026-11-24-simulator-snapshot.md)

UE5 + 原生 ROS 2 RoboMaster 仿真工程。当前已有 Windows AIM 哨兵接触物理驾驶、射击训练和原控制/传感器链路；长期目标保留 Windows/Ubuntu 原生同源算法、通用车辆包、可换地图、高画质和联机权威比赛。

[当前进度](docs/progress/current.md) · [长期目标](.codestable/goal.md) · [最新变更](docs/changes/2026-11-20-mujoco-integration.md) · [仓库与本地资源](docs/guides/repository.md)

## 统一工程与参数

当前活动工程统一在 `D:\simulator`。RMUC、弗兰德斯、训练和导航入口使用同一个 `unreal/RMSim/RMSim.uproject`；车辆动力学与手动驾驶参数共用 `assets/vehicles/aim_sentry/drive-profile.json`，云台和观察设置共用 `unreal/RMSim/Config/DefaultGame.ini`。导航沿用所选 `sentry_ws` YAML 的控制、规划、避障和速度平滑算法，运动能力限值从同一车辆 `drive-profile.json` 生成；手动与导航在车辆端共用连续整形器、执行器和传感器包。

AIM 当前车辆配置的前进、倒车、横移速度上限各为 **4.6 m/s**，每个悬挂模块为 **2000 N/m、280 N·s/m**，预载 **60 N**。切换地图直接选择入口，地图包保存地形、材质、布局和出生点。原地图工作树保留在本目录的 `artifacts/archive/wt-vlaanderen-before-unified-20261118`。工程整合、枕木与路肩碰撞、地表细节和植被修正的逐项结果见[本轮变更](docs/changes/2026-11-18-unified-flanders.md)。

## 弗兰德斯原图驾驶

双击 **Play-WarThunder.cmd** 打开完整导入的 War Thunder 弗兰德斯原图，驾驶原尺度 AIM 哨兵。右下角小地图显示真实车辆位置、朝向与出生点，**M** 展开或收起全图。Windows UE 5.8.4 / D3D11 使用同一套车辆控制和悬挂；原生关卡、材质、植被及两地图驾驶检查均按各次实际模块与资源哈希记录。

场景保留 1,365 个源网格、35,922 个视觉 placements、20,033 个原碰撞 placements 和原高度场；另为 18,454 个枕木与路肩实例生成按范围加载的精确碰撞。地表使用原地类细节、12 槽源微细节和 RVT，草地使用 10 个模型、16 层分布，并排除道路、铁路、水面和建筑区域。启动等待实际资源和渲染完成，原有悬挂与驾驶参数保持。地图的完整导航路线、稳定 1× 和原 Dagor 特效继续单独扩展。[操作与重建](docs/guides/war-thunder-flanders.md) · [来源与覆盖](assets/maps/wt_vlaanderen/SOURCE.md)

## 可视化 SLAM / 航点导航

双击 **Play-Navigation.cmd** 打开UE场景与RViz。定位就绪后，用 **Nav2 Goal** 点击拖动选目标；**Waypoint / Nav Through Poses Mode** 累积多个地点，再点 **Start Waypoint Following**；**Cancel** 取消任务。关闭RViz或运行 **Stop-Navigation.cmd** 结束本次会话。[操作指南](docs/guides/navigation-console.md)

已接入原sentry导航偏航目标整形，以及按源模型yaw_link实际位置/45°倾角安装的动态Mid-360与共装IMU。2026-11-17导航版本验收84项原生、13项真实GUI操作通过；离线120通过、2权限跳过。完整波浪路线实际单点误差0.1888m、航点队列终点0.1795m，取消后5.33cm停车，原25cm门限保持。[机器验收](artifacts/navigation/sentry-console-20261117/release-summary.json)

## 本机直接运行

双击 **Play-ManualVehicle.cmd** 驾驶，双击 **Play-Training.cmd** 进行静态靶射击训练。标准手动与 **Play-Navigation.cmd** 导航入口均直接采用 MuJoCo 车辆动力学和 UE5 画面，原独立 MuJoCo 入口已移除。默认进入 RMUC2027 来源场地，使用本机 `E:\simulator-tools\UE_5.8` 和已导入资源；其他引擎目录可设置 `UE_ROOT`。车辆驾驶参数读取共同车辆包；导航算法读取所选 `sentry_ws` 参数，速度和加减速等车辆能力限值按共同 profile 覆盖到生成配置。[融合与重建说明](docs/guides/mujoco-integration.md)

| 输入 | 行为 |
|---|---|
| W/S/A/D | 以实际云台水平方向为基准前后/横向平移 |
| Q/E；G；Z/X | 普通模式旋转；切换小陀螺；调整自转速度 |
| Ctrl；Space；R | 慢行；加强制动；复位 |
| 鼠标、C | 瞄准；按住 C 绕车观察，驾驶方向继续跟随实际云台 |
| Shift、右键、滚轮 | 瞄准镜；临时放大；镜头距离/倍率 |
| 左键 | 训练模式开火 |
| M | 弗兰德斯全图展开或收回；小地图显示车辆位置与朝向 |

松键主动制动。启动入口会阻止重复游戏实例与构建期间启动。[操作说明](docs/guides/manual-controls.md) · [训练规则](docs/guides/training-combat.md)

## 2026-11-17 实证：悬挂与接触驾驶

AIM 为原尺度15视觉部件/13刚体的四舵轮接触样例。11 月 17 日对照采用每模块 **2000 N/m、140 N·s/m**；上一轮1800/130，质量、预载、行程、轮胎凸包、摩擦与控制增益保持不变。

同模块原生对照：100N附加载荷压缩15.620→14.447mm，卸载回弹峰值1.085→0.774mm，冲量后反弹0.213→0.029mm。新配置两次专项结果一致。2026-11-17悬挂版本的模块/参数完成92项原生回归与48PNG，覆盖操控、小陀螺、红蓝波浪往返、坡面制动、射击/复位、ROS速度桥与原LK电流环；另有两次悬挂专项各4项/6PNG。

[悬挂参数、实测与回退](docs/guides/suspension.md) · [机器验收汇总](artifacts/control/suspension-round2-20261117/release-summary.json)

## 算法、地图与车辆扩展

Windows 原生 ROS 已接入速度指令、原LK电流/反馈、高层云台/发射、相机、IMU、LiDAR与原Point-LIO。11 月 17 日的 2000/140 悬挂配置下，原LK/Point-LIO联合11项通过。该配置完成两轮RMUC断线重连与自主往返各14项、平地实际R复位恢复17项，合计45项导航检查；两轮实际去程误差0.1871/0.1830m、返程0.0902/0.1026m，沿用0.25m门限。Windows导航进程使用经校验的127.0.0.1 UDP配置，解决已复现的重启消息中断；原失败证据保留。[导航指南](docs/guides/native-navigation.md) · [恢复验收](artifacts/navigation/reconnect-tf-20261117/release-summary.json)

标准手动与导航入口使用完整 RMUC2027 MuJoCo 场地包，切换其他地图时须提供匹配的物理模型；启动校验会拒绝身份不匹配的地图。弗兰德斯原图由专用 `Play-WarThunder.cmd` 打开已验证的保存关卡，其动态破坏与碎屑尚需建立对应 MuJoCo 实体。[地图说明](docs/guides/map-packages.md)

车辆包v1提供校验、受限URDF和规范化导出；AIM采用已运行验证的mesh-rig资产路径。任意新车的完整惯量/关节/传感器即插即用仍在后续范围。[车辆资源](docs/guides/aim-sentry-assets.md) · [构建与导入](docs/guides/unreal-import-lab.md)

原生Ubuntu、实车动力学/传感器标定、完整视觉/Hub/行为树、正式裁判、多机器人权威联机与原地图更多路线、程序内容及特效画质验收继续保留。[Ubuntu准备](docs/guides/ubuntu-offline.md) · [长期规划](.codestable/roadmap/rm-simulator/rm-simulator-roadmap.md)

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
