---
doc_type: acceptance
status: windows_scoped_verified
updated: 2026-11-18
feature: rmuc2027-arena-shell
---

# RMUC2027 四周连续碰撞围墙与 CC0 竞技馆

## 交付范围

- 针对当前 `rmuc2027` 的原坐标、原比赛几何 bounds，在 `arena-shell.json` 中定义四条没有缝隙的静态 `BlockAll` 守护墙；底部 -1.25 m、顶部 4.8 m，宽 0.48 m，四边距地图包络边缘内缩 0.25 m。
- 场外 CC0 素材复用：Kenney Mini Arena 1.0 的六种 FBX 和已导入的六个 UE static mesh；保留许可证和逐文件哈希。看台、立柱、旗帜、顶棚/灯带共 216 个实例、15 组，全部 NoCollision、无动态阴影/独立 Tick。
- 独立于原 `MapStage` CAD 几何、Point-LIO 定位地图、规划栅格和车包。原 mapa 身份/bounds 检查仍严格；普通 MapFile 与 RViz staged scene.json 两种入口都能加载相同扩展。其他无扩展的地图仍按原渲染。
- 赛场内优先保证轮胎/底盘的真实碰撞，场外后台、观众、看不到的房间与逐座椅几何省略。使用粗轮廓与低频色彩降低远景细节；不引入景深以免改变机器人视场。

## 实际原生证据

| 证据目录 | 内容 | 结果 |
|---|---|---|
| `artifacts/maps/rmuc-arena-20261118/import-01` | 原 UE Editor 导入六个固定 CC0 FBX | 通过 |
| `artifacts/maps/rmuc-arena-20261118/isolated-build-03` | 不覆盖正在使用 UE DLL 的隔离工程 Windows UBT 编译 | 通过 |
| `artifacts/maps/rmuc-arena-20261118/native-guard-02` | 原 RMUC map.json、28 条不同高度/边角的原生碰撞扫掠、216/15 实例、组件挂载无警告，含截图 | **28/28 通过** |
| `artifacts/maps/rmuc-arena-20261118/staged-preview-01` | 原导航 session/scene.json、查找原地图包、完整围墙与实例、截图 | **28/28 通过** |
| `artifacts/maps/rmuc-arena-20261118/canonical-build-01` | 在无本项目 UE 进程占用时构建实际工作工程 | **通过** |
| `artifacts/maps/rmuc-arena-20261118/realtime-02` | 加载围墙/场外美术后，实际 Windows UE + 原 ROS 2 Point-LIO/Nav2/手动已验证车包，90s 真实时间测量 | **导航14/14、实时/Draw/传感器32/32 通过** |

性能详情（真实场景 Draw、无 ROS RGB 图像采样，保留完整 Mid-360/IMU）：实测 **1.0001084×** 实时倍率，**59.9706 Hz** 场景绘制；滚动10秒窗口0.99799–1.00168×；20万射线/秒、10Hz扫描、双IMU 200Hz。实际单点误差 **0.07172 m**，双航点终点 **0.06266 m**，取消停车约0.1226m且后续漂移0m。

防坠落验证采用 Chaos sweep 查询。专项中只屏蔽原先已有的14个地图碰撞演员，避免旧网格掩盖围墙命中；生产场景仍全部保留这些碰撞。实际驶车高能量撞击多个边角、全场所有出生点和重建地图后的长时稳定性继续按后续路线增加，专项测试不外推到未经覆盖的场景。

## 修复记录

- 最初所有静态 ISM 均因挂在 Movable 根节点而被 Unreal 拒绝附着；已把 ArenaRoot 设为 Static，复测未出现挂载警告。
- 首轮围墙扫掠26/28，另两条起点命中已有 CAD 碰撞网格。现在只在围墙专项查询中排除原地图碰撞，28/28全通过。
- 最初工作工程的 UE DLL 被其他已授权 RViz 会话占用，编译链接出现 LNK1104。后续先独立编译隔离工程，确认进程退出后再编译实际工程；没有强制关闭其他用户进程。
- 现有场地渲染/定位来源的哈希与原坐标不变；特殊 CC0 资源及组件编号均进入单独来源记录。最终 Git 发行记录独立附在同一功能目录。

## 剩余范围

多场地图包的竞技馆模板、详细体育馆风格进一步打磨、站台细节的美术评价、带 ROS RGB 相机10Hz 的全负载实时验证、Windows/Ubuntu 原生双平台、多人比赛与完整裁判系统继续按总目标推进。当前成功范围为 RMUC2027 单车限定线路。

## Final matte-material rerun (separate evidence scope)

With the eight constant-material assets and 15 instance groups compiled into the actual WIP UE project, the *realtime-matte-02* session passed original Nav2 14/14 plus time/sensor 32/32: 90-second factor 0.99998138x, true single-goal error 0.15413 m, two-waypoint endpoint error 0.11160 m, cancellation braking 0.10007 m and subsequent drift 0 m. This is the **final matte-material combination**. Earlier *realtime-02* errors 0.07172/0.06266 m were obtained before adding the matte palette, and should be attributed only to that prior material revision.

The independently checked clean Git worktree passed full UE build and *clean-baseline-native-01*: eight independent engine/scene assertions including 28/28 boundary sweeps, 216 visual instances in 15 groups and two screenshots. That branch uses the same ArenaShell.cpp SHA-256 as the integrated WIP runtime, but the ROS 5 m/s timing and localization changes live in a separate, uncommitted development workspace.
