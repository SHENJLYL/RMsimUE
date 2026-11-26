# RMUC 2027 能量机关自定义客户端接入

此工具为 RMUC 仿真提供本机 MQTT 3.1.1 服务器。自定义客户端发布 QoS 1 的 `RuneActivateCommand`，订阅 QoS 1 的 `RuneStatusSync`。消息主题与字段沿用 RMUC 2027 V1.0.0，状态按客户端所属阵营以 1 Hz 发布。比赛时间、次数、两侧状态和实际转盘均由同一 UE 裁判管理。 [RMUC 2027 规则发布页](https://bbs.robomaster.com/wiki/20204847/1969149) · [协议定义及频率表](https://github.com/BUAA-Transistor-alg/RM2027-CustomTerminal-CommunicationBackend/tree/46757616c60aba1ec4e448c82b2dbed395767df2)

需要 Node.js 20 或更新版本。首次使用在本目录运行 `npm ci --ignore-scripts`，可用 `node --test sidecar.test.mjs` 检查原生 MQTT、Protobuf 载荷、红蓝状态隔离与身份拒绝。

## 启动

1. 设置 16 到 128 位随机字符串作为 `RMSIM_RUNE_BRIDGE_TOKEN`，让 UE 与 sidecar 继承同一值。UE 需显式启用 `-RuneClientBridgePort=9461`。
2. 在 sidecar 和外部客户端各自的终端设置对应编号的 `RMSIM_RUNE_OPERATOR_<编号>_PASSWORD`。不同编号使用独立密码。Sidecar 只允许显式配置的红方 `3`、`4` 或蓝方 `103`、`104` 连接。
3. 在 sidecar 终端运行以下示例，启用红方 3 号和蓝方 103 号。MQTT 监听环回地址 `127.0.0.1:3333`，其中 `--mqtt-port` 可修改端口。

```powershell
node sidecar.mjs --ue-port 9461 --operator 3 --operator 103
```

4. 将外部客户端指向该 MQTT 地址，使用实际选定的编号作为 Client ID 与用户名，使用对应密码。打开 MQTT 3.1.1 clean session。随附的协议联调客户端可在同一次比赛发起激活并等待「正在激活」状态。

```powershell
node client.mjs --id 3 --activate --expect 2
```

用 `node client.mjs --id 103` 单独读取蓝方状态。使用 `--port` 指定自定义 MQTT 端口。开通或拒绝请求的裁判回执会出现在 sidecar 控制台，原协议客户端通过 `RuneStatusSync` 观察状态。此入口覆盖能量机关的两条消息。第三方客户端若未提供用户名、密码和服务器地址配置，需要通过其自身配置或源代码开放这些选项。
