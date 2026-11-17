# 原生哨兵导航（进行中）

Windows测试场地已跑通原Point-LIO、原地形/扫描节点、AMCL、Theta*和Omni PID控制器。车辆实际绕障到达[5.5,1.0]米目标，首轮末端误差约0.20m；轨迹与同场景障碍物几何另有核对。这是当前未标定传感器/车辆模型的样例，尚不是整个导航feature验收。

## 当前启动方式

从项目根目录先完成原节点及sidecar构建：

```powershell
& .\tools\environment\Build-PointLio.cmd
& .\tools\environment\Build-Navigation.cmd
& .\tools\environment\run-env.ps1 -Command @('powershell','-NoProfile','-File','D:/simulator/tools/environment/Build-RosBridge.ps1')
```

关闭已开的手动预览，分别在四个终端运行：

```powershell
& .\tools\unreal\Start-PreparedVehicle.ps1 -EngineRoot 'E:/simulator-tools/UE_5.8' -MapFile 'D:/simulator/assets/maps/import_lab/map.json' -RosControl -RosImu -RosLidar -SimStepSeconds 0.001
& .\tools\environment\Start-RosBridge.ps1 -Lidar
& .\tools\environment\Start-PointLio.ps1
& .\tools\environment\Start-Navigation.ps1
```

定位和原节点激活后，另一个终端发送位置目标：

```powershell
& .\tools\environment\Send-NavigationGoal.ps1 -X 5.5 -Y 1.0
```

当前目标只要求位置，沿用原配置的宽松朝向条件。速度上限0.35m/s，使用实际车体0.52m方形包络配置。固定1ms物理步长可能约0.3倍墙钟，不能把这当实时性能保证。公开目标Python脚本已实际完成导航；Windows启动包装语法通过，完整包装入口的单独端到端验收尚待补充。

## 定位和坐标

原导航ffd68e5实车入口默认启用AMCL。本例运行同一处理链：原Point-LIO→loam_interface→sensor_scan_generation→terrain_analysis/ext→原pointcloud_to_laserscan→AMCL/原Nav2插件。Point-LIO位姿参考IMU，因此位姿接口使用实际IMU安装frame；代价地图射线观测原点使用LiDAR frame。

原terrain节点增加odom_frame参数，默认仍是odom；本例为aim_sentry/odom。原fake_vel_transform保留算法，仿真translation_scale=1，输出标准base_link速度。map→odom来自AMCL，odom→base来自估计；没有连续真值TF或真值订阅。

默认初值[0.6,-0.4,0.15]明确带误差，并通过initialpose消息给出0.25/0.25/0.09的x/y/yaw协方差。初始化阶段调用原AMCL的request_nomotion_update服务，依据估计协方差形成候选ready；评测节点独立检查实际误差。不要用set_initial_pose参数的零协方差把有偏初值当成确定位置。

占用地图必须来自当前场景，metadata.json中的source_md5与UE实际加载文件匹配。错图会进入map_mismatch，不启动导航子进程。现有导出器只支持平地box地图：

```powershell
$env:PYTHONPATH='D:/simulator/src'
python -m rm_map_tools export path/to/map.json --output path/to/new-navigation-directory --resolution 0.05
```

RMUC2027的2.5D通行表示、全场导航及多高度结构尚未完成，不能拿2025栅格或平地投影冒充。普通手动默认地图仍是RMUC2027。

## Windows 原生兼容修正

自定义代价地图库改名pb_nav2_layers，避免与Nav2内置layers.dll冲突。diagnostic_updater4.0.7的委托构造与虚表缺少DLL可见性，普通Updater对象会访问异常；补GenerateExportHeader声明后，原生命周期/诊断均正常，没有删除诊断功能。原源码、补丁及最小复现在artifacts/sources/navigation、third_party/patches/navigation-native.patch和artifacts/navigation。

依赖变更包含107净新增包和4个图像/压缩库调整；没有修改系统DLL或全局PATH。Nav2及原节点共用项目内18程序/301DLL运行目录，按哈希留证。Ubuntu保持同源码准备，尚无原生运行证明。

## 仍需完成

目标中止停车已有实际结果（约6cm停车），但native-08在等待真实R时因系统权限弹窗阻挡而超时；不能把它当完整恢复验收。RMUC导航、定位丢失/实际R与重连、多车命名空间、完整行为树决策/视觉/Hub、物理和传感器标定仍未验收。runner已有会话停止/重建实现，尚需实际故障场景证明。
