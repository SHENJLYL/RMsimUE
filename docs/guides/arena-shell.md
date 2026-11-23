# RMUC2027：旧版防坠落围墙与竞技馆

当前默认场馆采用校园体育馆，见 `docs/guides/campus-gym.md`。本页记录保留的 Kenney 旧版竞技馆；其配置存于 `assets/maps/rmuc2027/arena-shell.legacy.json`。原始赛场视觉／碰撞模型、出生点、导航栅格、定位地图和坐标保持原样。只有 `rm_mesh_map` 且地图包中存在扩展配置时才加载。

## 防坠落结构

旧版从地图声明的 `bounds_m` 计算四面边界，设置首尾交叠的四条 `BlockAll` 静态碰撞墙。边界内缩 0.25 m，厚 0.48 m，Z 从 -1.25 m 到 4.8 m，覆盖场地的 X 方向约 28 m、Y 方向约 16 m。墙体参与物理碰撞，外侧美术立面承担视觉效果。本轮装饰清理保留该历史版本的边界参数；贴合源场地外边缘的改动有独立验证记录。

原生围墙专项测试在东、西、南、北四边及四角，以 12 cm 球形在不同高度做连续扫掠。查询时跳过原本就存在的 14 个场地碰撞网格，以便隔离验证围墙；正常运行时所有原有碰撞仍有效。完整动力学试驾另行记录。

## 场外竞技馆

素材来自 **Kenney Mini Arena 1.0**，许可证为 **CC0**。原包地址、下载哈希、逐文件哈希和完整许可见 `assets/maps/rmuc2027/arena/kenney-mini-arena/SOURCE.json` 与 `LICENSE.txt`。当前导入和运行使用 wall、wall-gate、stairs、column、border-straight **五种模型**。原始下载包保留六种 FBX 的来源和哈希；banner 原文件存档。

外侧采用四圈逐层抬升的看台、开放式顶棚、立柱、简化通道和红蓝比赛灯带。**八面装饰旗帜已移除，现为 208 个实例、13 个 `UInstancedStaticMeshComponent` 分组**，其中原有八个边框装饰保持。静态网格共享材质，美术组件均为 `NoCollision`、无动态阴影、无 Tick。

实际加载由 `ArenaShell.cpp` 完成：直接打开原地图时从地图文件旁读取扩展配置，RViz 启动器复制地图到会话目录时有受限的 RMUC 包来源查找，随后按地图 ID 和 XYZ bounds 校验。

## 复现与验收

旧版验证使用独立地图副本：其同目录 `arena-shell.json` 采用 `arena-shell.legacy.json` 的内容。Windows UE 编译完成后运行：

```powershell
cd D:\simulator
python tests/control/test_rmuc_arena_shell.py -v
python tools/unreal/test_arena_shell.py --editor E:/simulator-tools/UE_5.8/Engine/Binaries/Win64/UnrealEditor.exe --project D:/simulator/unreal/RMSim/RMSim.uproject --map-file D:/simulator/artifacts/fixes/rmuc-branding-20261119/legacy-scene/map.json --output D:/simulator/artifacts/maps/rmuc-arena-new-test
```

本轮清理与验证见 `docs/changes/2026-11-19-rmuc-branding.md`。下列旧版证据保留其当时版本统计：

- `artifacts/maps/rmuc-arena-20261118/native-guard-02/`：28/28 实体扫掠、216/15 美术实例、无挂载警告，保留截图。
- `artifacts/maps/rmuc-arena-20261118/staged-preview-01/`：通过此前导航会话导出的 scene.json 验证来源查找，28/28 扫掠通过，保留截图。

当前原生构建、导航与性能结果以各自 release-summary.json 及当前进度中的实测为准。原素材来源记录及 Git 忽略规则保持。
