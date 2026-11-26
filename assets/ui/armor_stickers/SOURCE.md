# RMUC 2026 装甲图案

`sentry_sticker.png` 和 `outpost_sticker.png` 取自 RMUC 2026《机器人制作规范手册》V2.0.0 的附录图 5、6。使用前述赛季图案作为 RMUC 2027 仿真的识别基线。 [RoboMaster 官方发布页](https://bbs.robomaster.com/wiki/20204847/809871?source=7)

输入 PDF 标注日期为 2026-06-26，SHA-256 为 `77010458d06616b2bafad4400aad384f72e1b7007269c58061384de8fd0fb41b`。正面基准为 135 × 125 mm，纹理为 1080 × 1000 像素。RGB 存放黑白图案，Alpha 存放两条独立的灯条发光遮罩。保持官方图案方向和长宽比，CAD 安装孔由原件决定。

在具有 Python、Pillow 和 `pdftoppm` 的 Windows 环境运行 `tools/unreal/Prepare-RmucArmorPresentation.ps1 -Rulebook <官方PDF路径>`。脚本生成本机 PNG 以及 UE 装甲材质。原规则 PDF、派生 PNG 和 Unreal Content 作为本机资源管理。
