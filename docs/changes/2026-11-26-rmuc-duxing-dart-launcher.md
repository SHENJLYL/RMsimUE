# RMUC 2027 笃行飞镖发射架

此页记录初版。当前盖板行程、底部滑台和源 CAD 外观见 [更新记录](2026-11-26-rmuc-dart-station-guide-and-tray.md)。

RMUC 场地装置初始化时，在红蓝发射站的固定滑台上挂载同一份视觉模型。下载核对西安交通大学笃行战队的公开 STEP 包后，从国赛镖架总成提取九向轮廓，生成简化的外观 FBX，内部机构未进入 UE。 [作者原帖](https://bbs.robomaster.com/article/1942144?source=11) · [来源和校验](../../assets/maps/rmuc2027/dart-launcher/launcher-source.json)

原 CAD 的轴向包围尺寸约 101.10 × 58.47 × 90.99 cm。原比例在当前场地候选摆位存在与固定结构的干涉。仿真使用 70% 等比，轴向尺寸 70.77 × 40.93 × 63.70 cm。发射架沿滑台倾斜，红蓝摆位按场地中心做半转对称。离线检查与两侧固定网格均无三角面相交，最近支撑采样距滑台约 0.7 mm。旧全开门板遮住从架上出站的路径，保留旧关闭位置并把开门终点沿现有方向抬高 0.95 m。该行程是仿真摆位参数。每侧按约 5 mm 步长检查 435 个门位，装饰架与门板无相交，九组短出站线通过。 [安装数据](../../assets/maps/rmuc2027/dart-launcher/mount.json) · [净空日志](../../artifacts/unreal/rmuc-dart-launcher-20261126/gate-travel.log)

比赛中 `K` 申请开闸，`Shift+K` 换靶。既有裁判负责开闸资格、弹量、命中及效果。全开后，虚拟飞镖从架上的视觉出站点自动发射，第一段会查询活动闸门，之后沿原仿真飞行动画进入检测体积。模型不提供实物驱动或动力学标定。 [场地绑定](../../unreal/RMSim/Source/RMSim/RMUCFieldDeviceVisuals.cpp) · [虚拟飞行入口](../../unreal/RMSim/Source/RMSim/RMUCDartGameplay.cpp)

Windows UE 5.8 构建通过。两场离屏比赛均从发射架起飞并通过开闸，蓝方命中随机移动靶后对方基地由 10,000 降到 9,200 HP 并展开护甲，红方命中前哨后 HP 由 1,000 降到 0。两场均通过 76 项基地装甲和 128 项场地查询，四套 C++ 规则及两项 Python 场地描述回归通过。探针快进至首次开闸机会，发射和命中过程由 UE 游戏时间运行。首次出站拦截和重试构建日志保留在验收目录。 [验收回执](../../artifacts/unreal/rmuc-dart-launcher-20261126/verification.json) · [蓝方实拍](../../artifacts/unreal/rmuc-dart-launcher-20261126/20261126T052014Z-blue/launcher.png) · [红方实拍](../../artifacts/unreal/rmuc-dart-launcher-20261126/20261126T052014Z-red/launcher.png)

## 侧上方实拍和停位净空

使用开发探针 `-RefereeDartLauncherReview` 在蓝方闸门全开时拍摄 1920 × 1280 场景图和独立补光图，日志记为 `gate=1.000`。当前 FBX 与门板 CAD 的双向网格表面采样显示，蓝红全开最近样本距离均约 30.0 cm，关闭时约 41.2 cm。按竖直包围盒确定的全开下界约 5.0 cm。现有外观的九向投影、18 mm 体素及平滑会使薄件和硬边合并。 [场景实拍](../../artifacts/unreal/rmuc-dart-launcher-20261126/side-overhead-review/20261126T053122Z/launcher-side-overhead.png) · [补光图](../../artifacts/unreal/rmuc-dart-launcher-20261126/side-overhead-review/20261126T053122Z/launcher-station-detail.png) · [净空结果](../../artifacts/unreal/rmuc-dart-launcher-20261126/side-overhead-review/mesh-clearance.json)
