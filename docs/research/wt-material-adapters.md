# War Thunder material adapter scope

Updated 2026-11-18 for the Flanders saved-scene integration. The original preparation records from 2026-11-15 remain unchanged.

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
| Saved material resources 08 final | 104 unique assets, completed D3D11 shader statistics, zero compiler errors, saved hashes unchanged; exit 0, 54.526665 s | `artifacts/maps/wt-scene-20261118/native-materials-08-final/summary.json` |

The completed material06 baseline covered 88 resources before the global Land graph, pond base-color adapter and grass outputs. The updated `native-materials-08-final` gate passed 104 unique saved assets: eight map parents including Terrain and Water, two Leaves, 34 HeightDecal, one Land, one original river Decal, 43 ordinary tree instances, one Water instance, one grass parent and 13 grass instances. Actual vertex/pixel instruction ranges are 136–355 / 260–559, with 2–14 samplers and zero compiler errors. The grass gate verifies all 15 unique source Masks/Alpha/Land texture bindings, exact native dimensions, two-sided masked foliage and source color parameters. `verify_wt_materials.py` completes native texture compilation before reading platform dimensions and completes target shader compilation before collecting statistics; all saved asset hashes remain unchanged.

The successful grass bake binds module `c44d2c079aeaa597be3a5a5076787f89dce1f261ca77c29da26fba08cb263e18`. Water02 and grass reload02 bind `1bf4126eb733b12880e170a5eb4830f50b5f792cdda55a6ca2fd1507ed26558a`; material08 binds `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404`. The later Water/reload and material08 binaries also contain concurrent destruction-session changes; each gate retains its own execution hash and documented scope. The final material summary SHA256 is `b32bbdb322f105c90b80514156d36f85adce30566adb588d5f55b742c865b709`. World `20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25` and source manifest `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27` stayed unchanged during water02 and material08.

The earlier `flanders-contact-02-render-ready` screenshots supplied the tree-card and ground-decal diagnosis. In the baseline contact03 rest image, review confirmed the floating surface obstruction removed and gaps restored in the left foliage cutouts. GUI review of captures 01-07 on PID 43760 / module `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404` shows green/brown distant land, nearby grass and pink/white flower instances after actual W/A driving, minimap position/heading updates including Q input, and two M-key map expand/collapse cycles. The observed scope is bound by `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`. Material validation uses `-d3d11 -AllowCommandletRendering`; each execution records its exact shader-worker arguments. Current rerun commands are in the [Flanders guide](../guides/war-thunder-flanders.md).

## Source textures

The original model list alone omitted 78 road/static/landmesh texture names. `export_wt_textures.py --extra-textures .../additional-scene-textures.json` resolves those alongside the earlier list. The `textures-03/result.json` inventory has 917 outputs, 913 required references, no missing references, and no decode failures. Its per-entry paths point either to new outputs or hash-verified earlier PNGs. Float16 vegetation pivot maps remain lossless NPY data. The UE adapter copies consumed PNGs into the map package and records their UE paths and color-space role.

## Implemented adapters

- Simple: original diffuse slot 0 and AG packed normal/roughness/metallic slot 2, UV0. Absent source slots receive generated neutral defaults.
- Layered: original overlay UV1, base normal UV0, tiled detail UV2, mask UV1, source detail tile parameters and height-assisted mix. AG normal reconstruction and R smoothness/B metallic channels are retained.
- Perlin layered: original three color/normal layers and source tiling, using height-weighted layer mixing.
- Single-layer overlay: source diffuse/detail multiplication and tile parameters.
- Source ground decals: 34 HeightDecal materials consume recovered position.w and source diffuse-alpha height thresholds; the original world-decal mesh writes an RVT sampled by terrain and outer landmesh.
- Other scene decals: original authored geometry, image and alpha retain UE surface translucency.
- Foliage: original diffuse/normal and alpha-test threshold; 25 content-verified ordinary materials sample separate T1.r coverage. Legacy facing leaves retain T0 alpha plus recovered geometry and no-wind camera-facing displacement.
- Terrain: original macro color and nearest-sampled source index texture with verified negative-V mapping, grouped original microdetail at the source's 11.12 m tile setting, LandscapeVisibilityMask and the ground-decal RVT receiver.
- Outer landmesh: five original macro-color landclasses blended by the original lmap packed weight atlas, followed by the ground-decal RVT receiver; imported at checkpoint07.
- Local ponds: exact original level RGB in a dedicated base-color-only Water adapter; source contract, native water02 update and independent material08 compilation passed.

Shared parent graphs declare instanced-static-mesh usage. Import07 used VERSION 5 for the global Land mapping; VERSION 6 supplies the narrow pond base-color adapter. Both retain the VERSION 3 graph-rebuild fix: snapshot the old expression list, delete each expression, and assert an empty graph before authoring new nodes. This addresses the UE5.8 live-array deletion behavior observed in the native rebuild. Parent saving requires a successful completed graph compilation; the separate native material gate checks the saved resource.

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

## Runtime virtual texture composition

`wt_runtime_virtual_texture.py` creates `/Game/RMSim/Maps/wt_vlaanderen/Materials/WT_GroundDecalRVT` with BaseColor/Normal/Specular/Mask YCoCg. One source HISM, `world_decal_000`, writes the RVT and uses MainPass Never. Source geometry, instance identity and collision remain intact; Landscape and the Land parent receive the projected surface. Other unbound scene Decal components retain their standard translucent Surface outputs.

Native scene03 reads back the RVT asset, volume, writer and project physical-pool entry matching DXT5/BC5/DXT5 with 264 px bordered pages: 256 MiB, fixed size scaling, and residency mip bias group 3. Tile settings describe nominal addressing. The runtime gate records synchronized request rectangle coverage of 88 tiles and two subsequent completed frames; GPU-produced page counts were not measured. Screenshot review records the visible compositing result.

The UE5.8 Mask YCoCg layout clears auxiliary targets to `128/255` and writes premultiplied attributes. The receiver uses:

```text
coverage = saturate((Mask - 128/255) / (1 - 128/255))
color = RVTColor + BaseColor * (1 - coverage)
roughness = RVTRoughness + (BaseRoughness - 128/255) * (1 - coverage)
```

Zero coverage and positions outside the writer XY bounds return the original base surface. Normals are transformed into world space on write and combined with the world-space base normal on read. The receiver material sets `tangent_space_normal=False`.

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

`WT_Parent_Land` first reconstructs the original five-color/packed-weight base, then applies the existing ground-decal RVT composition. Terrain macro and index UV now use `float2(World.x,-World.y)/204800.0`; its 11.12 m microdetail retains positive `World.xy`. This covers the macro-color layer. Source landclass microdetail, derivative normals, vertical-cliff shading and dual-layer flowmaps retain their own adaptation scope.

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

`land_mesh` now consumes its verified global landclass macro-color and packed-weight binding, imported in checkpoint07. The saved `water_aces` adapter binds the original level RGB and records the remaining density/parameter and water-effect semantics separately. The successful standalone water02 update refreshed its provenance entry. Ground decals continue through the RVT. Further work covers paint palettes, wetness, interior/glass/emissive behavior, original spatial Perlin modulation, terrain dual-layer flowmaps, additional source decal/material effects, vegetation wind/pivot animation, and original lighting/postprocessing.

Original shader channel references include `layered_inc.dshl`, `perlin_layered_inc.dshl`, `get_mix_weight_inc.dshl`, `rendinst_layered.dshl`, and `rendinst_simple.dshl` at the pinned Dagor checkout. Original game content remains read-only and local.

## Saved-scene import and runtime

`tools/unreal/Import-WTScene.ps1` explicitly invokes `import_wt_scene.py` with `-SceneSource=<scene-source.json>`. The importer checks FBX/sample hashes, original triangle counts, stable material identities, instance files and virtual resource paths; it authors HISM and Landscape and saves `/Game/RMSim/Maps/wt_vlaanderen/World`.

Cache identity includes original FBX SHA, import version, material slots, collision policy, optional leaf contract/bounds, source vertex-opacity contract/proof, geometry-proof SHA and the saved asset hash. Reused meshes still receive native count, material and bounds checks. Duplicate source material slots may merge only by the same stable material ID.

`world_decal_000` retains all 87864 source FBX triangles. Its hash-bound original-geometry proof identifies 34 strictly coincident triangles, giving an exact native target of 87830; the separate distinct-position collinear face remains in that target. `wt_mesh_geometry.py` verifies proof identity, full face correspondence and material accounting, and the saved-world audit checks all 35 native material totals. The original compact proof record is `artifacts/maps/wt-scene-20261118/decal-proof-01/verification.json`; the opacity adapter reran it against the new FBX and records the new binding in `decal-opacity-01/adapt/verification.json`. Complete original geometry evidence stays local.

The map description uses `kind: rm_scene_map`, with an exact `/Game/RMSim/Maps/<id>/World` path. WorldSettings stores `RMSimMapId=<id>` and `RMSimSourceSha256=<sha256>`. Script preflight and C++ loading validate the map identity and source tags; Landscape and HISM collision must be ready before vehicle contact initialization. ROS reports the scene-map kind.

Each import writes a fresh local `scene-import-<UTC>/report.json` and the package `ue-scene-import-report.json`. The saved-stage status remains `imported_pending_runtime_validation`; the independent scene, material and driving records supply their own results. The pre-Land/grass/water scene03 baseline verified 1365 unique meshes, 726/642 visual/collision HISM groups and 35922/20033 placements. Source/native totals are 13478813 / 13478779 triangles, with exactly the 34 proof-bound removals; all 21 checks include the current opacity and RVT contracts. Its compact summary binds the full local report by SHA. Import06 and the material06 gate retain manifest SHA `ac0afa8a40d90feee51bc53d02b41aa1b33c10b9f3d2a574b0e7d7be1f18c7d9` and World SHA `720931dad262197929863d553c2388c3de18131277498749ef31faa201f12253`.

Import07 binds scene manifest SHA256 `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27` and saved World SHA256 `6e4b763968f7e71afadc5885d59b195c88b9e207491f8ff41da2fd87aa6218ef`. Its source graph version is 5. Subsequent grass authoring saved World `20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25`. The isolated Water update, independent saved-grass reload and material08 compilation passed with their own module-bound records; the separate GUI record `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json` confirms the observed distant land, grass-field view and map controls in captures 01-07.

The original tiny API fixture remains historical evidence in `artifacts/maps/wt_existing/scene-api-check-20261115T121010Z/result.json`. Current launch commands and the complete source-to-runtime sequence are maintained in the [Flanders guide](../guides/war-thunder-flanders.md).