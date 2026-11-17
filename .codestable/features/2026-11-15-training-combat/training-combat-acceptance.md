---
doc_type: feature-acceptance
feature: 2026-11-15-training-combat
status: passed
summary: Native Windows ballistic training passes14 checks and preserves18 controls.
tags: [gameplay, training, acceptance]
date: 2026-11-15
user_review: not_requested_under_standing_authorization
---

# 手动训练功能验收

范围：单车静态装甲靶训练与明确演示参数；不覆盖完整官方比赛、联网或自主算法目标。

UE5.8.4 Win64实际构建Result:Succeeded。artifacts/gameplay/combat-03包装脚本退出0，14项通过、10张PNG解码。control-regression-01退出0，旧操控18项/10PNG。rmuc-training-01在默认场地运行，无初始车辆重叠，真实瞄准相机可见靶。

共用ApplyManualInput从实际rig变换发射；球体扫掠和重力导致一次命中只扣20HP。15次实际命中击倒三个100HP靶，胜利清空弹丸。遮挡检查确认命中的是移到弹道上的真实地图障碍actor，靶HP保持100。弹量耗尽、热量上限、射速、1秒冷却、失焦/重新聚焦、epoch重置、超时和结束锁定均通过。50/60/90/144Hz各推进5秒，发射数及末次发射仿真时间一致。

失败证据保留：combat-01/02在热量检查失败，暴露严格100比较的浮点越界和2秒边界允许冷却后发射的测试假设。热量增加改为夹紧；检查先处理合法边界枪。独立审查指出时序量化、捕获焦点误射、遮挡检查未验证真实命中actor；均已修复并在03回归。训练橙框改为弹道预测，纯驾驶仍为射线。

限制：靶不是已验证视觉识别装甲模型；aim_camera提供预览炮口位置，无标定外参、空气阻力/反弹/移动对手。无服务器联机、官方赛季裁判或ROS传感器闭环。车辆移动不被回合重置改变。项目长期目标未标完成。
