# 原 LK 底盘控制器接入

此模式运行用户提供的 `LKFullWheelChassisController` 原生 C++ ROS节点，使用原 `ChassisControl`、`ReadLkMotorMulti` 与电流消息。UE将电流作用于真实轮轴/舵轴，并返回关节角和速度；有效电流控制期间不叠加UE本地速度伺服。

它是未标定的电机控制实验。物理步长固定1ms，控制器按配对的新反馈及仿真时间差计算PID；渲染较慢时仿真会慢于墙钟。当前实测约0.3倍速，HUD显示真实墙钟FPS和sim倍率。普通手动/速度模式沿原时间路径。

## Windows 构建

在 `D:\simulator` 的PowerShell运行：

```powershell
& .\tools\environment\Build-SentryControllers.cmd
```

本机MSVC入口默认 `D:\VS\Visual studio\VC\Auxiliary\Build\vcvars64.bat`，其他机器可设置 `RMSIM_VCVARS`。构建使用固定Pixi环境并安装到 `ros2_ws/install_control`，同时把原生程序所需的非系统DLL部署在EXE旁，哈希清单为其 `runtime-dependencies.json`。该步骤兼容从MSIX桌面应用启动子进程，不改系统PATH。Python适配器只在本进程注册两个自定义消息包的DLL目录。

`rmsim_bridge`修改后还须按 [原生桥指南](ros2-native-bridge.md) 重新colcon安装到 `install_bridge`。

## 启动原节点和车辆

关闭旧预览后，终端一启动已准备车辆：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:\simulator-tools\UE_5.8' -RosMotorControl
```

终端二保持电机桥运行：

```powershell
& .\tools\environment\Start-RosBridge.ps1 -MotorControl
```

终端三进入原生ROS环境并运行原控制器：

```powershell
& .\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-NoExit')
. D:\simulator\ros2_ws\install_control\local_setup.ps1
. D:\simulator\ros2_ws\install_bridge\local_setup.ps1
ros2 run chassis_controllers lk_full_wheel_chassis_controller --ros-args --params-file D:\simulator\assets\vehicles\aim_sentry\adapters\sentry_lk.yaml
```

加载同样环境的终端四可以运行三秒墙钟时长的低速演示：

```powershell
ros2 run rmsim_bridge drive_demo --ros-args -p control_backend:=lk -p vx:=0.2 -p duration_s:=3.0
```

演示使用原消息，默认功率限额100W，结束发零速。`vy`为向左平移，`wz`为正向左转，均是车体系。此模式底盘由ROS拥有，WASD不会竞争控制；鼠标观察，Space人工制动，R复位。手动键盘样例仍用 `Play-ManualVehicle.cmd`。

## 接入现有 sentry 程序

配置中的输入为 `/rmsim/aim_sentry/chassis_command`，类型 `chassis_controllers/msg/ChassisControl`。保持x前/y左、m/s与rad/s；`emergency_stop`、`power_limit`、`extra_info`保留原字段。持续发送新命令；250ms无新底盘命令、100ms无控制器电流输出时，桥请求仿真主动制动。UE另有独立250ms租约。原节点自身也检查指令/反馈新鲜度和online标志。

反馈及电流话题分别为 `/rmsim/aim_sentry/{steer,wheel}/feedback` 和 `/current_command`，顺序FL/FR/BL/BR。反馈来自刚体相对运动，编码器32768周期、角速度deg/s、电流刻度16.5/2048A。无温度模型，temperature=0是未支持字段。电流为当前理想执行器实际施加值，没有电感、母线电压、反电势或电流环仿真。

轮轴0.14Nm/A来自用户源码功率模型；舵轴暂用独立的未标定0.14Nm/A。UE支持 `-LkWheelNmPerAmp=` / `-LkSteerNmPerAmp=`校准入口，运行值也发布在诊断中。配置覆盖轮半径0.0425m及实际模型轮心半径约0.194m，未沿用源码0.40m默认值。

`feedback_driven: true`只用于本固定步长仿真。原硬件默认false，继续1ms壁钟回调。PID默认采样比例1保持原离散结果；仿真用真实反馈时间差缩放积分/微分。原PID增益及功率模型仍在工作，源码补丁在 `third_party/patches/sentry-lk-native.patch`，原用户目录未修改。

失联/人工制动由仿真安全停车伺服接管，其轮轴/舵轴力矩也受相同的2000counts执行器上限约束，默认约2.256Nm。此时`current_active=false`，反馈current表示外部电流通道为0，不是安全停车伺服的电流测量；不能把该停车行为当作真实硬件断电后的锁坡效果。

复位更换epoch/session并回跳仿真时间，原节点清除旧控制授权/PID；重连后桥等待新底盘意图，不重放旧电流。诊断 `/rmsim/aim_sentry/state` 包含motors、当前模式、保护原因、时钟倍率。评测真值仍不进入定位TF。

## 验证与边界

```powershell
& .\tools\unreal\Test-RosBridge.ps1 -EngineRoot 'E:\simulator-tools\UE_5.8' -EvidenceDirectory 'D:\simulator\artifacts\control\lk-motor-loop\my-new-run' -Port 19457 -LkMotorLoop
```

验证使用原生节点、实际物理位姿和电流反馈，包含原功率限制、前进/横移/旋转、稳态舵向、停发/停节点/断线、错误电流与会话拒绝。加 `-InteractiveReset` 会等待人工R键复位。历史native-05的粗方向检查虽通过，但舵向仍振荡；不要将它作为稳定验收结果，后续固定步长证据见对应feature记录。

尚未验证该原节点在RMUC波浪路上的完整路线，也没有完成实车参数标定、原生Ubuntu或完整sentry_ws/导航/感知闭环。此前波浪路通过属于UE本地手动控制器，不能直接外推到这个原LK控制器。

Ubuntu使用同一份源码、消息包和YAML，构建后 `Start-ManualVehicle.sh --ros-motor-control`。Linux不需要DLL部署；依赖需提前准备，仍须原生验证。
