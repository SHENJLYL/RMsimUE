# RMUC 无损相机传输与阴影裁切试验

比赛相机走原始 RGB8。渲染线程从 BGRA 读回并生成 RGB8。TCP 依次写头部、现有相机读回缓冲、雷达块，整个包继续按原大小和偏移契约校验。读回槽在发完、断线或清空待发包时复用。ROS 桥使用缓冲视图，直接构造消息所需的字节数组。显式 RenderScale 100 现在会下发 r.ScreenPercentage 100。

Windows UE、ROS bridge 构建均成功。协议与传感器合同测试及导航控制台合同通过。最高 16,381 字节的 TCP 读块接收完整图像及雷达数据，主动慢读触发断线后，新会话再次收到 6 张 720p RGB8 和有效雷达块。100% TSR 双边 3v3 两路约 15 Hz，点云约 10 Hz、扫描丢失计数为 0，仿真约 1×。约 63.8 FPS 为该局场景 Draw，构建、采样参数和比赛轨迹与此前 47 FPS 样本有差异。旧传感器总门禁在模型预检因源 RMUC 坐标元数据不符停止，另用本次真实 TCP 和双 ROS 席覆盖传输校验。 [原生慢读测试](../../artifacts/verification/rmuc-lossless-render-20261127/wire-backpressure/verification.json) · [双席短测](../../artifacts/verification/rmuc-lossless-render-20261127/wire-3v3/comparison.json)

## 阴影

同场馆、同索引环绕、固定云相位，1440p 100% TSR。移动镜头两次基线 GPU 中位为 8.76、8.79 ms，开启 CSM 裁切为 8.82 ms。移动视角阴影 pass 分别为 1.78、1.81、1.79 ms。裁切试验未进入正式配置。布局清单含 24 个网格组、169 个实例，全相同的材质、投影属性和变换组合共 0 个重复实例。 [抓帧及像素对照](../../artifacts/verification/rmuc-lossless-render-20261127/shadow-ab/summary.json) · [布局核查](../../artifacts/verification/rmuc-lossless-render-20261127/static-and-copy.json)
