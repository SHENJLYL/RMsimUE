# 底盘原始 IMU 验收

日期：2026-11-16。范围：Windows UE5.8.4 / 原生Humble，未标定物理底盘六轴输入。方案native-imu-design.md由既有持续实现授权推进，未声称用户逐篇审阅。

## 1. 接口契约核对

- 车辆sensors/imu.json和schema为1–4个base_link刚性安装传感器，明确SI单位、频率、偏置/白噪声与seed。非法负噪声配置实际拒绝启动。
- RosImuComponent产生带seq/stamp/integration_dt的实际样本；protocol.py校验，imu.py发布sensor_msgs/Imu及base→sensor静态TF。
- orientation_covariance[0]=-1；噪声协方差为std平方，全零表示未知。不输出AHRS或世界定位TF。配对90°安装通道误差约1.3e-14，安装与数据轴一致。

## 2. 行为与决策核对

- 实际点速度包含20cm偏置的转动效应；传感器窗口速度差减真实世界重力，实际角速度按轴向矢量转换。
- 世界重力9.8m/s²，不拿发射演示参数9.81替换。静止/坡上按真实车体姿态投影，坡面角不能当车体角。
- 每通道32样本，采样独立于状态/图像；物理帧不足仅计missed_periods，无伪造中间样本。慢读者限制队列并计drop；断开不积攒重放数据。
- R/显式ResetContactPose清导数历史；首次观测只初始化。无旧epoch冲击或积压消息。
- 挂载点rg逐项核对：GameMode开关/固定步长、RosBridge初始化/采样/队列/复位、VehicleRig真实状态和运动generation、Python验证/发布、Win/Linux包装及开发夹具，与方案一致。可移除可选挂载及IMU模块，已有速度/电流/瞄准协议不依赖IMU。
- 默认手动不开IMU、不启用固定1ms。通用固定步长显式选择；LK仍要求1ms。自由落体/坡面夹具只改真实刚体起始位置，不改传感器读数。

## 3. 验收场景核对

统一机器证据：artifacts/sensors/imu/release-summary.json。其记录模块/源码/输入/报告哈希、安装Python一致性和实测操控时序。

| 场景 | 原生证据 | 结果 |
|---|---|---|
| 静止、转向、偏置安装、坐标、噪声、采样、TF、慢读者、重连、真实R | native-04-release/ros | 12项通过 |
| 真实自由落体/历史清理 | native-freefall-01/ros | 3项；比力均值约0.266m/s²，保留真实阻尼/悬挂力 |
| 实际坡上姿态与重力投影 | native-incline-02/ros | 1项；车体24.49°，不误用坡面20° |
| 100Hz物理帧/200Hz请求 | native-frame-limited-01/ros | 11项；441真实样本+440未采周期，无插值 |
| 非法配置启动 | invalid-profile-02 | 明确拒绝日志、无ready标记、进程终止；实际退出0单列 |
| 既有普通ROS、瞄准/射击、手动 | regression-velocity/aim/manual | 11、20、19项及12PNG通过 |

16项离线协议测试、6个PowerShell脚本和Bash语法通过。Python源码与已安装模块逐文件相同。UE构建Result: Succeeded；测试包装退出0，夹具进程在结束后由测试器主动关闭。GUI已观察真实R使车辆回出生点，接收端验证新epoch样本。

最终默认RMUC手动训练窗口已恢复并真实R复位，观察232–241FPS、车速0、小陀螺关闭、弹量100/热量0，未启用ROS/IMU。详细UI记录artifacts/sensors/imu/gui-observation.json。

第一次静止/坡面测试错误使用理想水平/坡面角，已修正独立测试期望并保留失败记录，未放宽门限或改采样值。无完整OS延迟测量；最新手动引擎内舵向99.5ms、行进改向251.6ms。

## 4. 术语一致性

源码/指南保持raw6axis、specific_force、interval_average与orientation unavailable语义。未将其写为姿态估计、AHRS或实车校准。Point-LIO覆盖仅改变输入单位参数，不冒称定位闭环。

## 5. 架构归并

ARCHITECTURE.md已写入sampler→批传输→ROS发布、实际时间/丢样纪律和raw边界；非仅附链接。指南imu-runtime.md及sidecar README包含启动和消费者语义。

## 6. Requirement 回写

raw-chassis-imu.md为current能力；realistic-sensors.md保留完整愿景draft，仅注明已实现子集。

## 7. Roadmap 回写

新增独立native-imu为done，同步items及主表；完整ros2-sensor-control-bridge仍in-progress，single-sentry-closed-loop/native-sentry-profile未标完成。

## 8. 长期记录

按用户持续记录授权更新attention、current和当日C025变更。保留真实重力、窗口平均采样、姿态未知、物理帧不足及Ubuntu证据边界，避免下次重复误判。

## 9. 遗留

LiDAR/Point-LIO/完整sentry_ws、AHRS/云台IMU、实测惯量与传感器带宽/温漂、正式比赛/联机和Ubuntu原生验证仍未完成。固定1ms约0.3倍墙钟，不能拿HUD FPS当实时倍率。用户主观驾驶滞后仍持续接受反馈，不因本报告通过而关闭反馈渠道。
