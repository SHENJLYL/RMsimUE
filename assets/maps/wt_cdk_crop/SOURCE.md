# War Thunder CDK terrain crop

This is a 512 × 512 m terrain-only crop of the official CDK location_sample, not an extraction of a complete shipped battle map. Original game/CDK files were read only.

- Installed source: `D:/steamlaptop/steamapps/common/War Thunder/WarThunderCDK/levels/location_sample/heightmapLand/heightmap.r32.zst`.
- Compressed SHA256: `eeca156ec0cc64f732d915bdce88657132c2b3ff3c99e62e2016f2f1887e9a58`.
- heightmapLand.plugin.blk SHA256: `9aad966540bb1080d0d4f5fd5e0a9d4e418e8e11175118275a21334c16f685fe`.
- Source: 2048 × 2048 float32 samples, 16 m spacing, height scale 1314.41 and offset -784.413 m.
- Crop: pixel center (1120,1120), 16 cells each direction, 33 × 33 vertices / 2048 triangles. Center height 266.53338282597065 m is subtracted; local range is 0..20.457744598388672 m.
- Frame: X follows columns, Y follows rows, Z is height; no geographic or official game-frame claim.

default spawns near the center; hillside uses an exact triangle sample at (136,100,5.505534) m, approximately 5.21° slope, tested in UE.

No buildings, vegetation, roads, ground-layer textures or original lighting have been extracted. The plain preview material is authored here. Source content remains Gaijin/War Thunder content; no general redistribution rights are asserted. [Official CDK location documentation](https://wiki.warthunder.com/cdk/creation_location).

Small-file shipped candidates avg_football_field.bin and avg_training_ground.bin have DBLD3x64 headers. Byte sizes are not map dimensions. No verified geometry converter for those files is implemented; see artifacts/maps/wt_cdk_crop/compiled-map-inspection.json.

Reproduction: Python 3.14+ tools/maps/extract_wt_cdk_heightfield.py → Blender tools/maps/prepare_heightfield_mesh.py → common UE importer. Generated height.f32, FBX, JSON and UE assets support offline rebuild; the installed game is not a runtime dependency.
