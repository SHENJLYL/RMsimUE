# 原生迭代记录

- 原sp_msgs/pb_rm_interfaces共20文件保留并验证Git blob。PB补显式std_msgs依赖，两个包补runtime导出；4包Windows原生构建通过。原消息结构未改。
- 解释来源固定为工作区引用的universal_controller95c66c2、behavior aa4d82b；视觉仓库已从sp_vision_25_rosy重命名为26UL_sp_vision_25_rosy，固定2bab801d不变。实际publisher以node->now()填写timestamp；完整算法输入仍未适配。
- 首次UE编译暴露5.8共享JSON字符串键与FString的转换差异，改为明确TCHAR查询；Fire/Permit初始化警告已清理。构建成功。
- native-01：18项通过，原消息扫描/绝对角度/世界稳定/机械限位、真实弹丸击中靶、热量限射、数据隔离和独立有效期有证据。
- 补充关节速度按实际角度差采样，观察模式也有有效数据；ROS发射按帧前后炮口位姿插值。CombatProbe新增真实两发碰撞的时间分割验证，避免用帧末炮口姿态提前发射。
- native-02-final：21项通过，实际R只增加一次训练epoch、安装aim_demo运行成功。该运行在后述限位标志修正之前，不能把它的模块默认为最终模块。
- native-03-lk-combined：实际pitch始终正确限于0.55rad，但每次新指令清除mechanically_limited导致状态短暂错误。只修复反馈标志，不改变运动或放宽测试。
- native-04-lk-combined：修正后20项通过，原LK底盘、固定1ms时间和外部瞄准/发射同时工作。
- native-05-observe：4项通过；原消息与原始wire都被只读模式拒绝。真实鼠标拖动测得yaw=-0.194658rad、瞬时velocity=-9.424778rad/s；真实R在日志中为manual_R count1并重连。测试结束关闭窗口，R后截图未取得，不声称该截图存在。
- regression-combat：15项/10PNG通过，包括150ms炮口转动的两发实际碰撞，1/3时间分割一致；普通ROS11项已通过，其余回归执行中。
