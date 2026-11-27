# RMUC 3v3 点云与地图定位漂移

复用 11 月 27 日原 3v3 阵容，红蓝 S7 在各自 DDS 域录包。蓝 3 号保留原手动席，离屏验收时静置。车辆真值用于离线误差对照。

## 根因与修复

- 最早的偏差在 Point-LIO 原始输出。原 200 Hz 瞬时陀螺把一部分 1 ms 物理子步振荡采成持续旋转。蓝方与红方一秒积分姿态误差最大分别达到 166° 和 145°。新版 IMU 在同一 5 ms 窗口读取原生测点速度差和四元数旋转差。
- 原始扫描维持每帧 5,000 个槽、10 Hz。红方出现异常时有效回波中位数约 2,961 个，蓝方异常附近低至 899 个。缩小 Point-LIO 体素、保留更多点的同数据回放仍复现漂移。
- IMU 单项修复整局中，蓝方 AMCL 一度偏离真值 2.57 m，而 Point-LIO 保持稳定。使用相同的配准点云与同时间戳的里程计复建扫描。原 AMCL 参数最大误差 2.44 m，仅关闭随机恢复 2.55 m，仅收紧运动噪声 0.60 m。最终只在仿真比赛将运动噪声 alpha1 到 alpha5 设为 0.002。 [同录包回放](../../artifacts/verification/rmuc-cloud-drift-20261127/analysis/amcl-variants.json)

## Windows 原生整局

| 指标 | 红方 | 蓝方 |
| --- | ---: | ---: |
| Point-LIO 平面误差 P95 | 0.080 m | 0.070 m |
| Point-LIO 最大平面误差 | 0.470 m | 0.570 m |
| AMCL 平面误差 P95 | 0.255 m | 0.151 m |
| AMCL 最大平面误差 | 0.475 m | 0.643 m |
| 实际行驶轨迹长度 | 327.7 m | 396.8 m |
| 规划起点越界 | 0 次 | 0 次 |
| 已回读的原始点云 | 4200 帧 | 4200 帧 |

UE 与 ROS 桥编译成功，IMU、点云协议与 Point-LIO 单位校验合计 22 项通过。资产噪声与限幅检查有 3 项既有失败，用备份协议也能复现。最终局运行至裁判结算，双边正常封包后清理离屏测试进程。原始复现覆盖前约 100 s，红方录制进程提前退出后的落盘录包已修复索引。另保留 IMU 单项修复的整局，及合并试验的中途录包。

[红方录包](../../artifacts/verification/rmuc-cloud-drift-20261127/verified-motion-noise/red/rosbag/metadata.yaml) · [蓝方录包](../../artifacts/verification/rmuc-cloud-drift-20261127/verified-motion-noise/blue/rosbag/metadata.yaml) · [回读与版本身份](../../artifacts/verification/rmuc-cloud-drift-20261127/analysis/verification.json) · [点云定位误差时序](../../artifacts/verification/rmuc-cloud-drift-20261127/analysis/position-error.png)
