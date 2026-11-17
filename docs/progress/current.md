# 当前进度

更新：2026-11-17，Asia/Shanghai。长期目标保持 active，入口 `.codestable/goal.md`。Windows优先，Ubuntu保留原生同源适配目标；完整自主哨兵、正式比赛和权威联机继续推进。

## 当前基线：Git 与 AIM 悬挂

本地 Git 已建立源码/配置/精简证据基线 `02e68c9`，标签 `baseline/2026-11-17-pre-suspension`；测试夹具提交 `40927d8`。`.gitignore` 排除构建/安装/缓存、大型资源和凭证，`AGENTS.md` 固定项目接续入口。GitHub 私有仓库上传待完成：本轮建仓操作被平台安全检查阻止，已安装的 GitHub 连接在本轮未提供可调用写入工具，尚无 origin 或远程推送。

AIM 默认车辆包现采用每模块弹簧 **1800 N/m、阻尼130 N·s/m**，原值1600/90。质量、预载50N、行程65mm、源轮胎凸包、摩擦、驱动/舵向增益及自然休眠保持原配置。

同模块/同输入的两次0.005s原生悬挂测量结果一致：100N附加载荷压缩17.106→15.620mm，卸载回弹峰值2.643→1.085mm，4N·s冲量后的反弹峰值0.946→0.213mm。选定方案四项原生检查与严格稳定带检查均通过；原配置的卸载残余高度偏移另行记录。

**当前配置92项原生回归/48PNG通过**：坡面10、操控19、红蓝波浪各5、训练/复位10、高速改向5、自转稳定6、ROS速度桥11、原LK电流环21。另有两次悬挂专项，各4项/6PNG。ROS测试后的进程退出等待竞态曾中断批次；仅修复测试包装器对其自有进程的等待/释放，再以相同模块与参数哈希补齐LK21，原始中断与恢复记录保留。

离线42项中40通过，2项因Windows符号链接权限跳过。证据入口 `artifacts/control/suspension-20261117/release-summary.json`；指南 `docs/guides/suspension.md`。2600/170及其他未同时满足响应/路线要求的候选已拒绝并保留证据。当前参数属于已测演示配置，实车标定、用户手感评审与新悬挂下Point-LIO/Nav2联合验收仍待完成。

## 当前可运行内容

- `Play-ManualVehicle.cmd` / `Play-Training.cmd` 默认使用RMUC2027场地和原尺度AIM：约503.850×501.985×419.192mm，轮半径42.5mm；15视觉部件、13真实刚体，四悬挂/四舵向/四轮轴约束。
- 普通/小陀螺均WASD镜头相对平移；Q/E普通旋转，G切自转，Z/X调速，Ctrl慢行，松键主动制动，Space加强制动，R复位；鼠标瞄准，C自由观察，Shift瞄准镜。
- 单车训练提供真实弹丸扫掠、靶HP、弹量/热量、射速、倒计时、胜利与复位。原生ROS已接入TwistStamped速度、原LK电流/反馈、高层云台/发射消息、相机/CameraInfo/TF/物理时钟、raw IMU、LiDAR和原Point-LIO。
- 命令新鲜度、断线/过期制动、会话复位及独立评测真值有分项证据。当前本机单车通信与训练规则范围明确，完整裁判/多车/联机继续在长期目标内。

## 导航接续点

2026-11-16后续的 `artifacts/navigation/new-tyres-lio-lk-01/ros/verification.json` 已验证源轮胎下原LK、真实传感器和Point-LIO联合11项；短路线IMU点初始姿态对齐后误差约3.58mm。该结果使用旧悬挂，需要在当前1800/130下重新联合验证。

生命周期回复丢失的两种原生夹具已通过，记录在 `artifacts/navigation/lifecycle-reply-loss`。当前最新RMUC导航运行是 `rmuc-05-new-tyres`：目标[9.45,6.40]m，真实终点误差0.254827m超过0.25m门限，保持失败；往返、断线/重连、实际R恢复与启动包装完整验收待完成。

平地原AMCL/Theta*/Omni PID绕障和公开目标/取消停车已有历史成功证据。旧Windows安全弹窗曾阻塞实际R键测试；本轮未完成该导航键盘恢复用例。源摘要 `artifacts/navigation/checkpoint-summary.json` 仍是较早检查点，应同时阅读更晚的原始verification文件。

## 地图、车辆包与后续缺口

RMUC2027和WT CDK地形已有场景运行证据。弗兰德斯原图资源已解码/准备，完整UE场景导入和实际驾驶待验收。受限URDF车辆包离线校验和两个示例已通过；AIM专用mesh-rig导入运行已落地，通用新车的惯量/关节/传感器即插即用仍待扩展。

后续继续：新悬挂下定位/导航联合、完整视觉/Hub/云台电机环/行为树、真实传感器与动力学校准、正式赛季裁判、多机器人权威网络与话题隔离、原生Ubuntu构建运行，以及完整原地图与画质验收。

## 环境与记录

项目 `D:/simulator`，UE5.8.4 `E:/simulator-tools/UE_5.8`；原生Humble使用RoboStack Python3.14。原模型和原控制器来源目录保持只读。运行参数以车辆包 `drive-profile.json` 为准，导入绑定内联参数属于历史快照。

CodeStable现状/计划/问题、`docs/changes`、`artifacts` 分类型保存；旧进度已归档到 `docs/progress/archive/2026-11-17-before-suspension.md`。历史模块通过项只在实际重跑后计入当前验收。

## 当前手动预览

本轮默认RMUC手动窗口已重新打开，PID55380。实际窗口截图观察到AIM车辆、场地与HUD，截图时253FPS、0.0km/h、小陀螺关闭；项目窗口内未见安全弹窗遮挡。本次未发送OS键盘操作，用户手感评审保留。记录 `artifacts/control/suspension-20261117/gui-preview/verification.json`，窗口留开供试驾。
