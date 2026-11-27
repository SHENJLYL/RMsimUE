# RMUC 2027 蓝方半场标线

共四处蓝方出现复用红色材质的条纹。堡垒轮廓 18 面，补给区标记 12 面，中央高台边标 16 面，飞镖站外侧竖条 4 面。在导出阶段按四个原 CAD 实例把 `RM27_M07` 替换为现有蓝方道路的 `RM27_M04`，常规地图、中央平台的两套拆分资源及飞镖站的固定部件采用同一处理。 [改前 UE 俯视](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/before-blue/viewport.png)

Blender 回读核对七个输出网格的几何签名和材质计数。独立红方与碰撞资产的 18 个 SHA-256 均与基线一致。四个基础可视网格校验和已生成。 [FBX 校验](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/fbx-verification.json)

Windows UE 原生导入并保存七个可视资产，四个基础 FBX 的校验和已同步到场地清单。堡垒和补给区的新槽在首次载入时用了引擎默认材质，已显式绑定到蓝色道路材质并通过同机位复验。红蓝堡垒、补给区、中央高台和飞镖站外侧配色已检查。手动、比赛和导航使用同一组地图资源。 [资产回执](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/release-assets.json) · [颜色复验](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/native-visual-verification.json)

[蓝方俯视](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/final-blue/viewport.png) · [红方俯视](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/after-red/viewport.png) · [中央高台](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/after-centre/viewport.png) · [飞镖站外侧](../../artifacts/maps/rmuc2027/blue-ground-stripes-20261126/after-station/viewport.png)
