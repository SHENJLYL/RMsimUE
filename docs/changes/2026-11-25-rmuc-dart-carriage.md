# RMUC 基地飞镖滑台总成

红蓝基地的原 CAD 绿灯透镜、外壳、护边、下方滑块和靶板共用一套滑台变换。绿灯点光挂到滑台网格，显示与查询网格使用同一个偏移。横导轨和两端限位保持在基地固定部分。

每侧滑台复用源 CAD 的 16,111 个可见三角面与 10,988 个查询三角面。源面总数在固定网格、活动护甲和滑台之间守恒。 [源网格派生回执](../../artifacts/maps/rmuc2027/base-dart-carriage-20261125T090103Z/base-armor-source.json)

Windows UE5.8 重新生成基地资源并成功构建。原生复验五次，覆盖四种基地靶位模式和红方随机移动靶。实测滑台、绿色点光和查询网格位移同步，命中和护甲展开通过。每场 76 项基地装甲查询与 128 项场地装置检查通过。 [原生验证](../../artifacts/unreal/rmuc-dart-carriage-20261125T090151Z/verification.json) · [居中实拍](../../artifacts/unreal/rmuc-dart-carriage-20261125T090151Z/fixed_base-blue/detector.png) · [滑动后实拍](../../artifacts/unreal/rmuc-dart-carriage-20261125T090151Z/random_moving_base-blue/impact.png)
