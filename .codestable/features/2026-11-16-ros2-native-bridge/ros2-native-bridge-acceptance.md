---
doc_type: feature-acceptance
feature: 2026-11-16-ros2-native-bridge
status: accepted-scoped
summary: Native Windows ROS2 and UE physical velocity/camera/clock bridge verified; full sentry_ws and Ubuntu remain unverified.
tags: [ros2, unreal, native, control, sensors]
last_reviewed: 2026-11-16
user_review: not_requested_under_standing_authorization
---

# 验收范围

按既有自主实现授权完成此原生通信切片。原长期goal保持active。真实Windows进程运行，不使用WSL、假rclpy或合成图像。UE5.8.4，RoboStack Humble Python3.14.10/FastDDS；test domain173、localhost。源资产和用户控制器保持只读。

## 通过证据

所有目录在`artifacts/ros2/`，统一入口`native-release-summary.json`。

| 门禁 | 证据 | 结果 |
|---|---|---|
| 原生安装包/CLI | final-cli-demo-02/ros | 11项；实际ros2 run bridge与drive_demo，图像1280×720；前进1.047m、左移0.490m，独立demo前进0.352m；停发后测量窗漂移1.14mm，重连无回放 |
| TCP/物理/图像 | final-tcp-contract/tcp | 19项；106张真实RGB；错误会话/坐标/时间/序号/超限、重复键/非有限值/超长、分片、断线与慢读者背压 |
| 手动只读模式 | final-observe/tcp | 4项；接收图像、地标投影、60度传感器/30度观察镜头分离，拒绝远程驱动 |
| 游戏线程故障 | final-stall/tcp | 1项；仅开发测试旗标注入400ms停顿，旧会话关闭、排队输入不重放，漂移1.23mm |
| 真GUI复位 | ros-topics-03-visible-reset/ros | R键使epoch0→1、clock回跳、accepted_seq=-1；复位后离轴装甲49/49采样点为红色，投影匹配 |
| 手动影响面 | manual-regression/default | 19项/12PNG，退出0；行进90度改向251.1ms、静止四轮到80度101.7ms，G/Z/X/松键/瞄准与方向回归通过 |
| 离线协议 | rmsim_bridge/test | 12项真实loopback/协议检查通过；不替代原生ROS证据 |

`ros-topics-03-visible-reset`的原始自动检查名含`observer_zoom`，但GUI Shift未观察到切换；`ui-observation.json`明确标为未证实。该检查仅计为复位后固定光学投影通过，不据此声称实际缩放按键验收。60/30度分离有独立运行状态与离轴地标证据。

UE测试包装器完成客户端检查后终止自己的游戏进程，不把终止记成引擎正常退出0。手动回归自己的退出0独立保存。旧失败目录保留：native-bridge-01读取语义、native-bridge-03-backpressure重连竞态/过严测试假设、ros-topics-02-reset隐藏窗口、final-cli-demo旧安装包缺demo；对应最终门禁通过后才关闭问题。

## 实现及修正

- UE组件绑定loopback单连接；有限帧/缓冲、严格扁平JSON、明确会话/epoch/序号/SI坐标、250ms单调时间租约；VehicleRig在PrePhysics再次校验。GameMode在PostPhysics读取真实Chaos solver time。
- Recv在UE包装器中would-block为成功/0bytes，EOF为失败，不能使用遗留last-error判断。先处理旧连接EOF，再接受新连接，修复立即重连丢一次的问题。
- ROS侧使用array('B')填充Image.data，绕过数百万次Python逐元素验证。实际1280×720赋值基准从约120.7ms降到0.98ms；只是赋值局部耗时，不能充当全链路延迟指标。
- 独立固定视场摄像头按需捕获，图像、Info、光学TF同刻；真值仅评测topic，不发布定位TF。无ROS旗标不打开监听或额外采集。
- `Start-PreparedVehicle`沿用单实例保护并支持ROS旗标；原生安装加入tf2_msgs/ros2run，colcon包新增有界drive_demo。

## 明确未完成

完整sentry_ws/Point-LIO、原LK encoder/current/PID与功率反馈、远程云台和开火、LiDAR/IMU、实车摩擦/动力学/光学标定、正式裁判和联机、原生Ubuntu运行均未验收。相机同步GPU回读仍有帧率成本；10Hz为名义采样设置。训练时钟尚未统一到Chaos clock。此切片提供速度级接入点，不能替代这些后续工作。

用户手感仍以最新窗口反馈为准；引擎内251ms响应不等于键盘到屏幕的端到端延迟。最终手动窗口观察另记在汇总文件。
