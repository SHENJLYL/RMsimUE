---
doc_type: issue
status: in-progress
created: 2026-11-17
priority: current_user_priority
---

# AIM 悬挂偏软、阻尼手感不足

用户要求先建立 Git/GitHub 基线，再调整悬挂。已建立本地提交 `02e68c9` 与标签 `baseline/2026-11-17-pre-suspension`。GitHub 创建操作被平台安全检查拦截；插件目录随后显示 GitHub 已安装，但本轮工具列表没有可调用的 GitHub 操作，远程上传保持待完成。

原车辆包每轮弹簧为 1600 N/m、阻尼 90 N·s/m；预载 50 N、总行程 65 mm、车体质量 20 kg。首次候选为 2600 N/m、170 N·s/m，其余车辆配置保持逐字段一致。原参数、运行绑定与 DLL 保存在 `artifacts/control/suspension-20261117/before`；候选先使用独立包测试，默认参数尚未替换。

新增显式 `-ContactProbe -SuspensionProbe` 开发夹具：同一平地、0.005s 固定仿真步长，静止 3s、车体附加向下 100N 持续 2s、卸载观察 3s、施加同一向下 4N·s 冲量后观察 3s。所有响应来自实际 Chaos 刚体/弹簧/轮地接触。无姿态纠正、额外高度保持或摩擦修改。

逐帧保存车体高度、垂向速度、俯仰/横滚及滑块位移，分项记录静态压缩、回弹峰值、衰减时间、最终高度变化。先完成原生对比，再回归镜头相对操控、红蓝波浪、小陀螺、坡面制动与训练/复位。

引擎构建已成功；分析器 6 项单元测试通过。各次原始运行与失败结果保存在 `artifacts/control/suspension-20261117`，验收状态由后续真实结果更新。
