# RMUC 2027 大小能量机关灯效与选手端

以官方 V1.0.0 规则 §5.6.3 和图 5-18 至 5-22 为基线。规则 PDF SHA-256 为 `9e2eed9295536c6fd547b62d634df1d5e70a2e1e2250e5dff3521d557e5d54be`。规则明确了灯效状态与组间熄灭上限。呼吸频率、渐变时间和箭头流动周期是仿真呈现参数。 [官方规则发布页](https://bbs.robomaster.com/wiki/20204847/1969149?source=7)

原场地两面 CAD 转子和装甲碰撞保持同一物理位置。120 个仅渲染灯效段随转子运动，覆盖待击靶环、向外流动箭头、首击确认、大机关全灯臂的五段组进度和成功全亮。小机关累计点亮命中的灯臂，大机关完成组后按五分之一逐段扩展。组间和失败重置时短暂熄灯，到期渐灭。已有 CAD 包可运行 `tools/unreal/Prepare-RmucRune.ps1 -RuneLightsOnly` 重建专用发光材质。

标准比赛视图通过 `T` 请求机关，显示剩余机会、激活进度和增益倒计时。虚拟 S7 的本地 `T` 走裁判确认入口。正式步兵自定义客户端可以通过本机 MQTT 3.1.1 发送 `RuneActivateCommand`，从 `RuneStatusSync` 读取各自阵营的响应。支持配置红方 3/4 与蓝方 103/104，只有授权连接可申请开启激活窗口。MQTT 入口通过 `Start-Simulator.ps1 -RuneClientBridgePort 9461` 显式启用，配合本机网关与凭据。 [网关启动说明](../../tools/integration/rmuc-rune-client/README.md)

## Windows 验收

UE 5.8 构建、四套裁判规则测试和原生 MQTT 3.1.1 测试通过。蓝方 103 的请求测自真实 MQTT 连接，实测弹丸来自开发探针的虚拟试验发射点，命中经过原生旋转装甲查询和比赛裁判积分。Windows 复现入口为 `python -B tests/referee/verify_rune_lifecycle_windows.py`，两轮增益都按比赛时钟结算，开发探针只在小机关结束后快进至大机关赛时阈值。

| 机关 | 原生结果 | 增益 | 组间实测最大熄灯 |
| --- | --- | --- | --- |
| 小 | 单亮靶，5 组 5 次有效命中 | 25% 防御，45 秒 | 105 毫秒 |
| 大 | 双亮靶，5 组 10 次有效命中，平均 9.9 环 | 300% 攻击倍率、50% 防御、5 倍冷却，60 秒 | 105 毫秒 |

录下每轮亮靶、进度、成功和到期四个画面。两轮均观测到箭头位置与待击灯亮度变化，增益清除后灯带渐灭。客户端均收到未激活、正在激活、已激活、回到未激活，UE 场地装置查询 128/128 通过。 [原生回执](../../artifacts/unreal/rmuc-rune-lifecycle-20261125T010418Z/receipt.json) · [大机关亮靶与箭头](../../artifacts/unreal/rmuc-rune-lifecycle-20261125T010418Z/large-targets.png) · [大机关到期](../../artifacts/unreal/rmuc-rune-lifecycle-20261125T010418Z/large-expired.png) · [客户端回执](../../artifacts/unreal/rmuc-rune-lifecycle-20261125T010418Z/mqtt-client.jsonl)

外部验收客户端按官方 MQTT 和 Protobuf 消息联调，DJI 发布的成品选手端尚无本机连接记录。现有比赛实体为双哨兵，步兵经验系统沿用此前工程边界。
