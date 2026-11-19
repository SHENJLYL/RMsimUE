# War Thunder — Flanders original map

This package is generated from the user's installed `avg_vlaanderen.bin` and its referenced resources. Original game, CDK and extraction inputs remain read only. Map geometry, names, images and materials remain Gaijin / War Thunder content; provenance is retained with the local conversion.

Source level SHA256: `427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f`. Format and shader contracts were checked against official DagorEngine commit `9639942de5a8e5b01d91977f60ed82cda22d5f41`. The reader began from Dagor Asset Explorer `1f0d1e04ded04bf9b28e8d5be93cb79714f505d3`, with the audited compatibility patch under `artifacts/sources/wt_unpack`.

## Preserved source content

- 45,675 stored RIGz instances reference 659 model types. All referenced models were resolved and decoded. Typed declarations, source material indices and the independently recovered leaf/opacity payloads remain traceable.
- 35,825 ordinary rotation/scale instances remain instanced. The exact original matrices of 9,850 shear/reflection instances are baked into spatial batches. The prepared scene has 726 visual FBXs and about 12.1 million unique visual triangles.
- Original 4096 × 4096 HM2 samples at 0.5 m spacing are copied byte-for-byte to `terrain/height.u16`. The fine region spans 2,048 m and retains `hmin=-20 m`, `hscale=64 m`.
- Original outer landmesh and SCN static geometry provide an approximately 32 km background. The source exclusion indices remove the four coarse land cells beneath HM2. Source road, decal and combined geometry is retained.
- 616 source collision resources were matched, including 13 intentionally nonphysical resources. The 43 visual types without a matched collider remain listed in `collision-coverage.json`. SCol is retained; WCol is source water without PHYS_COLLIDABLE.
- 639 prepared collider FBXs contain 1,365,365 triangles, including affine bakes. Capsule tessellation has a bounded 1 mm model-space sagitta approximation. Source material names remain metadata for future physical-material calibration.

Coordinates are `Dagor(x,y-up,z) -> ROS(x,-z,y)`, with unchanged origin and metres. UE import mirrors ROS Y and converts metres to centimetres, producing UE world `(100*x,100*z,100*y)`. Landscape and world texture coordinates use that same origin.

## Audited source adaptations

Three legacy `rendinst_facing_leaves` models store four coincident centers plus a half4 card offset. The source payload was recovered and expanded into a no-wind reference plane: 91 cards / 182 leaf triangles across 2,369 unchanged source instances. The FBXs retain UV0 and carry raw offsets in UV3/UV4; native imports use five full-precision UV channels. Source/reference bounds and the complete camera-facing bounds envelope are recorded separately. Evidence: `artifacts/maps/wt_existing/leaf-adaptation-20261117-01/verification.json`.

`world_decal_000` retains 87,864 source FBX triangles. Its original-geometry proof identifies exactly 34 triangles with coincident vertices, giving a native target of 87,830. The distinct-position collinear face remains included. The source `short4n position.w` was recovered from the original typed draw bytes, with positions, indices and material IDs checked against all 99 original decal NPZ inputs. It was written to UV1.x without changing existing FBX geometry, UV0, transforms or material slots. All 263,592 polygon corners have zero scalar reconstruction error. UE uses two full-precision UV channels. The adapter reruns the independent geometry proof against the new FBX before publishing it.

The 983 original source material identities remain stable. Import07 used adapter VERSION 5 for global Land colors; VERSION 6 supplies the isolated source pond-color binding. The 34 `land_mesh_height_decal` instances consume source vertex opacity and diffuse-alpha height thresholds. The original world-decal mesh writes color, opacity and world normal into a runtime virtual texture sampled by Landscape and outer landmesh; its source geometry stays intact. A content/hash audit selects 25 ordinary foliage materials with opaque T0 color and separate grayscale T1 coverage. Legacy facing leaves continue using their original diffuse alpha. Formulae, source references and rendering scope are in [material adapters](../../../docs/research/wt-material-adapters.md).

## Original global landclass surface

The outer `land_mesh` material `WT_22356521c26047d92cb7` uses the original lmap global color/weight binding. `extract_wt_landclass.py` recovers five classes in their exact source order: `normandy_fields_a`, `avg_vlaanderen_detailed_biome`, `berlin_forest`, `sicily_city_tile` and `kursk_sand`. Their color tile sizes are 4096, 2048, 2048, 1024 and 128 m. The corresponding five original color images are retained with their DXP byte hashes.

The source packed atlas contains 1024 cell records and 448 bordered pages, decoded to 7616 × 272 pixels. `landclass/landclass.json` and its verification bind five macro-color PNGs, the linear blend atlas and two exact cell-record PNGs. Source cell addressing, bilinear weight sampling and `saturate(1-sum(previous weights))` for the last class are preserved. The weights retain their source values without extra normalization. Independent checks compare 17408 source/encoded samples.

Source macro UV is `((DagorX+16384)/size, -(DagorZ+16384)/size)` with original offsets. Terrain macro/index mapping now follows the same negative-V convention; 11.12 m microdetail keeps positive XZ. Three asymmetric source samples are recorded in the extraction verification. The Land graph reconstructs the five-class base before receiving source ground decals through RVT.

`prepare_wt_landclass.py` publishes the hash-bound `source_landclass_surface` contract while retaining every preexisting scene field. The two original Land FBXs, `world_land_000` and `world_land_001`, retain 382882 triangles and their previous source hashes. The current scene manifest SHA256 is `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27`; the landclass package manifest SHA256 is `7da04c12b1ef4f446b5fff63bc9f6ecdb9ea85bddcad0386dd3cbf42fad99e46`.

Source extraction, publication, six contract tests and pure preflight passed under `artifacts/maps/wt-scene-20261118/distant-land-02`. Import07 saved the new base-color graphs and World. The updated Land and Terrain resources passed the 104-asset `native-materials-08-final` gate under D3D11. Original landclass microdetail, derivative normals, vertical-cliff shading and full dual-layer flowmaps retain their separate adaptation scope. GUI review of captures 01-07 shows green/brown distant land surfaces; the observed scope is recorded in `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`.

## Original local pond color

The original `water_aces` material `WT_3baf052e10b5a3ecdbf7` covers three static pond objects, 23 triangles total, inside the 2048 m core. They remain in slot 4 of `world_static_000` at their original elevations: approximately 4.75, 0.5 and 0.667 m. The pinned Dagor `water_3d_color.dshl:424–427` uses the mesh world Y for this shader.

The original level configuration provides `water_aces_color_and_density=(0.20000000298023224,0.20000000298023224,0,15)` and `water_aces_params=(1,0.30000001192092896,5,0)`. `wt_water.py` checks the source map, material identity, receiver FBX hash and slot, then binds the exact RGB values. The dedicated `WT_Parent_Water` keeps an explicit neutral UE opaque surface: roughness 1, metallic 0, specular 0.5, two-sided instancing and no vertex displacement. Density and the second vector remain recorded source data; their consuming formulas and original water effects remain unbound.

Source geometry/configuration audit and six contract tests passed in `artifacts/maps/wt-scene-20261118/water-source-01`. Its source audit SHA256 is `83b8e07465427ec32048b667ccec72081eb52779ec170acbdffd0c64c2f56323`. The isolated `water-native-02` update passed with UE exit 0 in 14.1722834 s: six checks, the original MI and dedicated parent, exact source RGBA readback and completed shader statistics. World and scene-manifest hashes stayed unchanged. The subsequent `native-materials-08-final` run independently reloaded and compiled both saved water assets. Evidence: `artifacts/maps/wt-scene-20261118/water-native-02/summary.json`. Source pond geometry remains unchanged; rendered appearance is checked separately.

## Source grass supplement

The decoded original `levels/avg_vlaanderen.blk` selects 13 `randomGrass` layers and seven `RndGrass` resources from `vegetation.grp`: `grass_tulip_b`, `grass_paigle_a`, `grass_fill_a`, `grass_blueberry_a`, `grass_daisies_a`, `grass_heather_a`, and `grass_simple_a`. Their original LOD0 meshes contain eight triangles each. All seven FBX roundtrips retain 56 triangles and UV0, with a maximum bounds error of 5.96e-8 m. They use the existing metre-unit FBX convention and UE import scale 1.

`foliage/foliage.json` is an independent, hash-bound supplement. It binds the seven meshes, seven original RGB mask textures, seven separately sourced opacity textures, the original 4096-square HQ land-color image, and four packed 4096-square RGBA weight images for all 13 layers. The complete 2,048 m core is covered at 0.5 m per weight pixel. UE world centimetres map to weight UV as `(X/204800, 1-Y/204800)`; the image orientation preserves source negative V. The prepared distribution combines original biome-ID eligibility, bilinear `america_detailed_grass_masks.R`, and the original Flanders landclass blend weight.

Original grass opacity is a separate resource named by appending `_a` to the managed slot-0 mask name, as implemented in Dagor `randomGrass.cpp`. All seven recovered opacity images are nonconstant grayscale data. Layer colors preserve original E3DCOLOR BGRA bytes, their RGBA interpretation, and the source first-match rule for duplicate BLK parameters. Source horizontal/vertical scale ranges, source biome IDs and density parameters remain in the contract. UE density is derived from Dagor's 10-by-10 candidate grid, LOD0 density and 65,534-vertex cell limit at an explicitly recorded reference quality multiplier of 1; the native Landscape Grass implementation uses instances per 100 m².

The prepared package contains no grass instance positions. Native Landscape Grass reconstructs and streams decorative, noncolliding instances around views. Source microdetail-dependent removal, height-weighted biome boundary selection, grass-decal overrides, the separate `small_grass` billboard system, Dagor random positions, wind and multi-LOD transitions remain separate adaptations. The grass material uses the published billboard-grass RGB mask composition and original macro land color; the source runtime detail/decal color composite is not baked into grass color. Its alpha clip of one third is an explicit UE Masked adaptation value.

Source preparation and pure contract validation passed for seven models, four weight textures and 13 nonempty layers. `grass-native-07-resume` then baked all 13 layers, with valid GrassData on 1,089 Landscape components and nonzero weights on 986. Its two preview views produced 2,237 grass components and 52,984 render instances, measured by `GetNumRenderInstances`; all 54 saved foliage/Terrain files stayed unchanged while the World saved its baked data. `grass-reload-02-final` independently passed 27 checks with 58 files unchanged. Four source-bound points contain 0/4/8/4 nonzero grass layers, with maximum corner/interpolation differences of 1/0.25 byte. The saved grass parent and 13 layer materials also passed `native-materials-08-final`. Preparation evidence remains `artifacts/maps/wt-scene-20261118/foliage-01/prepared-01/verification.json`; the supplement SHA256 remains `8fa36662c1ecf48028284b735ad86336fd0e235c22e7ddeb3ccf16872a8f733e`. Native summaries are in the three named evidence directories. In the GUI run (PID 43760, module `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404`), actual W/A driving reached ROS `(1075.2,-1012.7)` m. Capture `gui-final05` shows nearby grass and pink/white flower instances clearly; its PNG SHA256 is `8ebe6b3381ac074936d85f540d19d808b41b3a7254b97aa70daf4785ba4f1771`. The minimap followed actual vehicle coordinates and the Q-key heading change, and M expanded/collapsed the map in two observed cycles. Evidence: `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`. These observations cover captures 01-07.

## Original tank spawn — 2026-11-19

The current `default` is the horizontal center of Flanders Dom Team A's second ground-vehicle spawn area, `t1_tankSpawn02_zone01`. The original installed `mis.vromfs.bin` SHA256 is `1cfb7ebbff304639f19052acab339627779ac75c0575f1265da95fe7c86dbd32`. Its `gamedata/missions/cta/tanks/vlaanderen/vlaanderen_dom.blk` selects `levels/avg_vlaanderen.bin` and imports the Dom battle-area template, which imports `mainareas/vlaanderen_dom_template.blk`. That template defines the Sphere and includes it in `dom_t1_tank_spawn_02_arcade` and `dom_t1_tank_spawn_02_hardcore`. The generic `dom_template.blk` sets the corresponding variable and registers it through `missionMarkAsRespawnPoint` with `team=A` and `tags/tank=true`. `spawn-source.json` retains the complete source paths, entry hashes, exact transform and registration fields.

The original Sphere center is Dagor `[439.0,1.0499999523162842,148.3000030517578]` m. The established coordinate conversion gives ROS `[439.0,-148.3000030517578,1.0499999523162842]` m. RMSim's spawn descriptor represents a ground anchor, so the exact source horizontal center is projected onto the original HM2 fine surface at ROS Z `-3.467716648308154` m. The horizontal displacement is zero; the vertical displacement is `-4.517716600624438` m and the resulting point remains inside the approximately 10 m Sphere. Source sample bytes are unchanged. The query lies on an X grid edge, where the height interpolation agrees with the Landscape triangles. Original coarse land cell `[16,16]` is excluded by HM2's `[16,16,18,18]` replacement bounds and is not used as support. The original physical-resource audit found no prop collider AABB in the center's XY ±2 m square.

The applied spawn is `default`, team `blue` (RMSim's mapping for source Team A), ROS position `[439.0,-148.3000030517578,-3.467716648308154]` m and yaw `-2.0472659359862844` rad. The yaw is derived from the area transform's first basis vector with `atan2(-tm[2],tm[0])`; the mission does not provide a separate explicit vehicle-yaw parameter. `Play-WarThunder.cmd` continues to select `default` and the existing vehicle-ground offset remains unchanged. Source and offline support evidence is in `artifacts/maps/wt-rails-spawn-20261119/spawn-audit/SPAWN_SOURCE_AUDIT.md`; native contact/reset/entry-point evidence is recorded by the accompanying runtime regression.

## Files and evidence

- `map.json`: `rm_scene_map` identity, exact saved level `/Game/RMSim/Maps/wt_vlaanderen/World` and the original Dom Team A second tank-spawn area selected as `default`.
- `scene-source.json`: imports, source material specifications, bounds, coverage and hash-bound optional adapters.
- `meshes/`, `collision/`: FBXs with hashes and measured local bounds.
- `instances.json`, `instances/*.rmi`: RMI1 count followed by float32 ROS position, quaternion XYZW and scale triples.
- `heightfield.json`, `terrain/height.u16`: Landscape import contract and original samples.
- `materials.json`, `collision-coverage.json`: source identities and coverage. `spawn-candidates.json` retains historical offline preview candidates; `spawn-source.json` binds the current `default` to the original mission spawn and its terrain projection.
- `geometry-audits/world_decal_000.json`: full source/FBX geometry and per-material face accounting.
- `geometry-audits/world_decal_000-opacity.json`: original short4n payload, new FBX binding and UV1 readback.
- `landclass/landclass.json`, `landclass/verification.json`, `landclass/textures/`: original five-class global color and packed-atlas source binding.
- `foliage/foliage.json`, `foliage/meshes/`, `foliage/textures/`, `foliage/weights/`: independent source grass supplement and view-streaming inputs.
- `preparation-verification.json`: unchanged historical base-preparation record. Subsequent leaf, opacity and foliage reports bind their separate changes.
- `material-import-report.json` and `ue-scene-import-report.json`: current local authoring/import reports; detailed assets, proofs and logs stay local.

Base preparation can be reproduced into a fresh package directory inside the isolated worktree:

```powershell
$wtRoot = 'D:\simulator-wt-vlaanderen'
$wtSource = 'D:\simulator\artifacts\maps\wt_existing\avg_vlaanderen'
$wtPrepared = Join-Path $wtRoot ('artifacts/maps/wt-replay-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '/wt_vlaanderen')
Set-Location $wtRoot
& E:\blender\blender.exe --background --python-exit-code 1 --python tools/maps/prepare_wt_scene.py -- --source $wtSource --output $wtPrepared
if ($LASTEXITCODE -ne 0) { throw 'Base scene preparation failed.' }
& E:\blender\blender.exe --background --python-exit-code 1 --python tools/maps/prepare_wt_scene.py -- --source $wtSource --output $wtPrepared --verify
if ($LASTEXITCODE -ne 0) { throw 'Base scene verification failed.' }
Copy-Item .\assets\maps\wt_vlaanderen\map.json (Join-Path $wtPrepared 'map.json')
```

Use `$wtPrepared` as `$wtPackage` in the [complete Flanders rerun sequence](../../../docs/guides/war-thunder-flanders.md): source leaf payload → three leaf FBXs → initial geometry proof → raw opacity → single decal FBX plus rebound proofs → original global landclass extraction/publication → full import → optional grass/water authoring → saved-scene/material/drive checks. Landclass and pond commands are documented in the [material adapter source sections](../../../docs/research/wt-material-adapters.md). The current package already contains these source adaptations; routine reimport starts at the guide's full-import step. Blender requires `--python-exit-code 1` to propagate Python failures.

## Latest import checkpoint — global Land and map UI

`import-07-land-ui` exited 0 and saved World SHA256 `6e4b763968f7e71afadc5885d59b195c88b9e207491f8ff41da2fd87aa6218ef` with scene manifest SHA256 `91f9efdcea0658f7d3c06bd2b64a4c1bb6ef7d928c23733b27b2626c7ae41e27`. Its NullRHI run validates asset import/save. The subsequent grass bake saved World SHA256 `20195170a5615e3df69a62574ec7e25f8e5239fa0dc40eb930e8b0d382b58e25`. The isolated water update and final saved-material audit left this World and source manifest unchanged.

`native-materials-08-final` exited 0 in 54.526665 s and passed 104 unique saved assets: eight map parents, two Leaves, 34 HeightDecal, one Land, one river Decal, 43 ordinary tree instances, one Water instance, one grass parent and 13 grass instances. Compiler errors were zero; all saved material hashes stayed unchanged. Its module SHA256 is `d79cf620925921fb55fc47a87b7ce9ee3e412ce1d787e567f1393ab0ba052404`. The earlier successful grass bake used module `c44d2c079aeaa597be3a5a5076787f89dce1f261ca77c29da26fba08cb263e18`; `water-native-02` and `grass-reload-02-final` used `1bf4126eb733b12880e170a5eb4830f50b5f792cdda55a6ca2fd1507ed26558a`. Each execution retains its own binary binding. Evidence: `artifacts/maps/wt-scene-20261118/native-materials-08-final/summary.json`, SHA256 `b32bbdb322f105c90b80514156d36f85adce30566adb588d5f55b742c865b709`. These source, saved-data and compilation records retain their stated scope. The separate GUI acceptance record documents the observed distant terrain, driven grass-field view and minimap/M-key interactions in captures 01-07: `artifacts/maps/wt-scene-20261118/gui-final-acceptance/verification.json`.

## Recorded baseline — 2026-11-18, before global Land, pond color and source grass

The original preparation checked all 1,365 then-current FBX hashes, 35,825 serialized instance transforms (maximum corner error 0.5341 mm), original height bytes and four representative FBX reimports. Its 109 repeated source draws are retained through separated vertex IDs.

`import-06` exited 0 after 175.87 s and saved the full World. `native-scene-03` exited 0 after 12.47 s with 21 checks: 1,365 unique meshes, 726 visual / 642 collision HISM groups, 35,922 visual / 20,033 collision placements, source/native totals 13,478,813 / 13,478,779 triangles, the two-UV opacity proof, RVT configuration, four height rays and original kiosk collision. The RVT configuration reads back the project pool entry matching DXT5/BC5/DXT5 and 264 px bordered pages, fixed at 256 MiB. Runtime evidence records the synchronized request rectangle coverage and two subsequent completed frames; screenshot review records the visible result.

- Manifest SHA256: `ac0afa8a40d90feee51bc53d02b41aa1b33c10b9f3d2a574b0e7d7be1f18c7d9`.
- Saved World SHA256: `720931dad262197929863d553c2388c3de18131277498749ef31faa201f12253`.
- Native scene summary: `artifacts/maps/wt-scene-20261118/native-scene-03/summary.json`.
- Raw-opacity adaptation: `artifacts/maps/wt-scene-20261118/decal-opacity-01/adapt/verification.json`.

The earlier `native-materials-06` baseline passed all 88 saved resources under D3D11 / NoShaderWorker (exit 0, 270.73 s), including 34 HeightDecal and all 43 ordinary tree instances. `flanders-contact-03-rvt` then passed five contact/return/reset checks and eight decoded PNGs (exit 0, 67.55 s) on this saved World. The observed rest view shows the ground obstruction removed and left foliage cutout gaps restored. Final reset-image and GUI appearance inspection are recorded separately. Wind, destruction, procedural landclass tree placement, original lighting/postprocessing, full source shader equivalence and native Ubuntu acceptance remain separate work. The grass supplement above has its own source-preparation and native-validation checkpoints. Source-restricted game resources remain local.
## 2026-11-19 surface detail and coverage revision

The `terrain-reflectance` contract restores 23 original reflection images in the same order as their albedo slices. The official CDK ground-decal export rule and its pinned Dagor `texExp` consumer jointly establish the cooked `_r` channels: R smoothness, G reflectance, B ambient occlusion, A microdetail ID. The adapter uses `1-R` for roughness and A for the categorical detail ID. G/B remain recorded source channels. Original PNGs, UVs, geometry and instance transforms are preserved.

Landclass lookup retains continuous derivatives for anisotropic `SampleGrad`, applies each source neighbour's self-color before weighting, and selects categorical detail IDs by the original maximum-neighbour/maximum-layer rules. Reflection and albedo share the same slice, rotation and final weights. The post-detail pass evaluates landclass and RVT floor/next detail contributions independently and blends their material effects by coverage and validity. The 18 height-decal polarity adaptations are explicitly bound to source IDs, diffuse/reflection names and PNG hashes; explicit source height parameters take priority.

`foliage-enhanced/source-alpha-mips/definition.json` binds 100 decoded original BC4 mip levels from 10 grass masks. UE imports linear R8 DDS with `LeaveExistingMips`; native verification exports the saved texture source and compares every mip's decoded pixel hash. The 0.5 cutout threshold for the three small-grass sources is derived from the original generator; the threshold for the seven random-grass sources is an explicit UE adaptation. Source geometry, placement weights, exclusion masks, density and scale are retained.

Layered Object normals use separate derivative frames for UV0 and UV2, source world-space RNM composition and an explicit conversion back to UE tangent space. Degenerate UV frames fall back to the geometric normal. Source color, opacity, texture coordinates and instance identity remain bound to the original assets.

The shared renderer now uses the effective UE5.8 anti-aliasing key and a full-resolution TAA default. Native authoring/reload identities, sampled frame times and visual evidence are recorded in [the surface-quality change record](../../../docs/changes/2026-11-19-flanders-visual.md). Source contracts are committed as byte-preserving JSON; original game images, DDS/FBX exports and UE-derived assets remain local and reproducible with the preparation/authoring scripts.
