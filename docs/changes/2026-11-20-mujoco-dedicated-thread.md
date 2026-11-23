# 独立 MuJoCo 步进与 UE 快照

标准 `Play-ManualVehicle.cmd`、`Play-Training.cmd` 和 `Play-Navigation.cmd` 的实时 MuJoCo 路径采用独立物理线程。工作线程在每个 5 ms 批次内运行五个 1 ms 原生物理步，并用单调墙钟保留迟到产生的步进欠账。UE 按自己的视口节拍消费完整车体、关节和时间快照。该设计复用 UnrealRoboticsLab 的单写者和完整快照思路。 [上游架构](https://github.com/URLab-Sim/UnrealRoboticsLab/blob/efbc5e272a51dbe641c7c078406bd3c076b05823/docs/concepts/architecture.md)

在游戏线程应用快照并维护 UE 物体、场景查询和 ROS 2。LiDAR 按真实 5 ms 查询快照回放车体和云台碰撞姿态，查询后恢复最新视觉姿态；固定 RMUC 场地一直使用原有碰撞几何。关节 TF 每个完成的物理批次只发布一次，IMU 使用原生 1 ms 支撑节点。大型传感器批次仍有界，查询目标中的其他场景实体采用当前 UE 状态。MuJoCo 观察窗口由求解线程继续发送原生 30 Hz 状态。

控制在当前 UE 更新内先于 LiDAR 和 RGB 工作提交，带速率的体坐标云台命令在物理批次间按其时间推进。工作线程检查实际墙钟租约；复位先加入旧线程并清理数据，再启动新一代求解。原 1 kHz LK 电流闭环保留同步执行，同步对照可在 UE 启动参数传入 `-MujocoSynchronous`。

## 已验证范围

- Windows 原生编译成功。游戏线程实际阻塞约 349.94 ms，求解器推进 350 个 1 ms 步，UE 消费的快照在阻塞期间完整保持，墙钟租约触发一次。复位后历史重建、视觉姿态一致，两次线程创建和加入均有日志。
- 完整 RMUC 传感器链通过 TCP 23 项和已安装 ROS 2 的 14 项。双 IMU 实测 200 Hz，LiDAR 保留 200,000 个发射槽/秒和连续 5 ms 查询端点。实际串并行 4,096 束对照的命中误差为 0，材质一致。相机、云台运动、重连均通过。
- 原 Point-LIO、AMCL 和 Nav2 动作验证通过。实际到点误差约 8.38 cm，真实取消后的制动距离约 14.93 cm，均通过原门限。完整同场 60 秒时钟观测整体 0.99992×、场景约 57.83 Draw/s，部分滚动时钟和分段绘制门限仍失败，合并门禁退出 2。历史快照版本另有 60 秒时钟通过及取消制动单项失败记录。
- 实际全屏三窗口 2560×1440、120 Hz 请求。`manual_R` 后导航恢复，观察窗口 2,574 条状态序号无缺口，真实车体姿态一致。复位前后分段约 51.03 和 46.13 Draw/s。按本文的全屏口径，焦点和 epoch 均改变，连续窗口验收失败，100 FPS 仍待实现。硬件光追有效配置为 `r.RayTracing=0`，Lumen 间接光照和反射运行。

测试入口都保留退出状态、同版模块哈希与失败报告。证据根 `artifacts/integration/mujoco-threaded-20261120`，精简机器结果为 [专项验证](../verification/2026-11-20-mujoco-dedicated-thread.json)。Ubuntu 原生验收待补。
