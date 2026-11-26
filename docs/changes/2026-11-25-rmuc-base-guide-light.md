# RMUC 基地绿色透镜照明位置

旧点光源放在靶板中心上方，距 CAD 绿色透镜的面中心约 11.5 cm。根据两侧原 CAD 的 `RM27_M22` 透镜三角面面积加权中心和法向，将光源放到透镜外侧并改为朝外的聚光灯。灯仍挂在滑台上，沿原透镜朝向照明。

Windows UE5.8 构建成功。比赛原生复验覆盖固定靶以及红蓝随机移动靶，灯的挂载、位置、朝向、查询位移、飞镖命中和护甲展开均通过。同机位固定靶画面的上方靶板区域中，绿通道比其余两通道高 20 个色阶以上的像素从 676 降至 0，正面透镜区域为 898 → 898。 [原生验证](../../artifacts/unreal/rmuc-dart-guide-20261125T090717Z/verification.json) · [修正前](../../artifacts/unreal/rmuc-dart-carriage-20261125T090151Z/fixed_base-blue/detector.png) · [修正后](../../artifacts/unreal/rmuc-dart-guide-20261125T090717Z/fixed_base-blue/detector.png)
