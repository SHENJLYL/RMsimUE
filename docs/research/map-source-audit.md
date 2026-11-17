# 地图来源与实现边界

核验日期：2026-11-15。当前运行平台：Windows / UE5.8.4。

| 地图 | 实际来源与转换 | 当前结果 |
|---|---|---|
| RMUC2027 | DJI官方V1.0.0 STEP；复用hxyulin/rm-map-tools的同源GLB；独立下载官方STEP核对SHA256 | 14组显示/碰撞网格、CAD原色材质、完整静态场地；默认地图 |
| War Thunder CDK crop | 本机官方CDK location_sample可编辑高度场，裁剪512×512m | 同一运行时已运行；地形含中心和斜坡出生点 |
| 战雷足球场/训练场 | avg_football_field.bin / avg_training_ground.bin，DBLD3x64编译场景 | 已检查头部/大小/哈希；无已验证的转换，未标成已提取 |

[DJI官方发布](https://bbs.robomaster.com/article/1970956)与[转换包发布](https://github.com/hxyulin/rm-map-tools/releases/tag/rm2027-assets-20261015)分别提供原始模型和转换来源。后者明确只有静态rest pose和采样网格检查，没有2027机械控制器或动态验证。本工程的运行证据来自本机UE测试，不能放大成官方动态认证。

[War Thunder官方CDK文档](https://wiki.warthunder.com/cdk/creation_location)说明location_sample是可编辑场景起点，导出的bin是游戏场景。当前从原始高度场提取，没有宣称还原完整游戏场景。

## 坐标与尺度

源GLB声明Z-up，转换器直接读buffer/node变换，避免glTF导入器额外旋转。RMUC内部坐标居中，中央地面最高点Z=0，变换保存在包中。28×16.003m是CAD外部包围尺寸，不替代规则书名义尺寸。

UE用独立隐藏triangle collision mesh，显示mesh不重复碰撞。实际UE bounds与源几何镜像/单位转换比较：RMUC最大差0.000236512cm，CDK裁剪0.00390625cm。这是数值转换误差，不是实物精度。

车辆仍为用户原WT模型当前尺度，约1.008×1.004×0.838m；没有为了通过窄路而缩放官方场地或关闭碰撞。车型尺寸/规则适配须按真实机器人标定。

## 实现与限制

地形跟随使用真实轮位射线、支撑平面和保守整车碰撞查询，保留2cm预览间隙；位移分步≤5cm、转角≤2°、坡度上限35°、支撑台阶/落差上限5cm。它仍可能保守地挡住坡折处，不提供悬挂、摩擦、轮胎、惯量或滑移物理结论；多层路面选层未验收。

RMUC出生坡度约1.5°；WT hillside约5.214°，前进一秒高度从552.55cm变到559.90cm。两地图各7项引擎检查通过，WT另有斜坡7项；旧平地18项操控通过。引擎共用输入路径不等同OS键盘测试。

比赛区域、补给/占点、可动机构、射击裁判、联机、ROS感知闭环和摄影级环境尚未实现。源资产权利与代码许可分别保存在包内SOURCE.md。本次未发布/上传第三方几何。

## 证据入口

- artifacts/maps/source-hashes-2026-11-15.json：源文件当前哈希。
- assets/maps/*/ue-import-report.json：实际导入bounds。
- artifacts/maps/rmuc2027/terrain-final-02：最终7项、4PNG及独立geometry-verification；terrain-01为较早证据。
- artifacts/maps/wt_cdk_crop/terrain-final-03：最终斜坡证据；terrain-01、terrain-hillside-02保留先前运行。
- artifacts/maps/flat-regression-after-terrain：18项、10PNG。
