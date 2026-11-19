# 弗兰德斯飞艇残骸与战雷地图界面

日期：2026-11-19，Asia/Shanghai。入口：`D:\simulator\Play-WarThunder.cmd`。

## 结果与操作

原弗兰德斯 Dom 任务的飞艇残骸已保存回正式 World：31 个原始资源、67 个实例、65 个原实体碰撞、12 项原材质与 26 张原纹理。桁架、艇身残架、前后舱室、螺旋桨及附属物均保留源位置。小地图与 M 大图采用原地图、字体、玩家与底盘图标、任务标记、面板背景和样式参数。

地图纹理、位置、方向和鼠标拾取共用修正后的显示坐标变换。箭头指向实际云台的世界水平前向：左转对应屏幕逆时针，右转对应顺时针。C 环视只改变观察扇区。

| 操作 | 行为 |
|---|---|
| Alt＋滚轮 | 缩放右下角小地图，比例尺同步变化 |
| M / Esc | M 展开或收起；Esc 关闭已展开地图 |
| 展开后滚轮 | 围绕鼠标所在地图位置缩放 |
| 展开后右键拖动 | 平移地图，边界受有效地图范围约束 |
| 地图光标模式左键 / 中键 | 临时位置标记 / 切换本地关注点 |
| C | 环绕观察，箭头继续指向实际云台 |

大小地图分别保存 1～10 倍缩放。地图光标输入独占该帧地图交互，退出时清除残留鼠标位移。Shift 瞄准中的 Alt＋滚轮只改变地图缩放。临时标记和关注点为当前单机功能。

## 来源与实现

飞艇源链为 `vlaanderen_dom → template_vlaanderen_objects → template_vlaanderen_object_groups`；此前基础 DBLD／RIGz 导入没有包含这组任务实例。完整仿射变换被烘焙到几何，离线最大位置误差约 0.000060704 m。55 个视觉资产含 151,462 个三角形，53 个碰撞资产含 35,207 个三角形；原桁架开口保留。两个没有源碰撞的装饰舱皮沿用原定义。飞艇标记为固定地标，组件为 Static / WorldStatic / QueryAndPhysics。

原材质槽、UV、颜色空间与纹理绑定逐项核对。玻璃保留源透明信息及法线／粗糙度通道，蒙皮保留 alpha-test，木质、金属与贴花沿原槽绑定。Dagor 到 UE 的材质映射详见 `assets/maps/wt_vlaanderen/mission-objects/SOURCE.md`。

UI 准备工具从本机只读原游戏资源准备 11 项界面资源和 7 个原 Dom 标记。地图高度为屏幕高度的 0.3，网格为 7×7，坐标采用原字母／数字。网格、边框和阵营颜色使用原 sRGB／alpha 值；M 页面使用原黑色遮罩与 option_select_odd 九切片背景。CSS 源切片参数为 (1,5)，当前实现按常规九切片和 UI 比例缩放边距；旧 daGUI 的完整布局解析器没有进入本次运行。

`MapFontRenderer` 从原 FontFace 字节通过 FreeType 栅格化：tiny_text_hud 在 1080p 为 18px、forceAutoHint=no；原 fontFx64 对应 scale=2.0、extent=2px。源 glow 与 coverage gamma 的文字纹理缓存供 Canvas 绘制。`ManualMapControl` 处理地图输入，`MapOverview` 统一绘制／投影／拾取，`MapUiProbe` 捕获真实游戏 viewport 中的 HUD。

## 原生验证

模块：`7269f29792d714ac6fe089ef5ccfa544b951093b998ae2ace2e40b188d45ca45`。World：`ba531c13f4a40c662d7bb361ba5aa669832ad9240aaddb42a7fe282f66814be0`。

| 检查 | 结果 | 证据 |
|---|---:|---|
| 原 UI 资源导入／读取 | 11 项通过 | `ui-import-report-06.json` |
| 方向、C、缩放、M、拖动、标记及焦点恢复 | 13 项 / 10 张 HUD PNG | `ui-native-03/verification.json` |
| 飞艇碰撞、受阻、顶压、制动、倒车及地标保持 | 8 项 / 14 张 PNG | `zeppelin-contact-03/verification.json` |
| C／Shift 与移动云台方向回归 | 16 项 / 8 张 PNG | `camera-regression-02/verification.json` |

同一最终模块共 **37 项原生检查 / 32 张 PNG**。飞艇受阻后持续顶压前进 0.931 cm，倒车脱离 100.265 cm；未使用位姿覆盖，未触发恢复复位。原生共享输入、实际 HUD 像素审核、OS 键鼠验收分别按各自报告记录。

证据根为 `artifacts/maps/wt-zeppelin-minimap-20261119`，精简汇总 `release-summary.json`。初次构建、导入和第一轮接触查询失败保留，修正后使用新编号重跑。

## 重建与保持

正式启动直接加载已保存 World。完整基础导入在新建空地图前运行 `wt_mission_supplements.preflight`，在基础 World 保存及最后一次旧 UObject 读取后运行 `restore`，恢复所属飞艇演员并更新 World 哈希。组合顺序及只读 preflight 已验证；本轮没有再次运行整张基础地图重建。独立任务导入、保存和已保存场景读取已经原生验证。

大型游戏资源与 UE Content 保留本机；源码、来源记录、工具和精简摘要进入仓库。5 m/s、2000/140 悬挂、真实出生点、原 overview PNG／校准和已完成钢轨实体沿用当前配置。

## 最终画面与桌面输入补测

实际 HUD 的箭头模板测得：左转 −35.00°、右转 +69.75°、C 环视 0.00°；三个方向判据均通过。40,553 个地形像素的原图方向审核通过，原字体、glow、面板透明渐变和亮边已人工复核。证据为 `ui-native-03/pixel-audit` 和 `minimap/ui-native-03/native-arrow-alpha-audit.json`。

正式 `Play-WarThunder.cmd` 已实际加载至渲染、地图与驾驶就绪。桌面 OS 输入补测在前台焦点丢失后触发保护，OS03 未完成，未计入通过项；全部注入键已释放，测试窗口随后关闭。OS01 的二维字段误用、OS02 的事务间输入归因缺口及后续修正、各次启动监视器失败均留有记录。最终通过的运行验证范围为前述 37 项原生共享输入／物理检查与实际 HUD 像素审核。
