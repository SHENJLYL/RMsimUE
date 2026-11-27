# RMUC 3v3 启动卡住

原 3v3 日志停在首辆车模型初始化阶段。MuJoCo 的同场景深拷贝会连带复制挂在 world 的柔性碰撞网格。旧代码在所有车辆复制完后才清理副本。使用 UE 同一 Windows DLL 和正式场地 XML 复现，组装过程的柔性网格数从 14 增至 28、56、112。 [用户启动日志](../../artifacts/verification/rmuc-3v3-startup-20261127/reported-startup.log) · [原生组装复现](../../artifacts/verification/rmuc-3v3-startup-20261127/assembly-before.log)

每辆车深拷贝完成后立即移除该车带出的场地网格副本，后续车辆只接触原场地网格。修复版原生六车组装每次回到 14 个场地柔性网格，解析与编译约 8.8 秒。正式 UE 六车模型确认 91 个刚体、84 个执行器和 630 对车辆自碰撞排除。 [原生验证](../../artifacts/verification/rmuc-3v3-startup-20261127/assembly-candidate.log) · [Windows 构建](../../artifacts/verification/rmuc-3v3-startup-20261127/build.log)

使用用户原编队通过正式启动器后台复测。蓝方 3 号步兵为操作视角，红蓝各启动自动哨兵。约 25 秒进入比赛，约 27 秒生成主视口实拍，约 36 秒双方导航和视觉连接器启动。红蓝双车手动编队通过同版 Windows 启动和视口回归。两个进程在验证结束后由测试程序关闭。 [六车启动](../../artifacts/verification/rmuc-3v3-startup-20261127/six-production/summary.json) · [六车实拍](../../artifacts/verification/rmuc-3v3-startup-20261127/six-production/viewport.png) · [双车回归](../../artifacts/verification/rmuc-3v3-startup-20261127/two-regression/summary.json)
