from pathlib import Path
import hashlib
root=Path("D:/simulator")
p=root/"docs/progress/current.md"
raw=p.read_bytes()
t=raw.decode("utf-8-sig")
head=t.splitlines(keepends=True)[0]
marker="## 2026-11-19：用户提供的学校体育馆与三维校园"
if marker in t:raise FileExistsError("School progress already inserted")
block="""## 2026-11-19：用户提供的学校体育馆与三维校园

RMUC2027 默认场馆已替换为用户上传的 `Indoor Basketball Gym.skp`（原始 SHA-256：`a40bbf0c69bace3b5ee1aed3795217e69c88c4c58ce71b191aff3e033e3502bc`）。使用 OpenSKP/Blender 转换为 UE 本地资产，保持原场馆约 55.47×38.12×14.02 米学校规模。体育馆和校园外景共含 19 个新网格；另复用 Innerscene CC0 的六连独立座椅模型，144 组共 864 座，并补建 72 段六级阶梯。室内窗外有真实三维校园教学楼、道路、绿地和 22 棵 Poly Haven 树。原 DJI/RoboMaster 标识经实机验收修正 UV 方向，Lumen 全局光照和反射均运行。

原比赛场地 CAD、车辆和导航数据未改变，玻璃内侧到比赛场地源边界的净距保持 0.001 米。UE 原生三种视角各 96/96 项围墙碰撞通过，4 面连续碰撞墙、60 片玻璃和 4 扇永久关闭装饰门保留。源 SketchUp 模型、复制导出素材与原生 UE Content 保持本机使用，不上传受限原始模型。变更细节在 `docs/changes/2026-11-19-school-gym-sketchupbox.md`，原生证据位于 `artifacts/fixes/downloaded-gym-20261119/school-native-views-02` 和 `school-native-views-03`。

"""
after=t.replace(head,head+chr(10)+block,1).encode("utf-8")
dest=root/"artifacts/fixes/downloaded-gym-20261119/progress-before-school-gym.md"
if dest.exists():raise FileExistsError("Unreviewed backup exists")
dest.write_bytes(raw)
if p.read_bytes()!=raw:raise RuntimeError("Progress document was edited concurrently")
p.write_bytes(after)
print("SCHOOL_PROGRESS_RECORDED",hashlib.sha256(after).hexdigest(),flush=True)
