# XD-1 上下核对与镖头配色

在原 CAD 正向镖头确定后，取原装配命名的左右机翼核对横向，再以由此确定的上方向复核设备舱盖。设备舱盖位于侧盖板和镖头中心的上方，和当前 UE 展示网格的上方向一致。侧视图中下垂的外形位于原模型下侧。 [源 CAD 核对](../../artifacts/unreal/rmuc-dart-xd1-up-and-color-20261127/orientation-audit.json)

蓝色镖头盖在源 STEP 外观中有独立材质槽。红方飞行展示为该槽加载红色材质，沿用蓝方材质的粗糙度和金属度。 [阵营材质数据](../../artifacts/unreal/rmuc-dart-xd1-up-and-color-20261127/red-nose-material.json)

Windows 原生红蓝飞行验证均通过。UE 检查确认两方绑定了各自的镖头盖材质，截图像素检测分别确认红和蓝。 [红方飞行](../../artifacts/unreal/rmuc-dart-xd1-up-and-color-20261127/red-flight/dart-model.png) · [蓝方飞行](../../artifacts/unreal/rmuc-dart-xd1-up-and-color-20261127/blue-flight/dart-model.png) · [原生验收](../../artifacts/unreal/rmuc-dart-xd1-up-and-color-20261127/verification.json) · [像素核对](../../artifacts/unreal/rmuc-dart-xd1-up-and-color-20261127/nose-pixels.json)
