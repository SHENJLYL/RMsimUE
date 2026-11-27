# RMUC 双哨兵 MID360 初始化

六车场地已完成组装，UE 报错发生在第一个 ROS 席的 MID360。双席降载将物理名义速率改为 50,000 后送入按 200,000 发射率设计的 v3 校验，触发初始化退出。 [原启动日志](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/reported-ue.log)

名义发射率继续使用车辆 MID360 v3 配置。双席通过单独的四通道组采样步长降低实际碰撞查询和点云数量，并携带原始发射偏移和采样步长。ROS 桥按该步长校验所选点位，单席继续使用步长 1。两种情况在 10 Hz 发布配置下分别输出 5,000 和 20,000 个点位每帧，未命中的所选点位以原格式保留。采样移除数单独计量。

复用用户报告中的蓝方 3 号步兵视角、3v3 编队和备赛设置。Windows UE 双席约 23 秒进入备赛并启动两路桥，约 27 秒在红蓝隔离 ROS 域完成点云探针。双席与单席各探针均收到 12 帧连续点云，11 个间隔均为仿真时间 100 ms，四通道时间戳和非空回波通过。 [双席启动](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/dual-preparation/summary.json) · [红方点云](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/dual-preparation/red-lidar.json) · [蓝方点云](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/dual-preparation/blue-lidar.json) · [单席全量点云](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/single-preparation/red-lidar.json)

MSVC 组采样模型测试和 13 项协议检查通过。额外的传感器配置套件有三项涉及现有 IMU 限幅、噪声和再生成的基线失败，测试及其配置输入与当前 HEAD 一致。 [C++ 模型测试](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/model-tests.log) · [协议回归](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/wire-tests.log) · [IMU 基线日志](../../artifacts/verification/rmuc-dual-mid360-startup-20261127/profile-baseline-tests.log)
