# 原生迭代

- 源输入12文件只读，已复制pristine与工作副本；完整custom_msgs固定EcatV2_Master 7c1bd8a3c7f47cc8505470640f8dd7baf5698d76，消息单位按原任务文档核对。
- C++原生构建成功；MSVC宏/缺失包依赖修正，LK添加新鲜度/有限值保护与int32编码器目标修正。原PID增益和功率模型未调小。
- native-01：Python自定义类型支持DLL加载失败；限定进程注册两个包bin目录后publisher确实构造成功。
- native-02/03：C++程序继承Codex MSIX包身份，GetPackageFullName直接返回包名；调试事件证实未进入main，普通PATH不参与该加载顺序。按Microsoft DLL search order本地部署DLL闭包（含动态typesupport companions）后原节点成功启动。73 DLL约22.4MB；只复制非系统DLL，不改系统PATH/安全设置。
- native-04：原节点、UE真实电流反馈已通；10ms桥轮询使舵向强烈振荡，功率门禁失败。
- native-05-fast-io：电机桥1ms轮询、每物理帧反馈、ROS depth1后8项通过；低功率最大电流6counts/漂移0.04mm，正常前进0.655m但横偏0.199m，左移0.424m但前偏0.219m；持续舵向振荡峰值2922deg/s。此结果只说明通路/粗方向和失联制动，不是稳定闭环验收。
- 下一步已明确：电机模式固定仿真步长+反馈驱动PID计算，增加稳定性门禁。普通手动当前窗口因模块构建已关闭，收尾须恢复；不能引用上一阶段窗口留开状态。
- native-06-fixed-step：固定1ms物理时间、原节点配对反馈驱动PID及时间比例修正后9项通过。前进1.164m/横偏0.434mm；稳态舵角误差0.143度、稳态舵速6deg/s；约0.334倍实时。PID原增益及轮/舵0.14Nm/A未调小，未用姿态重定位稳定车辆。
- native-07-final：17项通过后测试的重连等待错误地把“有TCP连接但尚无motors状态”当成就绪，KeyError退出；补充fresh/session/motors条件后复测。
- native-08-final：21项通过，包括实际GUI R使epoch0→1、原消息CLI样例前进0.156m、错误电流/会话/模式拒绝、桥/节点/指令停发制动和重连不回放。GUI273–300FPS、sim0.30–0.31x；随后再将安全停车力矩限制到电机同一最大电流对应2.256Nm，并推进native-09最终复测。
- native-09-release：限矩后的稳定运动/停发/estop10项通过；测试Peer在旧TCP EOF尚未退休时立即连接被独占服务端拒绝。现测试连接须在总超时内完成真实状态握手，可重试短暂拒绝，不把TCP connect成功误作ready。产品sidecar本来已有重连重试；native-10按该等待逻辑最终复测。
