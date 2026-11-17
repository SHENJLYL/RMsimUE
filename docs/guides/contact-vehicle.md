# 接触车辆与波浪路

Windows默认入口使用原生Chaos接触动力学，最新控制已升级为独立舵轮，见[舵轮与小陀螺操作](swerve-gyro-controls.md)。车辆保持503.850×501.985×419.192mm、轮半径42.5mm，地图未修改。

当前车体、4滑动模块、4转向carrier和4轮共13个独立刚体，12个物理约束。车辆位移由重力、电机、悬挂和真实接触解算；旧地形射线坡度门限不再参与行驶。关节投影和质量调节均关闭。

## 验证与边界

- `artifacts/gameplay/contact-physics/wave-native-09`：红蓝两侧3.4m波浪路，1m/s指令，均前进/制动/倒车通过。
- `wave-native-10-slow`：同路线0.35m/s指令均通过，前进约12.1–12.3秒，终点横向偏差小于1.6cm。
- `integration-02`：8项原生检查、10张PNG、退出0；包含共用左右转向、真实弹道命中和实体墙阻挡。持续油门撞墙后停止，未关闭墙体碰撞。
- 以上为原生Windows真实世界帧测试，不是OS键盘压力测试、原生Ubuntu验收或全部场地可达性证明。

上面的09/10及integration-02属于此前11部件差速接触基线。最新版本已分离原舵轮并实现独立舵向；云台碰撞仍固定为中立姿态，视觉云台可瞄准。质量、质心近似、摩擦、弹簧和电机参数尚未实车校准。

## 参数

最新参数在车辆包`drive-profile.json`，重启直接读取；`DefaultGame.ini`的`[RMSim.ContactPhysics]`只保留旧包回退默认值。下表是最初接触基线参数，最新值以车辆包为准。

| 参数 | 演示值 | 含义 |
|---|---:|---|
| ChassisMassKg | 20 | 车体质量；当前固定云台附加0.2kg |
| WheelMassKg / SliderMassKg | 1 / 0.35 | 每轮及滑动模块质量 |
| SuspensionTravelCm | 6.5 | 总行程，围绕中立位置±3.25cm |
| SpringStiffnessNPerM / SpringDampingNsPerM | 1600 / 90 | 每角刚度、阻尼 |
| SpringPreloadN | 50 | 每角预载；自由伸展31.25mm，静态位置接近原装配姿态 |
| MotorTorqueNm / BrakeTorqueNm | 4 / 8 | 单轮驱动/制动力矩上限 |
| WheelFriction / ChassisFriction | 1.1 / 0.5 | 轮与车身摩擦参数 |

源WT的导轨/动画行程只是几何参考，以上不是测得的实车参数。调参不应代替检查碰撞几何和求解器。

## 重建与离线迁移

`tools/unreal/Start-ManualVehicle.ps1 -EngineRoot E:\simulator-tools\UE_5.8`执行构建、导入、碰撞检查/缺失重建、运行。`Start-ManualVehicle.sh`使用同一C++和Python流程，仍需原生Ubuntu实际验证。

完整离线包应包含`assets/vehicles/aim_sentry`、场地包、`unreal/RMSim/Source`、`Config`、`Content`和`tools`。碰撞资产使用UE虚拟路径；缺失/哈希失效时`ensure_vehicle_collision.py`从本地视觉资产重建，不下载工具。精细车体使用128凸包、100万精度，其他部件16凸包上限，轮为实测尺寸32面圆柱。

派生资源位于`Content/RMSim/Collision/aim_sentry`；绑定包含源FBX与派生资产哈希。重建记录和旧rig备份保存在`artifacts/assets/contact-physics/prepare-*`。原`E:/WarThunderModding`及游戏/CDK资产只读。

旧运动学仅作诊断：PowerShell入口传`-KinematicPreview`，Linux入口传`--kinematic-preview`；低级`Run-ImportLab.ps1`通过`-ContactPhysics`显式启用接触模式。

## 重跑原生检查

```powershell
tools/unreal/Test-ContactRoutes.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory D:\simulator\artifacts\gameplay\contact-physics\my-wave-check -SpeedMps 0.35
tools/unreal/Test-ManualControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -Contact -ContactIntegration -WithTraining -ContactDistanceCm 150 -ContactSpeedMps 0.5 -EvidenceDirectory D:\simulator\artifacts\gameplay\contact-physics\my-integration-check
```

每次使用新的证据目录；失败不会覆盖旧报告。失败诊断包括车体/轮组位置、真实速度、质心、惯量、接触点和截图。`tools/assets/audit_contact_surface.py`可在只读原Blender模型上核查记录的车身接触点，避免只看包围盒而漏掉被凸包填平的凹槽。
