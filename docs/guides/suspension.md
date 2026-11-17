# AIM 悬挂参数与原生验证

运行时参数位于 `assets/vehicles/aim_sentry/drive-profile.json` 的 `physics` 节，启动时读取。本轮选定 1800/130 方案；最终运行验收记录见 `artifacts/control/suspension-20261117/release-summary.json`。

## 参数

| 参数 | 调整前 | 选定方案 |
|---|---:|---:|
| 每模块弹簧刚度 SpringStiffnessNPerM | 1600 N/m | 1800 N/m |
| 每模块阻尼 SpringDampingNsPerM | 90 N·s/m | 130 N·s/m |
| 每模块预载 SpringPreloadN | 50 N | 50 N |
| 总行程 SuspensionTravelCm | 6.5 cm | 6.5 cm |

质量、轮胎源凸包、摩擦、驱动/舵向控制增益和自然休眠行为保持原配置。参数修改在重新启动样例后生效。`ue_mesh_rig.json` 的内联参数及旧哈希是导入快照；实际运行值来自同目录的 `drive-profile.json`。

## 实测效果

同一引擎模块、平地、0.005s仿真步长，车体附加100N向下载荷后卸载，再施加4N·s向下冲量。原配置和选定方案各重复两次，所记录指标一致。

| 测量量 | 原1600/90 | 选定1800/130 |
|---|---:|---:|
| 100N附加载荷下车体下降 | 17.106 mm | 15.620 mm |
| 卸载后越过初始平衡高度的回弹峰值 | 2.643 mm | 1.085 mm |
| 冲量后的反弹峰值 | 0.946 mm | 0.213 mm |
| 卸载后的最终高度偏差 | 1.874 mm | 0.306 mm |

选定方案卸载后约0.295s进入并持续保持于测量稳定带；冲量后约0.225s。稳定带定义为初始偏移的5%与0.2mm两者较大值，并要求至少0.5s后续观察。原配置卸载后停在新静态高度，未满足原高度稳定带；其原生2mm恢复检查仍通过。静态残余偏移与持续振荡分开记录。

这些是当前实际Chaos样例的对比值，实车标定和用户键鼠试驾评审另行验收。较硬的2600/170曾通过平地响应但未通过红方波浪返程，因此未采用；原始失败证据保留。

## 复测

关闭其他 RMSim 实例后，在项目目录运行：

```powershell
$run = "D:\simulator\artifacts\control\suspension-check-$(Get-Date -Format yyyyMMdd-HHmmss)"
powershell -NoProfile -File tools\unreal\Test-Suspension.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory $run
powershell -NoProfile -File tools\unreal\Test-SuspensionRegression.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory "$run-regression"
```

第一条包含四项原生检查、六张PNG、输入哈希与逐帧分析。第二条覆盖操控、红蓝波浪、小陀螺、坡面制动、训练/复位、ROS速度桥和原LK控制器；每次使用新目录保存真实结果。测试脚本不会为通过而调整门限。

## 只回退车辆参数

```powershell
git restore --source=baseline/2026-11-17-pre-suspension -- assets/vehicles/aim_sentry/drive-profile.json
```

随后重新启动 `Play-ManualVehicle.cmd`。本操作只取回旧车辆参数；原生测试夹具和其他源码保留。实际旧运行绑定、DLL及参数字节备份在本机 `artifacts/control/suspension-20261117/before`，大型备份不随Git上传。
