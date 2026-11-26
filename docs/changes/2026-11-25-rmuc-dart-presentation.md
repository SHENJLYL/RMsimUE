# RMUC 飞镖模型、曳光与靶头动画

飞镖显示为曲面镖头、亮环、分段套筒和四片有厚度的后掠尾翼。曳光以尾端的世界坐标采样，保留 0.38 秒，逐段变细变暗。飞镖本体在抵达时隐藏，历史曳光继续消退。灯芯和曳光随发射方着色。

基地沿用原 CAD 导轨与靶头。原靶头从静态网格中拆分，原材质随动，查询网格同步移动。基地固定靶保持居中。随机固定靶在本次探测窗口选定一个位置，平滑移入后保持。随机移动靶在发射后移动至另一位置，末端移动靶在后段才开始移动。前哨使用原顶盖检测点。裁判确认后，靶点播放外扩光束和局部闪光，0.34 秒内消退。基地护甲按原裁判规则展开。

## Windows UE5.8 验证

- `tools/unreal/Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8` 返回 `Result: Succeeded`。运行模块 SHA-256 为 `265a1a65776c19f3985746937dd9607d7ee69e1934198de4d1f8da0548765b95`。
- 四套 C++ 裁判回归通过。D3D12 双车比赛离屏运行六场，覆盖蓝方五种目标模式和红方随机移动靶。每场均有 128 项场地和 76 项基地装甲检查，退出码为 0。验证了靶头起始、中段和收尾位置、有效命中、命中余辉、曳光回收及移动靶命中后护甲展开。
- `tools/unreal/Prepare-RmucRune.ps1 -BaseArmorOnly` 重建基地派生包。两处靶头分别继承 8,884 个视觉三角面与 7,280 个查询三角面。固定、护甲和靶头的源三角面总数守恒。 [CAD 派生回执](../../artifacts/maps/rmuc2027/base-dart-cad-20261125T084755Z/base-armor-source.json)
- UE 画面沿用赛场曝光。证据来自比赛关卡内的独立摄像机。测试探针快进首个开闸机会，飞行和动效使用引擎时间推进。

[原生回执](../../artifacts/unreal/rmuc-dart-art-20261125T085032Z/verification.json) · [蓝方镖身](../../artifacts/unreal/rmuc-dart-art-20261125T085032Z/random_moving_base-blue/dart-model.png) · [蓝方曳光](../../artifacts/unreal/rmuc-dart-art-20261125T085032Z/random_moving_base-blue/dart-trail.png) · [红方曳光](../../artifacts/unreal/rmuc-dart-art-20261125T085032Z/random_moving_base-red/dart-trail.png) · [命中实拍](../../artifacts/unreal/rmuc-dart-art-20261125T085032Z/late_moving_base-blue/impact.png) · [护甲展开](../../artifacts/unreal/rmuc-dart-art-20261125T085032Z/late_moving_base-blue/armor-open.png)
