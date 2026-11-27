# RMUC 2027 V1.0.0

- Original owner/source: DJI / RoboMaster, [official release](https://bbs.robomaster.com/article/1970956).
- Official STEP: `RMUC2027_V1.0.0.stp`, 653443279 bytes, SHA256 `a5ad3ede3b7674fc98f2e73b71717216383cb2b4b5dcdbe900c2038918bea8e0`.
- Conversion: [hxyulin/rm-map-tools release rm2027-assets-20261015](https://github.com/hxyulin/rm-map-tools/releases/tag/rm2027-assets-20261015), ZIP SHA256 `c9c3478189c3f417be6ab515b7f71e6b5f79b93f0d57992a1cd81bbc6ecef304`.
- `full-map.glb` SHA256 `09ca8e9d088e1945faf095cb90206fe330bd97ab6f57bd3fb2cf3b9645ed7b61`; its declared source STEP hash agrees with the independently downloaded official STEP.
- [UPSTREAM-NOTICE.md](UPSTREAM-NOTICE.md) retains ownership/terms. Tooling licenses do not relicense DJI geometry. This local derivative is not an official DJI simulator.

Preparation: tools/maps/prepare_rmuc2027.py reads the GLB's declared Z-up coordinates directly and exports 14 visual/collision FBX pairs. Internal translation: `[-0.000007152952372635468, -1.6243433957331543, 1.6403436655816601]` m. The central floor crest becomes Z=0; this is not a surveyed referee frame. UE mirrors Y and converts m to cm once at import. Current exterior bounds are approximately 28 × 16.003132 × 3.798683 m; visual mesh has 1,839,741 triangles.

The geometric markings come from the source CAD. Four blue-side markings use the existing blue road material, and the arena uses CAD colors for its other static geometry. Mechanical assemblies remain in static source rest poses. default/red are authored preview spawns, not certified competition starting positions. Regions, referee mechanisms and field electronics are not implemented.

map.json references /Game/RMSim/Maps/rmuc2027. mesh-source.json is the portable reimport descriptor. Original downloads/release reports are in artifacts/sources/rmuc2027; runtime evidence is in artifacts/maps/rmuc2027.
