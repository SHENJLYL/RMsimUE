# AIM 悬挂参数与原生验证

实际运行参数位于 `assets/vehicles/aim_sentry/drive-profile.json` 的 `physics` 节，启动时读取。当前采用 **2000/140**，验收入口为 `artifacts/control/suspension-round2-20261117/release-summary.json`。

## 参数

| 参数 | 首轮已验收 | 当前第二轮 |
|---|---:|---:|
| 每模块弹簧刚度 SpringStiffnessNPerM | 1800 N/m | 2000 N/m |
| 每模块阻尼 SpringDampingNsPerM | 130 N·s/m | 140 N·s/m |
| 每模块预载 SpringPreloadN | 50 N | 50 N |
| 总行程 SuspensionTravelCm | 6.5 cm | 6.5 cm |

质量、轮胎源凸包、摩擦、驱动/舵向增益和自然休眠不变。重新启动样例后读取新参数。`ue_mesh_rig.json` 中内联参数/旧哈希属于导入快照，实际值来自同目录drive-profile。

## 实测对照

同一引擎模块、平地、0.005s物理步长：车体附加100N向下载荷后卸载，再施加4N·s向下冲量。新2000/140重复两次，所列指标一致；原1800/130在同模块重新对照。

| 测量量 | 1800/130 | 2000/140 |
|---|---:|---:|
| 100N附加载荷下车体下降 | 15.620mm | 14.447mm |
| 卸载回弹峰值 | 1.085mm | 0.774mm |
| 卸载稳定带进入时间 | 0.295s | 0.250s |
| 冲击后反弹峰值 | 0.213mm | 0.029mm |
| 卸载最终高度偏移 | 0.306mm | 0.179mm |

稳定带为max(初始幅度5%,0.2mm)，要求保持在带内且之后至少观察0.5s。原生2mm恢复门限、波浪路线和坡面制动门限保持原值。完整回归92项/48PNG，两次新配置专项各4项/6PNG通过。

2200/170等更硬候选未通过全部门限，原始失败保留。降低休眠阈值没有消除对应高度偏差，该代码和参数已撤回。当前采用真实轮地接触与原生睡眠，新增诊断字段只记录chassis_awake。上述数值对应当前仿真测试条件，实车标定与用户手感另行评审。

## 复测

关闭其他RMSim实例，在项目根目录运行：

```powershell
$run = "D:\simulator\artifacts\control\suspension-check-$(Get-Date -Format yyyyMMdd-HHmmss)"
powershell -NoProfile -File tools\unreal\Test-Suspension.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory $run
powershell -NoProfile -File tools\unreal\Test-SuspensionRegression.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory "$run-regression"
```

第一条保存载荷、卸载、冲击的逐帧实际响应、四项检查、六张PNG和输入哈希。第二条覆盖手动、小陀螺、红蓝波浪、坡面、训练/复位、ROS速度桥及原LK电流环。每次使用新目录，失败证据也保留。

## 回退车辆参数

回到上一轮1800/130：

```powershell
git restore --source=release/2026-11-17-suspension -- assets/vehicles/aim_sentry/drive-profile.json
```

随后重新启动 `Play-ManualVehicle.cmd`。更早的1600/90保留在 `baseline/2026-11-17-pre-suspension`。第二轮运行前的实际绑定、模块和profile字节备份在本机 `artifacts/control/suspension-round2-20261117/before`；大型备份不随Git上传。恢复参数不会自动恢复其他源码版本，回退后应按所需范围重跑验证。
