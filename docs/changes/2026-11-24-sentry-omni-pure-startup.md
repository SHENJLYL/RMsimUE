# sentry_omni 纯仿真启动恢复

原生日志 `RMSim-backup-2026.11.24-06.15.35.log` 显示场景合成成功，UE 读入 canonical 车辆后误用了依赖网格 rig 轮组的 `SnapToTerrain`，报出 `terrain following requires at least three wheel supports` 并退出。canonical MuJoCo 启动现在用合成模型提供的出生高度与实际轮地接触，网格 rig 与运动学预览仍使用原地形贴合流程。

每次纯仿真运行将 UE 日志写入独立目录。`IMPORT_LAB_FAILED` 会将 UE 的具体报错传回启动器并返回退出码 2。包中缺少对应运行时配置时，启动器逐项提示 IMU 或 LiDAR 未启用并继续启动其他 ROS 接口。`sentry_omni` 目前只有车辆描述中的传感器声明，仍需要适用的运行时配置。

Windows 重编译成功。使用 `sentry_omni + rmuc2027 + default` 离屏验证，端口 19523 上确认 4 个 MuJoCo 轮组、独立物理线程、地图就绪和 ROS 桥 TCP 已连接。日志为 `artifacts/unreal/pure-simulator/1803e7cd1151453b9a988aa7300652aa/ue.log`。独立端口 19524 注入占用后启动器显示 `cannot bind exclusive ROS loopback port` 并返回 2。AIM Sentry 的 DryRun 仍传入现有 Mid-360 IMU 与 LiDAR 配置。此次覆盖启动、错误转发与 TCP 连接，未发送驾驶指令或验收传感器数据。
