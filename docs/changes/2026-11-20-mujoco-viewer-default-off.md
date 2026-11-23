# 标准入口默认关闭 MuJoCo 观察窗

`Play-Navigation.cmd` 默认打开 UE5 和 RViz。手动、训练、战雷及 Linux 手动入口原已省去独立 MuJoCo 窗口。导航启动器现在也跳过观察窗渲染环境预检、进程启动和专用 UDP 快照链路。UE 内部仍使用原生 MuJoCo 独立计算线程，ROS 桥、Point-LIO、AMCL、Nav2 和 RViz 正常启动。

需要检查原生碰撞或复现旧三窗口配置时，使用 `tools/environment/Start-NavigationConsole.ps1 -MujocoViewer`。既有观察窗诊断开关仍显式启动它，离屏和无 RViz 夹具保留原条件。

Windows 启动器回归 55 项通过。默认导航测试核对 UE 的 `-PhysicsBackend=mujoco`、`-RosLidar` 和 RViz 启动，并确认观察窗参数和进程均省略。手动等其他入口按启动命令复核。

用 `python tools/integration/measure_fullscreen_observers.py <新证据目录> --viewer-ab` 复现。两组使用同一 RMUC2027 场景、UE 模块、物理、画质和 RViz 覆盖插件，实际全屏 2560×1440，视口目标 120 Hz。脚本记录实际前台 client 尺寸、epoch、仿真时钟、UE 场景 Draw 计数和引擎 GPU 计时。Draw 是场景提交频率，显示器呈现未采样。

首轮开启组连续采了约 20.34 秒，关闭组仅有约 4.07 秒连续前台样本。两组都完成定位并启动 RViz，关闭组已确认未启用 MuJoCo 观察窗。首轮证据保留在 `artifacts/integration/mujoco-viewer-default-off-20261120/fullscreen-ab-01`。

| 可用前台片段 | 持续时间 | UE Draw/s | 引擎 GPU 中位耗时 | 仿真倍率 |
|---|---:|---:|---:|---:|
| 首轮开启观察窗 | 20.34 s | 50.49 | 19.55 ms | 1.0033× |
| 首轮关闭观察窗 | 4.07 s | 51.60 | 19.67 ms | 1.0296× |
| 第二轮开启观察窗 | 11.18 s | 52.05 | 18.61 ms | 0.9899× |

首轮取两组最后约 4.07 秒、RViz 均已停绘的等长片段，结果为 56.57 和 51.60 Draw/s。不同窗口内的波动超过两组整体均值的差额。第二轮关闭组完成导航和正常退出，采样期间焦点留在其他窗口，未产生合格的 UE 前台片段。当前实测无法确认持续升帧，保留关闭观察窗的默认设置。 [验收摘要](../verification/2026-11-20-mujoco-viewer-default-off.json)
