# RMUC 哨兵工字形贴纸

按 AIM Sentry 的原 CAD 车身重做四面贴纸。上下宽边、腰部灯条避空、倒角和四个安装孔都位于同一贴纸平面。贴纸沿面法向保留 0.25 mm 渲染偏移。根据源 FBX 的四面投影核对，贴纸和 LED 内缘的最小净空约 0.43 mm。投影 UV 沿用板面尺寸，中央图案及原生灯条保持原尺寸位置。

源 `body.fbx` SHA-256 为 `9a3d93daa5a62c1bc77250e3cfb66772630cccce6c5fff7641e4e89b62476c44`。Windows UE5 构建与两台车共八面的几何检查通过，检测上下边、腰部、四个安装孔及贴纸单平面。原生装甲命中回归的 16 个装甲查询点，以及四套 C++ 裁判测试通过。 [近景蓝方](../../artifacts/unreal/armor-i-sticker-20261125T041624Z/sentry-blue-oblique.png) · [近景红方](../../artifacts/unreal/armor-i-sticker-20261125T041624Z/sentry-red-1.png) · [验收回执](../../artifacts/unreal/armor-i-sticker-20261125T041624Z/verification.json)
