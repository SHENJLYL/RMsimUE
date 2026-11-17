---
doc_type: feature-acceptance
feature: 2026-11-15-source-mesh-maps
status: passed
summary: RMUC2027 is the native Windows default map; the same manual runtime drives a sourced WT CDK terrain crop.
tags: [maps, terrain, provenance, acceptance]
date: 2026-11-15
user_review: not_requested_under_standing_authorization
---

# 来源网格地图验收

## 契约与范围

rm_mesh_map包含版本、单位/坐标、来源哈希、bounds、显示/碰撞UE资源路径和出生点；regions暂为空。原输入只读，资产按assets/maps、转换工具按tools/maps、下载源按artifacts/sources、运行证据按artifacts/maps保存。

首地图使用DJI RMUC2027 V1.0.0同源转换几何；第二地图是官方WT CDK样例高度场512m裁剪。后者不冒充完整游戏小地图。

## 原生验证

环境UE5.8.4-58902052 / Win64 Development Editor。Build-ImportLab.ps1最终Result:Succeeded、进程退出0。首次MapStage局部Id隐藏成员触发C4458，改名GeometryId后构建通过，失败日志保留。

| 检查 | 证据与结果 |
|---|---|
| 来源身份 | source-hashes-2026-11-15.json；官方STEP、GLB包和WT高度场/设置哈希复核一致 |
| 真实几何导入 | RMUC28资产、WT2资产；实际UE bounds误差分别≤0.000236512cm、0.00390625cm |
| RMUC驾驶 | rmuc2027/terrain-final-02：进程0、7/7、4PNG；1.5°地面支撑，前进/刹车/倒车/转向瞄准/缺地面拒绝/障碍停止 |
| WT斜坡驾驶 | wt_cdk_crop/terrain-final-03：进程0、7/7、4PNG；5.214°支撑平面，高度随前进改变，裁剪边界停止 |
| 独立核对 | 两个geometry-verification.json比较导入源哈希、真实UE bounds、支撑法线与车身四元数 |
| 旧操控保持 | flat-regression-after-terrain：18/18、10PNG；最终转向扫掠改动仅位于terrain分支 |
| 默认入口 | 实际执行Play-ManualVehicle.cmd，GUI显示RMUC2027 V1.0.0 HUD、完整车辆、中央设施与场地 |
| 离线交接 | PowerShell解析、Python编译、Git Bash -n与--help通过；Linux入口复用同一导入器/源码，无Ubuntu原生运行结论 |

## 关键实现

先等碰撞body就绪，再按轮位射线落车。支撑平面控制Z与倾斜；整车box查询拒绝障碍，转向扫掠膨胀覆盖中间角度。瞄准转车体局部坐标，坡上不直接减世界yaw。平地逻辑保持独立，canonical primitive预览仍用import_lab，因为尚无轮位支撑配置。

Windows快捷入口和C++ mesh默认地图均为RMUC2027。传map.json/SpawnId即可换已导入地图；完整Windows/Linux重建入口都会调用同一个地图导入Python。SOURCE.md保存来源和局限。

## 尚未通过的范围

运动学有2cm间隙、5cm台阶和35°坡度限制，保守proxy可能挡住坡折；没有悬挂接触力、轮胎摩擦、实物惯量标定或多层选路保证。原WT车型约1m见方，没有偷偷缩放官方场地。

未实现官方裁判区域、活动机构、射击/伤害、联机、真实ROS算法传感器闭环、坡地导航投影或最终画质。战雷足球场/训练场编译bin未转换；Ubuntu待原生执行。这些不阻塞当前两张地图的手动样例验收，但整个长期目标仍active。
