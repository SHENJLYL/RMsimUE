# RMUC 飞镖光带与贴面落地

曳光由一条连续的双面网格光带构成。飞行中保留过去 0.62 秒的轨迹，光带向尾端连续收窄。命中时使用前哨和基地的 CAD 碰撞面，飞镖沿接触面的法向摊平，约 0.26 秒后隐藏，光带独立在 0.38 秒内淡出。命中火花和闪光灯组件已移除。

Windows UE5.8 原生构建通过。对前哨、基地随机移动靶和末端移动靶完成四场验证，覆盖红蓝双方。运行核验了连续光带拓扑、实际碰撞面、镖身与尾翼受到网格支撑、镖身隐藏、光带回收、裁判扣血和护甲展开。截图按落地后 0.20 秒与 0.30 秒取帧。蓝方随机移动靶使用同一模块补测护甲展开。 [验证结果](../../artifacts/unreal/rmuc-dart-landing-20261126T050604Z/release-verification.json) · [空中光带](../../artifacts/unreal/rmuc-dart-landing-20261126T050604Z/outpost-blue/dart-trail.png) · [前哨贴面](../../artifacts/unreal/rmuc-dart-landing-20261126T050604Z/outpost-blue/settled.png) · [基地贴面](../../artifacts/unreal/rmuc-dart-landing-20261126T050604Z/late_moving_base-blue/settled.png)

核查了本地场地资产和老版 RMUC 模拟器。SimulatorX 的飞行飞镖预制件引用 Unity 内置网格。当前沿用项目中已有的飞镖镖身。 [公开旧版预制件](https://github.com/scutrobotlab/SimulatorX/blob/7c453c2e775c16ddfc06269f47f502b35bb0005e/Assets/Prefabs/Items/RedDart.prefab)

工作区重编后的模块也通过了前哨光带与贴面回归。 [重编模块验证](../../artifacts/unreal/rmuc-dart-landing-20261126T051446Z/verification.json)
