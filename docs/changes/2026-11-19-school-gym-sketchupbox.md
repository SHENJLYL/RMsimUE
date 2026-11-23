# 2026-11-19 — 用户提供的 SketchUp 学校体育馆接入与窗外校园
> 历史记录：本文保留初版接入的资源数量、额外座椅和当时截图证据。后续已切换至 `SchoolGymFidelity`，当前为 23 个 native mesh、57 个布局实例，保留源看台且额外新增座椅为 0。材质继承恢复、运动区中心对齐及逐面核验见 [源体育馆还原与场内视野](2026-11-19-school-gym-fidelity.md)；最新地板承托与 0 m 净距围墙见 [2026-11-20 接触修正](2026-11-20-school-floor-contact.md)。下文 864 座、72 段阶梯、265 实例及旧裁面策略均属于初版历史方案。

## 历史交付范围（初版）
RMUC2027 主场馆现使用用户提供的 `Indoor Basketball Gym.skp`（SketchUp 8），原始文件 SHA-256 `a40bbf0c69bace3b5ee1aed3795217e69c88c4c58ce71b191aff3e033e3502bc`，位于 `assets/maps/rmuc2027/arena/sketchupbox-school-gym/source/`，保留同包 `readme.txt` 和 `Free Sketchup Models.url`。通过 OpenSKP 1.5.0（Windows Python 3.14）转换保留组件实例的 GLB，在 Blender 5.1.2 依据原单位转换并裁分成 15 块 UE 原生建筑网格。原场馆外廓约 55.47×38.12×14.02 米，保持学校综合体育馆实际尺寸。源篮球架相关对象已移除，中央比赛场地内的重叠可视三角面仅从**复制的导入几何**剔除，原 SketchUp 模型只读。
场馆窗口外新增真正的校园三维景观：4 块约 80×70 米地面绿地／道路模块形成完整外场、独立三层教学楼窗户与屋顶、道路家具、长椅、灯柱及 22 棵 Poly Haven CC0 树木；草地 PBR 来自 ambientCG CC0。窗外从场馆内部实际渲染为外部教学楼、树叶与透视景观，具有三维视差，而非单独 HDR 贴图。建筑、玻璃、树冠、木地板、金属和品牌材质走 UE 原生材质路径。4 个官方 DJI／RoboMaster 横幅源自现有官方品牌资源，经实机镜像方向核对后，仅修正派生的四张贴图副本。使用 existing Innerscene CC0 seat-row 原生 mesh 排列 144 组六座连椅（共 864 张独立椅），每侧六级 0.4 米升高／0.8 米进深的 72 段阶梯看台；阶梯与椅子为视觉资产且禁用碰撞。
新资源合计 **19 个新 UE 网格 + 一个复用的 CC0 座椅网格**，56 个新材质记录；整体 `rmsim_campus_gym_layout_v1` 具有 **20 个 native_meshes、21 个 groups、265 个布局实例**。12 盏室内矩形灯使用所选 `RMSimGymLumen` 配置（SM6／DX12／Nanite／Lumen GI 和 Reflections）。新体网格的原始贴图、PBR 与品牌材质均有来源记录。当前激活：`assets/maps/rmuc2027/arena-shell.json` → `arena/sketchupbox-school-gym/layout.json`。原 `assets/maps/rmuc2027/map.json`、车辆包、悬挂/操控/传感器、导航源数据均未改变。源边界的 `outside_source_bounds_v1` 与 `outside_clearance_m=0.001` 保持，四面连续碰撞 + 60 片可视玻璃 + 四扇永久关闭的装饰门仍在场。
## 历史原生验证（初版）
- `artifacts/fixes/downloaded-gym-20261119/school-native-import-final.json`：19 个模型资产、56 材质、16 贴图复用/导入、90 `.uasset` 文件，成功；后追加既有 CC0 座椅资产。
- `artifacts/fixes/downloaded-gym-20261119/school-activation-01/report.json`：场馆替换留有原始 `arena-shell.before.json` 回滚文件，原地图与车辆哈希未变。
- `artifacts/fixes/outside-navigation-20261119/school-active-preflight-02/verification.json`：场地碰撞与导航身份、所有源输入和原 CAD 追溯均通过。
- `artifacts/fixes/downloaded-gym-20261119/school-native-views-02/`：三个场景真实 UE 原生视角（受单独全馆镜头与构件遮挡影响，`CompleteGym` 预览镜头本身只拍到了墙面）；`WindowView` 显示了真实窗外建筑/树木和正确方向的标识，`DriverView` 显示场地、顶棚和新增真实阶梯座椅。三个视角均检测到 Lumen GI 和反射、20 native meshes、193 native instances；每次 96/96 的物理围墙扫掠通过，所有 314 个边界候选均在源 CAD 场外，最小净距 0.001 m，四面连续保护墙和四扇装饰门均完好。图片文件和完整探测数据分别留在各个视角子目录。
- 全馆新镜头、GUI 启动、真实帧率、光度与跨平台结果应按后续采样单独记录，避免用 Lumen 开启标识替代帧率验收。
## 模型使用许可与分发边界
- 原 SketchupBox 资源随附 `readme.txt` 说明：允许修改、扩充；使用时需注明来源 `www.sketchupbox.com`；禁止单独出售或重新分发模型。**原始 SKP、转换 GLB、OBJ、UE Content 不推送公共 GitHub**；仅分享脚本、配置、来源链接、许可说明、少量验证摘要。
- Poly Haven、ambientCG、Innerscene 的相应资源按 CC0 处理；DJI／RoboMaster 官方商标遵循原权利人标识规定，不能作为 CC0 重新授予。
## 初版工具与入口（历史）
从 `D:\simulator` 运行 `Play-ManualVehicle.cmd` 进入现有 RMUC2027 默认地图。修改画面后使用 `tools/unreal/capture_school_gym.py` 生成新原生截图，`tools/maps/activate_school_gym.py` 切换场馆清单，原始文件备份与 SHA 日志保留在独立 `artifacts/fixes/downloaded-gym-20261119` 目录。原生 UE 资源和大型第三方源素材仍本机保存。