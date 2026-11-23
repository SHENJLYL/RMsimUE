# RMUC 稳态 MPC 发布清单校验修复

当前默认 YAML 的 SHA-256 是 `75cd653d024e2751a16e65349b83bee6bca2dc33f17e91d659543c56259f139a`。更新时它写入了清单的顶层 `source_sha256`，启动器读取的 `simulation_navigation.sha256` 仍为 `ab157ba3b0c1fe5050872e4430de76f2d1788c3867a410606d362fb2feb306c5`。已同步 `simulation_navigation.sha256`。原始 MPC YAML、地图身份和两份网格校验值保留。后续每次更换默认 YAML 时同步此嵌套字段。

实际地图包通过规划图、定位图、三个地形引用与 Nav2 参数预检，`curve_clearance` 为 0.62。以 `Start-NavigationConsole.ps1 -Scene rmuc2027 -Offscreen -NoRviz -RunSeconds 3 -Domain 215 -Port 19513 -PaceRealTime -RenderScale 50 -ViewportHz 120` 原生离屏启动。首次验证受失配的工具进程 Git 环境变量影响，清理此次测试子进程变量后成功。UE5 和 ROS 导航达到 `READY`，运行完毕退出码 0。证据目录 `artifacts/navigation/mpc-manifest-smoke-20261123T170707Z`。本次验证覆盖启动，未复测跨场耗时。
