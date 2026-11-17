---
doc_type: issue-report
issue: 2026-11-15-aim-reticle-parallax
status: confirmed
tags: [controls, camera, aiming]
---

# 准星偏差与低灵敏度

用户提供截图：白色准星在墙面，橙色炮口框明显偏下；另反馈跟随延迟、鼠标灵敏度低。

复现：瞄准镜中改变目标，然后 Shift 返回第三人称。鼠标停止后，目标保持但第三人称中心射线未重新对齐。实际 UE 检查失败，角度差 3.089893°。

证据：`artifacts/unreal/2026-11-15/controls-03-aim-gap-repro/control-result.json`；该次进程退出 2，不能视为通过。
