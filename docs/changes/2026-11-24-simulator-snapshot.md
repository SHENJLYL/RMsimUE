# 2026-11-24 仿真源码快照

Windows 启动器内置导航模式和 `Play-Navigation.cmd` 使用 RMUC `navigation-console-platform-support-v2` 地图包的稳态 MPC 参数。Git 保留地图包启动校验所需 YAML 的原始字节。地图栅格、地形网格、MuJoCo 模型与 UE 导入内容按来源说明分别准备。

记录 `fast-26s-candidate/run70-mu105-tau008-safe16` 到达终点，墙钟 32.362 秒，仿真 32.37 秒，终点误差 0.2496 米。971 个占用检查样本有 7 个重叠，30 秒和 26 秒验收均未达标。当前 YAML 的原生启动证据见 [MPC 发布清单修复](2026-11-24-rmuc-mpc-release-digest.md)。37.82 秒跨场和平台录制保留为历史参考。

快照检查通过 RMUC 默认配置预检及 23 项导航启动和出生点测试。`test_navigation_goal_source.py` 的三项检查仍使用旧配置期望，涉及规划器的 `route_backend`、AMCL 的 `max_beams` 和目标距离容差。当前 MPC 参数按原样保存。

清理 `artifacts/navigation` 下已结束且退役的两批实验。`cross-field-turns-20261122` 清理 25 个 `.db3` 文件，`fast-26s-candidate` 清理 66 个，合计 79,930,003,456 字节。保留索引、日志、指标、当前基线和活动会话录包。录包、运行报告、生成模型和本地工作树的入库规则见 [仓库与本地资源](../guides/repository.md)。
