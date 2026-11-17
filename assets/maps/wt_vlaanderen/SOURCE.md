# War Thunder — Flanders original map

This package is generated from the user's installed `avg_vlaanderen.bin` and the resources it references. It is separate from the earlier CDK sample crop. Original game and CDK inputs were read only. Map geometry, names, images and materials remain Gaijin / War Thunder content; this local conversion does not assert redistribution rights or an official UE port.

Source level SHA256: `427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f`. Format code was checked against the official DagorEngine source, locally pinned at `9639942de5a8e5b01d91977f60ed82cda22d5f41`. The source reader began from Dagor Asset Explorer `1f0d1e04ded04bf9b28e8d5be93cb79714f505d3`, with the audited compatibility patch stored under `artifacts/sources/wt_unpack`.

## Prepared inputs

- 45,675 original stored RIGz instances referencing 659 model types. All referenced models were resolved and decoded; source models retain all declared UV channels and material indices.
- 35,825 orthogonal rotation/scale instances remain instanced. 9,850 affine instances with shear/reflection are baked into spatial batches at their exact original matrices, instead of silently approximating them as UE transforms.
- 726 visual FBXs, about 12.1 million unique triangles after those affine bakes. Batches contain at most 64 material slots and approximately 200,000 triangles, except an indivisible source mesh when required.
- Original 4096 × 4096 HM2 height samples at 0.5 m spacing, decoded from the original level and copied byte-for-byte to `terrain/height.u16`. Fine region is 2,048 m wide. Its original quantization is preserved (`hmin=-20 m`, `hscale=64 m`).
- Outer source landmesh and original SCN static meshes are retained; the whole background is approximately 32 km wide. The four coarse land cells beneath the fine HM2 region are excluded using the source exclusion indices. Road/decal/combined geometry remains present.
- Original matched physical colliders are integrated. 616 source collision resources were found, including 13 intentionally nonphysical resources; 43 visual types had no matched collider. No visual substitute collider is invented for these missing resources. The extracted source SCol is included; source WCol is traceable water without PHYS_COLLIDABLE and is not turned into a solid road surface.
- 639 prepared collider FBXs, 1,365,365 triangles including repeated affine instances. Source capsule tessellation has a bounded 1 mm model-space sagitta approximation; source material names remain in the extraction metadata, not a calibrated UE friction model.

Coordinate transform is `Dagor(x,y-up,z) -> ROS(x,-z,y)`, with no translation/recentering and no scale change. Standard UE import then mirrors ROS Y and converts metres to centimetres, yielding UE world `(100*x,100*z,100*y)`. Landscape origin is zero; texture world coordinates must use the same origin.

## Files

- `scene-source.json`: visual/collision imports, material specifications, source coverage and limits.
- `meshes/`, `collision/`: FBX assets; hashes and local bounds are recorded in the descriptor.
- `instances.json`, `instances/*.rmi`: UE instance import descriptors. RMI1 stores count then float32 ROS position, quaternion XYZW and scale triples.
- `heightfield.json`, `terrain/height.u16`: editor Landscape import contract.
- `materials.json`: stable material IDs derived from shader, parameters and all source texture slots.
- `collision-coverage.json`: intentional absence, missing colliders and world collision behavior.
- `spawn-candidates.json`: offline clear locations checked against source height/collider bounds. These are not official spawn points and still require native full-vehicle checks.
- `preparation-verification.json`: hashes, float32 transform bounds and actual Blender FBX reimport checks.

Preparation command:

```powershell
& E:\blender\blender.exe --background --python-exit-code 1 --python tools/maps/prepare_wt_scene.py -- --source artifacts/maps/wt_existing/avg_vlaanderen --output assets/maps/wt_vlaanderen
& E:\blender\blender.exe --background --python-exit-code 1 --python tools/maps/prepare_wt_scene.py -- --source artifacts/maps/wt_existing/avg_vlaanderen --output assets/maps/wt_vlaanderen --verify
```

The journal allows verified unchanged FBXs to be reused; source input must remain the pinned extraction. Blender's `--python-exit-code 1` is required because otherwise a Python exception can still exit with code zero.

## Verification and current limits

The final preparation verification checked all 1,365 FBX hashes, all 35,825 serialized instance transforms against original matrices (maximum corner error 0.5341 mm after float32 serialization), unchanged original height samples, and four representative visual/collision FBX reimports. The static mesh check exposed 109 repeated source triangles that FBX validation would otherwise remove. Their vertex IDs were separated, retaining every draw and its material.

Preparation is not native Unreal scene/drive acceptance. Dynamic wind, destruction, procedural landclass generation, original lighting/postprocessing and exact game shader equivalence are not provided by the geometry converter. Old facing-leaf models encode camera-facing offsets rather than final leaf vertex positions and require a shader/geometry adaptation. The material conversion and UE import/runtime evidence are recorded separately. No original game client was modified.
