# RMUC 双哨兵雷达开销对照

Windows 原生 UE5 与 MuJoCo 使用同一份六车 3v3 编队、同一对照模块及相同比赛画质配置。机器使用 Core Ultra 9 275HX 和 RTX 5080 Laptop GPU。UE 离屏目标分辨率 2560×1440，渲染比例 50，关闭 Lumen 和 RViz，两席相机均为 1280×720、15 Hz，视口目标 120 Hz。读取 ROS 桥实际统计，各席比较接收后第 18–33 个墙钟秒。使用 `run-match.cmd` 在 Pixi 中启动，原始 UE 命令行留在日志里。 [当前档日志](../../artifacts/verification/rmuc-lidar-load-20261127/4-a/ue.log) · [全采样日志](../../artifacts/verification/rmuc-lidar-load-20261127/1-b/ue.log)

| 指标 | 每台 50,000 射线/s | 每台 200,000 射线/s |
| --- | ---: | ---: |
| UE 场景绘制 | 约 92 FPS | 约 47 FPS |
| UE 主线程耗时中位数 | 约 9.2 ms | 约 19.3 ms |
| 单席雷达处理耗时中位数，每仿真秒 | 126 ms | 254 ms |
| Windows 整机 CPU 平均占用 | 约 50% | 约 53% |
| 仿真实时率 | 约 1.00× | 约 1.00× |
| 每席点云及观察到的掉帧 | 约 10 Hz，0 帧 | 约 10 Hz，0 帧 |

全采样将场景绘制帧率降低约 49%。UE 雷达计时包含射线准备、场景查询等待、光学处理和打包。异步读取的 UE 主线程计时为窗口采样中位数。两次自主行驶轨迹存在差异。降采样首轮的进程会话提前结束，保留了超过 43 秒的双席记录，覆盖完整比较窗口。全采样运行工具退出码 0。 [原始指标字段](../../artifacts/verification/rmuc-lidar-load-20261127/comparison.json) · [全采样运行回执](../../artifacts/verification/rmuc-lidar-load-20261127/1-b/summary.json)

全采样试验按现有生产配置进入 Point-LIO，生成参数 `point_filter_num=8`。50,000 档实际使用 1 并保留采样后的四通道。本轮评估的是在现有链路恢复雷达发射密度的成本，全采样加四通道 Point-LIO 没有另测。

双席比赛保留 50,000 射线/s 与 10 Hz，每帧 5,000 个点位。单席继续以 200,000 射线/s 运行。对照开关已从源文件移除，Windows 本机模块恢复为测试前的 `c196f94233658dfc13d35b6f337161be9d523d578352dc421ea166f207713ed7`。 [模块校验](../../artifacts/verification/rmuc-lidar-load-20261127/module-restore.json)
