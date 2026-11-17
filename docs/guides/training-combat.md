# 手动射击训练

双击根目录Play-Training.cmd，默认在RMUC2027创建三个红色装甲靶。原Play-ManualVehicle.cmd仍为纯驾驶入口。

| 操作 | 行为 |
|---|---|
| W/S、A/D、Space | 前后移动、转向、制动 |
| 鼠标、Shift、右键 | 瞄准、切瞄准镜、临时放大 |
| 左键按住 | 连射，受射速/热量/弹量限制 |
| R | 清除弹丸、恢复靶和弹量、开始新回合 |
| C按住 | 自由观察，保持原瞄准目标 |

白色十字表示观察方向，训练橙框表示当前炮口方向、弹速、重力和碰撞计算的预计落点。弹道下坠时二者不一定重合；点击重新捕获窗口的首帧不会开火。

HUD显示时间、弹量、热量、击倒数和三个靶的血量。只有命中红色装甲板扣血；靶归零变暗，全部击倒胜利，超时结束。结束后不能开火，R重开且保留车辆位置。

默认参数：180秒、100发、每发20伤害、每靶100HP、25m/s、重力9.81m/s²、最快10发/秒、每发热量10、上限100、每秒冷却20。这些是演示参数，不是官方RMUL/RMUC规则或实物标定。修改unreal/RMSim/Config/DefaultGame.ini的RMSim.Training节后重启生效。

切平地测试场：

    Play-Training.cmd assets\maps\import_lab\map.json

完整入口Run-ImportLab.ps1支持-TrainingMode。炮口位置沿用导入rig的aim_camera预览变换，尚无单独实测外参。弹丸使用真实场景球体扫掠和重力积分，无阻力、反弹或移动敌车。

## 已运行证据

artifacts/gameplay/combat-03：14项真实引擎检查、10PNG、进程0；覆盖共用左键命中、弹量/弹热/射速、实际障碍物遮挡、15命中三靶胜利、超时/重置/失焦，以及50/60/90/144Hz分帧一致性。control-regression-01保持18项旧操控通过。rmuc-training-01保存默认场地实际运行、无初始车辆重叠及相机截图。

复现（新证据目录）：

    pwsh -File tools/unreal/Test-ManualControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -Combat -EvidenceDirectory artifacts/gameplay/new-run

这些是共用输入路径测试，不冒充OS按键/端到端延迟测量。完整赛季裁判、敌车对抗、联网、ROS感知和sentry_ws闭环仍未完成。
