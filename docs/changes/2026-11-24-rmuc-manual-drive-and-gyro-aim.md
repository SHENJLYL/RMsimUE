# RMUC 手动试驾与小陀螺瞄准

启动器此前传入了引擎未读取的两个参数，实际采用默认视口节奏与 100% 屏幕比例。RMUC 地图旁的 `arena-shell.json` 声明了 Lumen 配置，手动试驾入口现与独立试驾脚本共用此选择。原生窗口日志确认 `RMSimViewportHz=120`、D3D12 SM6 和 `r.ScreenPercentage = "50"`，该次采样的平均帧间隔为 8.37 ms。

小陀螺下手动瞄准原先只更新云台的相对角度，伺服器按零目标角速度阻尼，造成持续世界朝向偏差。现在从 MuJoCo 快照取得底盘实际角速度，把世界瞄准方向在车体坐标系中的变化率交给现有云台轨迹接口。物理伺服仍应用角速度和俯仰行程限制。自由观察使用进入时的实际炮管方向。

| RMUC 原生条件 | 修复前最大指向误差 | 修复后最大指向误差 |
| --- | ---: | ---: |
| 原地小陀螺，5 rad/s 设定 | 31.12° | 0.74° |
| 约 0.6 m/s 前进，小陀螺开启并改变炮管俯仰 | 30.63° | 0.61° |
| 小陀螺开启，`C` 自由观察 | 31.13° | 0.60° |

对照采用 `-ManualGyroAimProof -NoReferee -RenderOffScreen -nullrhi -PhysicsBackend=mujoco -SimStepSeconds=0.005 -SimRealTimePace -RMSimViewportHz=120`，默认出生点及现用 AIM 车辆包，修复后底盘实测速率 4.72–4.90 rad/s。原始采样在 `artifacts/unreal/rmuc-manual-gyro-before-20261124T081517Z/runtime.log` 和 `artifacts/unreal/rmuc-manual-gyro-after-20261124T081952Z/runtime.log`。角度是炮管轴向与视角目标之间的三维夹角。

入口修复后，Windows 可见窗口实测 W/S/A/D 方向、Space 制动、G 切换、Z/X 调速、鼠标偏航与俯仰，以及 `C` 自由观察。随后加入角速度补偿，离屏原生测试通过。补偿版的可见窗口到达就绪，但自动测试未取得前台焦点。现有 MuJoCo 驾驶探针完成 9 项检查及 10 张截图，覆盖轮地接触、正反向、转向、横移、制动、炮管关节、传感器安装和复位。证据位于 `artifacts/unreal/rmuc-manual-drive-20261124T074626Z`。覆盖 RMUC 默认出生点附近，尚未测跨场或连续颠簸路段的指向误差。
