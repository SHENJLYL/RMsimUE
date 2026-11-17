# 根因

1. 炮口与第三人称镜头位置不同。原逻辑切镜后保留目标，但沿用炮口方向，造成静态视差；C 观察释放后也可能保留不匹配方向。
2. GetInputMouseDelta 返回 UPlayerInput 处理后的值，已受到引擎 0.07 轴灵敏度、FOVScale 和鼠标平滑影响；控制器随后再乘 0.12 和 FOV 比例，造成重复缩放。
3. 演示云台仅 yaw 90°/s、pitch 60°/s，大角度瞄准存在明显人为等待。

证据为本机 UE 5.8.4 的 PlayerController.cpp/GetInputMouseDelta、PlayerInput.cpp/GetKeyValue/MassageAxisInput 与 Engine/Config/BaseInput.ini，以及失败的引擎复现。

修复范围只在手动控制/相机边界，保留真正炮口标记、关节限位和碰撞；不通过把橙框强行画到准星上隐藏真实偏差。
