# RMUC 哨兵自动比赛跟踪与俯仰

比赛入口为自动席位启用车辆包中的导航云台跟随。有效的视觉跟踪接管云台，行进时由估计里程计和导航速度提供朝向。停车搜索使用有界的位置角跨越 ±π，订阅桥单独处理扫视拒收。扫视指令使用上仰正方向。比赛相机从车辆包读取水平 75°。

视觉插值匹配相机采集时间的实测云台方向，使用类别、角度和观测距离关联连续装甲。连续确认目标可见且观测新鲜时持续请求发射，裁判执行射速、热量和弹量规则。一次漏检保留短时跟随，暂停发射请求。前哨图案和目标灯条共同验证前哨候选。灯条颜色相同的合成背景对照被拒绝。 [实际图像和协议回归](../../artifacts/unreal/sentry-combat-fix-20261125/offline-continuous-fire-verification.json) · [固定分类模型标签](https://raw.githubusercontent.com/unnc-aim/26UL_sp_vision_25_rosy/2bab801d91c6944dbdb99cbb4d4d431ed15d6e0d/tasks/auto_aim/armor.hpp)

红蓝开场前往各自的中高地。当前高地目标完成才转入驻守。补给、防守与敌方前哨状态可以改变任务。推进敌方基地由裁判公布的正式控制或前哨重建归属触发。行为节点读取 `field_devices.red` 和 `field_devices.blue`，将 `target_priority` 发给视觉。视觉按优先级和裁判状态选择可交战类别。

按正仰负俯配置 −45° 至 +60°。ROS +Y 俯仰轴的下俯为正，车辆配置与 MuJoCo 使用 −1.0471975512 至 +0.7853981634 rad。`Prepare-MujocoModel.ps1 -RebuildModel` 确认 33 项运行时源绑定，物理模型 SHA-256 为 `d909f4c962d991d7e47ea45cb69b035ab15014e1e3bf455ba53598a00576d653`。原生关节读数上仰 59.999978°、下俯 45.000010°，两端均通过超限指令验证。 [关节端点实测](../../artifacts/unreal/sentry-combat-fix-20261125/pitch-01/verification.json)

## Windows 原生结果

`Build-RosBridge.ps1` 返回 0，`Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8` 返回 `Result: Succeeded`。模块 SHA-256 为 `0cb270d65ea4b07e8beefb4b53e08a039853d88f27a6f82d0f1de23ca5d5f718`。启动器、导航、协议和行为决策测试共 56 项及 31 个子项通过。

| 默认比赛席位 | Nav2 接受目标至到点 | 导航云台跟随样本 | 到点后本方高地有效裁判样本 |
| --- | --- | --- | --- |
| 红方 | 13.10 s | 540 | 77 |
| 蓝方 | 18.75 s | 724 | 60 |

两侧均推进至本方中高地并维持 `HOLD_HIGHLAND`。已观察的导航跟随样本开火数为 0。两侧启动均报告水平 75° 相机。 [红方导航](../../artifacts/unreal/sentry-combat-fix-20261125/nav-red-01/verification.json) · [蓝方导航](../../artifacts/unreal/sentry-combat-fix-20261125/nav-blue-01/verification.json) · [裁判高地读数](../../artifacts/unreal/sentry-combat-fix-20261125/highland-rfid-verification.json)

离屏旋转目标使用同一 RMUC 场地，测试出生点相距 1.7 米。受测视觉从实际相机帧获取目标，裁判确认发射和命中，对手角速度来自 MuJoCo 物理读数。默认场地结果覆盖导航和驻守。跨全图持续对抗仍需专项验证。

| 持续发射席位 | 对手实测角速度中位数 | 裁判确认发射 | 对手旋转时判定命中 | 连续请求发射的相机帧 |
| --- | --- | --- | --- | --- |
| 红方 | 4.8736 rad/s | 111 | 20 | 12 |
| 蓝方 | 4.8697 rad/s | 113 | 20 | 24 |

最终视觉节点 SHA-256 `9f99eed71cd2f05ee0d44a2062d321509907e957c2b15b9f0caa2436a2088db8`。红蓝各完成 35 s 观测和 10 项场景检查，均将旋转对手的 HP 从 400 降至 0。过热触发图传切断时，视觉停止许可，恢复新图像后重新跟踪。红方首次持续发射场次的 Windows 录包器提前退出，完整复跑用直接写入完成。 [红方旋转对抗](../../artifacts/unreal/sentry-combat-fix-20261125/gyro-red-continuous-retry/verification.json) · [蓝方旋转对抗](../../artifacts/unreal/sentry-combat-fix-20261125/gyro-blue-continuous/verification.json)

能量机关由本方操作手申请开启，裁判处理虚拟弹丸与本方亮靶的碰撞。当前自动视觉仍面向车体与场地装甲。此前的机关测试从靶面近处发射，实际 S7 车位上的完整命中不在本轮覆盖内。 [能量机关裁判测试](2026-11-25-rmuc-rune-client-and-lights.md)
