# RMUC 装甲图案与赛场照明

哨兵炮塔图案和前哨塔图案采用 RMUC 2026 制作规范附录 5、6。哨兵贴纸仅盖住中央 103 mm，左右使用车体原生灯条。前哨在 135 × 125 mm 面内使用遮罩发光，贴纸随转子转动。两类板面都沿原 CAD 和判定平面绑定，视觉网格为 NoCollision。 [资产再生成说明](../../assets/ui/armor_stickers/SOURCE.md)

车体原生灯条与前哨共用红蓝配色。哨兵受击闪烁、战亡熄灭，前哨易主后换色、摧毁时熄灯。车载 Mid‑360 与雷达玻璃使用原始材质。贴纸上的黑白图案使用中性无高光显示，局部彩灯照亮周围构件。

已有场馆矩形灯、日光与天光沿用。平方反比局部点光随哨兵灯条、前哨、能量机关、基地和前哨引导灯、基地与堡垒状态灯、飞镖尾灯与检测标记、红蓝 17 mm 弹丸联动。每条能量机关灯臂分为内段、中段与靶环三个光源，轴心 R 独立。局部灯使用有限半径并关闭独立阴影和体积散射。荧光弹丸按发射方着色，灯随弹丸回收。

## Windows 实测

`Build-ImportLab.ps1 -EngineRoot E:/simulator-tools/UE_5.8` 返回 Succeeded。实际 D3D12 双哨兵比赛采了 26 张 1280 × 720 的 aim_camera 帧。覆盖两台车的八块装甲、两座前哨的六块装甲、原生灯条、转子跟随、红转蓝、摧毁、受击、车辆战亡、两台车的原始雷达和弹丸。测试探针暂时移动已配置的 aim_camera，保存后恢复其安装位姿。

17 mm 弹丸回归覆盖 16 个装甲查询点、非装甲车身、地板、玻璃和前哨。两项 UE 进程均正常退出。单个前哨点光开关对照固定同一机位，附近有 6425 个像素的红通道增加至少 10 个 8 位色阶。白色图案像素 RGB 通道差的 95 分位在开灯和关灯时分别为 1 与 1 个色阶。 [原生回执](../../artifacts/unreal/armor-camera-20261125-neutral/verification.json) · [实拍总览](../../artifacts/unreal/armor-camera-20261125-neutral/armor-views.png) · [点光开启](../../artifacts/unreal/armor-camera-20261125-neutral/direct-light-on.png) · [点光关闭](../../artifacts/unreal/armor-camera-20261125-neutral/direct-light-off.png)

本轮大小能量机关原生运行均完成激活、检查到每面 15 个灯臂点光、按时清除增益和熄灯。 [小机关实拍](../../artifacts/unreal/rmuc-rune-lifecycle-20261125T031958Z/small-activated.png) · [大机关实拍](../../artifacts/unreal/rmuc-rune-lifecycle-20261125T031958Z/large-activated.png) · [回执](../../artifacts/unreal/rmuc-rune-lifecycle-20261125T031958Z/receipt.json)

## 原模型离线推理

在 18 张点亮的真实相机帧上按 0.8 置信度检查类别和颜色。 [原模型项目](https://github.com/unnc-aim/26UL_sp_vision_25_rosy/tree/2bab801d91c6944dbdb99cbb4d4d431ed15d6e0d)

| 推理路径 | 类别与颜色均通过 |
| --- | --- |
| 灯条 + tiny_resnet | 11 / 18 |
| YOLOv5 | 7 / 18 |
| YOLOv8 + tiny_resnet | 16 / 18 |
| YOLO11 | 8 / 18 |

结果限于所测机位、曝光和模型版本。 [逐帧检测和框图](../../artifacts/unreal/armor-camera-20261125-neutral/detection/detections.json)

模块 SHA-256 为 `1584822c963c40ec67b925e1e403e5f6f9268c9765e15d4c4f18fbcdaa74638e`。
