# ROS 云台、扫描与训练发射

接口使用工作区固定版本的原消息：`sp_msgs/AutoAimCommandMsg`、`pb_rm_interfaces/GimbalCmd` 和 `std_msgs/Int32`。它接收视觉/行为树的高层输出，当前云台执行器为有限转速的运动学关节，碰撞仍是中立位代理；原硬件云台电流环、动态云台碰撞与完整视觉识别尚未接入。发射复用实际弹丸扫掠、热量、弹量和靶HP逻辑，不直接设置命中。

## 启动

在 `D:\simulator` 的PowerShell先构建接口包（本机已构建）：

```powershell
& .\tools\environment\Build-SentryControllers.cmd
```

终端一打开场景，显式授予ROS云台/发射控制；本例仍可用键盘开底盘：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:\simulator-tools\UE_5.8' -RosAimControl -TrainingMode
```

终端二运行桥：

```powershell
& .\tools\environment\Start-RosBridge.ps1 -AimControl
```

终端三进入原生环境并运行5秒扫描演示：

```powershell
& .\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-NoExit')
. D:\simulator\ros2_ws\install_control\local_setup.ps1
. D:\simulator\ros2_ws\install_bridge\local_setup.ps1
ros2 run rmsim_bridge aim_demo --ros-args -p mode:=scan -p duration_s:=5.0
```

绝对世界方向示例（yaw左正，pitch下正，单位弧度）：

```powershell
ros2 run rmsim_bridge aim_demo --ros-args -p mode:=aim -p yaw_rad:=0.4 -p pitch_rad:=-0.1 -p duration_s:=3.0
```

默认不发射。加`-p shoot:=true`才请求训练发射；弹丸按当前实际炮口方向发出，转动未到位可能打偏，不会自动修改命中。演示结束撤销许可并停止目标/扫描。

同时交出底盘速度控制时，UE再加`-RosControl`；原LK电流底盘则用`-RosMotorControl`，桥加`-MotorControl`并运行原LK节点，见[原LK指南](sentry-lk-motor-loop.md)。LK模式保持1ms仿真步长，当前约0.3倍实时。`RosAimControl`不改变普通底盘的时间步长。

没有`-RosAimControl`时，网络消息不能改变云台或开火。启用后鼠标仅观察，Space按住抑制外部瞄准/发射，R复位车辆和一次训练回合，清空会话许可。持续运行的算法需在新状态后重新发许可和新指令。

## 原消息接线

| Topic | 类型 | 约定 |
|---|---|---|
| `/aim_sentry/autoaim_command` | `sp_msgs/msg/AutoAimCommandMsg` | timestamp来自/clock；control=true才参与自瞄；yaw/pitch为世界方向、rad，pitch下正；shoot请求发射 |
| `/aim_sentry/gimbal_scan_cmd` | `pb_rm_interfaces/msg/GimbalCmd` | header使用仿真时间；yaw左正；按当前sentry Hub的约定，pitch值/速度/范围向上为正，桥统一转换；速度模式在边界反转 |
| `/aim_sentry/auto_aim_switch` | `std_msgs/msg/Int32` | 0禁用自瞄，1许可；当前连接epoch内保持，断线/R清除 |
| `/aim_sentry/gimbal/joint_states` | `sensor_msgs/msg/JointState` | 车辆包关节名及实际场景角度/角速度，与相机TF同刻；不是实车编码器标定结果 |
| `/aim_sentry/training_state` | `std_msgs/msg/String` JSON | 自身弹量、热量、发射数、回合状态与演示限值，不含靶HP/命中明细 |
| `/rmsim/evaluation/aim_sentry/training` | `std_msgs/msg/String` JSON | 独立评测：靶HP、命中与得分，不能当真实视觉识别输入 |

原视觉输出`/sp_vision/autoaim_command`可remap到机器人topic，行为树的`/gimbal_scan_cmd`、`/auto_aim_switch`同理。所有时间戳必须使用仿真时钟。原视觉发布节点用`node->now()`打戳，完整程序的相机/IMU/时间输入仍需后续同源适配，不能仅remap就声称完整视觉已运行。

上游扫描插件未设置frame_id；空值固定解释为`rmsim_world`并记录诊断，显式`rmsim_world`也接受，未知frame拒绝。yaw_type/pitch_type为1绝对角度、2速度扫描；角度/速率/范围需有限，扫描min<max。两种原消息的pitch符号差异已按工作区固定消费源码核对。

新鲜且获得许可的自瞄优先于新鲜扫描。control=false或自瞄过期后回到仍新鲜的扫描，否则保持当前关节并停火。扫描本身不发射。许可必须在桥收到本次会话状态后发送，不能依赖旧epoch的缓存许可。

注意：固定原Hub还有RC、摩擦轮和导航门控，并未直接使用AutoAimCommandMsg.shoot；这里定义的是明确的仿真直连模式，未冒称运行了原Hub全部仲裁或发射电机环。

## 时间、失效与物理边界

云台/发射使用独立200ms命令有效期，底盘维持自己的有效期，两路流量不能互相续期。过期/错epoch/错frame/非法模式/非有限值会停止对应通道；坏帧或断线停所有外部输出。重连和R不会重放旧发射指令。

外部瞄准模式下，训练时间来自Chaos物理时间差；先推进上一条已接受指令，再接收下一条。发射事件按帧前后炮口位姿插值，避免把帧末方向提前用于帧初子弹。原手动训练接口保留，仍有其单独回归证据。

实际角度受车辆包机械限位和默认540/360deg/s转速限制，诊断报告mechanically_limited。当前解析支持标准Z-yaw/Y-pitch轴和X向炮口；其他安装方向需要显式适配。没有云台惯量/电气/噪声标定或动态碰撞，弹道亦为现有重力训练模型。

验证目录 `artifacts/control/ros-aim-fire`。测试用已知校准靶坐标计算角度，只证明控制/弹道链路，不能记成原视觉算法识别成功。Ubuntu同源入口有`--ros-aim-control --training`，原生Ubuntu仍待验证。
