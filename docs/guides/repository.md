# 仓库与本地资源边界

Git 保存源码、车辆/地图小型描述、配置、依赖锁、上游补丁、CodeStable 文档及精简验证记录。本地仓库与基线已建立；计划发布为私有 GitHub 仓库。2026-11-17 本轮远程建仓操作受到平台安全检查阻止，GitHub 连接虽显示已安装，但本轮没有可调用的 GitHub 写入工具，上传保持待完成。

## 入库内容

- `unreal/RMSim/Source`、`Config`、`RMSim.uproject`；`ros2_ws/src`；Python 工具、测试和 schema。
- `assets` 中的小型 manifest、URDF、控制/传感器参数、来源说明；`third_party/patches`。
- `.codestable`、`docs` 与真实 `verification.json` / `release-summary.json` / provenance 摘要。

## 保留本地内容

UE 引擎、导入后的 Content、Binaries、Intermediate、Saved、DerivedDataCache；ROS build/install/log 工作区；Python/pixi 缓存；大型网格、CAD、贴图、截图、录制与稠密轨迹；下载的第三方完整源码树；凭证及机器私有配置。

忽略规则保留本地文件，不删除它们。历史摘要引用的截图、日志和原始轨迹仍在 `D:\simulator\artifacts`，克隆仓库只会获得已入库的摘要。

## 恢复与复现

当前已准备好的本机样例用 `Play-ManualVehicle.cmd` / `Play-Training.cmd` 启动。全新克隆需要先安装原生 UE/ROS 依赖并准备有合法使用权的资源，再执行文档中的构建与导入步骤。模型路径、来源哈希和第三方固定版本保留在对应 SOURCE、provenance 和 research 文档。

车辆运行绑定 `ue_mesh_rig.json`、地图导入报告及 UE 二进制是派生的本机文件；运行参数 `drive-profile.json` 始终入库。调整车辆参数前建立提交/标签，调整后以实际测试更新记录。

上游文件保留各自版权和许可证；本次建仓不为第三方游戏资源或来源代码追加授权。没有自动把仓库改为公开，也不上传本机登录凭证。

## 提交流程

`git status --short` → 检查改动 → `git add` → `python tools/repository/audit_index.py` → `git commit` → `git push`。审计检查暂存内容中的大文件、生成目录和常见凭证格式；它是提交前防护，不能覆盖所有秘密格式。
