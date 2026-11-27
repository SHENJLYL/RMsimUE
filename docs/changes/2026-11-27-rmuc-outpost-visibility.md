# RMUC 前哨站停转选位与视觉切换

现有视觉包含前哨专用贴纸模板和 `outpost` 标签。真实双车对局与独立录包都观察到了前哨识别。双车使用 640 × 480、15 Hz 图传，三块装甲的高度和朝向不同。转动期间会出现板面变斜、两条灯条像素变窄、相邻板接替，以及少量把场地物识别成车辆的短暂候选。

- 虚拟前哨停在原 CAD 零位时，红蓝各使用地图包的优先观察位。当前两处代表视角与板面法线约成 11.1°，原首选点约 29.8°。UE 近景截图可见两条灯条，活动导航栅格两侧候选与原点在 0.45 m 障碍余量下都连通。无伤害回执或导航失败时，会切换备用点。
- 所有车型沿用原视觉识别类别。仿真前哨战术中，独立的三个图像观测需要维持同一车辆标签和跟踪编号，才会由视觉候选触发追击。重复发布的同一帧只计一次。红蓝隔离 C++ 夹具均用未入编的 4 号步兵标签测试了单帧保持前哨任务和三帧后允许追击。
- 统一启动器按 S7 标准出生点绑定现有自动哨兵席位。单席和双席的 Windows DryRun 均通过，地图中的其他车型出生点仍参与其自身场景配置。

三场 UE 视角与场地检查都通过 128 项查询。第一场基线中，转动期前哨血量从 1000 降至 800，停转后降至 680。停转触发了新导航目标，采样结束时 Nav2 还报告约 0.9 m 到点距离。该试验用同一张场地图的标准 S7 出生点副本，对手停在补给区。 [视线抽样](../../artifacts/verification/rmuc-outpost-visibility-20261126/cad-camera-visibility-3d.json) · [导航栅格](../../artifacts/verification/rmuc-outpost-visibility-20261126/navigation-grid-check.json) · [旧观察位](../../artifacts/verification/rmuc-outpost-visibility-20261126/parked-original-view/viewport.png) · [停转优先位](../../artifacts/verification/rmuc-outpost-visibility-20261126/parked-frontal-view/viewport.png) · [第一场原生录包摘要](../../artifacts/verification/rmuc-outpost-visibility-20261126/blue-native-match-03/summary.json) · [红方 C++ 验证](../../artifacts/verification/rmuc-outpost-visibility-20261126/red-handoff/summary.json) · [蓝方 C++ 验证](../../artifacts/verification/rmuc-outpost-visibility-20261126/blue-handoff/summary.json)

按实际名册屏蔽识别类别的试验已按用户纠正撤回。该轮对照留在历史证据目录。ROS 桥和视觉节点重建后恢复原有类别链路。新实现仅凭观测重复性控制仿真行为树的切换。

最后一轮用恢复后的原视觉节点和新版行为树运行 Windows UE。以首次进入前哨任务后的同一段 42 秒比较，原来识别消息仍出现 5 条步兵候选，切换追击 5 次，目标前哨从 1000 降至 980 HP。新版仍输出 5 条步兵候选，视觉误追为 0 次，目标从 1000 降至 840 HP。该轮对手驻停补给区，短场景结果独立于完整实战命中率。 [前后共同窗口](../../artifacts/verification/rmuc-outpost-visibility-20261126/final-handoff-comparison.json) · [最终原生录包摘要](../../artifacts/verification/rmuc-outpost-visibility-20261126/blue-handoff-native/summary.json)
