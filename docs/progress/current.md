# 当前进度

更新：2026-11-17，Asia/Shanghai。长期目标 active，完整要求见 `.codestable/goal.md`。保留 Windows/Ubuntu 原生同源ROS 2、通用车辆包、可换地图、高画质、正式裁判和权威联机目标。

## 最新完成：导航断线与实际R复位恢复

当前2000/140悬挂下，正式安装的原Point-LIO/AMCL/Nav2完成 **两轮RMUC断线重连与自主往返，各14项通过**，并完成 **平地断线、目标取消、实际OS R键复位及导航重建17项**，合计45项原生导航检查通过。

| 运行 | 实际去程误差 | 实际返程误差 | 断线停车距离 |
|---|---:|---:|---:|
| release-01，RMUC | 0.187068m | 0.090168m | 0.080842m |
| release-02，RMUC | 0.183015m | 0.102587m | 0.082859m |

实际R测试在import_lab进行：取消目标后6.226cm停车，R后原Point-LIO与导航栈重新建立，定位误差0.063997m；观察窗口内旧目标重放位移0m。输入仅发送给校验过PID、项目命令行及前台窗口的自有RMSim进程，截图留本地。RMUC全场、RMUC实际R与其他出生点仍需扩展覆盖。

原重连卡点已通过逐节点消息计数定位到Windows默认本机通信路径的重启后交付中断。原始失败、单项修复未解决整栈的失败、UDP对照和重复验收全部保留。Windows导航监督器及其子进程现在使用经严格校验的127.0.0.1 UDP配置；其他ROS入口保持原模式，系统防火墙和全局环境未改。另修复两处停顿仿真时钟下的阻塞TF查询，缺少变换时等待后续真实观测。

精简机器汇总：`artifacts/navigation/reconnect-tf-20261117/release-summary.json`。当前导航DLL、安装Python包、UE模块、profile和rig均与启动证据核对。独立原生接口夹具20项通过；最终离线92项中90通过、2项Windows符号链接权限跳过。真实终点0.25m、断线制动0.2m、旧目标不重放0.02m门限保持。问题记录：`.codestable/issues/2026-11-17-reconnect-tf-chain/reconnect-tf-chain.md`。

## 已验收悬挂与控制基线

AIM每模块 **2000 N/m刚度、140 N·s/m阻尼**，上一轮1800/130。质量、50N预载、65mm行程、源轮胎凸包、摩擦和原控制增益保持。100N载荷压缩15.6195→14.4467mm，卸载回弹峰值1.08465→0.77350mm，冲击反弹0.21319→0.02890mm。新配置两次专项一致。

对应UE模块/profile已完成92项原生驾驶、波浪、坡面、训练、ROS与原LK回归及48PNG。本轮导航修复未改这套UE模块与车辆参数。悬挂证据：`artifacts/control/suspension-round2-20261117/release-summary.json`。2000/140下原LK、IMU/LiDAR、原Point-LIO RMUC联合11项也有独立证据。

## 仓库与本机运行

仓库为用户指定的公开 `SHENJLYL/RMsimUE`，origin已配置；源码、配置、上游补丁、依赖锁与精简证据进入Git。大型模型/贴图、导入Content、构建安装产物、截图及稠密轨迹保留本地。基线、变更与证据按类型管理。

双击 `Play-ManualVehicle.cmd` 或 `Play-Training.cmd`，默认RMUC2027和原尺度AIM，15视觉部件/13刚体、四悬挂/舵向/轮轴。WASD镜头相对平移，Q/E旋转，G自转，Z/X调速，Ctrl慢行，Space制动，R复位。训练包含真实弹丸扫掠、靶HP、弹热/弹量和回合复位。

原生ROS已接入速度、原LK电流反馈、高层瞄准/发射、相机、IMU、LiDAR、原Point-LIO与限定路线Nav2。真实评测位姿保持独立，定位算法不消费真值。

## 接续优先级与长期范围

下一步先扩展RMUC不同出生点、坡面/波浪与实际R恢复覆盖，并验收公开启动包装；随后推进多机器人话题/控制权隔离、完整视觉/Hub/行为树与裁判链。

通用新车辆惯量/关节/传感器导入、原生Ubuntu编译运行、实车动力学与传感器标定、正式赛季规则和权威联网继续保留。RMUC2027与WT CDK地形可运行；弗兰德斯原图资源准备完成，完整场景/画质验收待推进。

环境：`D:/simulator`；UE5.8.4在`E:/simulator-tools/UE_5.8`；Windows Humble为RoboStack Python3.14。外部原模型/控制器目录只读。旧详细进度完整保存在 `docs/progress/archive/2026-11-17-before-navigation-reconnect.md`。
