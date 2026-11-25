# 启动指令与临时测试清理

- 清理本机硬编码入口 `Play_RMUC2027_Simulator.bat`。统一入口 `Play-Simulator.cmd` 提供 RMUC 场景选择和渲染比例。
- 清理本机测速快捷指令 `Run-CrossField-30s-Test.cmd`。跨场基准工具及既有结果继续可用。
- 移除 `tools/environment/Test-NativeMutex.cmd`。它是早期用于编译比较 Windows CRT mutex 构造方式的单次诊断入口。
- 移除 `tools/unreal/verify_school_first_saved.py`。完整场馆验收通过 `verify_school_fidelity_assets.py` 逐个网格执行同一个几何校验器。

核对比赛、统一启动、RMUC 导航、停止导航、手动试驾、训练和 Flanders 入口。Windows 下比赛启动器和导航控制台的同一组 17 项检查在清理前后均通过。四件旧脚本的原件保存在本机 `artifacts/repository/script-cleanup-20261125/backup/`。
