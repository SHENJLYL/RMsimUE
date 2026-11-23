# RMUC2027：完整防坠落围墙与低开销竞技馆

场地入口为 `assets/maps/rmuc2027/map.json`，竞技馆独立扩展文件为 `assets/maps/rmuc2027/arena-shell.json`。原始赛场视觉/碰撞模型、出生点、导航栅格、定位地图和坐标保持原样。只有 `rm_mesh_map` 且地图包中存在扩展配置时才加载。

## 防坠落结构

从地图声明的 `bounds_m` 精确计算四面边界，设置首尾交叠的四条 `BlockAll` 静态碰撞墙。边界内缩 0.25 m，厚 0.48 m，Z 从 -1.25 m 到 4.8 m。覆盖场地的 X 方向约 28 m、Y 方向约 16 m，角落没有开口。墙体只参与物理碰撞，不产生独立动态阴影；外侧美术立面承担视觉效果。

原生围墙专项测试在东、西、南、北四边及四角，以 12 cm 球形在不同高度做连续扫掠。测试在查询中跳过原本就存在的 14 个场地碰撞网格，以便隔离验证围墙；正常运行时所有原有碰撞仍有效。该测试证明静态阻挡存在，单次扫描不能代替任意速度/角度的完整动力学试驾。

## 场外竞技馆

素材来自 **Kenney Mini Arena 1.0**，许可证为 **CC0**。原包地址、下载哈希、逐文件哈希和完整许可见 `assets/maps/rmuc2027/arena/kenney-mini-arena/SOURCE.json` 与 `LICENSE.txt`。只选入 wall、wall-gate、stairs、column、banner、border-straight 六种 FBX（以及源调色图）；同时保存 UE5.8.4 已导入的六个 `.uasset`，故新 checkout 可直接读取。

外侧采用四圈逐层抬升的看台、开放式顶棚、立柱、简化通道、红蓝比赛灯带与旗帜，当前为 216 个实例、15 个 `UInstancedStaticMeshComponent` 分组。重用少量静态网格与扁平低频颜色，美术组件全部 `NoCollision`、无动态阴影、无 Tick；不建后台房间、看台背面细节、逐座椅网格、动态观众和场外不可见区域。远处形态靠低频轮廓自然弱化；默认不增加全局景深或模糊后处理，避免干扰机器人场内观测与 60 Hz 场景绘制。

实际加载由 `ArenaShell.cpp` 完成：直接打开原地图时从地图文件旁读取扩展配置，RViz 启动器复制地图到会话目录时有受限的 RMUC 包来源查找，随后按地图 ID 和真实 XYZ bounds 再次校验。缺少扩展配置的其它地图维持原有行为，错误的资源引用或边界会拒绝加载。

## 复现与验收

Windows UE 编译完成后：

```powershell
cd D:\simulator
python tests/control/test_rmuc_arena_shell.py -v
python tools/unreal/test_arena_shell.py --editor E:/simulator-tools/UE_5.8/Engine/Binaries/Win64/UnrealEditor.exe --project D:/simulator/unreal/RMSim/RMSim.uproject --output D:/simulator/artifacts/maps/rmuc-arena-new-test
```

两项地图原生证据目录：

- `artifacts/maps/rmuc-arena-20261118/native-guard-02/`：直接打开原 map.json，28/28 实体扫掠、216/15 美术实例、无挂载警告，保留截图。
- `artifacts/maps/rmuc-arena-20261118/staged-preview-01/`：使用此前导航会话导出的 scene.json（作为实际 RViz 启动器入口验证），28/28 通过，能找到原竞技馆包，保留截图。

完整编译与雷达/Nav2/60 Hz 性能应以该轮最终 release-summary.json 及当前进度中的实测为准。新增资源为用户授权的 CC0 素材，其他已来源绑定的 RMUC/CAD 原素材和 Git 忽略规则保持原约束。
