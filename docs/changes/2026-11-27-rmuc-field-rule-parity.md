# RMUC2027 复活、后勤与场地增益

## 裁判

按 V1.0.0 §5.3.2、§5.4.2、§5.6.1 和 §5.6.4.5 更新。自然复活的读条按战亡时比赛时间和买活次数计算，再按当前后勤系数缩放。位于己方补给区或己方基地血量低于 4000 时，读条每秒增加 4。自然复活给 10% 满血和最多 30 秒无敌，读取有效实体增益卡解除虚弱，并保留到复活后最少 10 秒的无敌。未读卡时，30 秒无敌结束仍有虚弱。付费满血复活带 3 秒虚弱及无敌和 4 秒底盘功率翻倍，功率上限 200 W。 [官方规则手册](https://bbs.robomaster.com/wiki/20204847/1969149?source=7)

哨兵每分钟累积 100 发免费额度，激活己方补给区后领取。五类区域原有的付费购弹资格继续由有效增益判定。红、蓝各有独立的 1,000 发付费额度，步兵和哨兵共用。远程订单接单时占用额度，到账和免费补给不会再次计入。

## 读卡和视觉

高地从同侧低卡读取到高卡限时 5 秒，中途其他卡打断。成功后获得 30 秒 25% 防御，战亡清除，虚弱时不持有跨越增益。同侧中央高地的持续 25% 防御仍由上位卡独立触发。

正式地图重新比对官方图 5-23 和 CAD 支撑网格。14 个主要区域在原生 UE 中进行了 110 个支撑面、内外边缘和基地孔洞复核。两个低位卡区各有 82 个离线支撑采样，全部位于源图标识和 CAD 允许面。12 个原有区的抽样包含 16 个堡垒低坡缘点，按既定的支撑高度规则排除。辅助隧道条带的图示与支撑面少量分歧留在独立报告中。 [原生边界](../../artifacts/verification/rmuc-rule-parity-20261127/boundaries/native-optical/verification.json) · [CAD 与官方图配准](../../artifacts/verification/rmuc-rule-parity-20261127/highland/summary.json)

按图 4-24 制作并安装红蓝 340 mm 补给区标签。车载相机取实际挂载位姿，以合法补给区、画面中心、滚转和几何遮挡确认标签，读卡与视觉检测分开报告。公开图文未提供设备识别阈值，仿真选择中央 50% 画面和 15° 滚转作为可复测的参数。 [标签与 CAD 位置](../../assets/maps/rmuc2027/supply-tags/SOURCE.md)

## 验收范围

Windows 原生红蓝复活各通过 14 项。场外自然读条耗时 10.005 秒，己方补给区内 2.505 秒。基地为 4000 与 3999 血量时，速度分别为 1× 与 4×。补给区使用真实读卡，等待无敌到期后再读取基地卡的用例由开发探针注入。 [复活回执](../../artifacts/verification/rmuc-rule-parity-20261127/respawn/20261127T032354151767Z/verification.json)

红蓝付费账本分别测试了临界额度、重复请求、远程交付和双方隔离。远程订单均在 6.005 秒后交付。时钟夹具推进到 390.105 秒后，已达付费上限的队伍仍可给哨兵发放累计 600 发免费补给。 [红蓝账本回执](../../artifacts/verification/rmuc-rule-parity-20261127/ammo/native-deployed/verification.json)

两侧图传的标签分别与官方图对照，手工标定检查区域后，49 格均相符。两车关闭模拟实体读卡，在相机对准时各以 10 金币购得 10 发。背向标签和在区外对准均不激活。裁判直接使用车载光学几何与遮挡完成模拟识别。 [红方图传](../../artifacts/verification/rmuc-rule-parity-20261127/supply-vision/red-center-tiles/fpv.png) · [蓝方图传](../../artifacts/verification/rmuc-rule-parity-20261127/supply-vision/blue-center-tiles/fpv.png) · [图案逐格比对](../../artifacts/verification/rmuc-rule-parity-20261127/supply-vision/glyph-check-tiles.json)

原生高地夹具用真实 CAD 支撑面读到低卡和高卡，再将两次读取按合规时序送入裁判，两侧均得到 30 秒增益。红蓝都实车驶入低卡区，单独停在高卡不获跨越奖励。中央直驶被原 CAD 台阶阻挡，红侧绕行读到了隧道卡并中断，当前 AIM 车辆连续驶完合规路线仍待验证。 [高地实车与裁判记录](../../artifacts/verification/rmuc-rule-parity-20261127/highland/summary.json)

分层 CAD 搜索使用 0.125 m 栅格、约 0.419 m 车体净空和连续表面高度检查。给定测试约束下未找到可在 5 秒内完成的 AIM 路线。综合回执保留各专项实际使用的 Windows 模块和记录位置。 [综合回执](../../artifacts/verification/rmuc-rule-parity-20261127/verification.json)
