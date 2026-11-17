# 仓库与本地资源边界

远程仓库为用户指定的 **https://github.com/SHENJLYL/RMsimUE**，当前可见性为 public。2026-11-17 已将本地 main 的 `8d85457` 及调整前/首轮悬挂两个标签推送成功，并通过 GitHub 连接独立读取远程分支核对。origin 已配置；后续验收通过的提交继续推送到该仓库。

## 入库内容

源码、车辆/地图小型描述、配置、依赖锁、上游补丁、CodeStable 文档及精简验证记录进入 Git。主要目录包括 `unreal/RMSim/Source`、`Config`、`ros2_ws/src`、`src`、`tools`、`tests`、`schemas`、`.codestable` 和 `docs`。来源声明、上游许可证及 `third_party/patches` 保留。

`artifacts` 只跟踪实际 verification、release-summary、checkpoint-summary 和 provenance 等精简结果。失败结果同样保留；截图、日志和稠密轨迹留在本机。

## 保留本地内容

UE 引擎与导入后的 Content、Binaries、Intermediate、Saved、DerivedDataCache；ROS build/install/log；Python/pixi 缓存；大型网格、CAD、贴图、录制；下载的第三方完整源码树；凭证及机器私有配置均由 `.gitignore` 排除。忽略规则不删除本地文件。

原始游戏/模型资源维持各自来源和许可边界，公开仓库只包含允许入库的工程代码、描述和说明。原始模型、贴图及授权凭证没有随本次推送上传。

## 恢复与复现

准备好的本机使用 `Play-ManualVehicle.cmd` / `Play-Training.cmd`。新克隆需先准备原生 UE/ROS、具有使用权的资源，再按指南构建与导入。仓库中的 manifest、源码、依赖锁和来源哈希用于复现；大资源另行准备。

运行参数以车辆包 `drive-profile.json` 为准。`ue_mesh_rig.json`、地图导入报告和 UE Content 属于本地生成文件，内联参数可能是历史导入快照。

回退标签：`baseline/2026-11-17-pre-suspension` 对应初始工程基线；`release/2026-11-17-suspension` 对应第一轮1800/130悬挂验收。保留旧标签，新一轮采用新标签。

## 提交流程

`git status --short` → 检查差异 → `git add` → `python tools/repository/audit_index.py` → `git commit` → `git push`。

审计实际暂存 blob，检查大文件、生成目录和常见凭证格式。推送使用正常快进，不强制覆盖远程。上传证据：`artifacts/repository/rmsimue-publish-20261117/verification.json`。
