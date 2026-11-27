# RMUC 3v3 补给、定位与目标保持

## 补给区与裁判

恢复 RMUC2027 CAD 两侧补给区的八个顶点，单侧读卡范围由 5.26 m² 恢复至 8.10 m²。手动 F 请求由裁判在更新本车读卡状态后处理。ROS 哨兵席位收到本车位置对应的读卡、购弹资格和待交付状态。自动哨兵已进入己方补给区且需要补弹时优先请求本地购买，离区后按原有远程资格下单。已接单的远程购买等候交付。 [RMUC2027 官方规则 §5.4.1–5.4.2](https://bbs.robomaster.com/wiki/20204847/1969149?source=7) · [区形来源及复核](../../assets/maps/rmuc2027/FIELD-ZONES.md)

Windows UE5.8 和 MuJoCo 四场验证通过。红蓝 3 号步兵在恢复的边缘区域各从 0 发购至 10 发，红蓝 7 号哨兵各从 300 发购至 310 发，金币均为 800→790。4 号步兵沿用同一手动处理分支。场外哨兵在己方步兵占区时仍无本地资格，远程扣 150 金币，在约 6 s 后发放 100 发。步兵测试通过开发探针调用 F 已绑定的实际裁判函数。交付与真实读卡状态均来自原生运行。 [四场回执](../../artifacts/verification/rmuc-match-20261127/supply/verification.json) · [红蓝 C++ 自动购弹验证](../../artifacts/verification/rmuc-match-20261127/navigation/verification.json)

## 3v3 导航

双 ROS 席的 MID360 点云已按四通道组降采样。原 `point_filter_num=8` 又固定抽取同一通道。已声明整组降载的仿真点云将该项设为 1，原距离与体素过滤继续处理四通道。若前哨导航动作失败，比赛行为树依次试三个观察位，再回到普通战术。驻停前哨优先使用停转观察位。 [定位运行器](../../ros2_ws/src/rmsim_bridge/rmsim_bridge/point_lio_runner.py) · [行为树验证与打包校验](../../artifacts/verification/rmuc-match-20261127/navigation/verification.json)

使用用户同一份 3v3 编队和 `Start-Simulator.ps1 -RosVehicleTeam both -AutoSentry -CameraHz 15` 在 Windows 离屏做前后对照。新版保持原地图与 MuJoCo 物理，红蓝两席的实际 Point-LIO 均加载了参数 `1`。UE 裁判输出车位只用来独立计算误差。约 48 s 的短场录包包含定位启动期及实战行进。

| 指标 | 红方旧版 | 红方新版 | 蓝方旧版 | 蓝方新版 |
| --- | ---: | ---: | ---: | ---: |
| 距出生点最大位移 | 4.69 m | 10.99 m | 2.13 m | 7.44 m |
| AMCL 平面误差 P95 | 8.34 m | 0.42 m | 3.51 m | 1.14 m |
| Point-LIO 最低 Z | −3.99 m | −0.06 m | −0.21 m | −0.05 m |
| 记录到的路径规划失败 | 14 | 0 | 3 | 0 |

联合测试退出码为 0。UE 模块 SHA-256 为 `c196f94233658dfc13d35b6f337161be9d523d578352dc421ea166f207713ed7`。保留一次仍加载旧桥安装副本的试跑，比较表采用桥重装后的有效运行。更广的 YAML 和速度源快照各有旧参数差异，详见独立的导航回执。 [联合运行](../../artifacts/verification/rmuc-match-20261127/combined/summary.json) · [轨迹对照](../../artifacts/verification/rmuc-match-20261127/native-motion-comparison.json)

## 仿真视觉与目标保持

仿真视觉等待裁判确认己方阵营，再核对图像上同一装甲两侧的灯条颜色。保留车型识别类别，前哨易手后依据实际归属更新可选目标。当前目标持续可见时延续同一跟踪，短暂遮挡保留跟踪状态。交接至少间隔 220 ms 且需要三个不同采集时间的有效候选，重复帧不累计。Windows 比赛使用的 C++ 节点和 Python 回退各通过 6 项 DDS 回放检查。联合复测中，红蓝哨兵各出现两段持续 6.7–7.6 s 的同编号敌方车辆跟踪。 [C++ 视觉回放](../../artifacts/verification/rmuc-match-20261127/vision/sim-native-clock.log) · [Python 视觉回放](../../artifacts/verification/rmuc-match-20261127/vision/sim-python-clock.log) · [红方遥测](../../artifacts/verification/rmuc-match-20261127/combined/red-telemetry.json) · [蓝方遥测](../../artifacts/verification/rmuc-match-20261127/combined/blue-telemetry.json)

本轮联合对照覆盖 Windows 开局和短时交战。更长对局、Ubuntu 原生运行和逐车型完整对战仍待独立验收。
