---
doc_type: issue-fix-note
issue: 2026-11-16-gyro-precision-ripple
status: native_verified_gui_input_pending
summary: Source tyre crown and demand-driven wake remove steady gyro ripple and parked chatter without changing control or physical parameters.
tags: [wheel-contact, gyro, sleep, native-evidence]
user_review: subjective_handfeel_pending
---

# 修复结果

两处根因已分别获得原生证据。

1. 旧44mm全宽等半径圆柱填平了原轮胎的弧形胎冠，在两侧虚增约4.6mm接触。现在从只读原模型提取独立凸包，视觉FBX与尺寸不变；导出、导入、绑定、重建以源几何哈希识别缓存，旧圆柱不会被错误保留。
2. VehicleDrive在零指令停车时仍逐帧唤醒刚体，反复触发接触沉降。现在仅运动/自转请求或自由滚动诊断主动唤醒，停车保持Chaos自然休眠；新指令和外部物理冲量仍能唤醒车辆。

本轮最终控制profile与before逐字一致，质量/摩擦/弹簧/阻尼/PI未调整。原LK源码12文件与原blend哈希一致。曾尝试的增益、阻尼、子步与位置保持方案全部撤回，候选证据保留；没有写车体位姿/速度、锁关节或扩大休眠阈值。

## 实测

- 0.35m/s、4rad/s持续自转：完整约4秒窗口，速度误差RMS从0.04261降至0.01016m/s；最终峰值0.02531m/s，世界速度向量RMS0.01673m/s。舵角误差由约5度降至约0.1–0.2度（源几何对照）。
- 20度坡：轮驱动关闭，顺坡0.802秒滚动71.189cm，横坡仅0.193cm。原保持3秒门限通过；实际0.35m/s下坡后松键5.356cm停住，末速0。
- 外部10Ns冲量让停车车体实际移动0.717cm、峰速19.718cm/s，随后正常停车；不是固定车体位置。
- 最终版本0.6m/s普通改向139.5ms，2m/s普通368.4ms，2m/s+4rad/s自转380.0ms；静止四轮到80度96.3ms。均为引擎内实际物理响应，不是OS到显示器时延。

## 当前同版本回归

| 用例 | 检查数 | PNG |
|---|---:|---:|
| final-traction | 10 | 4 |
| final-spin | 6 | 4 |
| final-controls/default | 19 | 12 |
| final-waves 红蓝往返 | 10 | 12 |
| final-integration 驾驶/训练/墙体/复位/跌落恢复 | 10 | 12 |
| final-fast | 5 | 4 |
| final-ros | 11 | — |
| final-lk 原C++电流控制器 | 21 | — |

合计92项、48张PNG。正式资产导入/重建退出0；缓存检查确认无需重建；源凸包导出helper表面误差最大约2.6e-8m；标准库验证测试覆盖非法顶点和几何缓存身份变化。

证据：artifacts/control/gyro-ripple-20261116/release-summary.json。手动用例含模块/profile/rig一致哈希；ROS/LK本次属于同一顺序冻结模块运行，但未逐次在启动时保存哈希，汇总明确该范围。Test-RosBridge已为未来运行补充启动快照，没有回填伪造启动证据。

## 诊断与夹具

原两秒慢行门限保留，PrecisionTrace另加完整四秒连续采样检查。坡面保持的准备条件收紧为连续0.5秒原点速度<0.5cm/s，再应用原位移<1cm、末速<0.5cm/s门限；新增真实下坡松键及外力唤醒检查。先前失败结果没有删除或改成通过。

UE5.8的GetConstraintForce输出实际为末子步冲量。新steering_solver_angular_impulse_nms正确标注；旧spring/axle调试字段保留名称并添加语义说明，早期trace-baseline误名torque_nm按Nms分析。

## 范围

原生Windows验证；Ubuntu仅同源离线准备。凸包未模拟橡胶形变/独立小滚子，动力学未实车标定。完整sentry_ws、官方裁判、联机与原WT完整地图仍未完成；既有Point-LIO/Nav2需在该新几何上继续联合回归。未自动提交Git。
