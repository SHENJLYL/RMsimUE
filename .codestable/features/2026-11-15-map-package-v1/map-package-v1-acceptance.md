---
doc_type: feature-acceptance
feature: 2026-11-15-map-package-v1
status: passed
summary: Two flat map packages run in native Windows UE with coherent geometry, spawn, region and static occupancy evidence.
tags: [map, unreal, acceptance]
date: 2026-11-15
---

# 地图包v1验收

## 1. 契约

map.json → FMapStage/UE实际geometry与spawn；同一数据 → rm_map_tools/PGM/YAML。米/ROS坐标只在UE边界转换一次。明确支持平地box地图，inspection_obstacle_id可选。

## 2. 决策与实现

场地已从GameMode硬编码块移到MapStage.cpp/h。Run/Start/Windows双击入口及Linux脚本可传MapFile/SpawnId。物理body未就绪前不开放控制输入；完整车体重叠即失败，语义区域不计入阻挡。没有裁判得分或自动导航假数据。

## 3. 场景证据

| 检查 | 结果与证据 |
|---|---|
| 数据边界 | Python 9项测试通过：未知/重复/非有限值、越界几何、阻塞出生、旋转box、空地图等 |
| 双地图原生运行 | lab-final和warehouse-ready各9/9移动碰撞相机检查、各6PNG |
| 几何/栅格/出生一致 | verify_runtime_map.py比较实际UE bounds/pose/region和PGM来源，两个verification.json为passed |
| 非法整车出生 | overlap-rejected-v2失败理由为vehicle collision proxy overlaps geometry at selected spawn，输入前拒绝 |
| 空地图 | empty-native地图报告为0障碍、无初始重叠，实际生成截图 |
| 操控保持 | control-regression-default-v2保持18/18通过 |
| 视觉 | 已查看两张实际UE俯视PNG：布局、区域位置、车辆出生不同且符合描述 |

首轮非法出生测试暴露初始化时物理查询尚未就绪的问题，已修复并保留失败/修复后证据。失败记录未删除或改成成功。

## 4. 术语

地图区域只是元数据和位置查询，导航栅格只是静态projection。两者不等于完整裁判或定位算法。

## 5. 架构归并

MapStage是场地geometry/spawn/region所有者；GameMode只协调车辆、光照与观察。rm_map_tools是离线验证与占用导出入口。源资产和生成证据按类型分开。

## 6. Requirement

extensible-worlds-vehicles增加已运行的平地地图子集，广义地图、车型动力学及跨平台愿景仍未全部完成。

## 7. Roadmap

map-package-v1按以上有界契约done；原生Ubuntu、裁判、联机、真实传感器与复杂地形仍为后续工作。

## 8. 持续约束

报告输出必须新路径；不能覆盖输入或旧证据。UE5.8 JSON key内部使用FSharedString，字段校验采用公共HasField接口避免错误隐式转换。

## 9. 限制

没有实际运行Nav2/Point-LIO；没有mesh场地、坡道、多层结构或材质摩擦标定；Linux只检查过脚本语法。当前地图不是官方比赛规则验证场。
