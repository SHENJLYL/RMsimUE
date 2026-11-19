# 当前进度

## 2026-11-19：校园体育馆与固定玻璃围栏

RMUC2027 现在位于完整校园体育馆中央。四周六排看台、864 个座位、木地板通道、钢屋架、木质吸声板、顶灯和高侧窗已进入原生运行场景。看台复用 Innerscene CC0 模型，PBR 材质来自 ambientCG，窗外与环境光采用 Poly Haven 庭院 HDR。58 块玻璃板和四扇装饰门保持固定关闭；门缝区域也有连续物理边界，车辆始终在比赛场内运行。

物理及雷达边界恢复原验收尺寸，玻璃内面与它对齐。原 CAD、车辆源模型、2000/140 悬挂、sentry_ws 速度与平滑/避障配置保留。发布切片四视角原生检查、重复资源导入、主工程整合检查及独立车辆撞墙/顶压/倒车均已验证，精确身份、最终 90 秒实时导航结果与历史失败见 `artifacts/maps/campus-gym-20261119/release-summary.json`。

新 checkout 先编译，再运行 `tools/unreal/setup_campus_gym.py` 重建忽略的 UE 资源。现有手动驾驶和导航入口加载 RMUC2027 时采用同一体育馆。指南：`docs/guides/campus-gym.md`；变更：`docs/changes/2026-11-19-campus-gym.md`；CodeStable 基线与验收：`.codestable/features/2026-11-19-campus-gym/`。Ubuntu 原生、1080p 实时和物理光度标定继续独立验收。

更新：2026-11-17，Asia/Shanghai。长期goal保持active，完整范围见`.codestable/goal.md`。Windows原生优先，保留Ubuntu同源、通用车辆包、可换地图、高画质、正式裁判与权威联机目标。

## 最新交付：可视化航点、原导航云台跟随与动态Mid-360

双击根目录 **Play-Navigation.cmd** 启动默认RMUC2027场景、原生ROS桥、Point-LIO、AMCL/Nav2和RViz。定位就绪后显示地图、障碍代价、实际规划路径、估计位姿、点云、云台和Mid-360坐标轴。RViz支持Nav2 Goal选点、Waypoint模式累积航点、Start Waypoint Following及Cancel。关闭RViz或运行Stop-Navigation.cmd结束本次拥有的进程。[完整操作说明](../guides/navigation-console.md)。

**当前真实验收：84项原生检查、13项图形操作检查通过；离线122项中120通过、2项Windows符号链接权限跳过。** 汇总：`artifacts/navigation/sentry-console-20261117/release-summary.json`，已核对当前UE/profile/rig、安装Python和导航DLL。

| 验证范围 | 真实结果 |
|---|---|
| 原Nav2完整波浪路线单点、两航点和取消联合 | 14项通过；单点实际误差0.188766m，航点队列终点0.179548m，取消后0.053320m停车、后续漂移0m |
| RViz实际选点 | 4项通过；目标[6.8,6.4]，实际终点误差0.201611m |
| RViz累积两个航点并启动 | 6项通过；按[5.8,6.4]→[6.8,6.4]执行，终点误差0.178089m |
| RViz实际Cancel按钮 | 3项通过；原action返回取消，观察窗口漂移0m |
| Mid-360动态安装专项 | 9项通过；关节转动改变光学原点，IMU测到对应角速度/比力，保留车体遮挡且不误开火 |
| 旧功能原生回归 | 手动19、ROS速度11、瞄准20、原LK/LiDAR/Point-LIO联合11，共61项通过；与Mid-360专项合为70项兼容性回归 |

84项包含14项联合与70项回归；图形13项单独统计。真实到点0.25m门限保持。完整波浪路线由原action接口验收，GUI用明确记录的短距离路线独立验收按钮和点击。

## 对齐sentry_ws

导航偏航目标整形参考`unnc-aim/universal_controller`固定提交95c66c28717e0b4d0f586c2ebf8df99d47a24ab0：按行进方向形成目标，保留最短角、1rad/s最大目标转速、2rad/s²加速度、0.3rad/s最低转动段速度、0.02rad容差及0.2s有效期。配置在`assets/vehicles/aim_sentry/navigation-follow.json`。估计底盘航向与实际关节反馈参与转换，扫描/获准自瞄优先，导航不开火。

完整联合路线记录12300个跟随样本，实际云台与整形目标中位角差0.00044835rad；累计实际云台转动12.5598rad，点云/共装IMU持续输出。原硬件云台电流PID和完整Hub/视觉仲裁仍按后续范围跟踪。

Mid-360来自原模型radar_mid360/bone_turret，对应yaw_link。零位光学中心相对底盘约[0.19812,0.07376,0.36119]m、前倾约45°。源blend哈希再次确认不变。射线/动态TF/共装IMU随实际云台关节运动，Point-LIO使用同一刚性父节点的安装外参。360°水平、-7°到52°俯仰、200k射线/仿真秒、10Hz为当前理想规格模型；专有扫描图样、材料反射与实车标定后续补齐。

## 本轮修正与证据边界

固定Point-LIO源码在归一化前用原始加速度做饱和检查；原参数acc_norm=1、satu_acc=3对应g输入。ROS SI适配现同步转换为9.81与29.43，保持原3g范围。6项单位测试与修正后的完整路线通过。单位错误导致的原失败、GUI工具选择/低倍率像素偏差失败和一次启动失败均保留，未放宽到点门限。

当前GUI使用原Nav2插件。自动验收分别核对原action、真实轨迹与取消结果；工具返回成功没有被单独当作车辆到点。依赖增加55包、没有替换旧包，前后锁文件与来源记录在artifacts/navigation/console-20261117。

旧断线/实际R恢复已有独立版本证据；本轮新Mid-360组合的实际R、更多出生点和跨高度路线继续扩展。Nav Through Poses按钮已保留原插件入口，其专项独立验收待补。

## 已保留的车辆与工程基线

AIM悬挂保持2000N/m、140N·s/m，质量、50N预载、65mm行程、源轮胎、摩擦和原驱动增益未改。手动/训练入口保留；本轮手动19及ROS/瞄准/LK联合回归已经重跑。地图与资源包按类型管理，大型资产、UE Content、生成文件和完整日志/截图留本地。

工作区D:/simulator；UE5.8.4在E:/simulator-tools/UE_5.8；原生Humble使用RoboStack Python3.14。源码、配置、依赖锁、上游补丁、CodeStable和精简证据发布到用户指定公开仓库SHENJLYL/RMsimUE。原模型和原控制器来源目录只读。

后续继续扩大RMUC路线/恢复覆盖、通用车辆导入、多机器人隔离、完整感知决策/Hub/裁判、权威联机、Ubuntu原生运行及实车物理/传感器标定。弗兰德斯原图完整场景与画质验收继续保留。旧进度归档：`docs/progress/archive/2026-11-17-before-sentry-console.md`。

## 当前窗口交接

console-08已实际观察到RViz地图/点云，Navigation与Localization均active，最后任务Feedback为canceled；action状态均已结束，未留活动目标。整套窗口保持打开，截图留本机，交接记录见artifacts/navigation/sentry-console-20261117/gui-handoff/verification.json。

## 2026-11-18 RMUC ?????????????

? bounds ????????? CC0 ????????Windows??????????Git??????????8????????????28??????????216?????????15????????????????????????14????/???32??90s?????0.99998?????????????/???????5m/s??????????????????????Ubuntu?????????????????
