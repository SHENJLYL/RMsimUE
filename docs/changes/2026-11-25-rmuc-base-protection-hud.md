# RMUC 基地保护和装甲状态提示

基地 100% 防御与大装甲的机械展开是独立状态。前哨阵地易手时基地可失去防御，闭合时上层小装甲仍可参与命中判定。基地内侧大装甲只有越过运行时展开阈值才接受命中。

莱茵 HUD 现在分别显示护盾保护、仅小装甲可受击和大装甲展开。展开提示与命中查询共用当前场景的展开阈值。传统 HUD 对应采用 `PROTECTED`、`SMALL ARMOR` 和 `LARGE ARMOR`。

Windows UE5 构建及四组裁判回归通过。双车复验中，蓝方前哨转为对手临时控制后，基地护盾解除、大装甲仍闭合，76 项原生基地装甲查询通过。原回放的文字播报检查未收到预期字符串，按原生遥测和碰撞查询重新核验并保留两份记录。 [本轮复核](../../artifacts/unreal/dual-match-outpost-20261125T080734Z/base-status-review.json)

较早的真实游玩日志记录了临时控制失去后，命中基地被己方前哨完全控制拦下。另一场记录了对方前哨被摧毁并重建。 [护盾提示](../../artifacts/unreal/pure-simulator/c1e580f5f08847c08e17d30846436e76/ue.log) · [前哨移交](../../artifacts/unreal/pure-simulator/b744b406fa8843bf8d2e87c84c085fa0/ue.log)
