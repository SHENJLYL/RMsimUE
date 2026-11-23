# RMUC 坡脚识别与转弯速度复测

对照使用 Windows 原生 UE、MuJoCo、ROS 2 和 RViz。跨场从 (7.86, −6.78) 到 (−8.32, 6.16)，起步实测小陀螺以 2 rad/s 设定转动。对照车在右回头弯约 18 秒收到了持续零平移。停住时估计位置比实体轨迹向南偏约 0.47 米。44 帧近场地图回放显示，保持计划原方向无法脱离已有的定位余量占用。该回放用于定位原因，运行时继续使用估计位置和实时地图。

记录最快原回放的弯道物理数据。两弯约 20.2 秒，物理制动合计 1.63 秒。约 3.47 秒内指令大于等于 1.4 m/s，整形输出和车速均低于该门限。另有约 5.35 秒内整形输出达到该门限，车速仍低于门限。因此，消除全停只解决一部分时间差。

## 坡脚修复

近场和远场 `SlopeGround` 共用同一规则。相邻坡面与平地只有在高度差符合可通行最大坡度，且实测平地高度与坡面外推相合时才视为连续。原实现只接受平地高于相邻坡面。新规则按高度差绝对值验证连续性，覆盖波浪路下坡接平地。合成用例在旧版复现失败，修复后通过，陡面、台阶、坡上凸起和落差回退仍通过。

波浪路、六边形平台和下坡出口分别检查 457、802 和 217 个车身位置，全部无静态占用重叠。平台一段采到 17 个上游标记接近地图支持面，15 个有时间接近的代价地图快照，其中 2 个附近同时存在最终占用格。此观测无法证明这两格是误标，地形效果继续以障碍和真实净空验证。

## 跨场原生回放

| 回放 | 动作墙钟 / 物理时间 | 终点状态与净空 |
| --- | --- | --- |
| 此前最快候选 | 41.39 / 40.90 秒 | 4,325 个位置，0 处重叠，passed |
| 匹配启动朝向的对照 | 67.06 / 67.78 秒 | 超时取消 |
| 保留已验证路径前缀 | 50.62 / 52.30 秒 | 4,759 个位置，0 处重叠，passed |
| 低速调整脱离余量方向 | 49.70 / 56.42 秒 | 4,792 个位置，0 处重叠，passed |
| 停车后用新扫描重匹配 | 44.71 / 46.04 秒 | 4,538 个位置，0 处重叠，arrival_error |
| 坡脚修复 | 40.05 / 40.03 秒 | 4,207 个位置，0 处重叠，passed |
| 坡脚修复复跑 | 46.23 / 46.30 秒 | 4,493 个位置，0 处重叠，passed |

保留有效路径前缀在右弯减少大角度重规划变化，跨场速度仍低于此前最快结果。脱离余量方向与停车重新匹配均未触发各自新增分支，保留原试验日志，生产源码已回退这些试验。坡脚规则与回归测试保留。跨场候选配置仍独立，严格 26 秒尚未达成。

## 开源依据

Nav2 MPPI 的全向运动模型在短时域内比较整段速度轨迹，并同时评价障碍、路径进度和对齐。现控制器按前视目标生成方向，在代价地图发生变化时再逐周期裁速度。采用联合轨迹预测需要保留现有的整车停止验证与车端加速度限制。 [Nav2 MPPI 官方文档](https://docs.nav2.org/rolling/configuration_and_development/configuration_guide/controller_plugins/mppi_controller/configuring_mppic/) · [Nav2 源码](https://github.com/ros-navigation/navigation2/tree/main/nav2_mppi_controller)

TOPPRA 沿路径反向计算可制动的速度区间，再前向选择可达速度。它适合作为全路径速度分配的参考，碰撞和短时观测仍由局部控制检查。 [TOPPRA 论文](https://arxiv.org/abs/1707.07239) · [实现](https://github.com/hungpham2511/toppra)

Patchwork++ 使用局部支持面和垂直结构剔除处理分层地形。它验证了以连续支持面解决地面漏分的方向。现项目在已有局部坡面拟合内修复了下坡到低平台的连续性，保留实体台阶和凸起逻辑。 [Patchwork++ 论文](https://arxiv.org/abs/2207.11919) · [开源实现](https://github.com/url-kaist/patchwork-plusplus)

RoboStack 提供与现有环境匹配的 Humble 1.1.20 Windows 包。独立锁文件在原 520 个包上只增加 8 个包。已通过 C++ `pluginlib` 在 Windows 实例化 `MPPIController`。Humble 版全向预测对速度指令使用一步延迟，尚需接入舵轮速度整形和独立旋转底盘的制动扫掠才能做原生导航验收。 [ROS 包索引](https://index.ros.org/p/nav2_mppi_controller/) · [Humble 运动模型源码](https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_mppi_controller/include/nav2_mppi_controller/motion_models.hpp)

[回放身份、样本与复测结果](../verification/2026-11-22-lower-landing-navigation.json)
