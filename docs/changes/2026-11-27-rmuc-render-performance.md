# RMUC 体育馆与双相机渲染

RMSimGymLumen 主视角改用 FXAA。传统阴影分辨率上限为 1024，屏幕空间环境光遮蔽和屏幕空间反射关闭。比赛车辆灯条使用原自发光材质，六车共省去 24 个装甲板近地补光点。前哨实灯和命中闪烁由原流程处理。

GPU 读回完成后，渲染线程按读回行跨度将 BGRA 转成 RGB8。游戏线程把已打包的 RGB 字节追加到现有同步发送缓冲。帧戳、光学坐标、发送格式和读回队列继续沿用同一路径。

Windows 原生 3v3 短测使用 2560×1440 离屏、50% 渲染比例、两路 1280×720 相机各 15 Hz。按观察器启动后第 18–33 秒统计。每个条件运行一次，使用同一阵容和渲染分辨率。

| 指标 | 原配置 | 调整后 |
| --- | ---: | ---: |
| 场景 Draw 帧率 | 66.62 FPS | 66.60 FPS |
| GPU 耗时中位数 | 7.58 ms | 6.56 ms |
| 游戏线程耗时中位数 | 12.73 ms | 13.37 ms |
| 红方相机发布 | 15.00 Hz | 14.96 Hz |
| 蓝方相机发布 | 15.04 Hz | 15.04 Hz |

GPU 中位耗时下降，场景 Draw 帧率约为 66.6 FPS。50% 内部渲染时远处细栏杆出现可见锯齿。红蓝 ROS 相机实收 RGB8 图像，逐张校验尺寸和字节数。红蓝 S7 装甲相机探针通过。早期探针重试使用 CaptureDir 保留目标，原步兵样例的初始弹量为零，无法通过探针内的投射物检查。失败日志保留在验证目录。

[性能统计](../../artifacts/verification/rmuc-render-20261127/comparison.json) · [主视角](../../artifacts/verification/rmuc-render-20261127/4-after/viewport.png) · [红方 ROS 相机](../../artifacts/verification/rmuc-render-20261127/4-after/red-camera.png) · [蓝方 ROS 相机](../../artifacts/verification/rmuc-render-20261127/4-after/blue-camera.png) · [装甲探针](../../artifacts/verification/rmuc-render-20261127/armor-proof-sentry/ue.log)
