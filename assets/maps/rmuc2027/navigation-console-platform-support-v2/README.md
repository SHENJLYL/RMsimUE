# 本地导航高程网格

RMUC 模拟导航随地图包提供默认稳态 MPC 配置，已有跨场记录为 32.362 秒。通过 `--navigation-source` 提供的配置优先。[当前配置与证据](../../../../docs/changes/2026-11-24-simulator-snapshot.md)

默认配置需要两个与 `navigation-release.json` 的 SHA-256 一致的本地文件。`terrain-ground.bin` 含墙边掩码，供两个地形节点使用。`terrain-support.bin` 是局部和全局避障体素层使用的支持面。按项目约定，两份生成网格都由原资产离线恢复。

先准备地图包的规划栅格。若本地缺少 `map.pgm`，可将 [RMUC 原资产说明](../SOURCE.md) 中转换包的 `full-map-collision.glb` 置于 `artifacts/sources/rmuc2027/optimized/rmuc2027/`，再从仓库根目录运行现有恢复命令。

```powershell
python tools/maps/export_boundary_navigation_package.py --restore-package assets/maps/rmuc2027/navigation-console-platform-support-v2/SOURCE.json
```

使用 [RMUC 原资产说明](../SOURCE.md) 中的已下载转换包，取其中的 `full-map-collision.glb`，然后从仓库根目录运行下列命令。需要 Blender 5.1.2，以及安装了 NumPy 和 PyYAML 的 Python。命令会验证原资产和两个输出网格的 SHA-256。

```powershell
python assets/maps/rmuc2027/navigation-console-platform-support-v2/restore_navigation_ground.py --blender <Blender可执行文件> --collision <full-map-collision.glb路径>
```
