# LiDAR / Point-LIO 原生记录

- 固定原源码：Point-LIO3099b9dd、Livox a8230b02；完整原checkout只读，工作副本与原消息接口包独立。provenance及可逆补丁位于artifacts/sources/point_lio和third_party/patches。
- 原Point-LIO构建缺口：GNU专用flags、未使用的Python/glog/unistd/pcl_ros依赖、两处多余template emplace_back语法。定点修复后原生MSVC成功，146个非系统DLL约159MB局部部署，不改系统PATH/安全设置。
- pointlio-native-01解析回放5项通过，97帧、0.8m平移末端0.278mm。这是解析传感器夹具，不是UE或实机证据。
- RosLidarComponent按真实物理帧射线首回波，不忽略自身；200k射线/仿真秒、10Hz扫描，默认未标定均匀图样/常反射率。1ms物理帧量化记录，跨帧保留运动畸变。
- v2在原有有界包后追加二进制扫描，v1无LiDAR入口不变；点20bytes。原消息批量CDR经原解码器核对0/1/2/31/20000点全字段，20k封装约0.3ms。原生成器未定义padding内容，不能把padding字节相等误作语义要求。
- native-01真实UE7项通过：静止456射线最大约1mm误差；动态逐点时间还原均误差0.514mm，帧末快照29.94mm；实际车辆前进1.332m，命令仍有效。
- native-02-pointlio联合9项通过，原算法68帧，跟踪约1.333m。初版误差直接比不同坐标系；最终度量改为一次初始姿态对齐并比较实际IMU安装点，保留旧报告。
- 原算法时间倒退时仅return丢新数据，未清地图；新增外部同源runner按传感器session/epoch重启原进程，从当前IMU/LiDAR安装推导外参，无真值输入。
- native-03-reset在复位前失败：生成的单位矩阵JSON为integer_array，但原参数要求double_array；原节点吞掉异常继续运行导致约0.95m错误。已显式float输出，并让原readParameters异常终止启动(return2)，估计公式保持原样。失败文件保留，不能标复位通过。
- 当前手动预览为编译已关闭，收尾需恢复。Ubuntu只有同源脚本，原生验收未做；完整导航、比赛、联网、设备光学/物理标定仍未完成。
- 最终后续：native-04-reset13项通过，真实R触发runner starts2并回到新局部定位原点；默认场景约1.05mm对齐误差。native-05-mount11项约1.02mm；native-06-rmuc10项约9.4mm；native-07-lk-rmuc11项约4.4mm，真实原LK电流反馈保持有效。均是本模型/短路线，不是实机指标。
- pressure-02共3项，明确观察丢帧、停车及非空新扫描不重放；pressure-01的最后空列表判断过弱，保留旧结果但最终采用02。self/blind_low/blind_high各3项通过：765自身点、1329近墙点，盲区下无非法背景返回。
- 原错误参数验证退出2；频率0和重名配置均在UE实际拒绝。IMU11/速度ROS11/瞄准20/TCP19/手动19项与12PNG回归通过。原controller构建和默认73DLL部署也重跑通过。
- release-summary.json记录最终模块/原节点/安装源一致性、报告及输入哈希；早期解析回放属于原节点更早构建，已单列，不混为最新模块全量重跑。
- 最终默认RMUC手动窗口PID56136已实际观察并R复位，235–237FPS，留开。第一次窗口清单尚未出现、沙箱内Get-Process也未看到；沙箱外匹配CIM确认进程活着，随后CUA确实看到窗口，未误启动第二实例。
