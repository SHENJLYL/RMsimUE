# 2026-11-18 AIM 手动 5 m/s 与 C/Shift 相机控制修正

## 适用范围
Windows UE5.8.4 原生 `D:/simulator` 当前工作分支的 AIM Sentry 车辆包、手动输入及共享控制探针。保留独立 ROS/Nav2 外部速度整形、轮地真实接触、原生 `sentry_ws` 导航速度来源。

## 运行参数
`assets/vehicles/aim_sentry/drive-profile.json`：
- 前进 3 → **5 m/s**；后退 1.5 → 2.5 m/s；横移 2 → 4 m/s。
- 车轮上限 4 → 6.5 m/s；纵向加速 0.8 → 2 m/s²，松键减速 3 → 4 m/s²，Space 制动 6 → 7 m/s²。
- 加速度 jerk 4 → 6 m/s³；横向加速 10 → 8 m/s²；方向 jerk 100 → 70 m/s³。
- 轮胎/悬挂几何、20 kg 车体质量、2000 N/m 与 140 N·s/m 弹簧阻尼、转向伺服与电机扭矩保持原值。数值是可调的模拟样例，不替代实车标定。

## 输入语义
- 接触式舵轮车的 WASD 世界运动基向量取实际云台的枪口/aim capture 前向在水平面上的投影，坐标变换按 ROS 左正。C 键只改变 orbit chase camera 的 yaw/pitch，不改变云台世界目标或底盘运动方向。
- Shift 进入瞄准切换到枪口镜头；退出 Shift 时只进行一次以当前世界目标为中心的 chase 镜头投影，同时解除滞留的 scope world aim，随后由正常 chase 镜头实时重新选定目标，避免持续追踪已退出的瞄准框。
- HUD 橙色枪口目标框仅在 sight 模式展示；C 观察和正常 chase 不显示。原中央白色准星仍保留，且始终不参与速度基向量。
- 外部 ROS 底盘/云台接管时保持既有逻辑；不在仿真层重写 Nav2 速度上限与源控制器。

## 变更
`ImportLabGameMode.cpp/.h`、`ManualControlProbe.cpp`、`GyroProbe.cpp`、`Test-ManualControls.ps1`，均有原始备份：
`artifacts/control/manual-5m-camera-20261118/baseline`。

## 已有实测（Windows 原生）
- UE 构建：`Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8`，exit 0。
- `manual-controls-01`：18/18 shared manual-control checks 通过、10 张 PNG 解码通过，exit 0。
- `camera-speed-native-02`：6/6 检查通过、8 张 PNG 解码通过，exit 0。C 观察偏转 90.55° 时车体指令与云台投影 dot=1.000000；Shift 退出后旧 aim sticky=false、橙色目标框隐藏、追踪镜头 yaw 漂移 0°。
- 原生 Chaos 全油门 3 秒：指令 5.0000、平滑命令 5.0000、实测 **4.9511 m/s**，位置 x=685.49 cm；之后 1.8 秒制动至 0.0000 m/s，最终 x=954.39 cm。
- 上述会话实际测量的是 flat import_lab 场地与用户提供的 AIM 接触车，无 OS 硬件键扫描断言；不同 terrain、原生 Ubuntu 与高速度自动导航仍需各自专项验证。失败/早期构建诊断原样保留。

## 验证入口
`powershell -NoProfile -File tools/unreal/Test-ManualControls.ps1 -EngineRoot E:/simulator-tools/UE_5.8 -EvidenceDirectory <new-dir> -Gyro -CameraDrive`

## 完整回归与测试通道
- `gyro-regression-01` 的固定场地旧测试最初在 gyro 阶段撞上 `import_lab` 固定障碍箱，保留失败证据；新加速度下从上一个横移终点启动导致该障碍截断了连续旋转积分。
- `GyroProbe.cpp` 仅调整对应连续 gyro 实验的**起始工况**为已知无障碍出生点，原测试 6 秒平移与转角验收阈值、真实 Chaos 接触及前后其他控制检查不变。
- `gyro-regression-02` 在相同原 `import_lab` 场地完成 **19/19** 原生 gyro/手动路径回归，**12 PNG** 解码通过，exit 0；全程 gyro 前进 337.752 cm、累计自转 11.267 rad，侧向偏移 12.060 cm；全部舵向达到 80° 用时 89.9 ms，运动中 90° 改向实际速度响应 161.4 ms；Ctrl 慢速 0.3458 m/s（目标 0.35 m/s）。
- Windows 原生 probes 操作的是共享输入路径，OS 级 C/Shift 物理键观察及更复杂地形 5m/s 路线保留为手动整体验收。

- 最终同模块哈希 `31A5E8DEEF6F902DE71AC566271D5AB945CFB54CDA43C0C4A49AA26553CED07C` 下，`camera-speed-final-03` 再次 **6/6 + 8 PNG**，exit 0；3s 末实测 **5.0261 m/s**（目标/整形指令均 5.0000 m/s），Space 后 **0.0000 m/s**。两次实际车速读数 4.9511 与 5.0261 m/s，反映物理轮地闭环的小幅动态偏差。
