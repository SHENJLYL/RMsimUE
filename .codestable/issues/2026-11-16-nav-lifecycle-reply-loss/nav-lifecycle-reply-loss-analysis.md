---
doc_type: issue-analysis
issue: 2026-11-16-nav-lifecycle-reply-loss
status: confirmed
authorization: standing_autonomous_simulator_request
tags: [navigation, lifecycle, rpc]
---

# 根因与方案

Humble nav2_util无超时的change_state重载调用无限等待的ServiceClient.invoke。RMW FastDDS6.2.10在服务回复读写端尚未匹配时可返回RMW_RET_TIMEOUT；转换本身已经执行。单纯重发configure会重复非幂等操作。

- [固定RMW源码](https://github.com/ros2/rmw_fastrtps/blob/6.2.10/rmw_fastrtps_shared_cpp/src/rmw_response.cpp)
- [固定Humble客户端](https://github.com/ros-navigation/navigation2/blob/1.1.20/nav2_util/src/lifecycle_service_client.cpp)
- [当前上游管理器的service_timeout机制](https://github.com/ros-navigation/navigation2/blob/main/nav2_lifecycle_manager/src/lifecycle_manager.cpp)

工作副本管理器增加默认5秒service_timeout（Humble API只支持整秒，向上取整）；切换回复异常后仅查询实际状态，达到目标才继续，不重发转换。未达到或状态查询失败仍停止bringup。原代码、工作副本、patch分开保存。

验证夹具：原生manager对已提交但无回复情形应继续到active且configure只调用一次；未提交状态且无回复应返回inactive，不误报成功。夹具不连接仿真车辆，不代替实际RMUC导航验收。

首次直接调用Build-Navigation.ps1导致MSVC缺标准库map包含路径，属于未经过vcvars的调用错误。正确入口tools/environment/Build-Navigation.cmd；失败日志保留在log_navigation/build_2026-11-16_11-07-01。
