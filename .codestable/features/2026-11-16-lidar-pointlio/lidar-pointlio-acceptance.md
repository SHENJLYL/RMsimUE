# LiDAR 与原 Point-LIO 验收

日期：2026-11-16。Windows UE5.8.4 / RoboStack Humble / MSVC原生；范围为当前未标定单车样例。用户授权持续实现，不宣称其逐篇审阅了方案。

## 1. 接口契约核对

原Point-LIO3099b9dd与Livox a8230b02完整checkout保持原样。原CustomMsg/CustomPoint逐字保留，接口投影不含Linux硬件SDK。Point-LIO消费原消息纳秒偏移/xyz米/line/tag；通过原ROS解码器验证0/1/2/31/20000点CDR全字段，填充字节不作为语义比较对象。

车辆sensors/lidar.json与独立schema可配安装、频率、范围和模型噪声，原profile启动时校验。v2按明确偏移拼接RGB及20字节点区，总二进制16MiB/JSON64KiB；无LiDAR仍v1，命令格式不变。RGB并行、分片、坏布局、非法值/时间均有测试。

## 2. 行为与决策核对

实际世界射线第一命中，包含车体；不透过盲区遮挡、不伪造未命中点。默认非设备图样、常反射率明确标注。每物理帧射线共享真实时间/位姿，跨帧保留畸变，不插值发明5µs状态。最多4096射线/物理帧、65536点/扫描、2帧待发；溢出及丢样显式记录。

原节点估计公式未替换。移植范围为编译/无用依赖/两处模板语法及实际暴露的参数失败仍继续运行修复。runner只读取传感器配置及会话，推导外参/单位，按session/epoch重启原进程和局部原点。ROS图实测无真值订阅。

挂载点实际rg盘点：RosLidarComponent；RosBridge初始化/Advance/QueueState/断线/epoch；GameMode RosLidar开关；Imu UsesId跨传感器重名校验；Python protocol/lidar/node/point_lio_runner；Win/Linux入口；原消息/估计器工作副本及构建/部署工具。均已写入方案；移除可选挂载后v1速度/电流/瞄准路径独立，默认手动无新传感器开销。

## 3. 原生场景与反向核查

机器汇总artifacts/sensors/lidar/release-summary.json包含模块/原节点/输入/源码/报告哈希及安装Python一致性。失败记录保留。

| 场景 | 证据目录 | 结果 |
|---|---|---|
| 原节点解析回放 | pointlio-native-01 | 5项；仅移植/接口，不计物理场景 |
| 默认物理场景、原算法、真实R | native-04-reset | 13项；1.34m行进，初始姿态对齐后的IMU点误差约1.05mm |
| 改安装位置/朝向及自动外参 | native-05-mount | 11项；误差约1.02mm |
| 默认RMUC原场景 | native-06-rmuc | 10项；1.33m行进误差约9.4mm；网格场景未重复解析距离oracle |
| RMUC原LK电流+传感器+原Point-LIO | native-07-lk-rmuc | 11项；约1.39m，误差约4.4mm，真实电流反馈保持有效 |
| 真实30Hz扫描压力 | pressure-02 | 3项；队列丢帧、失效停车及非空新扫描无重放 |
| 自身遮挡、近墙和盲区阴影 | occlusion-01 | 各3项；765点命中车体，近墙1329点，进入盲区后无非法背景返回 |
| 非法频率/重复传感器ID | invalid-profiles-01 | 2项；明确引擎拒绝及进程终止 |
| 原节点错误参数类型 | pointlio-invalid-type | 配置错误退出2，不进入估计器 |
| 既有IMU/ROS/瞄准/TCP/手动 | regression-* | 11/11/20/19/19项，手动12PNG通过 |

静止解析距离oracle抽查456条射线，最大约1mm；运动按逐点时刻还原平均约0.514mm，误用帧末姿态约29.94mm。原算法定位误差仅本理想传感器/路线样例结果，不是实车标定精度。

真实R的UI和ROS证据在gui-reset.json及native-04-reset：新进程starts2、新epoch定位原点恢复，旧点云未重放。19项离线协议测试、JSON schema、PowerShell与Bash语法通过。Ubuntu仅离线准备。

收尾已恢复默认RMUC手动训练并真实R复位，窗口留开，235–237FPS、车速0、gyro off、弹量100/热量0；未启用ROS或传感器。gui-restored.json记录实际观察。最新手动引擎内响应：96.7ms静止舵向、251.5ms行进改向。

## 4. 术语一致性

明确区分原始射线、实际传感器时刻、解析回放、原估计器、初始姿态对齐评测与实机标定。未称完整Mid-360扫描、真值定位、完整sentry_ws或联网比赛完成。

## 5. 架构归并

ARCHITECTURE.md加入物理LiDAR/v2输送/原算法runner的数据流和复位边界。指南lidar-pointlio.md及sidecar/environment README提供源构建、运行、模型简化与协议内容。

## 6. Requirement 回写

lidar-inertial-estimation.md落为current子能力；realistic-sensors和完整native-algorithm-testing愿景未由该子集替代。

## 7. Roadmap 回写

lidar-pointlio独立条目标done；完整传感器/同源算法/单哨兵导航与比赛、联机、Ubuntu保持各自未完成状态。

## 8. 长期记录

按既有授权回写attention、current、当日变更及runtime-notes。新增保留规则：CDR填充不比较；原ROS矩阵须float数组；参数失败不得继续；原Point-LIO跨仿真会话需新进程/地图；原输出当前单车命名空间边界。

## 9. 遗留

无硬件扫描/强度/束宽/随机噪声实机标定，无全场路线或回环/重定位/Nav2验收，无多车输出隔离、比赛裁判/网络和Ubuntu原生运行证明。默认1ms可约0.3倍墙钟；默认手动不启用这组传感器。完整原WT地图导入继续保留在原roadmap，未被此功能替代。
