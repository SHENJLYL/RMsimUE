# RMUC 比赛读取车辆小陀螺配置

AIM 车辆原配置的 `GyroMaxRadS` 为 12 rad/s。UE 原先在共同车辆加载逻辑中以 10 rad/s 校验该字段，再统一把小陀螺压到 5 rad/s。比赛因此在 ROS 桥开始监听前退出，报 `invalid or missing drive parameter: GyroMaxRadS`。

UE 现在校验小陀螺转速字段为有限数值，并继续检查最小值、默认值和最大值的顺序。正式比赛的主车与共享 MuJoCo 世界里的另一席读取同一车型配置，比赛的操作指令也按该车型限值校验。AIM 配置仍为最高 12 rad/s，默认 5 rad/s。普通训练和非比赛入口继续使用已有的 5 rad/s 策略。裁判功率、ROS 接口及战术树沿用本轮改动前的实现。

MuJoCo 用原场地几何及原车型参数重新生成并校验模型清单。活动模型 XML 的 SHA-256 仍为 `d909f4c962d991d7e47ea45cb69b035ab15014e1e3bf455ba53598a00576d653`。33 项运行绑定和 35 项源文件检查通过。 [生成和身份校验](../../artifacts/integration/rmuc-match-gyro-profile-20261126/model-verification.json)

Windows UE 5.8 编译成功。比赛启动器与导航控制台回归 18 项及 3 个子项通过。额外的旧导航调参回归有 3 处与当前 YAML 基线不符，涉及原参数快照、碰撞观测距离和预期线速度。

使用报错场次的原编队重新离屏启动，主视角为蓝方手动车辆，红方由 sentry_ws 控制。车辆配置报告为 12 rad/s。UE 裁判和 ROS 桥就绪，C++ 树发送的首个导航目标已被 Nav2 接受。测试启动器、UE、ROS 桥和连接器均已退出。 [原编队复验](../../artifacts/integration/rmuc-match-gyro-profile-20261126/release-summary.json)
