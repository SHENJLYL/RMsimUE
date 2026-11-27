# RMUC2027 补给区视觉标签

标签样式、尺寸和靠公路区墙面的位置来自 RMUC2027 V1.0.0 第 47 页图 4-24。外框宽 340 mm，黑色区域宽 280 mm。`placement.json` 按官方图保存红蓝 7×7 方格，UE 以黑白实体面片绘制。图示的标签右缘到墙体右端为 2,970 mm，下缘到墙底为 400 mm。 [官方规则手册](https://bbs.robomaster.com/wiki/20204847/1969149?source=7)

场地 CAD 中，两侧墙面位于 X≈±10.50 m，右端的绝对 Y 坐标约为 7.523 m，墙底 Z≈−0.197 m。标签中心使用上述相对尺寸定位，向补给区偏移 2 mm 以防止与墙面叠绘。 [官方 CAD 发布页](https://bbs.robomaster.com/article/1970956)

图 5-5 在第 74 页描述了按车载相机画面中心、滚转和标签可见性判断补给区。UE 按相机投影和遮挡实现该检测。公开规则给出中心区域和滚转要求，仿真取图像中央 50% 宽高及绝对滚转角不超过 15°，并要求四角均在视场内和无遮挡。实体图传的实际检测阈值需实地测量。

标签材质由 Unreal Editor Python 工具生成到 `/Game/RMSim/Maps/rmuc2027/SupplyTags/SupplyTagSurface`。 [材质源](../../../../tools/unreal/import_rmuc_supply_tag_material.py)
