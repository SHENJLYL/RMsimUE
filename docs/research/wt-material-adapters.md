# War Thunder material adapter scope

Updated 2026-11-18 for the unified `D:\simulator` project and VERSION 9 height-decal strength / local LC1 normal refinement. Original preparation and earlier saved-scene records remain historical checkpoints.

`tools/unreal/wt_materials.py` consumes prepared `scene-source.json` and the extraction inventory, authors shared parent materials with source-specific instances, and returns `(id_to_material, terrain_material)`. The stable ID is `WT_` plus the first 20 hex digits of SHA256 of compact sorted JSON containing exactly `shader`, `parameters`, and `texture_slots`. Terrain uses `<ue_folder>/Materials/WT_Terrain.WT_Terrain`.

## Actual verification

| Checkpoint | Verified result | Evidence |
|---|---|---|
| Original material API fixture, 2026-11-15 | Four source instances and terrain graph, Windows UE5.8.4 exit 0 | `artifacts/maps/wt_existing/material-api-check/api-result.json` |
| Source leaf geometry | Half4 recovery and independent source checks; three FBX roundtrips, five UV channels, 91 cards / 182 leaf triangles / 2369 source instances | `artifacts/maps/wt_existing/leaf-adaptation-20261117-01/verification.json` |
| Native mesh preservation | Three trees retain 104 / 84 / 64 triangles and five full-precision UV channels; duplicate-slot buildings retain 376 / 588 triangles | `artifacts/maps/wt-scene-20261117/native-mesh-02/verification.json` |
| Source height opacity / single FBX | 99 original inputs, zero position error; 263592 UV1 corner values match exactly; original geometry and UV0 remain unchanged | `artifacts/maps/wt-scene-20261118/decal-opacity-01/adapt/verification.json` |
| Ordinary foliage coverage | 43 material specifications inspected, 25 selected from 7 hash-verified split color/alpha texture pairs | `artifacts/maps/wt-scene-20261118/tree-split-alpha-01/verification.json` |
| Baseline full import 06 | All 1365 meshes imported or natively revalidated from cache; World saved, exit 0, 175.87 s | `artifacts/maps/wt-scene-20261118/import-06/execution.json` |
| Baseline saved scene 03 | 21 checks, including opacity proofs, two-UV native precision, RVT configuration, source geometry and collision; exit 0, 12.47 s | `artifacts/maps/wt-scene-20261118/native-scene-03/summary.json` |
| Baseline material resources 06 | 88 resources compile under D3D11 / NoShaderWorker, zero compiler errors and unchanged saved asset hashes; exit 0, 270.73 s | `artifacts/maps/wt-scene-20261118/native-materials-06/summary.json` and `verification.json` |
| Baseline contact/reset 03 before global Land, water and grass | Five native Chaos checks and eight decoded captures, exit 0, 67.55 s; same World/source/module checkpoint | `artifacts/maps/wt-scene-20261118/flanders-contact-03-rvt/summary.json` |
| Global landclass source binding | Five original color classes, 1024 cell records, packed source atlas and eight PNG contracts; six unit tests and source preflight passed | `artifacts/maps/wt-scene-20261118/distant-land-02/extracted/verification.json`, `publish/verification.json`, `preflight/verification.json` |
| Import07 global Land + map UI | UE exit 0, source manifest `91f9efdcea06…`, saved World `6e4b763968f7…`; NullRHI asset import followed by the separate saved-resource gate below | `artifacts/maps/wt-scene-20261118/import-07-land-ui/execution.json` |
| Pond source RGB contract | Three original pond objects / 23 triangles and exact level RGB; six contract tests and actual source hash checks passed | `artifacts/maps/wt-scene-20261118/water-source-01/verification.json` and `preflight/verification.json` |
| Source grass preparation | Seven FBX roundtrips / 56 source triangles, 14 original mask/alpha images, four packed weight textures, 13 nonempty layers; pure contract validation passed | `artifacts/maps/wt-scene-20261118/foliage-01/prepared-01/verification.json` |
| Native pond update 02 | Two water assets, six checks, exact source RGBA and completed shader statistics; World/source unchanged, exit 0, 14.1722834 s | `artifacts/maps/wt-scene-20261118/water-native-02/summary.json` |
| Native grass bake 07 resume | 13 layers; 1,089 valid / 986 weighted components; 52,984 render instances across two preview views; 54 foliage/Terrain files unchanged, World saved, exit 0 | `artifacts/maps/wt-scene-20261118/grass-native-07-resume/summary.json` |
| Saved grass reload 02 final | 27 checks; seven meshes and four source-bound points; 58 files unchanged, exit 0 | `artifacts/maps/wt-scene-20261118/grass-reload-02-final/summary.json` |
| Baseline saved material resources 08 final | 104 unique assets, completed D3D11 shader statistics, zero compiler errors, saved hashes unchanged; exit 0, 54.526665 s | `artifacts/maps/wt-scene-20261118/native-materials-08-final/summary.json` |
| Source material refinement preflight | 197 decal contracts, 65-biome/23-detail source table, 26 original PNGs; nine source/numeric checks passed | `artifacts/maps/wt-refine-20261118/material-source-preflight-01/verification.json` |
| Unified incremental material update 02 | Five parents + 198 instances, actual 1024-square x 23-slice array / 11 mips, zero shader/engine errors, old grass closure and World/source unchanged; exit 0, 197.7521351 s | `artifacts/maps/wt-refine-20261118/materials-native-02/summary.json` |
| VERSION 7 saved-material reload 01 | 307 unique assets including all 197 rendinst decals and 16 grass layers; completed shaders, zero compiler errors and unchanged saved hashes; exit 0, 102.7623425 s | `artifacts/maps/wt-refine-20261118/materials-reload-01/summary.json` |
| Height surface / microdetail source preflight | 34 original d/null/r/null contracts, 64 unchanged PNGs, 12 original microdetail slots; 78 checks including opacity/ID transport, adjacent-layer continuity and gradient orientation | `artifacts/maps/wt-refine-20261118/ground-refine-preflight-01/summary.json` |
| VERSION 8 ground update 01 | Four parents + 36 instances; 512-square x 12-slice original array / 10 mips, exact normal/payload pins, all 16 grass names/types and 26 upstream nodes preserved; exit 0, 94.0252208 s | `artifacts/maps/wt-refine-20261118/ground-native-01/summary.json` |
| VERSION 8 / grass V3 saved-material reload 03 | 307 saved targets, 197 rendinst decals, 34 HeightDecal and all 16 grass layers; exact output connections, both source arrays, no compiler errors, all target files unchanged; exit 0, 90.1431163 s | `artifacts/maps/wt-refine-20261118/materials-reload-03/summary.json` |
| VERSION 9 local-normal source validation | 17 original-pixel/continuity checks plus 81 source preflight checks; 3,003 color samples identical and 36 categorical seams continuous | `artifacts/maps/wt-refine-20261118/lc1-normal-local-candidate-01/summary.json` |
| VERSION 9 ground update | Four parents + 36 instances; 34 native strength overrides and exact local-normal receiver pins; Land/Terrain remain at 16/8 samplers; World/source unchanged; exit 0, 121.4692641 s | `artifacts/maps/wt-refine-20261118/ground-normal-native-01/summary.json` |
| Final VERSION 9 / grass V3 reload 04 | 307 unique saved assets, 12 native parent compiler checks with zero errors, all file hashes unchanged; exit 0, 89.8077153 s | `artifacts/maps/wt-refine-20261118/materials-reload-04/summary.json` |
| Final grass generation freshness | True rebake followed by 72 read-only checks; all 1,089 components valid/fresh with zero mismatches | `artifacts/maps/wt-refine-20261118/grass-final-reload-01/summary.json` |
| Final slope / default static views | Regular LC1 grid lines absent at the slope; the prior strong black/diagonal gravel patch absent at default spawn; original colors and surface details retained | `artifacts/maps/wt-refine-20261118/gui-final-slope-view-01/entry.png` and `gui-final-default-view-01/entry.png` |

The completed material06 baseline covered 88 resources before the global Land graph, pond base-color adapter and grass outputs. The later baseline `native-materials-08-final` gate passed 104 unique saved assets: eight map parents including Terrain and Water, two Leaves, 34 HeightDecal, one Land, one original river Decal, 43 ordinary tree instances, one Water instance, one grass parent and 13 grass instances. Actual vertex/pixel instruction ranges are 136–355 / 260–559, with 2–14 samplers and zero compiler errors. The grass gate verifies all 15 unique source Masks/Alpha/Land texture bindings, exact native dimensions, two-sided masked foliage and source color parameters. `verify_wt_materials.py` completes native texture compilation before reading platform dimensions and completes target shader compilation before collecting statistics; all saved asset hashes remain unchanged.

The successful grass bake binds module `c44d2c079aeaa597be3a5a5076787f89dce1f261ca77c29da26fba08cb263e18`. Water02 and grass reload02 bind `1bf4126eb733b12880e170a5eb4830f50b5f792cdda55a6ca2fd1507ed26558a`; material08 binds `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404`. The later Water/reload and material08 binaries also contain concurrent destruction-session changes; each gate retains its own execution hash and documented scope. The baseline material08 summary SHA256 is `b32bbdb322f105c90b80514156d36f85adce30566adb588d5f55b742c865b709`. World `20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25` and source manifest `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27` stayed unchanged during water02 and material08.

The earlier `flanders-contact-02-render-ready` screenshots supplied the tree-card and ground-decal diagnosis. In the baseline contact03 rest image, review confirmed the floating surface obstruction removed and gaps restored in the left foliage cutouts. GUI review of captures 01-07 on PID 43760 / module `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404` shows green/brown distant land, nearby grass and pink/white flower instances after actual W/A driving, minimap position/heading updates including Q input, and two M-key map expand/collapse cycles. The observed scope is bound by `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`. Material validation uses `-d3d11 -AllowCommandletRendering`; each execution records its exact shader-worker arguments. Current rerun commands are in the [Flanders guide](../guides/war-thunder-flanders.md).

## Source textures

The original model list alone omitted 78 road/static/landmesh texture names. `export_wt_textures.py --extra-textures .../additional-scene-textures.json` resolves those alongside the earlier list. The `textures-03/result.json` inventory has 917 outputs, 913 required references, no missing references, and no decode failures. Its per-entry paths point either to new outputs or hash-verified earlier PNGs. Float16 vegetation pivot maps remain lossless NPY data. The UE adapter copies consumed PNGs into the map package and records their UE paths and color-space role.

## Implemented adapters

- Simple: original diffuse slot 0 and AG packed normal/roughness/metallic slot 2, UV0. Absent source slots receive generated neutral defaults.
- Layered: original overlay UV1, base normal UV0, tiled detail UV2, mask UV1, source detail tile parameters and height-assisted mix. AG normal reconstruction and R smoothness/B metallic channels are retained.
- Perlin layered: original three color/normal layers and source tiling, using height-weighted layer mixing.
- Single-layer overlay: source diffuse/detail multiplication and tile parameters.
- Source ground decals: 34 HeightDecal materials consume recovered position.w, diffuse-alpha thresholds and alpha-height normals. Their original `_r` image supplies smoothness and microdetail IDs through an explicit source-layout adapter. The original world-decal mesh writes an RVT sampled by terrain and outer landmesh.
- Rendinst scene decals: 157 linear multiply, 39 diffuse and one normal-only source instance use dedicated parents, original depth fades and the 0.5 cm camera offset; normal-only leaves BaseColor unconnected.
- Foliage: original diffuse/normal and alpha-test threshold; 25 content-verified ordinary materials sample separate T1.r coverage. Legacy facing leaves retain T0 alpha plus recovered geometry and no-wind camera-facing displacement.
- Terrain: the original 65-biome dual-layer table selects 23 source albedo/height images at 11.12/11.23 m, with categorical mip0 index, source noise/flow rotation, an explicit UE per-neighbor alpha-gradient normal and LandscapeVisibilityMask. Source ground-decal RVT composition is followed by the original 12-slot microdetail adapter.
- Outer landmesh: five original macro-color landclasses retain lmap packed weights; core Flanders LC1 receives the indexed detail graph, followed by ground-decal RVT composition and the same source-ID microdetail layer.
- Local ponds: exact original level RGB in a dedicated base-color-only Water adapter; source contract, native water02 update and independent material08 compilation passed.

Shared parent graphs declare instanced-static-mesh usage. Import07 used VERSION 5 for the global Land mapping; VERSION 6 supplies the narrow pond base-color adapter; VERSION 7 supplies the dedicated rendinst decals and indexed ground details; VERSION 8 corrects height-decal reflection-channel use and adds the source-bound post-RVT microdetails. These versions retain the VERSION 3 graph-rebuild fix: snapshot the old expression list, delete each expression, and assert an empty graph before authoring new nodes. This addresses the UE5.8 live-array deletion behavior observed in the native rebuild. Parent saving requires a successful completed graph compilation; the separate native material gate checks the saved resource.

## Rendinst mesh decal blending

The stable source material set contains 157 multiply decals, 39 diffuse decals and one normal-only decal. The original wall-dirt mask has an opaque alpha channel and a neutral RGB border of 128/255. Its source color is a linear multiplication factor: Dagor's destination-color/source-color blend gives `2 * source * destination`. A source value of 0.5 preserves the destination. Sampling that border as sRGB opaque BaseColor instead produces approximately 0.216, which explains the rectangular gray background in the earlier wall view. The normal-only source `WT_286f7555de920d058456` has no T0 and stores its packed normal in T2; it must leave BaseColor unconnected. The source fixtures and numeric checks are recorded in `artifacts/maps/wt-refine-20261118/material-source-preflight-01/verification.json`.

`wt_rendinst_decals.py` follows `decals/modulate2x_decal_inc.dshl`, `decals/deferred_decal_inc.dshl`, `decals/blend_diffuse_decal_inc.dshl`, `decals/decal_inc.dshl` and `rendinst_decal.dshl` at Dagor commit `9639942de5a8e5b01d91977f60ed82cda22d5f41`.

| Source family | Source instances | UE parent and actual output |
|---|---:|---|
| `rendinst_deferred_modulate2x_decal`, `rendinst_modulate2x_diffuse_decal` | 157 | `WT_Parent_DecalModulate`: Surface / Unlit / Modulate; linear T0, source intensity/threshold/desaturation, neutral factor through Emissive |
| `rendinst_deferred_decal`, `rendinst_blend_diffuse_decal` | 39 | `WT_Parent_DecalDiffuse`: native mesh DeferredDecal; source T0 color/coverage, T2 AG normal and R smoothness/B metallic |
| `rendinst_deferred_normal_decal` | 1 | `WT_Parent_DecalNormal`: native mesh DeferredDecal; no BaseColor, source AG normal and `saturate(abs(T2.g-0.5)*20)` coverage |

UE5.8's DBuffer path converts Modulate decals to Translucent; the multiply family therefore uses the native Surface/Unlit/Modulate pass. It preserves the source neutral and overlapping color products while multiplying UE's lit scene color. Source smoothness/metalness multiplication remains recorded in provenance. Diffuse decals use the source albedo gamma for UE's shared opacity; the independent source normal gamma, per-section `draw_order` and palette painting retain separate adaptation scope.

The source `DECAL_TO_CAMERA_OFFSET=0.005` m becomes a 0.5 cm camera-directed WPO. Deferred multiply decals fade between 30 and 40 cm of scene/pixel depth difference; the deferred diffuse/normal families use 8 to 10 cm. Original nondeferred variants keep their disabled depth branch. The original FBX vertices and instance scale stay unchanged. Missing required source T0 or normal-only T2 is rejected. The multiply parent's neutral fallback is linear mid-gray.

The actual `materials-native-02` run compiled all 197 source instances and the three parent graphs. The standalone saved-resource gates `materials-reload-01` and `materials-reload-03` verified all 197 instances again. Rendered HISM/decal appearance and moving-camera depth stability use the separate contact and GUI captures.

## Indexed source ground details

The independent package `terrain-details/definition.json` binds the original Flanders GRP resource, its decoded DataBlock bytes and 26 source PNGs. The resource hash is `8cf1aa8f23f927e07c5034bc91d485c35102bc9d46655d458126da30ecbb6b80`; the preserved decoded source JSON hash is `66cb40e875ef86b612bd89bdc38a561a419061ce8e4597538ae3ee6f6640ade7`. The definition hash is `5596ab7b7b4a6c4dd60b1f5884d21719981bb227a824d8d5629d99fe964dfb86`. The scene manifest and existing `landclass/landclass.json` remain unchanged, preserving the vegetation proof's original atlas binding.

The source supplies 65 biome records selecting two layers from 23 unique 1024-square albedo/height textures. `wt_landdetail.py` retains the source 11.12/11.23 m layer scales, four-neighbor height blending, original layer weight multipliers, 13.1-degree secondary rotation, value-noise variation and source flow rotation. Macro/index UV uses `(UEWorldX,-UEWorldY)/204800`; detail tiling uses positive `World.xy` in metres. The categorical index is nearest-filtered and sampled explicitly from mip 0. Averaging IDs into mipmaps could select an unrelated biome, such as IDs 0 and 34 becoming 17.

`WT_LandDetailAlbedo` stores independent original image slices. The native Texture2DArray factory creates an empty asset, then setting editable `SourceTextures` invokes UE5.8's real `PostEditChangeProperty -> UpdateSourceFromSourceTextures` source rebuild. Both Land and Terrain readbacks reported **1024 x 1024 x 23, 11 mips**, original Source dimensions **1024 x 1024 x 23**, and the exact 23-entry source order. The saved array hash is `8c0e65e458687ff23f58dee90865b530833ba72c54e2fe2d9a247c284b36281f`. Original color slices use sRGB/default compression and wrapped addressing; the categorical index uses linear/no-mip/nearest data sampling.

Terrain and the core Flanders LC1 region of the outer landmesh consume the same source detail graph before the existing ground-decal RVT composition. Other landclasses keep their original macro colors and packed source weights. The source resource has no normal-texture fields. The implemented normal is an explicit UE local alpha-gradient adaptation using original `height_scale` and `normal_scale` as multipliers. Those fields do not establish physical metres of height. VERSION 9 fixes each neighbor's slice, rotation and mip while taking two continuous UV differences, generates its unit normal, then blends those normals with the unchanged final fourth-power source weights at both levels. Geometry remains unchanged. The original packed reflectance, self-color/wetness flags, other landclass details and vertical-cliff branches retain separate adaptation scope.

The source texel scale is approximately 1.086/1.097 cm at the two tile scales. Actual rendered resolution depends on mip selection, distance, viewing angle and screen coverage. VERSION 7 native compilation measured 15 samplers / 979 pixel instructions for Land and 7 samplers / 855 pixel instructions for Terrain. VERSION 8 adds one source microdetail-array sampler after RVT composition, producing 16 / 8 samplers for Land / Terrain. Near/far and slope transitions remain part of the separate image review.

### LC1 categorical-boundary normal correction

The original Flanders resource contains albedo and reflectance fields without a normal-texture binding. The pinned public `make_clipmap.dshl` samples independent normals, rotates their XY and blends them using sharpened weights; its scalar height reaches the displacement branch. The previous UE fallback differentiated the mixed alpha-height scalar. That operation introduced a height-times-weight-derivative term at the 0.5 m categorical grid, including changes between per-cell rotations. It produced a regular normal pattern in the green-slope view.

VERSION 9 calculates the original continuous color UVs and their `ddx`/`ddy` mip basis before selecting categorical IDs. Each selected slice is then sampled at its center UV and two continuous offsets using the same slice, rotation and mip. Only the resulting normals are blended. Original colors, IDs, texture bytes, source rotations, weighting and color mip arithmetic stay unchanged. This adds 16 array reads across the eight source samples and reuses the existing array sampler. `WT_LandDetailNormalAdapter` records the adaptation; the native gate verifies its metadata, exact generated code and the saved `NormalOut` reroute into the Land/Terrain receiver.

The original-pixel validation covers 3,003 samples around the green slope, station grass and a neighboring area. All output color samples are bitwise identical to the prior CPU color path. Across 36 real categorical boundaries, the maximum two-sided unit-normal difference is **3.00033e-5** at offsets of **1e-7 m**. Green-slope border tilt median / P95 changes from **42.85 / 77.91 degrees** to **15.16 / 40.61 degrees** in the top-mip, 2 mm finite-difference analysis. The saved report records the original image hashes and the sampling model. Evidence: `lc1-normal-local-validation-01/verification.json`, SHA256 `047208145edb2df87618709f3ff8d6edfe2bf6d119454537d9d8bf97ec31a044`; compact source summary SHA256 `eb7cf3621dc00bf5167bbbef231ef5ededcb1a9d76774b8227bfe375616032df`.

The actual VERSION 9 ground author completed under UE PID **42476**, exit **0**, in **121.4692641 s**. All **40** targets and nine checks passed. Land / Terrain have **1196 / 1073** pixel instructions, compared with VERSION 8's **1056 / 932**, and retain **16 / 8** samplers. Both original arrays keep their dimensions and source order. The 64 original height-texture assets retain their bytes and encoding; all 16 grass entries and 26 upstream nodes remain connected. World `0a57103fcb58de77467bc9f75fcbd0019b6a2d62e687d0a6080e43e763597419`, source `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27` and module `37daaf0efd01868a11c094b7b91481b222141400a679595dfd199575a3c14b0e` are unchanged across this run. Compact native summary SHA256 is `f18e88f4a5f53aa2d764b70d3735da3d49b52abf8a96090366ca78048dcd246d`; full audit SHA256 is `d4cc99046c9124cc64146c9d482d4ca718d15bf59750ac7241ca8468811c231e`. The subsequent final saved-material and grass-generation checks passed as recorded below. Static slope/default views have also been reviewed; movement images are reviewed separately.


`prepare_wt_landdetail.py --scene-source <scene-source.json> --source-landclass <original decoded Flanders landclass JSON> --texture-inventory <textures-03/result.json> --evidence-root <fresh directory>` verifies the original GRP payload and texture bytes, copies source bytes into the independent package and writes its proof. `verify_wt_material_refinements.py --scene-source <scene-source.json> --output <fresh directory>` checks the source contracts, multiply/depth fixtures, gradient direction/units and color-space pairing without UE.

## Incremental material authoring in the unified project

`update_wt_materials.py` loads the saved World and updates stable material identities without reimporting scene FBXs. VERSION 8 `-GroundOnly` selects HeightDecal, the original ground Decal, Land and Terrain: four parents plus 34 height, one original river/ground and one Land instance, totaling 40 assets. Full mode also selects all 197 rendinst decals, totaling seven parents and 233 instances. Existing material/texture files and the provenance report are backed up before writing; native graph/source checks and completed shader statistics gate each selected target.

`terrain_material(..., preserve_grass=True)` retains the GrassOutput and its recursive upstream closure. Ground update01 preserved **16 names/types and 26 nodes**, comparing node paths, classes, connections, Custom source and texture bindings before and after. The earlier material update02 preserved its then-current 13 names/types and 23 nodes. Grass visual authoring/baking follows the material update as a separate stage.

Run serially from the unified project using:

```powershell
& 'D:\simulator\tools\unreal\Run-WTNativeScript.ps1' `
  -EngineRoot 'E:\simulator-tools\UE_5.8' `
  -PythonScript 'D:\simulator\tools\unreal\update_wt_materials.py' `
  -ReportArgument MaterialUpdateOutput `
  -EvidenceDirectory 'D:\simulator\artifacts\maps\wt-refine-20261118\<fresh-material-run>' `
  -ExtraArguments @('-GroundOnly') `
  -Rendering
```

The successful `materials-native-02` run used D3D11, four shader workers and a 4096 MiB ordinary texture streaming budget for the current 16 GB VRAM machine. UE PID 20492 exited 0 in **197.7521351 s**. Module SHA256 was `028c4791c5a637d66544c229e3336a11c63b4a92e5d36ce452f3d17b7000b3d7`, unchanged through the run. All 203 targets completed; the log contained zero material-compile failures and zero engine error lines. World remained `20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25` and scene manifest remained `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27`.

Compact evidence: [materials-native-02/summary.json](../../artifacts/maps/wt-refine-20261118/materials-native-02/summary.json), SHA256 `f9f611d6b90e0ae8618ab03b3d1c42bafa4e5414effbb4ac83b08c8c32e02a72`. It binds full audit SHA256 `537015f820a94785a848fde42f1d7088922dc77f928f637d0963643e91679797`, execution/log hashes, actual array order, all parent statistics, the preserved grass closure and script hashes. The separate full saved-material gate now covers every source decal and the enhanced grass definition; its expected total is 307 assets. That VERSION 7 checkpoint was subsequently covered by the 307-asset `materials-reload-01` gate. VERSION 8 ground update01 passed separately. After grass V3 authoring, `materials-reload-03` passed all **307 targets** under UE PID 32780 in **90.1431163 s**. Its 12 parent resources returned empty compiler-error lists; all 307 saved files retained their before/after hashes. Land / Terrain / HeightDecal / Grass used **16 / 8 / 2 / 6** samplers. The complete readback includes the source **1024 x 1024 x 23** land-detail array and **512 x 512 x 12** microdetail array, original slice order and duplicate microdetail slots 4/11. The grass V3 graph has the source `NormalOut` connection through its world-space transform and a separate single-output normal Custom; its 16 instances retain 96 source color vectors and `UseSourceNormal=0` for the original 13 layers / `1` for the three sourced-normal short-grass layers.

This saved-resource run binds World `0a57103fcb58de77467bc9f75fcbd0019b6a2d62e687d0a6080e43e763597419`, module `7a4edd9792accc9f50151e2bfe38e70785c679db2ce684046ecdf78e1083f987`, and unchanged source `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27`. Compact summary SHA256 is `acc7d247a99747e27d20a277e0e01973ae963d3761dd1c3350416951199fb946`; full verification SHA256 is `51df04c1c0a8d5595c8695c45d953c8f392c29f5f9e8fb45e2b8f901602972ee`. The preserved `materials-reload-02` failure, exit -1 / 92.0948564 s, exposed an output-name assumption in the verifier: UE Custom nodes with no additional outputs expose an empty primary name. The corrected verifier still checks the exact normal node, code, float3 type and upstream connections; the saved assets required no rebuild for that fix.

Same-view contact images show the sidewalk's gray stone surface restored and higher-frequency ground detail. The lower-left gravel patch also shows stronger diagonal fragments. Read-only projection of 8,253 pixels to original `gravel_border` faces found source-alpha height-normal tilt median / P95 **54.54 / 78.66 degrees**, while microdetail AG measured **3.32 / 15.82 degrees**. Evidence: `ground-normal-diagnosis-01/verification.json`, SHA256 `44ec9ab52b97e28584f552836004affb959a8a04d8d83bc8429c405d8df7c789`. These are original-pixel and nominal-RVT-footprint calculations, separate from native GBuffer measurements. VERSION 9 adds the explicit UE `HeightNormalStrength=0.2` to this no-normal source-height fallback, retains the source `0.08` UV-height scale and leaves original color, IDs, microdetails and geometry intact. The ground-native update passed all 34 instance overrides, reading the native float value `0.20000000298023224` and its matching metadata. The final saved-material reload and the default-spawn static image passed as detailed below.

### Final saved resources and visual observations

The final `materials-reload-04` run loaded the saved scene under module `37daaf0efd01868a11c094b7b91481b222141400a679595dfd199575a3c14b0e`. UE PID **42648** exited **0** in **89.8077153 s**. All **307** unique targets passed and retained their file hashes: 11 map parents, two Leaves, 34 HeightDecal, one Land, one original river Decal, 43 ordinary trees, one Water instance, one grass parent, 16 grass instances and 197 rendinst decals. All 12 parent compiler-error arrays are empty. Saved LC1 metadata and `NormalOut` connections, all 34 height-strength overrides, both source arrays, 96 grass color vectors and the three packed source normals passed. Compact summary SHA256: `985e9a77ce19262e875e5c56ca74129c2184420f32ca2670e2f25e4e4c115fe1`. Full native audit SHA256: `a7e63cc44b231842f5443d74ccca1b61940820f0cae223183fe5029fee288097`.

Terrain's material state changed during the ground update, so the grass maps were rebuilt. The resumed author completed in **82.963949 s**, retained all 72 foliage/Terrain authoring files and saved World `3311399568469e48d8580f1b41897ad5899321b51617638dd2c541e875ef1066`. A separate UE process then passed **72 checks** in **12.8125819 s**: every one of the **1,089** saved/current `ComputeGrassMapGenerationHash` pairs matched, with 1,015 weighted components and zero stale maps. This final World stayed unchanged through the material reload. Grass summaries: `grass-final-rebake-01/summary.json`, SHA256 `9eaf5fb33524577d94b9d85f62074cbe50678fe1e61104a5850e61b81109345b`; `grass-final-reload-01/summary.json`, SHA256 `6904ba0903746a37c186f60a1ea446be184558617e32322976c65b402c29efb4`.

The original game-window captures were opened for visual review. At `green_slope` (ROS 1146.3, -942.3), the earlier regular 0.5 m grid lines are absent around the vehicle. The ground is continuous, foreground soil/fine-stone detail remains visible, and grass, flowers, ferns and the railway boundary remain present. The slope image is `gui-final-slope-view-01/entry.png`, SHA256 `c4d6ba31a456557ce1b7945cd3edc417bb69bea5d421c81f38819460380af103`. At the canonical default spawn (ROS 1028.0, -1020.0), the former strong black/diagonal fragment patch to the vehicle's front-left is absent; gravel, gray platform stones and shoulder detail remain continuous. Its image is `gui-final-default-view-01/entry.png`, SHA256 `02c85d9b372412e4aead9b526ec7e34159da8826f69b0c115aa43385a6c13fea`. The original pale gravel color is retained. These two views establish the observed corrections at their recorded positions; temporal movement is tracked by the separate final input/frame sequence.

## Ordinary foliage split-alpha contract

The current manifest contains nine `rendinst_tree` and 34 `rendinst_tree_colored` material identities. Actual PNG inspection selected 25 of those 43 materials: source `atest > 0`, T0 alpha uniformly 255, and a nonconstant grayscale T1 coverage image. All original/package texture hashes match. The seven unique texture pairs cover populus, populus flowers, poplar, tilia, dry burnt branches, ivy and grape. The remaining 18 material specifications keep their previous sampling path.

`wt_tree_alpha.py` binds the adapter to the recorded stable IDs and exact texture hashes. `AlphaFromT1` selects T1.r, imported as linear data, while preserving the original threshold. Source `tree_populus_branches_tex_a` has 86.9373% of pixels below 127, explaining the solid cards when diffuse alpha alone was used. The exact record is `tree-split-alpha-01/verification.json`, SHA256 `67f35df9251d377a92641d61481b697b0167c21b9d19b0b05bb268aa6b41f18f`.

Official `prog/daNetGame/shaders/rendinst_vegetation.dshl` at the pinned Dagor commit distinguishes plain `rendinst_tree_colored` using T0.a from `rendinst_tree_colored_alpha_split` using T1.x. The prepared source retains its original plain shader names; the adapter is selected by actual split-texture content and hashes. The two legacy facing-leaf diffuse textures already carry source alpha and remain on their T0.a path.

## Legacy facing-leaf source contract

The three affected models use `rendinst_facing_leaves`. Each source card stores four positions at its center plus a half4 payload. `extract_wt_leaf_payload.py` recovers the original payload from the typed declaration and raw draw bytes; `verify_wt_leaf_payload.py` independently reads the stride-24 fields at offsets 0 / 8 / 12 / 20. Original NPZ position error is exactly zero.

| Model | Native/reference triangles | Leaf triangles | Original instances |
|---|---:|---:|---:|
| `kh_gol_euraspen_fall_03_rendinst` | 104 | 62 | 593 |
| `kh_gol_liveoak_03_rendinst` | 84 | 80 | 1171 |
| `kh_gol_tree_simple_a` | 64 | 40 | 605 |
| Total | 252 | 182 (91 cards) | 2369 |

All 2369 affected instances have positive uniform scale; their source RMI records remain unchanged. The adapter creates a source-defined reference card, exports five UV channels, and retains the raw displacement for the material:

| Quantity | Contract |
|---|---|
| Source half4 | `(dx, dy, dz, windNo)`, original metres and wind-group index |
| Reference Dagor position | `center + (dx, dy, -dz)` |
| Reference ROS offset | `(dx, dz, dy)` metres |
| Reference UE local offset | `100 * (dx, -dz, dy)` centimetres |
| UE UV3 / UV4 | `(dx, dy)` / `(dz, windNo)`; Blender writes `(dx, 1-dy)` / `(dz, 1-windNo)` to compensate for the importer V flip |
| UE camera-space desired offset | `100 * (-dx, dy, -dz)` |
| World-position offset | `length(InstanceToWorld(1,0,0)) * CameraToWorld(desired) - InstanceToWorld(reference)` |

UV0 continues to sample the source image. Native `PrepareSourceLeafMesh` requires one LOD and five UV channels, then builds full-precision UV storage. `bounds_m` describes the actual reference FBX geometry; `billboard_bounds_m` unions the source center ± displacement-length spheres with the static geometry. Import validates bare mesh bounds first, then applies the measured culling extensions.

The no-wind basis and camera handedness follow official DagorEngine commit `9639942de5a8e5b01d91977f60ed82cda22d5f41`: `prog/engine/libFx/leavesWind.cpp`, `prog/engine/coreUtil/flyMode.cpp`, and the raw tc2 layout in `prog/gameLibs/bvh/shaders/bvh_process_leaves_vertices.dshl`. The independent transform checks are recorded in `artifacts/maps/wt-scene-20261117/leaf-facing-math-01/verification.json`. Wind animation and the BVH-specific sun-direction shadow displacement remain separate work.

## Original height-decal opacity payload

`land_mesh_height_decal` also consumes the original normalized position.w. The initial landmesh converter retained xyz and UV0; this scalar is now recovered by `extract_wt_decal_opacity.py` from the original short4n declaration and raw draw bytes. All 99 source decal NPZs match the recovered positions, indices and material IDs exactly: 85418 source vertices and 87864 triangles, with zero position error. The raw scalar has 6353 distinct int16 values in 0..32767.

`prepare_wt_decal_opacity.py` adds UV1 directly through Blender's binary FBX parser/writer. The original two repeated-face copies retain 85424 FBX control points. Every existing FBX element, UV0, material assignment and transform remains unchanged. UV1.x stores `max(int16_w / 32767.0, -1)` in float32 source precision; the stored FBX V is 1 so the UE UV1.y is 0. All 263592 polygon corners read back with zero scalar error.

Only `world_decal_000` references the 34 height-decal material identities. Its mesh record now carries `source_vertex_opacity` with the UV1.x/full-precision contract and a hash-bound `geometry-audits/world_decal_000-opacity.json`. The original 34 coincident-face geometry proof was independently rerun against the new FBX and rebound; source and native triangle targets stay 87864 / 87830. Before copies of the FBX, manifest and old proof remain in `artifacts/maps/wt-scene-20261118/decal-opacity-01/adapt/before/`. The compact adaptation result is the adjacent `verification.json`. The native scene03 audit confirms two full-precision UV channels and the rebound proof; material06 validates all 34 HeightDecal instances. The implemented formula follows `prog/daNetGame/shaders/make_clipmap.dshl` at the pinned commit:

```text
sourceAlpha = (1 - 2 * height_above) * diffuse.a + height_above
opacity = saturate((pow(vertexOpacity, vertex_gamma) - sourceAlpha)
                   / (0.0001 + diffuse_threshold))
```

`vertexOpacity` is UV1.x from the verified short4n payload. Material parameters retain source `diffuse_threshold`, `vertex_gamma` and `height_above` values.

## Height-decal surface channels and post-RVT microdetails

The saved source binds all 34 height materials as **T0 `_tex_d`, T1 empty, T2 `_tex_r`, remaining slots empty**. The pinned public `make_clipmap.dshl` declares separate normal/reflection textures in slots 2/3. `wt_height_decals.py` records the shipped-resource mapping explicitly and applies the published reflection-channel contract:

| Original `_r` channel | Source interpretation | UE adaptation |
|---|---|---|
| A | Smoothness | `roughness = clamp(1-A, .04, 1)` |
| G | Ambient occlusion | Recorded source scope |
| R | Reflectance | Recorded source scope |
| B | Microdetail ID | Original CPU scale and 12-slot array selection |

The previous HeightDecal graph decoded this image as an AG normal and used B as metallic. On the original river/sand/gravel-border image, 48.6086% of the resulting normals had zero Z, while mean B implied metallic 0.913. The corrected graph sets metallic to zero and derives its normal from diffuse alpha using `make_clipmap.dshl:1397–1435` and `psh_derivate.hlsl:33–48`: two adjacent UV samples and source `bump_scale=.08`. The value is a scale in the UV height-gradient formula; no vertices move. Receiver RNM combines the clipmap normal with the geometric terrain normal.

The native update captured all 64 unique height-texture assets before and after. Their bytes, dimensions and encoding settings were unchanged: T0 uses sRGB/`TC_DEFAULT`; T2 uses linear/`TC_VECTOR_DISPLACEMENTMAP`. The defect was in channel interpretation. The source PNGs and geometry remain unchanged.

`ground-microdetails/definition.json` binds original level configuration SHA256 `e5a7d781e3a98e899c5e9665a1671eb88a5e12b31acf42344fa66fc73dcc5869`, definition `2312ee57fba11d5bca259cfe3dd5888f83524fef358e0181499023f19e037d54`, and preparation proof `327b433807856e123f1752e952652b47e35e7c346ac7a996d8de28b070d0f0b6`. Eleven original 512-square RGBA top mip images from `landscape_extra.dxp.bin` produce the required 12 slots:

`stone_a, scan_asphalt, sand_a, soil_a, generic_granules_a, grass_a, forest_floor_a, fabric_a, wood_b, metal_a, snow_a, generic_granules_a`.

The duplicate final slot is preserved. Native `DescribeMapTextureArray` read back **512 x 512 x 12, 10 mips**, matching source dimensions and exact source-asset order in both Land and Terrain. Array SHA256 is `d493ee69512b4b40de83a204a2ee706b678791a2488a6aec85d4154bd42a4cdd`. These slices remain linear RGBA data. The delivered PNGs retain original top-mip pixels; UE generates the array mip chain.

The original world UV scale is `0.5099999904632568` per metre, with no rotation: `UEWorld.xy * .01 * scale`. The logical source tile period is approximately 1.9608 m and top-level texel spacing 3.83 mm. Screen detail still follows mip selection, view angle and distance. Published `land_micro_detail.dshl` supplies `diffuse *= (R*2)^2`, AG normal decoding and `smoothness = saturate(smoothness * R*2)`. The original params are preserved in the definition; separate reflectance/AO, self-color and wetness behavior remain scoped. The pinned public `land_aces.dshl` consumes clipmap data but has no matching color/normal microdetail apply call, so the post-RVT consumer is explicitly a source-backed UE adaptation.

The final material order is **indexed land detail → projected RVT source-over → original-ID microdetail**. A fully opaque ground decal no longer erases every high-frequency layer. The 23 indexed albedo slices remain in their original pre-clipmap stage.

### RVT ID transport and continuity

Original CPU `landMicroDetails.cpp` computes `255.f / (255 / 11)`; integer division gives the float32 scale `11.086956977844238`. HeightDecal writes raw `(1 + clamp(_r.B * scale,0,11)) / 13` into RVT Specular. The original ground Decal with no verified source ID writes zero. Mask writes one. UE `VirtualTextureMaterial.usf` multiplies these raw values by opacity once, and its native blend is ONE / INVSRCALPHA. The receiver removes the `128/255` clear contribution once, recovers coverage and payload, then continuously blends floor/next array slices. Low coverage and invalid payload reduce strength continuously; explicit world-UV gradients are calculated before the coverage branch.

The physical Mask/YCoCg layout stores Specular in the third BC3 texture's R channel. Compression, filtering and mips can alter its continuous value. The adapter uses the published non-correct-bilinear floor/next branch, while the original default quality's four-ID gather remains separate scope. The offline R5 sensitivity model measured at most 0.210 layer error at full coverage and 2.091 at 0.1 coverage; corresponding modulation strength stays at most 1.0 / 0.1. This model checks numeric behavior; actual page updates and moving-view stability use the runtime image comparison.

`ground-refine-preflight-01` passed 78 checks, including 60 opacity/ID roundtrips (maximum error `9.39e-13`), overlapping-layer recovery, three asymmetric original-texture samples across ten ID boundaries and analytic gradient direction under rotated/reflected UV bases. The prior successful asset baseline contains 121 hash-verified files / 864,139,088 bytes in `ground-refine-01/before`.

Ground native update01 completed with UE PID 39628, exit 0, **94.0252208 s**, four shader workers and no engine errors or material-compile failures. The actual 40 targets include all 34 HeightDecal instances. Native checks verify each saved NormalOut/MicrodetailPayloadOut connection, the post-RVT property outputs, linear array/source bindings and the exact preserved grass closure. Land uses 16 samplers and Terrain 8. World `1cb484b861b8ee2cc8f2463a294abe7165af680718077cebc3de37390ef1400c` and source `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27` remain unchanged. Module `facf04a32b0ee4393022a49b48ade2ee7f78d876c4e5529c42b0b76aa151a3a5` includes concurrent external-session destruction work; this result covers material authoring.

Evidence: [ground-native-01/summary.json](../../artifacts/maps/wt-refine-20261118/ground-native-01/summary.json), SHA256 `24c83818fa13b06ebd762ed704c32b42fabfc6181cbb9c11ed4e00d73cbe546f`, and [ground-refine-preflight-01/summary.json](../../artifacts/maps/wt-refine-20261118/ground-refine-preflight-01/summary.json). A fresh full saved-material reload and same-pose/movement image review follow the grass V3 update.

## Runtime virtual texture composition

`wt_runtime_virtual_texture.py` creates `/Game/RMSim/Maps/wt_vlaanderen/Materials/WT_GroundDecalRVT` with BaseColor/Normal/Specular/Mask YCoCg. One source HISM, `world_decal_000`, writes the RVT and uses MainPass Never. Source geometry, instance identity and collision remain intact; Landscape and the Land parent receive the projected surface. Other unbound scene Decal components retain their standard translucent Surface outputs.

Native scene03 reads back the RVT asset, volume, writer and project physical-pool entry matching DXT5/BC5/DXT5 with 264 px bordered pages: 256 MiB, fixed size scaling, and residency mip bias group 3. Tile settings describe nominal addressing. The runtime gate records synchronized request rectangle coverage of 88 tiles and two subsequent completed frames; GPU-produced page counts were not measured. Screenshot review records the visible compositing result.

The UE5.8 Mask YCoCg layout clears auxiliary targets to `128/255` and writes premultiplied attributes. The receiver uses:

```text
coverage = saturate((Mask - 128/255) / (1 - 128/255))
color = RVTColor + BaseColor * (1 - coverage)
roughness = RVTRoughness + (BaseRoughness - 128/255) * (1 - coverage)
```

Zero coverage and positions outside the writer XY bounds return the original base surface. HeightDecal writes its world-aligned clipmap normal directly; the original generic ground Decal retains Tangent-to-World conversion. On read, RNM combines the recovered clipmap normal with the receiver geometric normal before the post-RVT microdetail layer. The receiver material sets `tangent_space_normal=False`.

UE `VirtualTextureCompress.usf` rounds packed Z to a sign bit. The receiver removes the clear bias and opacity from decoded XY, then reconstructs Z from `sqrt(saturate(1-dot(XY,XY)))` and the sampled sign. Independent review found and corrected an earlier continuous-Z inverse; 12 analytic normal/coverage combinations now agree to at most `1.33e-15` reconstruction error. Clear/color/roughness and overlapping-layer equations were also checked. Evidence: `artifacts/maps/wt-scene-20261118/rvt-composition-review-01/verification.json`, with exact engine-source hashes. Native sign/compression quantization remains part of the codec; the report records its near-grazing, low-coverage boundary.

## Original global landclass surface

The source material `WT_22356521c26047d92cb7` uses `land_mesh` with empty per-material texture slots. Its base color comes from the level's global landclass data. The original `lmap` detail block contains five ordered classes and a packed blend atlas:

| Source index | Landclass / macro color texture | Source tile size |
|---:|---|---:|
| 0 | `normandy_fields_a` | 4096 m |
| 1 | `avg_vlaanderen_detailed_biome` / `avg_vlaanderen_detailed_biome_tex_d` | 2048 m |
| 2 | `berlin_forest` | 2048 m |
| 3 | `sicily_city_tile` / `sicily_small_city_tile` | 1024 m |
| 4 | `kursk_sand` | 128 m |

`extract_wt_landclass.py` reads the original level chunk, embedded DataBlocks, all 1024 cell records and the packed DDSx atlas. The atlas has 448 pages arranged in 112 columns; each page contains a 64-square interior and a two-pixel border, yielding a 7616-by-272 image. The 32-by-32 cells span 1024 m each. All five original color textures are resolved through their DXP resource names and source-byte hashes. Their order is the lmap order; the separately decoded RIGz vegetation landclasses keep their own identities.

`lmeshLandClasses.cpp`, `lmeshRenderer.cpp`, `lmeshWeightAtlas.cpp`, `land_weight_atlas.hlsli`, `land_weight_inc.dshl` and `make_clipmap.dshl` at the pinned Dagor commit supply the mapping. Color UV is `((DagorX+16384)/size, -(DagorZ+16384)/size)` for these source offsets. Wrapping gives the equivalent `(DagorX/size,-DagorZ/size)` for the five tile sizes. The packed weight lookup retains the source mirrored cell addressing, page border, bilinear LOD0 sample and class count. The last class gets `saturate(1-sum(previous slots))`; weights are not renormalized. Source BC1 quantization can leave a total above one, and the adapter preserves that result.

The package `landclass/landclass.json` binds eight PNGs: five original macro colors, the linear weight atlas, and two nearest-filtered RGBA cell-record images. Color images remain sRGB and wrapped; atlas/cell data use linear uncompressed sampling, no mip chain, no streaming and clamped addressing. Metadata byte roundtrips and source/encoded weight agreement were checked at 17408 points. Three asymmetric source samples independently record the old positive-V and correct negative-V macro/index reads.

`wt_landclass.validate_landclass_surface` checks the original map identity, stable material ID, both receiver meshes, the package/proof bytes, every texture hash and the original cell records. `prepare_wt_landclass.py` backs up and compare-and-swaps the scene manifest; adding `source_landclass_surface` leaves all existing mesh, instance, material and geometry fields unchanged. The source material remains on `world_land_000` and `world_land_001`, totaling 382882 original triangles.

`WT_Parent_Land` reconstructs the original five-color/packed-weight base and applies the existing ground-decal RVT composition. VERSION 7 inserts the source indexed-detail graph into the core LC1 contribution. Terrain and LC1 macro/index UV use `float2(World.x,-World.y)/204800.0`; the two source detail scales retain positive `World.xy`. The indexed-detail section above records the source layer/noise/flow rules and explicit UE gradient-normal adaptation. Other landclass detail and vertical-cliff branches retain their own scope.

Source extraction, publication and six contract tests passed in `distant-land-02/extracted`, `publish` and `preflight` evidence. Package manifest SHA256 is `7da04c12b1ef4f446b5fff63bc9f6ecdb9ea85bddcad0386dd3cbf42fad99e46`; its source verification SHA256 is `087186b0095c1dd7ec08729ac1d4a6c0ea8895e3f6af6972fd49b534f77b1c9f`. Import07 saved these assets and the World using NullRHI. Their saved graphs, source bindings and completed shader statistics passed the independent D3D11 material08 gate.

For a fresh package, run `tools/maps/extract_wt_landclass.py --map <original avg_vlaanderen.bin> --game-root <installed game> --dae-root <fixed DAE src/dae> --oodle-library <local Epic oo2core.dll> --output <fresh extraction>` with the Pillow/NumPy Python environment. Then run `tools/maps/prepare_wt_landclass.py --scene-source <prepared package/scene-source.json> --extracted <fresh extraction> --evidence-root <fresh publication>`. The publisher rejects an already adapted package; use the recorded before copy or a fresh base for reproduction.

## Original local pond base color

Only `WT_3baf052e10b5a3ecdbf7` uses `water_aces`. Its original 11 texture slots and material parameter string are empty. All 23 triangles are in three source static objects inside the 2048 m core, assigned to slot 4 of `world_static_000`:

| Original object | Triangles | Dagor XZ bounds, metres | Original Y, metres |
|---|---:|---|---|
| `water_transparent_848` | 7 | `(688.014,776.329)` to `(814.150,870.357)` | 4.72968–4.75 |
| `water_transparent_886` | 9 | `(779.637,552.415)` to `(941.963,752.585)` | 0.5 |
| `water_transparent_892` | 7 | `(1102.350,71.740)` to `(1250.710,131.987)` | 0.666992–0.667160 |

The decoded original `levels/avg_vlaanderen.blk` provides `water_aces_color_and_density=(0.20000000298023224,0.20000000298023224,0,15)` and `water_aces_params=(1,0.30000001192092896,5,0)`. Dagor `prog/gameLibs/render/shaders/water_3d_color.dshl:424–427` uses each water_aces vertex's world Y for water height. The adapter preserves those pond elevations and does not apply the level-wide water plane at -5 m.

`wt_water.py` registers the audited map SHA, original material identity, original static FBX SHA and material slot. It binds the exact RGB values to `SourceWaterColor` on `WT_Parent_Water`. The explicit UE surface choices retain the previous neutral Surface's roughness 1, metallic 0 and specular 0.5, with an opaque two-sided graph, instancing usage and no displacement. Density 15 and the second vector remain in provenance; their original consuming formulas, waves, reflection and refraction are unresolved source semantics.

The source audit `water-source-01/verification.json` passed with SHA256 `83b8e07465427ec32048b667ccec72081eb52779ec170acbdffd0c64c2f56323`. It binds the original NPZ geometry, decoded level SHA256 `e5a7d781e3a98e899c5e9665a1671eb88a5e12b31acf42344fa66fc73dcc5869` and exact public shader references. Six contract tests, source global equality, original FBX hash and Python syntax passed in `water-source-01/preflight/verification.json`. Native `water-native-02` then passed all six checks, authored the two water assets and exited 0 in 14.1722834 s. It read back exact RGBA `(0.20000000298023224,0.20000000298023224,0,1)`; both resources completed 136 vertex / 260 pixel instructions. World/source hashes stayed unchanged. Its compact summary SHA256 is `d8f88f9fe1619f6fe0ca44cabeab257d00360d5585b90945037ef9c0731d8f07`. Material08 independently reloaded and compiled the saved parent and MI. Appearance remains covered by separate rendered-image evidence.

`update_wt_water.py` accepts `-SceneSource=<scene-source.json> -WaterUpdateOutput=<fresh audit directory>` with `-AllowCommandletRendering -d3d11 -NoShaderWorker`. It backs up existing water material files and the material report, updates only the original MI and its dedicated parent, reads back source RGB, requires completed shader statistics, and checks that the World and scene manifest hashes remain unchanged. The full material gate adds this parent and instance to its actual asset list. The explicitly texture-free Water graph permits zero texture samplers while retaining nonzero completed vertex/pixel instruction checks and compiler-error checks.

UE5.8 `MaterialEditingLibrary.cpp:1573–1581` leaves the vector setter's Boolean result false after applying its value. The adapter validates the actual override through `IsMaterialInstanceParameterOverridden` and reads back each RGBA channel within `1e-7` before saving. The failed first attempt and its unchanged original MI are preserved in `water-native-01/summary.json`; the successful second attempt binds `wt_materials.py` SHA256 `6118aa0b5de326818f4a106445d28beff21bd46f20efadf89e0a786f03eab9fd`.

## Source grass layers and streamed population

The original Flanders level configuration selects 13 `randomGrass` layers using seven `RndGrass` resources (resource class `0xA7D3ED6A`) from `vegetation.grp`. `extract_wt_foliage.py` resolves their real LOD0 geometry, source material records, masks, paired alpha, original biome IDs and configuration. `prepare_wt_foliage_meshes.py` uses the existing scene coordinate converter; seven FBX roundtrips retain eight triangles each, 56 total, with UV0 and a maximum local bounds error of 5.96e-8 m. Import uses the metre-unit FBX conversion and `import_uniform_scale=1`.

`prepare_wt_foliage.py` writes the independent `assets/maps/wt_vlaanderen/foliage/foliage.json` contract. It contains seven FBXs, 14 original grass mask/alpha images, original HQ Flanders macro color and four 4096-square RGBA textures holding 13 layers. All 13 layers have nonzero prepared weights. Weight UV is `(UEWorldX/204800, 1-UEWorldY/204800)`, with a linear sampler and explicit bounds masking to the source 2,048 m core.

Each weight is original 0.5 m biome-ID eligibility multiplied by bilinear `america_detailed_grass_masks.R` and the Flanders class weight from the source lmap atlas. The lmap decoder's original class order and page addressing are used; the last blend slot is `max(0, 1-sum(previous slots))`. Source weights are not renormalized. The packed PNGs read back byte-for-byte; uint8 quantization error is recorded in `foliage-01/prepared-01/verification.json`.

| Layers | Source model | Original biome IDs | Original relative density | UE reference candidates per 100 m² |
|---|---|---|---:|---:|
| 00–04 | `grass_tulip_b` | 29, 30, 31, 32, 33 respectively | 5 | 51.82734375 |
| 05 | `grass_paigle_a` | 27 | 2 | 51.82734375 |
| 06 | `grass_fill_a` | 27 | 5 | 55.2825 |
| 07 | `grass_blueberry_a` | 27 | 1 | 40.5 |
| 08 | `grass_daisies_a` | 0 | 0.4 | 16.2 |
| 09 | `grass_heather_a` | 1 | 1 | 40.5 |
| 10 | `grass_simple_a` | 1 | 6 | 51.82734375 |
| 11 | `grass_simple_a` | 0, 1 | 4 | 51.82734375 |
| 12 | `grass_fill_a` | 0, 1, 2 from the original RGB biome-mask defaults | 5 | 55.2825 |

Density follows `randomGrass.cpp` and `randomGrass.h` at the pinned Dagor commit. At the explicit reference quality multiplier of 1, the source radius plus fade is 400 m, the 10-by-10 grid cell is `800/9` m, and base candidates are `floor(400² * 2)`. A layer requests `floor(base * layerDensity / 100 + 0.5)` candidates per cell, capped by `floor(65534 / sourceVertexCount)`; the prepared UE density is that capped count divided by the cell area and multiplied by 100. Source LOD0 is used. UE5.8's Landscape Grass implementation divides centimetre-squared area by `1000*1000`, making its density unit instances per 100 m².

Horizontal and vertical scales retain original parameter ranges; the source reversed horizontal `.9 -> .7` interval is preserved in provenance and supplied to UE as the equivalent sorted interval `[.7,.9]`. GrassType uses locked XY scale and source radius/fade ranges. Each layer gets its own material override so the five tulip palettes remain distinct while sharing one source mesh.

### Source grass mask and color semantics

The slot-0 `*_tex_m` image contains RGB color masks. `randomGrass.cpp:802–818` appends `_a` to that managed texture name and loads a separate alpha resource; lines 1314–1315 bind the two textures independently. All seven recovered `*_tex_m_a` images are nonconstant L-channel data. The UE adapter samples their linear red channel.

Original E3DCOLOR bytes are BGRA; the contract preserves those bytes and their RGBA interpretation. Duplicate color parameters follow `DataBlock::findParam`'s first matching parameter, verified in `blk_core.cpp:727–753`. The six source endpoints are copied into the source grass color buffer by `randomGrass.cpp:848–853`.

`fast_grass.dshl:218–227` explicitly identifies this composition as taken from billboard grass:

```text
red   = lerp(RMin, RMax, random1)
blue  = lerp(BMin, BMax, random2)
green = lerp(GMin, GMax, mask.g)
color = lerp(landColor, red, mask.r)
color = lerp(color, blue, mask.b)
color = color * green * 4.59479341998814
```

The multiplier is `2^2.2`, defined in `prog/daNetGame/shaders/shader_global.dshl:715`. The chosen land-color PNG is the original 4096-square HQ resource, SHA256 `9f961b92696b646d79c84a43d5c3a3227a419ae6544fabfd4ebabf7ad6c75a0c`; the contract retains its source pack, offset, original packed hash, flags and dimensions. Runtime detail/decal color is outside this macro-color adaptation.

The original `random_grass_gpu` shader's alpha-test value is not present in the pinned public source. The material therefore records `alpha_clip_value=1/3` as the UE Masked adaptation threshold. The two-sided masked material uses UE per-instance variation and `PerInstanceFadeAmount`. Wind is not animated.

### Verification scope and field samples

The prepared supplement SHA256 is `8fa36662c1ecf48028284b735ad86336fd0e235c22e7ddeb3ccf16872a8f733e`. `wt_grass.validate_foliage` passed seven model contracts, four packed textures and 13 layers. The compact preparation record is `artifacts/maps/wt-scene-20261118/foliage-01/prepared-01/verification.json`, SHA256 `68215d5c9bf0b9b1001d08ba6f53fbe7ebc6599fad6d0b383208754bac301b22`. `grass-native-07-resume` completed the native bake with all 13 layers nonempty, 1,089 valid components, 986 weighted components and zero outdated maps. The two preview views generated 2,237 grass components / 52,984 render instances measured by `UGrassInstancedStaticMeshComponent::GetNumRenderInstances`. All 54 saved foliage/Terrain files retained their hashes; the World saved the baked GrassData. `grass-reload-02-final` independently passed 27 checks with 58 files unchanged. Four source-bound points retained 0/4/8/4 nonzero layers, at most 1-byte corner error and 0.25-byte interpolation error. Its summary SHA256 is `e9d73ab2836d8ae028582845fc8449c84c5f99c367d0d7d485a6d44f0b17c6bd`. The grass parent and all 13 layer MIs also completed the material08 native shader and source-parameter gate. Actual W/A driving reached ROS `(1075.2,-1012.7)` m. The reviewed `gui-final05` image shows nearby grass and pink/white flower instances; PNG SHA256 is `8ebe6b3381ac074936d85f540d19d808b41b3a7254b97aa70daf4785ba4f1771`. The GUI review also observed minimap updates with actual coordinates and Q-key heading, plus two M-key expand/collapse cycles. Evidence: `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json` (captures 01-07).

The package stores source rules and weights. Landscape Grass generates decorative, noncolliding positions around views. Source height-dependent four-biome selection, microdetail-removal bits, grass-decal channel overrides, the separate `small_grass` billboard system, original random seeds and multi-LOD transitions remain separate adaptations.

The original grass-weight image is zero within 10 m of the preview spawn. The nearest prepared weight above one half is about 46.7 m away at ROS `(1074.25,-1011.75)`; the original HM2 bilinear height there is `-0.344426642` m. Additional sourced flower/tulip locations are `(893.75,-1029.25,2.488624399)` m and `(1019.25,-1217.75,1.907072557)` m. `foliage-01/field-samples.json` binds source height bytes, four-corner heights, original biome details, source grass/hard-surface neighborhood statistics and all 13 actual packed weight channels. Native collision traces and rendered vegetation remain the separate runtime checks.

## Remaining rendering adaptations

`land_mesh` consumes its verified five-color/packed-weight binding, VERSION 7's core LC1 indexed details and VERSION 8's source-ID post-RVT microdetail layer. Terrain uses the same source dual-layer/noise/flow rules and explicit UE gradient-normal adapter. The saved `water_aces` adapter binds the original level RGB and records density/parameter and water-effect semantics separately. Ground decals continue through the RVT; rendinst multiply/diffuse/normal-only families use their dedicated passes. Further work covers source decal smoothness/metalness multiplication and independent attribute opacity, per-section draw order, paint palettes, packed ground reflectance/self-color, other landclass microdetails, wetness, interior/glass/emissive behavior, original spatial Perlin modulation, vegetation wind/pivot animation and original lighting/postprocessing.

Original shader channel references include `layered_inc.dshl`, `perlin_layered_inc.dshl`, `get_mix_weight_inc.dshl`, `rendinst_layered.dshl`, and `rendinst_simple.dshl` at the pinned Dagor checkout. Original game content remains read-only and local.

## Saved-scene import and runtime

`tools/unreal/Import-WTScene.ps1` explicitly invokes `import_wt_scene.py` with `-SceneSource=<scene-source.json>`. The importer checks FBX/sample hashes, original triangle counts, stable material identities, instance files and virtual resource paths; it authors HISM and Landscape and saves `/Game/RMSim/Maps/wt_vlaanderen/World`.

Cache identity includes original FBX SHA, import version, material slots, collision policy, optional leaf contract/bounds, source vertex-opacity contract/proof, geometry-proof SHA and the saved asset hash. Reused meshes still receive native count, material and bounds checks. Duplicate source material slots may merge only by the same stable material ID.

`world_decal_000` retains all 87864 source FBX triangles. Its hash-bound original-geometry proof identifies 34 strictly coincident triangles, giving an exact native target of 87830; the separate distinct-position collinear face remains in that target. `wt_mesh_geometry.py` verifies proof identity, full face correspondence and material accounting, and the saved-world audit checks all 35 native material totals. The original compact proof record is `artifacts/maps/wt-scene-20261118/decal-proof-01/verification.json`; the opacity adapter reran it against the new FBX and records the new binding in `decal-opacity-01/adapt/verification.json`. Complete original geometry evidence stays local.

The map description uses `kind: rm_scene_map`, with an exact `/Game/RMSim/Maps/<id>/World` path. WorldSettings stores `RMSimMapId=<id>` and `RMSimSourceSha256=<sha256>`. Script preflight and C++ loading validate the map identity and source tags; Landscape and HISM collision must be ready before vehicle contact initialization. ROS reports the scene-map kind.

Each import writes a fresh local `scene-import-<UTC>/report.json` and the package `ue-scene-import-report.json`. The saved-stage status remains `imported_pending_runtime_validation`; the independent scene, material and driving records supply their own results. The pre-Land/grass/water scene03 baseline verified 1365 unique meshes, 726/642 visual/collision HISM groups and 35922/20033 placements. Source/native totals are 13478813 / 13478779 triangles, with exactly the 34 proof-bound removals; all 21 checks include the current opacity and RVT contracts. Its compact summary binds the full local report by SHA. Import06 and the material06 gate retain manifest SHA `ac0afa8a40d90feee51bc53d02b41aa1b33c10b9f3d2a574b0e7d7be1f18c7d9` and World SHA `720931dad262197929863d553c2388c3de18131277498749ef31faa201f12253`.

Import07 binds scene manifest SHA256 `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27` and saved World SHA256 `6e4b763968f7e71afadc5885d59b195c88b9e207491f8ff41da2fd87aa6218ef`. Its source graph version is 5. Subsequent grass authoring saved World `20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25`. The isolated Water update, independent saved-grass reload and material08 compilation passed with their own module-bound records; the separate GUI record `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json` confirms the observed distant land, grass-field view and map controls in captures 01-07.

The original tiny API fixture remains historical evidence in `artifacts/maps/wt_existing/scene-api-check-20261115T121010Z/result.json`. Current launch commands and the complete source-to-runtime sequence are maintained in the [Flanders guide](../guides/war-thunder-flanders.md).