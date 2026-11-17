---
doc_type: feature-acceptance
feature: 2026-11-16-sentry-lk-motor-loop
status: accepted-scoped
summary: Supplied native LK ROS controller drives physical steering/wheel currents with stable fixed-step feedback and failure handling on Windows.
tags: [ros2, native, chassis, physics]
last_reviewed: 2026-11-16
user_review: not_requested_under_standing_authorization
---

# 验收结论

原LK控制器最小电机闭环已通过Windows原生验收。不是重写一个近似Python控制器：实际运行用户C++包中的舵向角度/速度PID、轮速PID和PowerLimiter，工作副本的兼容/时序补丁单独保存。原12源文件哈希未变。整个长期goal仍active，完整sentry_ws、联网、真实硬件校准和Ubuntu尚未完成。

## 可复查证据

统一入口 `artifacts/control/lk-motor-loop/release-summary.json`，包含最终UE模块、原节点EXE、环境锁、源码/补丁和73个局部运行DLL哈希。下表均使用最终模块，门禁包装器退出0；其UE进程由测试脚本在收尾终止，不能将包装器0误称为UE正常退出0。手动probe另有自己的进程退出0。

| 门禁 | 结果 | 证据目录 |
|---|---|---|
| 原LK电机闭环 | 22项通过；原节点和原消息、功率限制、前/横/旋转、舵向收敛、零速/指令停发/节点停止/桥断线、会话/非法电流/模式拒绝、真实R、安装CLI | native-10-release/lk |
| 原生速度ROS回归 | 11项通过，1280×720图像/内参/光学TF/时钟与运动、CLI、重连 | regression-ros/ros |
| 原速度TCP合同 | 19项/107张真实RGB通过，含分片、背压、无效与过期输入 | regression-tcp/tcp |
| 普通手动/小陀螺 | 19项/12PNG通过；静止舵轮到80度96.5ms、行进90度改向256.7ms | regression-manual/default |
| PID采样修正 | 原1ms离散结果及变采样积分/微分断言，原生MSVC通过 | tools/environment/Test-SentryPid.cmd、pid/ |
| 协议与离线语法 | 13项unittest；8个PowerShell解析、Linux入口Bash语法通过 | rmsim_bridge/test、工具输出 |

## 平地样例实测

- 0.4m/s目标、3秒仿真时间：前进1.16517m，横偏0.000197m。稳态四舵轮最大角误差0.13184度，稳态舵速最大2deg/s，无此前持续振荡。
- 0.3m/s左移：左移0.88336m，转舵过渡引入前向位移0.03338m。该数值不是全路线误差上界。
- 原节点停止后独立测量窗漂移0.000161m；原消息CLI演示实际前进0.15846m。
- 回退制动与当前电机模型的最大电流一致，轮轴上限2.255859375Nm；外部电流有效时禁用UE原位置/速度伺服，轴扭矩和反作用力均施加到真实刚体。
- 实际GUI R使epoch0→1，回出生点、清旧电流；GUI约275–280FPS，仿真约0.28–0.30倍实时。普通RMUC手动窗口已重新打开并R复位，观察212–240FPS。

这些是未标定模型的引擎内结果，不是实车误差或键鼠端到端延迟测量。固定1ms为仿真步长，不代表墙钟1kHz硬实时。

## 必要适配

1. 完整custom_msgs固定EcatV2_Master 7c1bd8a3c7f47cc8505470640f8dd7baf5698d76，原Git blob字节核对；原用户控制器独立pristine，补丁在third_party/patches。
2. 原LK增加输入/反馈有效期、有限值检查、online保护和int32/正模编码器处理。原增益与功率模型未降低来掩盖不稳定。
3. 10ms桥轮询与可变物理步长曾造成持续舵向振荡。电机模式使用1ms I/O、最新值QoS、固定1ms物理步长；原节点按配对反馈计算，并按真实仿真采样时间差缩放PID积分/微分。硬件默认feedback_driven=false，比例1保持原离散公式。
4. Windows原生DLL加载失败已定位到继承的MSIX包身份；局部部署完整DLL闭包与动态ROS类型支持插件，Python限定注册两个包的DLL目录。没有改系统安全设置、全局PATH或绕过加载检查。依据：[Microsoft DLL search order](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order)。
5. source/command消息没有Header，适配器用新鲜高层意图、反馈会话及两路电流有效期共同控制。速度与电流模式互斥，复位/断线不重放旧电流。

历史失败不删除：native-01/02/03为DLL加载阶段；native-04/05暴露反馈时序和振荡，05的粗门禁通过不能当稳定验收；native-07/09为测试对重连就绪的错误假设。已补真实状态握手与就绪条件，最终native-10全部通过。

## 边界与后续

原LK模式目前约0.3倍实时，尚未实现实时解算与渲染完全分离。轮轴常数来自源码0.14Nm/A，舵轴0.14Nm/A仍为演示假设；没有电感、母线/反电势、温度或实测摩擦/惯量标定。PowerLimiter执行证据不等于实际电池功率或官方裁判功率校准。

安全停车由仿真伺服接管并受同一最大扭矩约束；current_active=false时反馈current只表示外部电流通道为0，不是停车伺服电流测量。原LK的RMUC波浪路线、完整sentry_ws/感知/导航、ROS云台开火、联机和Ubuntu原生仍未验收。此前手动波浪通过不能直接外推到原LK模式。

使用：[原LK接入指南](../../../docs/guides/sentry-lk-motor-loop.md)。默认手动入口保持实时本地控制，原节点模式须显式RosMotorControl及MotorControl桥参数。
