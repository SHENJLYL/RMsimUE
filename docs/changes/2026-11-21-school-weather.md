# RMUC 体育馆玻璃、入口围墙与云影

## 启动与渲染

双击 `Play-Navigation.cmd` 启动 RMUC，或使用 `Play-ManualVehicle.cmd` 和 `Play-Training.cmd`，默认启用 50% 内部渲染比例及 120 Hz 视口目标。窗口大小沿用现有启动模式。直接调用 PowerShell 启动器时仍可选择 50–100%，例如用 `-RenderScale 100` 恢复主视口原生采样。RMUC 的 TSR 闪烁检测周期从 3 调至 4，历史上限从 16 调至 24，着色拒绝后的历史样本从 2 调至 3。 [Epic TSR 调整说明](https://dev.epicgames.com/documentation/unreal-engine/temporal-super-resolution-in-unreal-engine)

比赛场地围栏使用全新的无光照半透明材质。玻璃按入射角混合轻微蓝灰色和模糊静态环境贴图，保留透视感。关闭了此玻璃的屏幕空间反射，其他表面继续采用原本的光照。材质由 `tools/unreal/author_school_weather.py` 生成，原材质资产保留。

## 庭院和天气

东、西两面连续砖墙各长 64 m，两个端部各向馆侧折回约 4.3 m。用户反馈初版太近后，两面墙都向外又退 10 m，墙内面位于 ROS X=-42.0158752 m 和 +41.9841248 m。相对外侧台阶的原最小间距约由 4.26 增至 14.26 m，地上墙高约 2.54 m。沿用已验证的 CC0 砖墙模块，56 个实例均属于室外装饰，比赛区碰撞和原导航地图由其既有资产提供。

云层以运动噪声叠加到天空材质，太阳使用同一云图的世界坐标投影调制直射光。两者使用同一游戏时间、同一高度和风速。场景投影继续与原太阳阴影叠加，原天光环境保持可用。运行时可用测试参数固定云的时刻，采集不同相位的同机位对照。 [Epic Light Function 说明](https://dev.epicgames.com/documentation/en-us/unreal-engine/using-light-functions-in-unreal-engine)

## 原生测试

- Windows UE 构建与模型预检通过。1 ms MuJoCo 物理模型仍绑定原有运动和碰撞资产。
- 终版玻璃、东门、西门及室外两相位共五个固定机位均通过原生边界检查。对照图显示两侧后退的墙挡住门内视野里较低的远方接缝。相同外景机位在 0 s 和 60 s 的天空和地面均有变化，选取的草地区域平均亮度由 211.96 降至 206.75（8 位刻度）。
- 冻结云时刻的 50% 内部渲染对照，每种设置记录 14 张连续实际视口帧。远处窗户和栏杆区域内，亮度标准差超过 3 级的像素，由 2.030% 降至 1.498%，相对减少约 26%。调整纹理 mip 偏置收益较小，保留作诊断。
- 56 项 Windows 启动合同通过。最终导航会话进入原生 Nav2 和 RViz，50% 渲染比例及历史采样设置生效并正常退出。运动镜头离屏 GPU 中位数为 7.82 ms。全屏运行在末段报告 115.08 Draw/s、8.01 ms GPU 和 0.998× 仿真倍率；监控未获得连续前台的有效样本，末段读数不作为持续帧率结论。

运行证据在 `artifacts/changes/school-weather-20261121`，后退前的对照保留在 `views-t0`，终版截图与组合图保存在 `final-views-t0`、`final-views-t60` 和 `final-visual-comparison.jpg`。原 `layout-before.json` 和 `arena-shell-before.json` 为可恢复的活动配置基线。 [验证摘要](../verification/2026-11-21-school-weather.json)
