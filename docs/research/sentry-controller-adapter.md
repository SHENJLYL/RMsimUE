# sentry 控制逻辑适配记录

用户提供只读源目录`E:/27UL_Sentry_chassis_controllers-main/27UL_Sentry_chassis_controllers-main`。12个源文件的哈希见`artifacts/sources/sentry_chassis_local/source-manifest.json`。这是ament_cmake ROS2包，依赖rclcpp/custom_msgs/Eigen3；原目录未修改。本文保留初期数学适配记录，最新原LK节点运行情况见文末；完整sentry_ws仍未接入。

## 已落实的可复用边界

- 原`ChassisControl.msg`提供x_speed/y_speed/spin_speed/emergency_stop/power_limit；LK实现将平移速度按轮半径转RPM，旋转速度乘轮距，单位对应m/s与rad/s。
- 原LK轮向在内部用顺时针编码器角，y分量取反；仿真接口统一ROS X前/Y左/Z上，适配层显式转换符号，不直接把电机编码器角当ROS舵向。
- 四轮向量解算、32768周期最短转角与180度反转轮速原则已提取为无ROS依赖的`Control/SentrySwerveKinematics.h`，由实际VehicleDrive调用。保留速度限幅、平滑、轮向未对齐抑制和有限力矩物理执行。
- 修正源算法int16目标缩窄及负数取模可能导致的零点跨界问题；用int32与正模。测试覆盖编码器跨界、原地转动、横移、倒退和轮速反转后的向量重构。
- 本机源码wheel_radius=0.0425m与模型一致；chassis_radius默认0.40m与模型轮心半径约0.194m不符。仿真使用原尺度实际轮心位置，不照搬默认半径。

## 停车与坡面

松开WASD输出零平移目标，由速度环主动制动；保留最后舵向以免零速噪声使轮组乱摆。Space使用更强减速。小陀螺中只取消平移，不隐式取消自转；Space可同时制动，G明确关闭自转。

滚动方向由真实转向carrier/轮轴约束决定，横向运动需要克服轮地滑动摩擦。高轮胎摩擦与电机制动一起作用于实际接触，不把车体固定在坡面上。轮胎离地时不虚构支撑力。

## 后续同源节点接入

`SetContactPlanarDrive`接收有坐标声明的SI目标速度，键盘只是其中一个输入来源。2026-11-16 UE服务端和原生rmsim_bridge已实际验收；后续原LK电流模式也已运行，见下述最新记录。旧“尚未接入”结论仅适用于此前数学提取阶段。

完整运行原LK节点还需要custom_msgs、电机编码器零点、rpm/deg/s反馈、电流到轮轴/舵机扭矩的标定、功率/超级电容反馈和固定控制周期。当前使用本地演示执行器，不把这部分称为原硬件PID或完整sentry_ws原生闭环。原`emergency_stop`清零电流不等于坡上主动速度制动，适配时须保留此区别。

车辆包已准备`adapters/sentry_lk.yaml`：按实际轮心半径0.194146113939m覆盖源码默认0.40m；轮半径0.0425m。虚拟编码器中立点统一为0，话题指向/rmsim/aim_sentry，不指向真实EtherCAT。该文件只是原节点接入预备配置，不能替代尚未实现的电机消息/电流反馈适配器。

默认控制不再用仿真车体线/角速度作为附加外环；实际闭环读轮/舵的物理运动，对应可由编码器获得的信息。键盘世界平面指令只是操作者接口；ROS车体系指令仍使用同一SI入口。车体速度真值仅用于调试/HUD/评测，未替代导航定位。

## 2026-11-16 原节点运行更新

用户原包位于third_party/sentry_chassis_original，工作副本ros2_ws/src/chassis_controllers；原12文件哈希不变。custom_msgs从AIMEtherCAT/EcatV2_Master固定7c1bd8a3c7f47cc8505470640f8dd7baf5698d76读取，完整原消息和仓库LICENSE已保存。补丁限于构建依赖/MSVC兼容、输入/反馈新鲜度、int32编码器跨界、最新值QoS及可选反馈采样时间适配。

实际运行的是原C++节点的角度/速度PID与PowerLimiter，不再只提取运动学公式。UE在外部电流有效时关闭原本的位置/速度驱动，电流×扭矩常数施加到轴并加反作用力；原节点收到真实刚体相对角/速度。源增益保留，通过固定1ms仿真步长及新反馈触发计算消除原变步长/通信迟滞造成的持续舵向振荡。native-06为9项稳定性检查，native-08为21项含真实R复位/CLI/故障检查，最终收尾见feature验收。

仍未标定电机/电气/轮胎模型；原功率限制代码执行通过不等于电池功率或官方裁判功率校准。原LK模式的波浪路线、完整sentry_ws和Ubuntu原生尚未验收。使用见docs/guides/sentry-lk-motor-loop.md。
