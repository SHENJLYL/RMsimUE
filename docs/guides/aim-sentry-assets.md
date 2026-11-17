# AIM Sentry existing-asset import

This package reuses the user's local `E:/WarThunderModding/war_thunder_sentry/aim_sentry.blend`. It is a **manual kinematic visual rig**, not a calibrated robot dynamics package. The source Blender file, CAD manifests, source scripts and textures are read only. Their before/after SHA-256 values are recorded in `assets/vehicles/aim_sentry/asset_import.json` and checked by the verifier.

## Files

- `assets/vehicles/aim_sentry/asset_import.json`: `schema: manual_asset_rig_v1`; seven parts and eleven material mappings.
- `assets/vehicles/aim_sentry/model/*.fbx`: body, yaw assembly, pitch assembly, and four separate wheels.
- `assets/vehicles/aim_sentry/textures/*.png`: source-preserving base colors; standard DirectX/OpenGL normal maps; separate linear roughness and metallic maps.
- `artifacts/assets/aim_sentry_source_audit.json`: all 98 source objects, parents, matrices, collections, materials and image references.
- `artifacts/assets/scale-fix-20261115/verify/aim_sentry_asset_report.json`: serialized FBX geometry/material-slot/unit checks, texture checks, file hashes and native-import limitations.
- `artifacts/assets/scale-fix-20261115/export/aim_sentry_export_preview.png`: Blender render of the exported geometry reconstructed at its measured pivots. This is not a UE screenshot.

## Units, scale and pivots

Source coordinates are right handed, X forward, Y left, Z up. The War Thunder source explicitly enlarged the CAD by2. The robotics exporter now removes that multiplier: **0.503850 × 0.501985 × 0.419192 m**, ground minimum approximately Z=0. Mesh vertices, pivots, wheel radius, muzzle and preview camera offsets are normalized together. The former retention of game presentation scale was incorrect for this robot.

Each FBX has its geometry offset around its own pivot and zero node translation. `origin.xyz` in the manifest is relative to the parent part's pivot; `pivot_world_m` is the zero-pose pivot in source coordinates. All zero-pose part frames are parallel to the source coordinate axes. Existing War Thunder bone rotations are baked into geometry rather than reused as robotics joint axes.

| Part | Parent | Pivot in normalized robot coordinates, metres |
|---|---|---|
| body | none | (0.000000000, 0.000000000, 0.000000000) |
| yaw | body | (0.000000000, 0.000000000, 0.190629959) |
| pitch | yaw | (0.032498382, -0.104497179, 0.317629993) |
| wheel_l_02 | body | (-0.137267292, 0.137301117, 0.042500004) |
| wheel_r_02 | body | (-0.137267336, -0.137298912, 0.042500004) |
| wheel_r_01 | body | (0.137260780, -0.137298912, 0.042500004) |
| wheel_l_01 | body | (0.137260810, 0.137301102, 0.042500004) |

Yaw rotates about source +Z; pitch and wheel rolling axes are source +Y. User input signs are a controller concern and must account for ROS-to-UE handedness. Wheel radius after removing the source enlargement is 0.0425 m (diameter 85 mm). Only wheel/tire/hub geometry follows wheel rolling; the suspension carriers, covers and referee terminal are fixed to the body for this first manual example. The original War Thunder parents attached those parts to wheel control nodes, which would incorrectly spin entire suspension assemblies in this simplified rig.

The muzzle is recorded under `muzzle`, relative to `pitch`, with forward direction +X. It is a geometric reference from the source, not a calibrated camera optical center or a validated launcher model.

## FBX and Unreal import

Exports use FBX 7.4 binary, `axis_forward='X'`, `axis_up='Z'`, `apply_unit_scale=True`, `apply_scale_options='FBX_SCALE_UNITS'`, and scale 1. FBX `UnitScaleFactor=100` means one geometry unit is 100 centimetres. Blender writes its axis-conversion rotation, approximately Z=-90 degrees, on the mesh node. Node translation is zero and scaling is one.

Use the project's UE importer to perform normal FBX scene/unit conversion and bake that node axis conversion. Its expected settings include `convert_scene=True`, `convert_scene_unit=True`, `force_front_x_axis=True`, `transform_vertex_to_absolute=True`, and `bake_pivot_in_vertex=False`. Place components using manifest origins converted once from ROS metres to UE centimetres. Do not separately mirror the mesh vertices or multiply scale by 100 a second time.

**Before accepting the UE import**, verify that the whole asset has the reported dimensions, the muzzle faces UE +X, source left appears UE -Y, all wheels touch the ground in the rest pose, and yaw/pitch rotate around their recorded joints. Material slots must match each part's `material_slots` list exactly. This document does not claim those engine checks have already passed.

Meshes are triangulated for tangent-space export. The final files contain **1,260,698 triangles** at the restored size (four tiny degenerate faces collapse during triangulation; bounds are unchanged). No decimation is performed; triangulation removes a small number of degenerate source faces. Legacy CAD vertex alpha encodes material classes and is removed; the unused War Thunder `Camouflage` UV layer is also removed. The actual `UVMap`, source atlas, material assignment and geometric shape are retained.

The pure-Python FBX parser bundled with Blender independently reads all seven output files and verifies vertex bounds, triangle indices/counts, units, node translations/scales and ordered material connections. The installed Blender 5.1.2 native FBX mesh importer successfully read a wheel but repeatedly crashed on the larger CAD body, including with custom-normal import disabled. This is recorded as an unresolved importer/runtime limitation, not hidden behind the structural checks; UE import and runtime appearance remain a separate acceptance step.

## PBR conversion

The original materials use Dagor/War Thunder node groups, not portable Principled BSDF graphs. Their `tex0` color and `tex2` packed data are read from the exact image paths stored in the Blender file, currently under the local WarThunderCDK asset directory. Source color RGBA bytes are preserved exactly when converting TGA to PNG; the verifier reads the resulting PNG and compares every pixel.

`tex2` is **not a standard RGB normal map**. For this source its encoding is A=X, G=Y, R=gloss and B=metallic:

- DirectX normal: X = 2A-1; Y = 2G-1; reconstruct positive Z = sqrt(max(0, 1-X²-Y²)), then encode XYZ into RGB. If XY exceeds unit length it is normalized; the count is reported.
- OpenGL normal: same map with the green channel inverted, consistent with the source's Blender preview decoder.
- Roughness = 1-R; metallic = B. These are source appearance conversions, not measured material calibration.
- UE uses `normal_dx`, normal-map compression and sRGB off. `normal_gl` is for Blender/GL consumers. Roughness and metallic are linear, sRGB off. Base color uses sRGB on.
- `base_color_factor`, `roughness_factor` and `metallic_factor` are neutral texture multipliers, all 1. Opacity and emission are separate source-derived fields. Red LED emission remains red with strength 1; do not multiply it by the albedo.

## Physical assumptions and provenance

The original 3000 kg mass, tracked double-differential handling, 84 hp engine, 40 mm weapon, ammunition and tank damage-module attributes were demonstration settings for War Thunder. None are adopted as RoboMaster mass, inertia, drive mechanics, projectile parameters or match rules. `physics.mass_kg` and `physics.inertia` are null, with `calibrated:false`. Temporary manual motion limits or collision proxies must be documented by the runtime integration as assumptions.

Consequently this asset does not pretend to satisfy the physical `vehicle.json` v1 contract, which requires positive per-link masses and valid inertias. A later physical package can reuse these meshes after identifying the actual robot scale, masses, inertia, drive topology and control limits.

The model/CAD is a user-provided local derivative. No blanket redistribution license for the CAD or complete vehicle is established here. The existing project documents ambientCG Metal028 as a CC0 material source; that does not extend a license to unrelated CAD or robot assets. Source metadata and hashes are retained rather than guessing ownership or granting permissions.

## Reproduce

```powershell
& 'E:\blender\blender.exe' --background --factory-startup --disable-autoexec --python 'D:\simulator\tools\assets\export_aim_sentry.py'
& 'E:\blender\blender.exe' --background --factory-startup --disable-autoexec --python 'D:\simulator\tools\assets\export_aim_sentry.py' -- --verify-only
```

The exporter loads the source with embedded scripts disabled, modifies only in-memory copies, and never saves over the source. Generated files are confined to `assets/vehicles/aim_sentry` and `artifacts/assets`. Verification uses a separate process to avoid the installed Blender's native reset/import failures.


## 2026-11-15 real-scale correction

The source STEP path recorded by the original project is currently unavailable. Retained original millimetre PLY components were directly measured and the WT rig records pre-enlargement bounds. Original angled CAD visible pose is about510×502×432mm; the neutral barrel pose is504×502×419mm. Some War Thunder muzzle/housing edits remain in the visual mesh; scale restoration is not a claim that every modified part equals originalCAD. Mass/inertia remain uncalibrated. Original files were not edited.

Evidence: artifacts/assets/scale-fix-20261115 includes before-export backups,25 half-scale checks,actualUE7partbounds,sourcehash verification and native control/terrain/combat results. Current terrain collision uses seven stable part boxes; wheels do not inflate their proxy with visual spin. Central12degree ramp traversal passed1.6m. More severe slope transitions and low-clearance passages remain constrained by actual geometry and the uncalibrated kinematic model.
