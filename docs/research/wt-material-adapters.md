# War Thunder material adapter scope

2026-11-15, Flanders original-map preparation.

`tools/unreal/wt_materials.py` consumes prepared `scene-source.json` and the extraction inventory, authors shared parent materials with source-specific instances, and returns `(id_to_material, terrain_material)`. The ID is `WT_` plus the first20 hex digits of SHA256 of compact sorted JSON containing exactly `shader`, `parameters`, `texture_slots`. No source name participates. Terrain path is `<ue_folder>/Materials/WT_Terrain.WT_Terrain`.

## Actual verification

`test_wt_materials.py` ran in native Windows UE5.8.4 on2026-11-15, exit0. It created four source material instances and the terrain graph. `artifacts/maps/wt_existing/material-api-check/api-result.json` and the adjacent log record the real API outcome. No project-material/shader compiler error was found. This is graph authoring/compilation proof, not a rendered-appearance acceptance or complete original-map integration.

The subsequent source-alpha-cutoff override and instanced-static-mesh usage flag are matched against installed UE headers; the full-scene call must exercise them. Root owns that runtime integration.

## Source textures

The original model list alone omitted78 road/static/landmesh texture names. `export_wt_textures.py --extra-textures .../additional-scene-textures.json` resolves those alongside the earlier list. Current inventory `textures-03/result.json` has917 outputs,913 required references, no missing references and no decode failures. Its per-entry paths point either to new outputs or hash-verified earlier PNGs. Float16 vegetation pivot maps remain lossless NPY data; they are not converted to arbitrary colors. The UE adapter copies only consumed PNGs into the map package and records their UE paths and color-space role.

## Implemented adapters

- Simple: original diffuse slot0 and AG packed normal/roughness/metallic slot2, UV0. Absent source slots receive explicitly generated neutral defaults.
- Layered: original overlay UV1, base normal UV0, tiled detail UV2, mask UV1, source detail tile parameters and height-assisted mix. AG normal reconstruction and R smoothness/B metallic channels are retained.
- Perlin layered: original three color/normal layers and source tiling, using height-weighted layer mixing; spatial Dagor Perlin modulation remains different.
- Single-layer overlay: source diffuse/detail multiplication and tile parameters.
- Mesh decals: original authored geometry, image and alpha with UE surface translucency. This is not Dagor deferred G-buffer composition.
- Foliage: original diffuse/normal and source alpha-test threshold; wind/pivot deformation and legacy facing-leaf vertex expansion remain unimplemented.
- Terrain: original macro color and nearest-sampled source index texture, with grouped original microdetail textures at the source's11.12m tile setting. LandscapeVisibilityMask is connected. Full dual-layer flowmap blending remains incomplete.

## Explicit missing rendering behavior

`land_mesh` and `water_aces` carry no image slots because their renderer obtains scene data elsewhere. They are recorded as `unresolved_external_scene_binding`, and their neutral diagnostic defaults must not be presented as original terrain/water appearance. These IDs are listed in `material-import-report.json`. Also incomplete: paint palettes, wetness, specialized interior/glass/emissive effects, original Perlin noise, terrain flowmap layering, billboard leaf geometry. Material-instance creation success does not erase those differences.

The original shader formulas and channel usage were checked against the pinned official Dagor checkout, especially `layered_inc.dshl`, `perlin_layered_inc.dshl`, `get_mix_weight_inc.dshl`, `rendinst_layered.dshl`, `rendinst_simple.dshl`. Original game content remains read-only and has not been published.

## Saved-scene import entry

`tools/unreal/import_wt_scene.py` accepts `-SceneSource=<scene-source.json>`. It validates original FBX/sample hashes, instance files and virtual resource paths, authors materials, imports visual/collision assets, creates an empty editor world, invokes MapImportLibrary for HISM and Landscape, and saves `/Game/RMSim/Maps/wt_vlaanderen/World`.

`ue-mesh-import-cache.json` permits reuse only when original FBX SHA, importer version, material slots, collision policy and saved `.uasset` hash agree; it still compares actual loaded mesh bounds and slot names. Each execution gets a fresh `artifacts/maps/wt_existing/scene-import-<UTC>/report.json`. Completion is explicitly `imported_pending_runtime_validation`, not driving or original-renderer acceptance.

`test_wt_scene_api.py` is a tiny cube/128²-heightfield fixture to exercise the Python-exposed C++ return tuples and save-map API. It actually ran in native Windows UE5.8.4 and exited0: one instance, a128² heightfield (padded to2×2 UE sections) and saved `/Game/RMSim/Maps/WT_ImportApiCheck/World`. Both C++ functions returned the expected `(value,error)` tuple. Evidence: `artifacts/maps/wt_existing/scene-api-check-20261115T121010Z/result.json` and `scene-api-check.log`. No Python/material/Landscape error was found. The fixture is not the original game scene and does not prove full-map resource use or runtime collision.
