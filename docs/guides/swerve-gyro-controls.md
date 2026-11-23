# 舵轮与小陀螺操作

双击`Play-Training.cmd`进入RMUC2027驾驶/射击训练，`Play-ManualVehicle.cmd`进入纯驾驶。快捷入口只启动一个实例；已有预览、验证进程或编译正在运行时会提示，避免多个UE实例争用资源。需要开发用多实例时可显式调用`Start-PreparedVehicle.ps1 -AllowMultiple`。

## 键盘映射

| 输入 | 行为 |
|---|---|
| W / S | 沿当前镜头水平朝向前进 / 后退 |
| A / D | 相对镜头向左 / 向右平移 |
| Q / E | 普通模式手动车体左转 / 右转 |
| G | 开关小陀螺；WASD方向不会随底盘自转 |
| Z / X | 降低 / 提高自转设定，步进0.5rad/s，范围0.1–5 rad/s |
| Ctrl + WASD | 慢行，平面速度上限0.35m/s，适合坡道和波浪段 |
| 松开WASD | 主动减速至零平移；小陀螺仍按设定自转 |
| Space | 更强制动，包含平移与自转；G负责关闭小陀螺模式 |
| 鼠标 | 云台瞄准与俯仰 |
| C / Shift / 右键 / 滚轮 | 自由观察 / 瞄准镜切换 / 临时变倍 / 镜头距离或FOV |
| R | 回到出生点并重置训练；跌出地图最低面5米会自动恢复 |

普通模式和小陀螺模式采用相同的镜头相对平移。镜头俯仰不改变地面移动方向，只使用水平朝向。HUD显示小陀螺设定/实际转速、慢行状态和FPS；车速按底盘参考原点计算，避免偏置质心绕转被误显示为平移。

在 `Play-Navigation.cmd` 中，G / Z / X 使用同一套转速配置。车体自转时，导航继续控制平移方向。停车且局部代价地图确认旋转净空时，可以保持自转。Space、取消动作和指令失效优先制动。

## 手感与物理

导航云台根据已接收的平移速度朝向转动。车辆自转时持续补偿车体角度。临时停止平移时保留最近朝向。AIM 仿真导航的世界系转速上限为 3 rad/s。

速度大小和行进方向分别限幅。起步加速度0.8m/s²；松键减速度3m/s²，Space6m/s²；改向按10m/s²横向加速度及100m/s³方向jerk约束。不会为了加快改向同时提高起步冲劲。180度反向先减速再反向。

四个舵轮分别求解方向与轮速，选择最短舵向，必要时反转轮速。舵机有有限力矩、约150RPM的速度上限和速度前馈；轮驱动采用有积分上限的速度环。车身运动来自轮地接触、重力、悬挂与约束，行驶过程不强制改写车体位置。

轮胎碰撞现在采用原模型的弧形胎面凸包，保留源半径和宽度。旧全宽等半径圆柱会添加原模型没有的侧缘接触，使持续自转出现滑移。车辆导出/导入携带`collision_vertices_m`，碰撞缓存同时校验几何哈希，修改轮胎后会自动重建。刚性凸包仍未模拟橡胶形变。

停车时保留Chaos自然休眠，新的运动/自转指令会主动唤醒；外力可以使车辆移动。没有新增锁车位姿或轮轴位置保持控制。

轮胎抓地参数为1.8。沿轮面方向可滚动，横向需要克服滑动摩擦；停车靠电机主动补偿坡面重力。验证中只关闭轮驱动、保持舵向：20度坡顺坡轮在0.802秒滚动71.189cm，横坡轮仅下移1.93mm。当前保持段未测得继续下移；实际下坡后松键约5.36cm停住。适用范围为该原生夹具，非所有地面/载荷标定。

手动窗口只渲染玩家视图，两个未使用的离屏视图不再逐帧渲染。截图/验证仍按需捕获。这不是ROS传感器采样器；后续传感器发布需要独立时钟调度。

## 车辆参数与控制接入

参数在`assets/vehicles/aim_sentry/drive-profile.json`，重启后直接读取，无需重新导出FBX。`LateralAccelerationMps2`和`DirectionJerkMps3`单独调整行进改向；`LinearAccelerationMps2`控制起步。`ue_mesh_rig.json`内联参数是导入快照，实际启动以外部profile文件为准。当前15个视觉部件、13个独立刚体、4悬挂/4舵向/4轮轴约束。

- `SetContactPlanarDrive`接收线速度m/s、角速度rad/s；线速度明确选择ROS车体系或世界平面。速度整形在世界平面进行，随后转换为各轮目标，避免底盘自转带着移动方向转。
- 实际调用的`Control/SentrySwerveKinematics.h`适配用户提供的LK轮向/轮速解算及32768编码器最短路径，修正整数跨界处理。
- 原LK节点的仿真配置在车辆包`adapters/sentry_lk.yaml`。轮半径0.0425m、轮心半径约0.194146m来自模型；虚拟编码器中立点为0，电机话题与真实EtherCAT分开。
- 原LK节点、电流/反馈及其PID/功率路径已在独立RosMotorControl模式接通，见[原LK指南](sentry-lk-motor-loop.md)。本手动入口仍使用UE本地控制；完整sentry_ws和实车校准尚未完成。

质量、摩擦、弹簧与电机仍是演示参数，未实车标定。云台视觉可动但其碰撞仍为中立姿态焊接；完整比赛、网络与传感器不由本次控制验收覆盖。

## 验证入口

使用新的证据目录：

```powershell
tools/unreal/Test-GyroControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory D:\simulator\artifacts\gameplay\gyro-control\my-controls
tools/unreal/Test-GyroControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory D:\simulator\artifacts\gameplay\gyro-control\my-waves -WaveRoutes -PrecisionKeys -SpinRadS 4
tools/unreal/Test-ManualControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory D:\simulator\artifacts\gameplay\gyro-control\my-slope -Traction
```

响应计时从实际提交指令时刻开始，记录真实世界帧、目标舵角与实际舵角；不是从上一项存盘阶段开始，也不是OS键鼠到显示器的端到端延迟。Windows原生与Ubuntu离线准备必须分开记录。
