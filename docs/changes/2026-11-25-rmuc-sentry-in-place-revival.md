# RMUC 比赛哨兵原地复活

普通读条和金币复活都会进入 `ARMUCGameReferee::RespawnCompetitor`。此前该入口把整车传送到开局位置，并推进运动历史代数。Mid‑360 看到代数变化会丢弃当前扫描和历史，点云也会跨越传送产生的位姿差。现在复活只恢复裁判记录，MuJoCo 继续使用当前车体和云台状态。

Windows 上使用 `Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8` 编译，返回 `Result: Succeeded`。最终模块 SHA-256 为 `ff084572ef76ccddb848fb9273c049a96db6f268c422a59bc2a3606ef5bbdae7`。

Windows D3D12 离屏比赛在域 125 下使用 `cmd_vel` 把红方 ROS 席位开离出生点 2.330 米。开发测试参数 `-RefereeRespawnProof` 通过裁判的受击入口触发战亡，并分别验证正式读条和金币复活逻辑。验证通过，收到 324 帧点云和 1314 个位姿样本。 [验证结果](../../artifacts/unreal/rmuc-respawn-20261125T051102Z/verification.json) · [ROS 录包](../../artifacts/unreal/rmuc-respawn-20261125T051102Z/rosbag/metadata.yaml)

| 复活方式 | HP | 复活附近最大相邻位姿平移 | Mid‑360 最大扫描间隔 |
| --- | --- | --- | --- |
| 读条 | 0 → 40 | 0.00549 mm | 100 ms |
| 金币 | 0 → 400，消耗 50 金币 | 0.00574 mm | 100 ms |

两次复活前后都沿用同一个 ROS 会话与 epoch，雷达历史重置计数均为 0。首次交互验证停在 Windows 前台焦点检查，正式结果来自离屏回归。 [交互尝试记录](../../artifacts/unreal/rmuc-respawn-20261125T050853Z/verification.json)
