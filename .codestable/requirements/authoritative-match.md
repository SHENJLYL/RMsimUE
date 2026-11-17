---
doc_type: requirement
slug: authoritative-match
pitch: 多个机器人程序在同一场比赛中对抗并获得一致裁判结果
status: draft
last_reviewed: 2026-11-13
implemented_by: []
tags: [multiplayer, referee, rmul, rmuc]
user_review: not_requested_under_standing_authorization
---

# 联机比赛对抗

## 用户故事

- 作为机器人程序开发者，我希望不同电脑上的机器人进入同一场比赛。
- 作为比赛测试者，我希望命中、血量、弹量、热量和场地事件按同一套规则计算。
- 作为调试者，我希望回看某次比赛，定位算法、通信或裁判处理造成的问题。

## 为什么需要

各电脑分别演示机器人运动不能验证对抗。比赛状态若由各客户端分别计算，命中与胜负也可能不一致。

## 怎么解决

所有参赛程序接入同一场比赛，由比赛端统一处理状态与事件。保存规则版本、场景版本和事件记录，便于复盘。

## 边界

- 按明确赛季和赛事规则逐项验收；RMUL 场地模型不代表完整 RMUC 规则。
- 联机需要验证时序、断线与带宽，不能以连接成功代替比赛正确。
- 不把评测用的全场真值暴露给参赛算法作为常规传感器输入。
