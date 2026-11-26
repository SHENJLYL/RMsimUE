# RMUC 基地飞镖导轨固定支撑

上版 CAD 分组把导轨下方六个支撑件归进滑台。现将红蓝两侧的支撑台面、底架和连接件归回基地固定网格。每侧归回 2,686 个可见三角面与 1,192 个查询三角面，原 CAD 面数保持守恒。靶板、透镜和滑台外壳沿原导轨一起移动。 [源网格分组](../../artifacts/maps/rmuc2027/fixed-dart-mount-20261126T052525Z/base-armor-source.json)

只重导了固定基地与红蓝滑台的六个显示和查询资产。红方滑台的首次写入遇到占用，导入器在同场完成再次保存，随后三个新 UE 进程重载资产。Windows UE5 原生通过一场固定靶和两场随机移动靶，核验滑台及绿灯的位移、飞镖接触和护甲展开。实际画面能看到支撑台居中，滑台在导轨侧面。 [验收结果](../../artifacts/unreal/rmuc-dart-mount-20261126T053107Z/verification.json) · [居中实拍](../../artifacts/unreal/rmuc-dart-mount-20261126T053107Z/fixed_base-blue/detector.png) · [修正前](../../artifacts/unreal/rmuc-dart-landing-20261126T050910Z/random_moving_base-blue/armor-open.png) · [修正后](../../artifacts/unreal/rmuc-dart-mount-20261126T053107Z/random_moving_base-blue/armor-open.png)
