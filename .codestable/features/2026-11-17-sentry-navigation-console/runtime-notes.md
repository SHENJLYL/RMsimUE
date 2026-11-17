# 可视化导航接续记录

当前分支：feat/sentry-navigation-console-20261117；已提交基线0a83cc4。接续时保留此前未提交实现，并将55个源码/配置文件、工作区patch及SHA256备份到artifacts/navigation/sentry-console-20261117/resume-baseline。

## 已观察

- RViz2及原Nav2面板已实际运行，具有地图、局部代价地图、规划路径、Point-LIO点云、机器人/云台/Mid-360坐标轴和航点工具。窗口与生命周期由Play-Navigation.cmd/Stop-Navigation.cmd统一管理。
- 原radar_mid360位于yaw_link；源blend哈希再次核对不变。native-04九项传感器专项和loam-alignment-01四项已有通过记录。
- follow-01八项只读观察通过：3518跟随样本，实际云台相对整形目标中位误差0.000895rad；同时接收云台安装点云、IMU及动态TF，导航未触发开火。
- 接续离线测试resume-offline-01：116项，114通过、2项Windows符号链接权限跳过。

## 图形验收夹具修正

早期GUI验收混入了变化后的相机视角。辅助脚本现在保存并读取当前RViz视角，按原TopDownOrtho相机Z旋转计算像素。已核对ros2/rviz Humble的fixed_orientation_ortho_view_controller.cpp。测试可重置相机视角，保持车辆与定位状态不变。

Qt可访问性TogglePattern只改变工具勾选状态，曾使实际Move Camera仍处于活动状态。脚本改用工具QAction的InvokePattern，并在移动工具仍活动时拒绝地图点击。所有鼠标操作保持项目PID、前台窗口和目标像素所属进程校验。失败的GUI验收记录全部保留；未将输入辅助脚本返回成功等同于Nav2收到目标。

## Point-LIO SI饱和阈值

独立native-actions-01真实原Nav2目标试验失败；观测到估计高度出现米级偏移。检查固定原源码Estimator.cpp::h_model_IMU_output发现：satu_acc直接与原始acc_avr比较，然后另用acc_norm归一化。原mid360.yaml为acc_norm=1、satu_acc=3；此前适配只将acc_norm换为9.81，遗漏了阈值的同单位换算。

新增pointlio_units.py将两者配对转换为9.81和29.43，保持原3g物理范围。6项独立单元测试证明原饱和判定、归一化结果保持，倾斜安装下静止重力不会被误判饱和。sidecar重新colcon安装并核对源码/安装SHA；native-actions-02与native-console-02用于修正后的完整导航联跑。

估计器公式、动力学、碰撞、执行租约和0.25m实际到点门限保持。是否解决整个联跑问题以新运行真实verification为准。后续需补GUI单目标、多航点、取消及旧手动/ROS/瞄准接口回归，再形成发布汇总。
