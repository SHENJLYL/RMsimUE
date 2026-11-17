---
doc_type: issue-analysis
issue: 2026-11-16-gyro-precision-ripple
status: confirmed
authorization: standing_user_request_to_implement_and_verify
tags: [source-geometry, tire-contact, swerve]
---

# 原因：轮胎碰撞体填平了源胎冠

只读加载用户aim_sentry.blend并除去WT展示倍率2：原轮胎中央约42.5mm半径，两侧轴向±22mm处仅约37.944mm。现有analytic_32_sided_cylinder却将全宽44mm都填到42.5mm，添加了源模型没有的侧缘接触。持续自转时这些接触导致轮地滑移和舵向阻力；不是未到达的键盘消息。

## 区分与证据

- trace-baseline采集实际2–6秒窗口578帧，速度0.2815–0.4410m/s；目标0.35m/s，速度误差RMS0.04261m/s，世界速度向量误差RMS0.06205m/s。轮速跟踪误差约0.01–0.02m/s，但舵角约5度且存在滑移。
- 舵向刚度1000的隔离对照将舵角降至约0.6–0.9度，但速度误差RMS仍0.03537，2秒仍0.4209m/s。该候选撤回，恢复80；没有用高增益掩盖几何问题。
- 源胎冠凸包(364–378顶点/轮)候选保持原控制/摩擦/力矩参数；5项通过，2秒实际0.3509m/s。相同2–6秒窗口速度0.3242–0.3681m/s，速度误差RMS0.01031、世界向量RMS0.01678m/s，舵角RMS约0.1–0.2度。
- 底盘位置差分与原点速度查询的RMS差约0.002m/s，排除用质心/原点混淆伪造波动。悬挂滑块相对车体角偏差约0.01度，排除悬挂角锁失效。
- 所有数据保留于artifacts/control/gyro-ripple-20261116；原尺寸/可见FBX几何不变，源blend SHA与既有导出来源一致。

## 定点实现

导出器从归一化原轮胎网格提取凸包；manifest和runtime rig携带ROS局部米制顶点。导入、prepare、bind、ensure以独立几何哈希判别碰撞缓存；源几何变化时旧圆柱不能继续被复用。旧未提供顶点的车辆仍有显式圆柱回退。凸包只作为独立碰撞资产，不修改源文件或可见模型。

新PrecisionTrace保留原2秒门限，并增加完整4秒窗口的峰值速度误差<0.03m/s、世界速度向量RMS<0.025m/s、实际采样覆盖/间隔门限。不能用一次瞬间达标替代稳定性。

## 诊断单位修正

UE5.8本地源码JointConstraintProxy.cpp把GetAngularImpulse写入名为Torque的输出，PBDJointContainerSolver.cpp把位置级lambda除以dt后输出冲量。故GetConstraintForce在本版本返回末子步冲量。新字段steering_solver_angular_impulse_nms正确标注；早期trace-baseline torque_nm仅为误名，分析按Nms解释，未把它用于标定电机力矩。旧spring/axle调试字段保留名字但contact状态增加明确单位说明。

## 验证状态

正式Import-VehicleAssets -ContactCollision已原生退出0，四个旧圆柱缓存已因几何哈希变化被替换。导出helper对称凸包表面差最大约2.6e-8m；最初按最近顶点比较误判共面顶点选择差异，失败脚本保留，改用严格表面距离验证。原生全套回归仍在进行，尚不标issue最终完成。

## 后续坡面根因：静止岛被逐帧唤醒

源胎冠去掉虚假接触后，坡面回归进一步暴露停车微动。最终定位VehicleDrive在停车状态仍每帧WakeAllRigidBodies，身体刚进入休眠就被再次唤醒，反复开始接触沉降。修改为仅运动/自转请求及自由滚动诊断主动唤醒，保持引擎自然碰撞/外力唤醒。

保持原PI/全部物理参数、关闭位置保持的隔离用例traction-rest-wake8项通过，原停车漂移及速度门限未改。已彻底移除位置保持候选；最终新增外部10Ns冲量唤醒及恢复，final-traction10项通过，下坡松键5.356cm停住，外力使车移动0.717cm/峰速19.718cm/s后正常停车。

夹具准备条件同时收紧：必须连续0.5秒原点速度<0.5cm/s才开始保持测量（原为2秒后速度<5cm/s）。原保持3秒位移<1cm/末速<0.5cm/s保留，新增真实下坡0.35m/s行驶后松键及外力检查。先前Ki/弹簧/阻尼调参未通过且全部恢复；最终profile与本轮before逐字一致，源LK控制器未修改。
