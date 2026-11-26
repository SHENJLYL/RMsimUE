# RMUC 2027 飞镖站导轨、滑台和发射架外观

官方图 4-14 显示的是盖板留在导轨内的全开状态。图 4-15、4-16 的 1000 × 600 mm 放置面位于站底抽拉滑台。旧版错把上方倾斜盖板当作安装面，又延长盖板开门终点来给飞行路径腾空间。当前把站体、带滑块的盖板和带支撑轮的滑台从原场地网格拆开，导出每侧三个网格，源三角面总数和材质槽均保留。 [官方规则](https://bbs.robomaster.com/wiki/20204847/1969149?source=7) · [站体 CAD 导出](../../artifacts/maps/rmuc2027/dart-tray-foot-clearance-20261126/dart-source.json)

## 运动与摆位

- 全开与原 CAD 位姿一致。按两条固定导轨的轴线反推关闭位置，模拟关门端保留约 8 mm 底缘间隙，导轨方向行程 1.14971 m。原网格提取的滑块在关门端和开门端分别保留约 15.5 mm 和 60.7 mm 的导轨余量。 [导轨数据](../../assets/maps/rmuc2027/dart-gate-kinematics.json)
- 在比赛启动器勾选「赛前准备」，按 `L` 抽出或推回本方滑台。发射架跟随底部滑台向装填侧平移，模拟抽出行程 1.000 m，最后 3 秒自动收回。抽出期间禁止用 `F6` 跳过准备开赛。
- 发射架从笃行国赛 STEP 保留 26 个外部观察方向采样到的原 CAD 可见面，合并为静态展示模型。当前模型 48,047 个三角面，按源 CAD 的 100% 比例放置，外廓约 101.1 × 58.5 × 91.0 cm，脚撑纵向覆盖约 96.8 cm。四块 33.7 mm 的仿真安装垫块使脚撑避开站体的低位凸起。 [来源和配色](../../assets/maps/rmuc2027/dart-launcher/launcher-cad-source.json) · [装配摆位](../../assets/maps/rmuc2027/dart-launcher/mount.json)

## 脚撑与滑台端罩

此前 68 mm 的结果量的是 80% 模型与旧滑台网格的端缘，漏掉旧版拆分留在固定站体里的灰黑端罩。按旧位姿重放，抽出 1 m 时支持部位仍有 992 对相交三角面。新版将端罩及原回边归入活动滑台，保留原有几何、材质槽及红蓝站体三角面总量。100% 比例的脚撑与端罩沿滑台方向净空约 16.8 mm。 [旧版扫掠](../../artifacts/unreal/rmuc-dart-guide-repair-20261126/foot-clearance/baseline-sweep.log) · [新版网格核查](../../artifacts/unreal/rmuc-dart-guide-repair-20261126/foot-clearance/clearance.json)

## Windows 验证

UE5.8 编译和重导入通过。红蓝各 201 个滑台位置和 231 个闸门位置的网格采样中，架体及垫块未发现穿插。Windows UE 检查了两侧的 1 m 抽出、架体随动和归位，蓝方虚拟比赛命中移动基地靶及装甲展开通过，每场 128 项场地查询及 76 项基地装甲查询通过。 [原生验证](../../artifacts/unreal/rmuc-dart-guide-repair-20261126/foot-clearance/verification-20261126T062652Z.json) · [同机位俯视](../../artifacts/unreal/rmuc-dart-guide-repair-20261126/foot-clearance/20261126T062652Z-blue-tray/tray-top-clearance.png) · [滑台抽出](../../artifacts/unreal/rmuc-dart-guide-repair-20261126/foot-clearance/20261126T062652Z-blue-tray/tray-extended.png)

## 发射架材质分色

五份开源 STEP 中记录的 RGB 都是同一种蓝灰，其中国赛架 379 条。原生机械附件索引未发现同版国赛架的分件记录。采用笃行国赛封面做可见光条件下的近似配色。亮件归为拉丝铝，侧板为哑光石墨色，五金、罩壳分别采用深钢灰和深灰金属。四种材质的线性颜色、金属度、粗糙度和参考色记录在模型清单。原站体、盖板、滑台和灯条沿用场地 CAD 材质。回读两份 FBX 比对，发射架的 48,047 个 CAD 三角面未变。 [笃行国赛封面和发布帖](https://bbs.robomaster.com/article/1942144?source=11) · [材质和校验](../../assets/maps/rmuc2027/dart-launcher/launcher-cad-source.json) · [网格对比](../../artifacts/unreal/rmuc-dart-guide-repair-20261126/colour-mesh-check.log)
