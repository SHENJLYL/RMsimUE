# RMUC 2027 双哨兵与 ROS 2 裁判接入

## 比赛入口与接口

在 `Play-Match.cmd` 中为红、蓝各配置一台 AIM S7。一方选操作手并设为驾驶席，另一方选 `sentry_ws`。启动器根据编队绑定 ROS 底盘、云台、Mid‑360、IMU、相机和裁判话题，并按 ROS 车的出生点自动启动外部 Point‑LIO 与 Nav2。当前 Windows 外部连接器会拉起定位和导航。原版自瞄全流程尚未通过。

比赛内手动与 ROS 射击共用同一裁判判定。V1.7.0 的 `pb_rm_interfaces/msg/GimbalCmd` 提供云台指令。触发沿用 `sp_msgs/msg/AutoAimCommandMsg` 和 `/aim_sentry/auto_aim_switch` 的许可机制。`/rmsim/aim_sentry/state` 的 `aim.match_fire` 提供接受次数、请求序号及拒绝原因。传输仍通过原有 ROS 2 话题和本机 `rmsim_bridge`。

两车位于同一个 MuJoCo 模型、同一固定步长物理线程，并共用原场地的 14 个柔性网格。红蓝 #7 各自记录热量、弹量、底盘功率与缓冲、处罚、补给、占点、伤害与复活。操作手相机、输入与 HUD 绑定驾驶席。以 ROS 受控车为视角的裁判发布保留操作手的阵营。里程计速度帧的能力与操作手小陀螺权限独立，旧版桥接状态仍可读取原有字段。Mid‑360 初始化保留已注册的装甲弹丸查询通道。训练靶场仍走原有模式。

## Windows 核验

- UE 5.8 编译 `Result: Succeeded`。四套裁判 C++ 测试、两项场地描述、34 项 ROS 桥接、5 项比赛启动器和 12 项导航入口检查通过。原生 `TrainingMode` 的 ROS 云台与开火探针通过 20 项检查。 [训练回执](../../artifacts/unreal/dual-match-training-regression-20261124/aim/verification.json)
- 独立 MuJoCo 探针检查了两辆车的反向转向、车间真实接触以及单车复位的状态隔离。原生比赛加载 2 台车、31 个刚体、28 个执行器和原场地 14 个柔性网格。 [原生物理回执](../../artifacts/integration/rmuc-dual-native-20261124/native-stage-verification.json)
- 真实 DDS 模拟比赛中，红方发射 6 次，弹量减少 6，蓝方装甲受击后从 400 降至 280 HP。红方超热时回执为 `overheat`，持久在线发射请求没有进一步消耗弹量。独立复活场次中蓝方从 0 HP 自动恢复至 40 HP，红方 ROS 会话和时钟持续运行。 [DDS 开火](../../artifacts/unreal/dual-match-dds-20261124T151419Z/dds.json) · [复活回执](../../artifacts/unreal/dual-match-dds-20261124T152405Z/dds.json)
- 同场可见窗口复验通过 12 项 DDS 检查。蓝方 WASD、鼠标与左键从 Windows 输入到达实际驾驶席。蓝方回击使红方 HP 从 400 降至 380，红方射击计数及余弹保持原值。红方同时发布 RGB、CameraInfo、底盘与雷达 IMU、Livox CustomMsg 及 PointCloud2，通过超热拒绝和图传切断检查。莱茵 3D HUD 和小地图实拍，UE 日志无 Ensure 或 fatal。 [DDS 回执](../../artifacts/unreal/dual-match-dds-20261124T153725Z/dds.json) · [操作手回执](../../artifacts/unreal/dual-match-dds-20261124T153725Z/manual.json) · [HUD](../../artifacts/unreal/dual-match-dds-20261124T153725Z/hud.png) · [UE 日志](../../artifacts/unreal/dual-match-dds-20261124T153725Z/ue.log)
- 外部 Point‑LIO 接收真实模拟点云和 IMU。Nav2 从约 `(8.00, 2.00)` 驶向 `(7, 2)` 并返回成功，实际终点为 `(6.849, 1.980)`，目标误差 0.153 米。测试使用正常 ROS 规划和控制话题，位置真值只用于验收。 [导航回执](../../artifacts/unreal/dual-match-nav-20261124T153159Z/nav.json)
- 双车占领测试中，红方于仿真 1.685 秒驶入蓝方前哨控制区，6.685 秒获得临时控制。蓝方基地保护解除，塔体仍为蓝方且保持 1000 HP。 [前哨回执](../../artifacts/unreal/dual-match-outpost-20261124T153235Z/receipt.json)
- 原版 Windows 视觉探针读取 4 帧真实比赛 RGB 及 CameraInfo，均未识别出装甲目标。正对照中原队伍录像 24 帧有 23 帧检出。原始 Aimer 编译包含 Linux SocketCAN 硬件依赖，完整的视觉跟踪和比赛触发未验收。 [审计复验记录](<C:/Users/SHEN/Desktop/document/3d哨兵过洞仿真/.codestable/audits/2026-11-24-rmuc-match/evidence/p0-recheck-2026-11-24.md>)
