# UE5 手动车辆导入与运行

2026-11-15：已用本机UE5.8.4真实编译、导入、运行。Windows手动首样例完成；原生Ubuntu、真实动力学、ROS算法和比赛联机未验收。

## 日常启动

双击仓库根目录 `Play-ManualVehicle.cmd`，默认RMUC2027地图。默认引擎在 `E:\simulator-tools\UE_5.8`，可用UE_ROOT覆盖。操作说明见 [manual-controls.md](manual-controls.md)，换图见[地图指南](map-packages.md)。

只启动任意已导入的手动包：

    pwsh -File tools/unreal/Run-ImportLab.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -InputType MeshRig -InputFile assets/vehicles/aim_sentry/ue_mesh_rig.json

## 完整重建和导入

    pwsh -File tools/unreal/Start-ManualVehicle.ps1 -EngineRoot E:\simulator-tools\UE_5.8

替换新手动visual rig包时添加 `-Manifest <asset_import.json>`。它按Check→Build→Import→Run执行。FBX/PBR与部件约定见 [aim-sentry-assets.md](aim-sentry-assets.md)。任意URDF或特殊驱动机构仍需要对应支持，不伪造质量/惯量。

可以分步执行 Build-ImportLab.ps1、Import-VehicleAssets.ps1、Run-ImportLab.ps1。引擎路径必须真实存在；构建失败或输入缺失会非零退出。脚本不下载引擎。

## 验证

证据目录必须为新的路径，防止混用旧结果：

    pwsh -File tools/unreal/Test-ManualControls.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -EvidenceDirectory artifacts/unreal/new-controls-run

这在真实UE中向共用ApplyManualInput传入控制帧，验证18项行为，解码10张图像；不冒充OS输入自动化。正式按键输入也调用相同函数。

    pwsh -File tools/unreal/Run-ImportLab.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -InputType MeshRig -InputFile assets/vehicles/aim_sentry/ue_mesh_rig.json -ManualProbe basic -EvidenceDirectory artifacts/unreal/new-motion-run -TimeoutSeconds 180

此路径验证9项运动、碰撞、轮转、云台及相机行为，解码6张图像。PNG/JSON失败、非零进程或旧记录不会被当作通过。

最新已通过证据：`artifacts/unreal/2026-11-15/controls-04-aim-fix` 和 `probe-07-final-controls`。controls-03保留了修复前的真实失败。

## 镜头与单位

模型描述保留米/弧度/ROS右手坐标；UE边界转换到厘米左手系。旋转使用 SRS 的镜像四元数，关节轴按轴向量转换。实际UE mesh bounds已经独立核对，而不是只靠导出清单。

第三人称pivot固定在初始车顶上方40cm，默认距离5m，不随炮塔视觉bounds移动。视角切换保留世界目标，并从新镜头位置重新投影。相机PNG使用sRGB RenderTarget、连续历史和暖机，不能把此预览当作标定的工业相机数据。

## Ubuntu 原生入口

    bash tools/unreal/Start-ManualVehicle.sh --engine-root /path/to/UE --manifest assets/vehicles/aim_sentry/asset_import.json

消费相同C++/FBX/JSON，调用Linux Build.sh与UnrealEditor，并导入选中地图的mesh-source.json。脚本语法/缺失路径已在Windows Git Bash检查；未在Ubuntu执行，不算双平台通过。[离线交接](ubuntu-offline.md)。

## 当前限制

- 手动运动是平地/地形跟随运动学与保守box查询，无悬挂接触求解和质量惯量标定。
- 灯光和模型预览可用，不等于完整赛场或最终摄影级效果。
- 无射击伤害、比赛裁判、联网、真实ROS自瞄/哨兵/雷达算法闭环。
- 手动演示云台角度限位仍有效，不能将不可达目标伪装为命中对齐。
