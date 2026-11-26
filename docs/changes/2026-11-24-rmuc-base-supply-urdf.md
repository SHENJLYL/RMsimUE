# RMUC 基地护甲、补给占点与场地 URDF

## 基地护甲

红蓝两侧各用三组原 CAD 转角外壳，保留每组外壳相邻的两面。按 2027 规则手册图 4-11、4-12 的闭合和展开外轮廓，以及官方演示约 00:16–00:21 的连续运动，重建向外 0.25 m、下降 0.075 m、外翻 6° 的轨迹。1.5 秒开门时间参考开源仿真 Arena-1。规则未公布精确机构行程及速度。外壳用原几何，展开后露出的内部装甲和支架采用图示重建尺寸。[规则手册](https://bbs.robomaster.com/wiki/20204847/1969149) · [官方图纸](https://bbs.robomaster.com/article/1970956) · [官方演示](https://www.bilibili.com/video/BV1cV41197Vd/) · [Arena-1 参考实现](https://github.com/RM-Arena/Arena-1/blob/e70c1f010edfd5d2b4454c6d5a21bee71c0baba4/simulation/src/structures.rs)

红蓝基地各接收自身裁判展开条件。UE 固定结构、移动外壳、闭合时外露装甲和展开时内装甲分别参与查询。未改写原 MuJoCo 场地接触模型。`assets/maps/rmuc2027/base-armor-kinematics.json` 统一描述滑移、转轴和近似参数。`tools/unreal/Prepare-RmucRune.ps1 -BaseArmorOnly` 从已有授权的源 CAD 转换产物重建 UE 资产。

## 补给与占点

`field-zones.json` 使用规则图 5-23 推定 12 个区域轮廓，带陆地、平台高度限制和基地结构排除孔。车体下方的虚拟读取点向实际 CAD 地面短距离检测，再按交点和高度判定。出生阵营使用场地出生点的团队属性。边界、读取距离和射频覆盖尚无实场测量。[区域推定与 CAD 核对](../../assets/maps/rmuc2027/FIELD-ZONES.md)

活动区域进入现有前哨、基地、堡垒裁判条件。支持补给回血、补给区领取已发放的弹量和按 `F3` 进行本地兑换。每次合规兑换扣 10 金币并加 10 发，比赛规定的购买上限生效。相同帧首次争用中央高地时，由该区域原属方优先。当前开放本地兑换，远程兑换和实际射频误差模型留待接入。

## ROS 2 接入

`tools/maps/export_rmuc_field_description.py` 由两份地图数据生成 `ros2_ws/src/rmsim_bridge/description/rmuc2027_field.urdf`。包含红蓝三组装甲的 12 个滑移/转动关节和 12 个区位固定坐标。UE 运行状态直接提供实际关节位置和仿真时间戳。现有桥发布 `/rmsim/field/robot_description`、`/rmsim/field/joint_states` 及 `/tf`、`/tf_static`。RViz 的 Fixed Frame 为 `rmuc2027_field`，RobotModel 使用 `/rmsim/field/robot_description`。场地 TF 树保持独立，场地 URDF 的模型和区域轮廓用于显示。

## Windows 验收

- 完整 UE5 构建成功。真实场地留原物理身份，向临时地图副本只添加测试出生点。两场 MuJoCo 预检分别验证 33 项绑定。
- 蓝方补给区内置诊断触发后，蓝方哨兵从 280 HP 恢复到 400 HP。兑换后金币从 800 降到 790，弹量从 300 升到 310。诊断触发蓝方基地展开，三组关节由零连续到 0.26100766 m 和 0.104719755 rad，红方保持闭合。72 次基地和 32 次其他装置查询通过。
- 红方从蓝方前哨增益点的合法位置原生出生。持续读取约 5 秒后蓝方完全控制转为红方部分控制，蓝基地保护解除，前哨塔体仍归蓝方且保留 1000 HP。
- 四组独立 C++17 回归、两项 URDF/关节契约检查通过。Windows ROS 2 DDS 回放原生采集的闭合、途中、展开帧，验证晚加入订阅者收到 URDF 和 12 个静态区位，相同仿真时间戳对应的 12 个 JointState 和动态 TF 均通过。

原生运行、查询日志与紧凑回执位于 `artifacts/maps/rmuc2027/base-supply-native-20261124`。该验收覆盖合法位置上的读取与规则联动，未计入从默认出生点手动行驶进区的路线测试。
