# 外部 sentry_ws 连接器的 RViz 启动修复

外部入口先用配置的出生点生成 RViz 俯视参数，再把配置字典序列化写入 `.rviz`。RViz 固定帧和显示坐标系使用传入的 RobotId。配置完成后启动 Point-LIO、导航和 RViz。

默认连接复用 RMUC 导航地图包，在录制目录生成规划地图与相邻元数据。定位地图从地图包声明选择。地图身份使用运行中 UE 加载的场景文件字节，AMCL 初值使用所选出生点。界面传入当前地图和出生点，显式外部栅格仍按绑定的场景身份验证。

在 Windows 原生执行 `Start-ExternalSentry.ps1 -Domain 213 -RobotId sentry_rviz_probe`，确认加载有效 `.rviz`、创建原生 RViz 窗口以及两项 ROS 进程初始化。规划地图的 `source_md5` 与纯仿真入口的地图文件一致。隔离域未运行 UE，导航等待仿真状态。`test_navigation_console.py`、`test_navigation_spawn_prior.py` 和 `test_navigation_goal_source.py` 合计 30 项通过。验收目录为 `artifacts/navigation/external-20261123T112617Z-38604`。

## 纯仿真桥接恢复与地图显示

现有 UE 已监听 TCP，Point-LIO 等待传感器，Nav2 尚未启动地图节点。独立复现旧启动命令得到 `ParserError: InvalidEndOfLine`、退出码 1。纯仿真改为调用 `Start-RosBridge.ps1`，通过独立参数传递 RobotId、ROS Domain 和端口，并保存桥接日志。随 UE 关闭时停止其桥接子进程。

现有会话 `artifacts/navigation/external-20261123T113222Z-28124` 使用修复后的入口补接桥进程。UE 与 RViz 保持打开，Point-LIO 与导航进入 `running`，`localization_map_verified=true`，RViz 创建 582×344 的 RMUC 地图和 200×200 的局部代价地图。桥接日志为 `artifacts/ros2/simulator-bridge-38228`。`Start-Simulator.ps1 -DryRun -RosBridge -RosControl -RosImu -RosLidar` 及三个变更脚本的 PowerShell 解析检查通过。
