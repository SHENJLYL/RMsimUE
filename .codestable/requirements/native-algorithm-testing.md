---
doc_type: requirement
slug: native-algorithm-testing
pitch: Windows 和 Ubuntu 使用同一套机器人程序完成仿真测试
status: draft
last_reviewed: 2026-11-13
implemented_by: []
tags: [native, windows, ubuntu, ros2]
user_review: not_requested_under_standing_authorization
---

# 原生同源算法测试

## 用户故事

- 作为当前使用 Windows 的开发者，我希望在本机原生运行机器人程序和仿真。
- 作为同样需要在 Ubuntu 测试的开发者，我希望两端使用同一份 `sentry_ws` 源码。
- 作为哨兵程序维护者，我希望测试现有自瞄、决策、导航与控制链，发现实际接入问题。

## 为什么需要

现有程序偏向实车 Linux 环境。只有画面能在 Windows 打开，无法证明同一套机器人算法能够在那里工作。

## 怎么解决

两端加载相同版本的程序、场景和参数，并分别记录构建与运行结果。硬件输入在仿真中由相同语义的数据替代，算法和决策保持同源。

## 边界

- 不使用 WSL 满足 Windows 原生要求。
- 两端可以使用不同的本机编译器和驱动，不能以不同算法实现替代同源验收。
- 格式校验和单个模块通过，不代表整套机器人程序或实车性能已通过。
