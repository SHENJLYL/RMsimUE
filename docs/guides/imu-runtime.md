# 物理底盘 IMU

IMU读实际Chaos底盘在安装点的速度与角速度，输出`/机器人ID/传感器ID/data_raw`。加速度是传感器系中的比力，单位m/s²；角速度为右手系rad/s。静止读数随安装姿态投影重力，不能一律要求X/Y为0。当前UE世界重力实测为9.8m/s²，发射演示自己的9.81参数不用于IMU。

这是未标定的原始六轴模型。没有AHRS/磁力计或姿态估计：orientation_covariance[0]=-1。没有世界位置或姿态真值定位TF。固定偏置和逐次高斯噪声可配置，不包含温漂、量程饱和、随机游走或实际硬件带宽标定。

## 启动

PowerShell从`D:\simulator`执行：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:\simulator-tools\UE_5.8' -RosImu -SimStepSeconds 0.001
# 另一终端：
& .\tools\environment\Start-RosBridge.ps1
```

加`-RosControl`可接受已有ROS底盘速度指令；`-RosAimControl -TrainingMode`可组合高层瞄准/发射。原LK电流模式固定1ms步长，与传感器共享同一物理时钟。未指定固定步长时保留原实时路径，传感器可能受物理帧率限制。

不开`-RosImu`不创建IMU采样器。默认配置从所选车辆包的`sensors/imu.json`读取，也可用`-ImuProfile '完整路径'`指定文件。原源模型目录不需要修改。

## 安装与测量

配置结构见`assets/vehicles/aim_sentry/sensors/imu.json`及`schemas/imu-runtime-v1.schema.json`，最多4个唯一ID传感器。当前支持相对发布的`base_link`刚性安装；不是任意URDF活动关节的传感器实现。origin.xyz为米、rpy为弧度，默认位置[0,0,0.15]是演示安装点。

默认id=imu对应`/aim_sentry/imu/data_raw`与`aim_sentry/imu_link`。安装变换发布到`/tf_static`，只有base_link→sensor，没有map/world→base。更换安装配置需要重启仿真，消费者也应重置相关缓存。

点速度包含转动的偏置安装效应。比力由连续真实采样窗口的点速度差减去世界重力，再变换到当前传感器系；属于该窗口的平均加速度。角速度读取当前刚体状态，按轴向矢量进行UE→ROS反射转换，不能像普通向量一样只翻Y。第一帧只建立历史，不输出伪造加速度。

## 频率、丢样与复位

采样按物理时钟推进，与相机10Hz、状态传输50Hz分开；传输按批次发送真实采样。默认200Hz，若物理步长为1ms，可采到5ms间隔的数据。物理步长若为10ms，则最多每帧一个真实样本，未采到的周期记录为missed_periods，不插值制造200Hz。

每个传感器最多缓存32样本，满时丢最旧样本并累计dropped_samples。无连接时不积攒待重放数据。诊断`/rmsim/aim_sentry/state`含实际采样计数、未采周期、缓冲丢样、历史重置和非法数据计数；DDS最佳努力传输本身仍可能丢消息，消费者应检查时间间隔。

R清空epoch、采样历史及待发队列。显式物理位姿复位也会触发历史清理，避免用复位前后的速度构造不存在的冲击。开发自由落体/坡面夹具仅放置真实刚体，从不向采样器写入预期读数。

噪声协方差对角线使用配置标准差的平方；全零表示未知协方差，不能当作真实零误差保证。固定偏置不当作随机噪声协方差。相同seed和同一采样序列可复现模型噪声，R重新开始该序列。

## sentry / Point-LIO 适配

固定源码的mid360.yaml默认`mapping.acc_norm=1.0`表示g单位。本接口使用SI，预备覆盖文件`adapters/point_lio_imu.yaml`设置9.81，使源码中乘`G_m_s2/acc_norm`不会额外放大数据。它也指定200Hz的0.005秒名义间隔和本机IMU topic；实际时间以header.stamp为准。

该文件仅为IMU侧适配，不包含LiDAR、外参或Point-LIO原生运行验证。当前世界重力与源算法初始化参数也需在完整接入时核对。原云台控制器需要的姿态估计不能直接用本data_raw替代。

参考：[ROS IMU消息](https://github.com/ros2/common_interfaces/blob/humble/sensor_msgs/msg/Imu.msg)、[REP145](https://github.com/ros-infrastructure/rep/blob/master/rep-0145.rst)、[固定Point-LIO配置](https://github.com/unnc-aim/26UL_Sentry_point_lio/blob/3099b9dd65345f6ba2f2c56404501f96610d2a15/config/mid360.yaml)。

Ubuntu同源入口使用`--ros-imu --sim-step-seconds 0.001`，仍只有离线准备，尚无Ubuntu原生验收。

Windows验收记录为`artifacts/sensors/imu/release-summary.json`：最终运动/压力/重连/真实R 12项，自由落体3项、坡面1项、物理帧不足11项和非法配置拒绝；普通ROS、瞄准/射击、手动回归均通过。原生运行不代表实车标定。
