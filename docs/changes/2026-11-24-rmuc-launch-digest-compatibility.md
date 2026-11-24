# RMUC 曲率规划 MPC 启动校验

启动器报错前，MPC YAML 的 `v_linear_max` 更新到 4.6 m/s。按最终字节计算，配置 SHA-256 为 `dabc8441fdd496754afa57f7519d6762c5da3af7e739d3d1ea375cc852d3f4b2`。清单只更新了顶层 `source_sha256`，嵌套 `simulation_navigation.sha256` 仍指向旧配置，因而在 ROS 启动前失败。已将当前哈希移至规范字段。

旧清单若只更新顶层哈希，启动器会用它核对 YAML 原始字节并提示迁移。两个哈希均不匹配时启动失败，并给出应更新的字段和实际哈希。地图源清单、规划栅格和地形网格继续独立校验。项目交接约定已记录每次改完参数后同步规范字段并验证默认入口。

## Windows 验证

默认配置预检通过，三个地形引用均绑定到已校验的本机支持网格。12 项导航控制台测试通过，覆盖旧式哈希、两个哈希均失配和支持网格失配。

`Start-NavigationConsole.ps1 -Scene rmuc2027 -Offscreen -NoRviz -RunSeconds 3 -Domain 216 -Port 19514 -PaceRealTime -RenderScale 50 -ViewportHz 120` 达到 `READY: UE5 and ROS navigation`，正常退出 0。生成配置确认 `v_linear_max: 4.6`。日志及录包位于 `artifacts/navigation/mpc-manifest-smoke-compat-20261124T055030Z`。本次覆盖启动，跨场耗时和 RViz 窗口留待对应运行验证。
