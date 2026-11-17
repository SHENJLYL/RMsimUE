# 原生迭代状态

- trace-baseline：长窗失败，source tyre crown尚未应用。该版steering_solver_torque_nm字段误名，值实际为Nms。
- trace-stiffness-1000：舵角<1.2度但慢行仍失败；刚度已恢复80。
- source-hull-candidate：只换源胎冠凸包，全部原控制参数保持；trace-source-hull5项成功，完整4秒窗口峰值误差0.02584m/s/RMS0.01031m/s。
- Formal Import-VehicleAssets -ContactCollision退出0；四个wheel旧圆柱因几何哈希变化失效并由原网格凸包替换。pipeline-verification.json确认只有轮碰撞变化、FBX未改、参数当时不变。正式新文件可按相同脚本在Ubuntu离线重建，但Ubuntu未原生测试。
- 新PrecisionTrace原生加入precision_steady_spin检查，保留2秒原点，并验证4秒全窗口误差/采样覆盖。release-spin6项、release-controls19项、release-waves红蓝各5项通过。
- release-traction前5项通过：顺坡自由滚动71.826cm；横坡仅0.223cm。最终hold位移0.868cm通过<1cm，但车身COM速度0.758cm/s超过0.5cm/s，整组失败。轮心速度约0.14cm/s，同时有车身摇摆，仍不放宽门限。
- 当前候选：仅AIM profile中的WheelVelocityIntegralGainPerSecond由3→6，测试traction-integral-6，目的是让重力偏置更快收敛。未完成前不得宣称最终参数/完整回归通过；原值保存在artifacts/control/gyro-ripple-20261116/before/drive-profile.json。若无效恢复3。
- 原run-release在release-traction处退出；integration/ROS/LK尚未执行，不能按脚本清单宣称通过。后续必须从实际证据继续。
- 预览PID35612为编译已核实并关闭；当前未恢复交互窗口。旧安全弹窗待用户取消，不重复请求、不给防火墙放行。

- traction-integral-6末速0但位移1.045cm仍超1cm，候选撤回，Ki恢复3。原Ki3末端轮心沿坡速度约0.14cm/s而车身原点0.92cm/s，存在明显悬挂俯仰摇摆。当前试验改为SpringDampingNsPerM90→140，其他参数恢复；按25kg/4、1600N/m估算单轮临界阻尼约200Ns/m，140对应约0.7阻尼比，属于未实车标定的演示阻尼配置。traction-damping-140验证中。

- traction-damping-140末速1.058cm/s、位移1.025cm仍失败；阻尼恢复90。确认只接触四轮，没有底盘/载架触地；原Ki3轮驱动合计约3.67Nm，接近25.6kg在20°坡的理论3.65Nm，偏置尚在收敛。Ki6已经末速为0，但累计位移略超限。下一候选Ki8，保持全部物理参数原值；其0.125s积分时间尺度且Ki*最大控制步长0.1s<1，继续验证而非放宽停车门限。

- traction-integral-8在初始坡面稳定性门限失败：世界up.z=0.89993（原门限>0.9），并非停车阶段，未取得Ki8停车结论。源胎冠去掉人工边缘接触后，20°坡上的弹簧静态俯仰下沉接近6°；当前1600N/m、每角约5kg，俯仰回复刚度约120Nm/rad。
- 下一份AIM演示配置保持Ki8，弹簧2200N/m、阻尼140Ns/m：提高静态支撑/俯仰回复刚度至约165Nm/rad，每角按约5kg估计阻尼比约0.67。该组是未实车标定的配对悬挂配置，质量/摩擦/行程/预载/电机上限不变，不更改测试门限；traction-supported-profile验证中。所有原值可从before恢复。

- traction-supported-profile仍失败（位移1.026cm/末速0）。所有Ki/弹簧/阻尼候选已撤回，drive-profile与本轮before逐字一致，当前仍Ki3/K1600/D90。
- 更准确定位到夹具前提：traction_reset_for_hold原只要求2秒后速度<5cm/s；后续立即以静止保持标准<0.5cm/s衡量，起点实际仍有运动与弹簧沉降。现前提收紧为底盘原点连续0.5秒速度<0.5cm/s，最多等12秒；原后续<1cm/3秒与末速<0.5cm/s完全保留，并新增真实0.35m/s下坡后松键制动（<12cm、末速<0.5cm/s）。这属于测量前提修复，不能把等待时间隐去。新原生用例traction-settled-start验证中。

- traction-settled-start在12秒仍无法持续0.5秒低于0.5cm/s，说明不只是夹具起点，原零速PI存在持续微动。维持收紧前提与新增下坡松键检查。
- 当前产品候选加入有限力矩的停车轮轴位置保持：停车请求且轮面速度<0.02m/s后记录实际关节角，位置刚度WheelHoldStiffnessNmRad=40（可设0关闭），保留已有力矩上限和负载偏置；行驶/自由滚动/原LK电流接管时关闭位置保持，R清理目标。没有刚性锁关节或写车体位姿/速度。新增参数可选，旧车辆包仍可读取。其余控制/质量/摩擦/弹簧/阻尼已全部恢复本轮before原值。
- 最新构建成功；traction-position-hold原生8项检查运行中，尚未算通过。

- traction-position-hold仍不能满足连续静止前提。数据出现多帧速度精确归零、随后重新微动，发现VehicleDrive每帧无条件WakeAllRigidBodies，包含停车状态。当前隔离对照：WheelHoldStiffnessNmRad=0禁用新增保持，保留原Ki/物理参数；只在运动请求或自由滚动诊断时主动唤醒，外部接触仍交给Chaos唤醒。新增awake状态记录；traction-rest-wake原生8项正在验证。若此根因修复有效，移除不必要的位置保持候选。

- traction-rest-wake8项原生成功：关闭位置保持、原Ki/全部物理参数，静止前提2.001秒完成；保持段位移约0/速度0；真实下坡51.753cm后松键5.447cm停车/末速0。awake记录确认停车时允许自然休眠。根因是每帧无条件唤醒，已只在行驶/自转/自由滚动诊断时主动唤醒，新命令自动重新唤醒，外部物理作用仍由Chaos处理。
- 已删除整个位置保持候选（候选代码留rejected-position-hold），drive-profile已逐字恢复before。保留更严格的静止前提；进一步加入外部10Ns冲量唤醒和恢复两项，测试总计10项，以防停车被错误固定。最新最终构建/回归待跑。

- final-traction10项/4PNG已通过：原坡向抓地、收紧静止准备、原保持门限、实际下坡松键5.356cm停车、外力10Ns唤醒/恢复。final-spin6项/4PNG通过：579帧/3.999秒，峰值速度误差0.02531m/s、世界向量RMS0.01673m/s、最大间隔8ms。final-controls及后续路线/训练/ROS/LK仍运行中。
