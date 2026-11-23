# Flanders original crashed zeppelin

This package restores the original mission-authored landmark omitted by the base-level RIGz import. Source archives and the original game remain unchanged.

## Original source and placement

mis.vromfs.bin -> gamedata/missions/cta/tanks/vlaanderen/vlaanderen_dom.blk -> objects/template_vlaanderen_objects.blk -> objects/template_vlaanderen_object_groups.blk. The final mission template selects 35 actual zeppelin composite groups. aces.vromfs.bin/gamedata/objectgroups/us_zeppelin_whale_*.blk expands them into 67 original RendInst instances from 31 source resources.

The mission's misleadingly named us_zeppelin_whale_barrel_a36 actually references market_cart_c far from the wreck and is excluded by resource identity. All seven actual zeppelin barrels, three propellers, four trusses, both cabins and their original contents, and nineteen main body segments are retained.

The original middle body base is Dagor (516.950012, -2.950000, 1028.550049) metres, equivalent to ROS (516.950012, -1028.550049, -2.950000) and UE (51695.0012, 102855.0049, -295.0000) centimetres. Exact child and parent transforms are in the generated mission manifest. Dagor's official dag_TMatrix.h confirms four Point3 columns and parent-by-child composition. Each complete affine linear transform is baked into the mesh, with the original translation serialized to RMI1.

## Geometry, materials and fixed collision

There are 55 visual FBX assets, 53 original collision FBX assets, 67 visual instances, 65 colliding instances, 12 source material identities and 26 original textures. Expanded visual geometry contains 151,462 source LOD0 triangles. Original collider instances contain 35,207 physical triangles. The two original cabin panelling resources have no source collision and remain nonblocking.

FBX geometry preserves source normals, UV channels and all material slots. The textured tarp uses its original alpha cutout at 127/255. The cracked-glass source provides T0 color/opacity and T2 AG normal/R smoothness; the scoped native author uses lit surface translucency. Source mesh decals retain native diffuse decal or linear Modulate blend modes. Shared map material adapters remain in use for original layered materials. Dagor global palette painting, spatial Perlin distribution and renderer-specific optical/decal effects are recorded as adaptations in the native report.

Collision uses original active PHYS_COLLIDABLE geometry, with double-sided complex-as-simple static meshes. Open truss areas retain their source openings. Actors/components carry RMSim.WTMissionObjects.Zeppelin and RMSim.WTFixedLandmark tags; they receive no destructible behavior.

## Reproduction

Run the extract stage with the original game root and installed Epic Oodle DLL, writing to a fresh work directory:

~~~powershell
python tools/maps/prepare_wt_mission_objects.py extract --game-root 'D:\steamlaptop\steamapps\common\War Thunder' --work artifacts/maps/wt-zeppelin-minimap-20261119/zeppelin-audit/extraction-new --oodle-library E:\simulator-tools\UE_5.8\Engine\Source\Programs\Shared\EpicGames.Oodle\2.9.10\runtimes\win-x64\native\oo2core.dll
~~~

Prepare and verify using the installed Blender:

~~~powershell
& E:\blender\blender.exe --background --python-exit-code 1 --python tools/maps/prepare_wt_mission_objects.py -- prepare --work artifacts/maps/wt-zeppelin-minimap-20261119/zeppelin-audit/extraction-new --output assets/maps/wt_vlaanderen/mission-objects
& E:\blender\blender.exe --background --python-exit-code 1 --python tools/maps/prepare_wt_mission_objects.py -- verify --output assets/maps/wt_vlaanderen/mission-objects
~~~

After the caller backs up World.umap and holds the project mutex, run tools/unreal/author_wt_mission_objects.py in the existing project editor. Optional arguments are --package, --evidence, and --verify-only. Assets are scoped to /Game/RMSim/Maps/wt_vlaanderen/MissionObjects. Only actors with the exact ownership tag and exclusively owned mesh paths are replaced. Other saved-world actors are fingerprinted before and after.

The default native report is artifacts/maps/wt-zeppelin-minimap-20261119/native-zeppelin-01/native-authoring.json. It records current helper hashes, mesh geometry/material/UV/bounds readbacks, all native instance poses, fixed complex collision, world save/reload, and an actual collider-hit/opening-miss pair selected from source-proven candidates.

## Evidence

source-provenance.json binds original source hashes, resource identity, exact placement, preparation scripts and verification. preparation-summary.json gives compact counts and bounds. Complete generated manifests, FBX/texture data and native caches remain local. The verified offline maximum geometry/translation error is 0.0000607042341 m. Native rendering and gameplay acceptance are recorded separately by the caller.
