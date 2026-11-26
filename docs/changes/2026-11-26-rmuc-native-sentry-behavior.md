# RMUC 2027 原版 C++ 哨兵行为树接入

Windows 安装层为 `ros2_ws/install_behavior`。原版 `behavior` 锁定 `aa4d82b73ae3c404b3037f2630eb2950dc0bbea4`，裁判消息锁定同一实车工作区的 `cf28347bfc284930cc68b206c41f171b0fe32bfc`。Humble 原有的 Nav2 BT.CPP v3 和实车行为树 v4 独立保留。使用 `tools/environment/Build-Behavior.ps1` 走项目的 Pixi/ROS 2 Humble、Ninja 和 MSVC 工具链。 [原版行为仓库](https://github.com/unnc-aim/27UL_Sentry_behavior/tree/aa4d82b73ae3c404b3037f2630eb2950dc0bbea4)

自动比赛连接器启动原版 `behavior_server` 和 `behavior_client`。ROS 节点名为 `/rmsim_sentry_bt_server` 和 `/rmsim_sentry_bt_client`，避免与 Nav2 的 `/behavior_server` 重名。固定提交的原始入口为 `competition_phase1`。连接器使用安装的 XML 生成四份比赛会话副本，保留原版树结构、血量阈值、热量控制、转移和 C++ 插件。按 RMUC 2027 填充双方的中高地与补给区坐标。`/map` 继续发布地图，导航目标使用 `<robot>/map`，就绪检查使用 `<robot>/base_link`。生成树目录、参数、进程输出位于外部连接器会话目录。

原版自瞄切换发布至 `<robot>/behavior/auto_aim_switch` 并由已有视觉节点消费。视觉只发布其自己真正取得的许可到 UE 桥输入。C++ 大树决定允许自瞄的战术阶段和热量条件。底盘平移继续经 Nav2。原版零 Twist 转成当前仿真时钟下的停机请求，待机与驻守小陀螺请求经 ROS 转接。运行状态下接入现有 UE 的 5 rad/s 小陀螺设定。C++ 树的等待阶段为 0，原实车 XML 仍保留各阶段的转速。

裁判桥在既有 `/rmsim/referee/state` JSON 和 `pb_rm_interfaces` 之外发布原版行为服务器用的 13 类 DJI 话题。伤害消息按裁判事件发送。裁判缺少队友真实位置时，`ground_robot_position` 不填入虚构的坐标。UE 比赛、莱茵 HUD 与场地源码不参与本轮迁移。

原生构建 6 个包成功，隔离 ROS 验证注册 24 个原版插件 DLL，保留 `map` 默认帧并通过仿真帧覆盖试验。实车遗留接口只生成原版 `Armor`、`Armors`、`Target`。入口、导航、通信和协议回归共 67 项及 5 个子项通过。 [编译与插件加载](../../artifacts/integration/sentry-behavior-native-20261126/checkpoint-summary.json) · [裁判接口](../../artifacts/referee/native-dji-20261126/referee-summary.json)

## Windows UE 5.8 实机验收

红蓝外接哨兵席位分别启动 RMUC 2027 和正式连接器，Point-LIO、AMCL、Nav2、DDS 裁判桥及原版 C++ 客户端与服务器均在线。测试使用现有裁判调试输入保持赛前阶段、释放倒计时、触发 20 点一档的扣血。阶段和血量来自 UE 裁判，位移取 UE 真值评估。执行窗口关闭 RViz。

| 检查 | 观测 |
| --- | --- |
| 赛前 | 裁判进度为 3，`competition_wait_game` 运行，C++ 服务器发布 0.5 rad/s 的云台扫视，Nav2 尚未收到比赛目标 |
| 赛前云台反馈 | 红方的 147 个关节样本覆盖 4.865 s 仿真时间，实测扫过 133.94°，角速度中位数为 0.494 rad/s |
| 开赛 | 裁判进度切到 4，原版 `SendNav2ThroughPoses` 发送各自高地目标，蓝红车辆在触发扣血前分别实移 0.151 m、0.152 m |
| 撤退 | 裁判血量为 140 时，双方的 C++ `IsHpLow threshold=150` 成立，前进目标取消，再由 C++ 树提交独立撤退目标 |
| 恢复 | 蓝方撤退 6.62 m 至 (-12.145, -6.147) m，补给至 222 HP。红方撤退 6.51 m 至 (12.225, 5.855) m，补给至 189 HP |
| 清理 | 两场各核验 27 个会话进程，测试结束后均已退出 |

首轮实机日志发现 Humble Nav2 的多点执行树在 `RemovePassedGoals` 内硬编码坐标系。外接比赛模式现在只为会话副本绑定 Nav2 当前的地图帧和导航基座帧。原始 XML 和内置导航入口继续使用原配置。最终复测同时确认 Nav2 和哨兵行为服务器各有唯一的 ROS 节点名。

旧 Python 决策、航点逻辑和对应单测已从 ROS 源目录迁出，原文件及哈希写入本地 `python-archive`。保留 `vision_autoaim`、`--no-behavior` 和 `--no-vision`。本轮实机覆盖开赛与低血闭环，未统计视觉命中率。

[蓝方实机摘要](../../artifacts/integration/sentry-behavior-native-20261126/live-match-blue-final/summary.json) · [红方实机摘要与关节反馈](../../artifacts/integration/sentry-behavior-native-20261126/live-match-red/summary.json) · [C++ 树日志](../../artifacts/integration/sentry-behavior-native-20261126/live-match-blue-final/behavior_server.log) · [Nav2 日志](../../artifacts/integration/sentry-behavior-native-20261126/live-match-blue-final/navigation-01-bt_navigator.log) · [首轮失败证据](../../artifacts/integration/sentry-behavior-native-20261126/live-match-blue-attempt-01/summary.json)
