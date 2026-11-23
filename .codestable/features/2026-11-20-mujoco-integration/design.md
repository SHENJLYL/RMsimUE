# MuJoCo 与 UE5 融合

状态：标准入口、同源驾驶控制、MID-360/ROS 和导航动作已完成 Windows 验证；完整实时性能验收 partial，2026-11-20 Asia/Shanghai。

## 本轮交付目标

在现有 RMUC2027/学校体育馆视觉场景中加载 AIM 哨兵，使用 MuJoCo 的刚体、关节与接触求解驱动车辆，复用现有手动控制、ROS 命令、RGB、Mid-360/IMU 与复位接口。独立检查真正的轮地接触、固定步时间、双重求解禁用、源碰撞/材质保留以及传感器时间一致性。

## 选择依据

官方 MuJoCo 4.0.1 (4ed093a9f06b170bd1564942fa37e1bb907d93e3) 提供 Win64/Linux C API。URLab-Sim/UnrealRoboticsLab efbc5e272a51dbe641c7c078406bd3c076b05823 提供成熟集成结构参考，其公开支持版本为 UE5.7；本工程继续 UE5.8.4，直接增加本地 C API 后端，复用现有引擎与 ROS 接口。oneclicklabs d2cb5a8a8f9154e670c15fa7eb0944f5a218a92a 为较早概念实现。

## 合同

- 物理状态使用右手 SI；显示转换为 UE 左手厘米。角速度/力矩采用轴矢量变换。
- MuJoCo 分支不激活该车辆的 Chaos 动力学；UE 查询几何供相机/射线使用。
- 所有控制仍经轮/转向/云台执行器，目标速度不直接写入底盘速度或位姿。
- 手动与导航的车辆能力及共同整形参数由同一 drive-profile.json 提供：前进/倒车/横移 5/2.5/4 m/s，加速/减速/制动 2/4/7 m/s²，jerk 6/20 m/s³，普通 yaw 上限 π/2 rad/s、角加减速 3/5 rad/s²。导航指令先转换为 base_link 再通过原 velocity_smoother，并进入共同 C++ 整形器；连续消息保留整形状态。
- 悬挂2000 N/m、140 N s/m及当前车辆配置来源保持；生成器读取独立 drive-profile.json。
- 接触几何优先复用当前249个车体原生凸壳。静态凹场地保留源三角及空隙；效率优化必须提供几何误差与接触证据。
- 统一模型/资产/配置哈希绑定，明确缓存失效。缺失或过期模型明确失败。
- mj_step 后通过同刻 forward/状态同步发布；实际固定步累计，不默丢仿真时间。
- 标准入口采用 1 ms MuJoCo 积分、5 ms UE 世界步和 60 Hz 视口目标。实时 pacing 保留按实际仿真推进的绝对墙钟 deadline；暂态迟到通过后续完整物理步追赶，持续超负荷按实际运行倍率记录。初始化、generation 或时间重置时重新建立锚点。
- ROS真值只用于评测，定位继续消费传感器。R/断线不重放旧指令。

## 基线与验收记录

基线：artifacts/integration/mujoco-ue-20261120/baseline-01，HEAD ce79043744156313e02cb109f9fe7fd09e0eb91c，288文件和原DLL已保存。SDK官方下载、SHA、header/runtime版本检查记录于artifacts/dependencies/mujoco。

最终 build13 模块 85ee653ec4a901cc3b65ff9f05104d6aadb056bc2da5aeab5fca79f963a00217 编译 exit 0。native-shared-drive-02 通过13项，核对全部27项控制数值、车体坐标限幅、加减速/jerk、连续指令及watchdog；manual-camera-rmuc-03 通过6项，覆盖C/Shift/退出瞄准、5 m/s指令与制动。navigation-standard-07 动作18/18，实际参数、目标误差0.131949 m、取消制动0.169945 m及停车漂移均通过，完整动作在同一60 s时钟窗内。整体倍率0.999841×，时钟30/32，分段及滚动10 s窗口门限保留失败。即时零目标锁存已实际触发，首公共零指令stamp较取消时最新solver诊断时间晚25 ms，观察墙钟间隔26.3207 ms，4次残余输出被抑制。

红蓝波浪、边界接触及之前的动作记录继续各自绑定原构建。standard-04/05 的末端低速停滞及源几何审计保留：失败姿态的车尾底板与约70 mm源波纹接触。车辆参数、源碰撞与原approach策略保持。未获得实际快路覆盖的接触身份缓存实验已撤回，最终使用原UE身份查询；SDK固定world三角面缓存继续沿用已验证版本。

MID-360 完整帧、5 µs槽、200 Hz IMU、真实安装点/轴向、ROS消息与重连已在同一最终模块的 native-sensors-09-final-query 通过TCP23项、ROS14项；4096束原生查询命中差0 cm、材质一致。扫描光机/NIR/噪声近似保持未标定状态。标准手动入口实际加载最终后端，唯一可见窗口与真实截图审查通过并保留供试驾；本次可见交接未发送驾驶输入。完整结果以 docs/changes/2026-11-20-mujoco-integration.md 与 docs/verification/2026-11-20-mujoco-mid360.json 为准。
