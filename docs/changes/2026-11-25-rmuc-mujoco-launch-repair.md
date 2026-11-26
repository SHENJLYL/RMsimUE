# RMUC 比赛入口 MuJoCo 模型绑定修复

Windows 12:51 和 12:52 两次启动均在模型绑定检查处以状态 `2` 退出。当前 `ue_mesh_rig.json` 的 MD5 为 `cc4b6f40a2f71785b23d69db1dbd3352`，旧清单保存的 MD5 为 `f30ed901ce5df6cc1723f21b6ee378a2`。错误发生在 UE 发出 ROS 桥就绪信号之前。 [原始 UE 日志](../../artifacts/unreal/pure-simulator/de846fc585d545019b08d31f808a8ed5/ue.log)

使用工程已有的 `Prepare-MujocoModel.ps1 -ModelDirectory D:/simulator/assets/vehicles/aim_sentry/mujoco/rmuc2027 -RebuildModel` 按当前车辆和地图源重新生成。命令退出 `0`，校验通过 33 项运行时绑定及 35 个源文件。 [当前模型清单](../../assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json) · [旧包归档](../../artifacts/mujoco/model-history/20261125T045453Z-02aae6f5/manifest.json)

新清单 SHA-256 为 `b7450c28cc0b74ab8d67fda3328daf476a55d4248198ea6f74f048c1151ab0c9`。重建前后模型 XML 的 SHA-256 均为 `113bcc4beee0d442e53e229036a8ba79b77c5bb9173198fe68ad000839922841`。模型仍为 MuJoCo 4.0.1、步长 1 ms，配置包含 249 个车辆碰撞网格和 14 组场地三角面。对照运行时绑定时，唯一不同的源文件是 `vehicle_rig`。

通过比赛启动链 `Start-Simulator.ps1 -MatchRoster <阵容> -RosVehicleTeam red -AutoSentry -Offscreen -Domain 123 -RosPort 55561` 离屏实测，红方为 ROS 席位，蓝方为手动席位。UE 记录双车辆物理世界、专用求解线程和地图就绪，ROS 2 桥接入 10 Hz 相机并采得 314 帧。RViz 收到地图，Nav2 接受前哨目标后 12.68 秒返回成功，站姿切换为 `ATTACK`。验证程序返回 `0`，并结束本次测试进程。 [UE 日志](../../artifacts/unreal/pure-simulator/dc17a004a7ee40fbbb76b80b9ab2f1f9/ue.log) · [Nav2 回执](../../artifacts/navigation/external-20261125T045551Z-41532/sentry_behavior.log) · [验证数据](../../artifacts/unreal/match-autonomy-20261125T045525Z/verification.json)
