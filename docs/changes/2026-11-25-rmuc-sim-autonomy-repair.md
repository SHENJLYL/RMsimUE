# RMUC 比赛模式自主哨兵联调修复

## 定位与改动

- 旧比赛连接器漏载 `install_control`。日志显示 `sp_msgs=False`，视觉节点没有发布指令。Windows 原生环境加载覆盖层后，`sp_msgs` 和 `pb_rm_interfaces` 类型支持均可加载。比赛入口现在加载该覆盖层，模型缺失时报告具体路径。
- 行为树按 Nav2 接受、取消和结果更新目标。相同目标执行中不再每 12 秒抢占，目标成功后也不再重复提交。比赛模式的平移指令上限为 2.6 m/s。中高地停车目标采用红蓝镜像的 `(±2.96, 0)`，按局部代价地图实测留出旋转空间。旋转仍检查足迹、地图与位姿时效。
- 比赛开赛后为 ROS 席位使用 5.0 rad/s 的小陀螺设定。操控同一席位时，G、Z、X 仍走原输入映射。平移走既有导航坐标系接口。云台按 `rmsim_world` 水平扫视，识别结果使用来自相同捕获时间的云台反馈和相机内参。目标待定或丢失时收回开火许可。
- `MOVE` 和 `ATTACK` 以有效比赛裁判状态为准。中高地停车目标成功后维持 `ATTACK`。场地增益仍由裁判按真实区域判定。仿真裁判确认每次发射、弹量和击中。
- 完整启动器测试复现过 Windows `amcl.exe` 在定位建立前异常退出。应用事件的故障模块为 `pf_lib.dll`，偏移对应 `pf_cluster_stats`。比赛连接器当前仅在该异常发生于初始定位时重启定位栈一次，并保留两次日志。Windows 退出连接器时按所属进程树收尾。

## Windows 原生验收

`Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8` 返回 `Result: Succeeded`，`Build-RosBridge.ps1` 返回 0。启动器、外部导航连接器、协议、战术决策共 36 项测试及 3 个子检查通过。AMCL 恢复的 6 个进程退出码与阶段用例通过。UE 模块 SHA-256 `9bf2c750fcccb34140a0b7c037393f05f9b08094baf699aff5b5ac37fb9e1c25`。

Windows 原生验证由 `Start-Simulator.ps1 -MatchRoster <阵容文件> -RosVehicleTeam red -AutoSentry -Offscreen -Domain 123 -RosPort <隔离端口>` 启动 UE D3D12、ROS 桥、Point-LIO、Nav2、行为和视觉。正常启动器还打开 RViz。验证订阅比赛状态并通过 ROS 2 写入录包。 [启动器预检](../../artifacts/unreal/match-autonomy-20261125T043905Z/preflight.txt)

第一次把停车点设在 x=3.10 时，录包表明障碍单元到车心约 0.415–0.46 m，低于 0.502 m 的旋转检查半径。最终中高地版本从红方出生点出发，RViz 已显示地图，Nav2 接受目标后 14.00 秒返回成功。保持 `ATTACK` 的末段 87 个零平移指令样本中，角速度中位数为 4.87 rad/s，74 个样本超过 4.5 rad/s，6 个样本处于制动。 [旋转净空对照](../../artifacts/unreal/match-autonomy-20261125T043230Z/rosbag/metadata.yaml) · [到点与站姿](../../artifacts/navigation/external-20261125T044150Z-31204/sentry_behavior.log) · [UE 遥测](../../artifacts/unreal/match-autonomy-20261125T044124Z/sample.jsonl)

近距离双车对抗使用实际相机画面、实体物理和裁判。`tiny_resnet.onnx` 复用 `unnc-aim/26UL_sp_vision_25_rosy` 的 `2bab801d91c6944dbdb99cbb4d4d431ed15d6e0d`，模型 SHA-256 `9f45670da9e61f31b60c12efb08c8a7522ff56cc220c7fa9907283ce7e730179`，位于本地 `artifacts/runtime/vision/`。最终近距离复验使用默认 10 Hz RGB，录到 379 帧，红方发射 65 发，弹量 300→235，蓝方 HP 400→0。 [视觉与裁判回执](../../artifacts/unreal/match-autonomy-20261125T044402Z/verification.json) · [原模型来源](https://github.com/unnc-aim/26UL_sp_vision_25_rosy/tree/2bab801d91c6944dbdb99cbb4d4d431ed15d6e0d)

联调期间，工作区的进攻策略从中高地推进切换为本方前哨增益区。此策略保留并复测。红方从默认出生点出发，Nav2 接受目标后 11.59 秒到达本方前哨增益区，裁判确认 `ATTACK`。停车位边缘的 100 占用栅格进入约 0.502 m 的检查半径，禁止原地旋转。测试过同一区域的 `(2.56, 3.08)` 停车点，12.24 秒到点且成功切换站姿，停车后仍因占用栅格进入旋转半径而制动，故恢复原前哨坐标。本轮正常地图未出现稳定敌方装甲锁定和发射。 [前哨导航回执](../../artifacts/navigation/external-20261125T044551Z-45508/sentry_behavior.log) · [正常场地遥测](../../artifacts/unreal/match-autonomy-20261125T044525Z/verification.json) · [前哨停车对照](../../artifacts/unreal/match-autonomy-20261125T044759Z/verification.json)

验收覆盖红方导航、中高地停车小陀螺、站姿和独立近距离自瞄发射。前哨停车旋转、完整原场地进攻闭环、蓝方原生全场和 Ubuntu 仍待验收。比赛内的高层行为和视觉是 ROS 2 仿真适配，使用固定消息接口以及复用的分类器。
