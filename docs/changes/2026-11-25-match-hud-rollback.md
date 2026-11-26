# 恢复原比赛界面

撤回本轮临时比赛 HUD、专用启动入口和对应文档。十个变更过的源码、启动器与测试文件逐一恢复到改动前的 SHA-256。比赛从 `Play-Match.cmd` 进入。

Windows UE 5.8 编译、比赛启动预检、5 项比赛启动器测试和 12 项导航入口测试通过。原比赛在 D3D12 离屏启动到就绪，保存原 HUD 截图并正常退出。

模块 SHA-256 为 `369f96ea4a2c96cc557a238b8b59433e8f1ae2118830a275e69cdd6e4537edca`。 [原生启动回执](../../artifacts/unreal/match-hud-rollback-20261125/verification.json)
