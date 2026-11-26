# RMUC 2026 装甲图案

`sentry_sticker.png` 和 `outpost_sticker.png` 取自 RMUC 2026《机器人制作规范手册》V2.0.0 的附录图 5、6。使用前述赛季图案作为 RMUC 2027 仿真的识别基线。 [RoboMaster 官方发布页](https://bbs.robomaster.com/wiki/20204847/809871?source=7)

输入 PDF 标注日期为 2026-06-26，SHA-256 为 `77010458d06616b2bafad4400aad384f72e1b7007269c58061384de8fd0fb41b`。正面基准为 135 × 125 mm，纹理为 1080 × 1000 像素。RGB 存放黑白图案，Alpha 存放两条独立的灯条发光遮罩。保持官方图案方向和长宽比，CAD 安装孔由原件决定。

在具有 Python、Pillow 和 `pdftoppm` 的 Windows 环境运行 `tools/unreal/Prepare-RmucArmorPresentation.ps1 -Rulebook <官方PDF路径>`。脚本生成本机 PNG 以及 UE 装甲材质。原规则 PDF、派生 PNG 和 Unreal Content 作为本机资源管理。

`infantry_3_sticker.png` 与 `infantry_4_sticker.png` 取自 RMUC 2026 制作规范 V1.2.0 附录图 3、4 的中央数字。输入 PDF 校验为 `f3b168cf3df10ca0f7ee92884586537d658da6b05e752d4c19bcdea0ed57eb9e`。生成器在现有 AIM 贴纸上替换中央图案，并沿用尺寸、黑白水平和 Alpha。 [官方制作规范 PDF](https://bbs-web-static.robomaster.com/a36873dca86c4883b9b067eda72ad43b1767087376172/RoboMaster%202026%20%E6%9C%BA%E7%94%B2%E5%A4%A7%E5%B8%88%E9%AB%98%E6%A0%A1%E7%B3%BB%E5%88%97%E8%B5%9B%E6%9C%BA%E5%99%A8%E4%BA%BA%E5%88%B6%E4%BD%9C%E8%A7%84%E8%8C%83%E6%89%8B%E5%86%8CV1.2.0%EF%BC%8820251230%EF%BC%89.pdf)

运行 `python tools/ui/generate_infantry_stickers.py <V1.2.0 PDF 路径>`，生成两张步兵贴纸。比赛车辆席位的可选 `armor_number` 选择显示图案。省略或设为 7 会使用既有哨兵图案。
