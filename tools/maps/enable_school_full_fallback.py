from pathlib import Path
import hashlib,json
r=Path('D:/simulator');p=r/'tools/unreal/import_school_gym_assets.py'
s=p.read_text(encoding='utf-8-sig');o=r/'artifacts/fixes/school-view-fidelity-20261119/full-fallback-source-01';o.mkdir(exist_ok=False)
(o/'import_school_gym_assets.before.py').write_text(s,encoding='utf-8')
old='def import_mesh(entry, root, manifest, materials):'
new='def import_mesh(entry, root, manifest, materials, full_fallback=False):'
assert s.count(old)==1;s=s.replace(old,new)
old='    nanite.set_editor_property("fallback_percent_triangles", 1.0)\n    SMS.set_nanite_settings(mesh, nanite, True)'
new='''    nanite.set_editor_property("fallback_percent_triangles", 1.0)
    if full_fallback:
        # Auto chooses a reduced fallback even when the numeric percentage is 1.
        nanite.set_editor_property("fallback_target", unreal.NaniteFallbackTarget.PERCENT_TRIANGLES)
        nanite.set_editor_property("keep_percent_triangles", 1.0)
        nanite.set_editor_property("trim_relative_error", 0.0)
    SMS.set_nanite_settings(mesh, nanite, True)'''
assert s.count(old)==1;s=s.replace(old,new)
compile(s,str(p),'exec');p.write_text(s,encoding='utf-8')
print('OPTIONAL_FULL_FALLBACK_ADDED',hashlib.sha256(p.read_bytes()).hexdigest())
