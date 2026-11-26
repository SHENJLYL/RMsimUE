# RMUC 能量机关深蓝灯效与中性底色

按用户校正，中央转盘未亮的部分采用中性石墨灰。亮灯时，蓝方靶环、箭头、实际 CAD 灯臂边缘统一深蓝，红方对应部件亮红。移除全亮状态下额外生成的中间和两侧三条长线。短箭头和大机关分段进度继续由现有裁判阶段驱动。

启动时中性化旧资产的彩色旋转网格槽。CAD 导入也把红蓝两面的固定 R 标志重新分配到中性灰材质，共 108 个三角面。两套固定平台资产均已原生导入，保留转盘三角面、碰撞和原有场地其他配色。

## Windows 验收

UE5 编译成功。红蓝两面分别实测亮靶、成功全亮和 45 秒增益到期后的熄灯，使用 UE 原生截图检查转盘色彩。两次均通过 128 项场地装置查询测试。

- 蓝方 [亮靶](../../artifacts/unreal/rmuc-rune-neutral-blue-20261125T011529Z/small-targets.png) · [全亮](../../artifacts/unreal/rmuc-rune-neutral-blue-20261125T011529Z/small-activated.png) · [熄灯](../../artifacts/unreal/rmuc-rune-neutral-blue-20261125T011529Z/small-expired.png) · [颜色检查](../../artifacts/unreal/rmuc-rune-neutral-blue-20261125T011529Z/rotor-color.json)

- 红方 [亮靶](../../artifacts/unreal/rmuc-rune-neutral-red-20261125T011642Z/small-targets.png) · [全亮](../../artifacts/unreal/rmuc-rune-neutral-red-20261125T011642Z/small-activated.png) · [熄灯](../../artifacts/unreal/rmuc-rune-neutral-red-20261125T011642Z/small-expired.png) · [颜色检查](../../artifacts/unreal/rmuc-rune-neutral-red-20261125T011642Z/rotor-color.json)
