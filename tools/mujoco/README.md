# AIM / RMUC MuJoCo model preparation

This directory prepares the source-bound MuJoCo model consumed by the UE native
backend. UE retains the existing visuals, materials, input and sensor presentation.
The model uses SI units in the ROS right-handed frame: X forward, Y left, Z up.

## Prepare the complete field

Use the isolated MuJoCo 4.0.1 environment created by the repository SDK setup.
The commands below use the current Windows workspace:

```powershell
$MujocoPython = 'D:\simulator\artifacts\environments\mujoco-4.0.1\Scripts\python.exe'
& $MujocoPython D:\simulator\tools\mujoco\prepare_model.py --output D:\simulator\assets\vehicles\aim_sentry\mujoco\rmuc2027
```

The output directory must be new. Archive an existing package before regeneration.
The generator writes a failure manifest if preparation fails; launch preflight
accepts only completed packages. Generated XML, geometry and runtime reports are
local derived assets and are excluded from source control.

When the original geometry cache already exists, reuse it with:

```powershell
& $MujocoPython D:\simulator\tools\mujoco\prepare_model.py --output D:\simulator\assets\vehicles\aim_sentry\mujoco\rmuc2027 --geometry-directory D:\simulator\artifacts\integration\mujoco-ue-20261120\geometry-source-01
```

Cache reuse checks the raw collision GLB, source coordinate metadata, complete
14-object coverage, each payload hash and counts. Unverified planar candidates
are rejected. All referenced vehicle meshes and terrain vertices/elements are
inline in model.xml, so runtime packages have no external MJCF mesh dependency.

## Verify before native launch

```powershell
& $MujocoPython D:\simulator\tools\mujoco\verify_model.py --manifest D:\simulator\assets\vehicles\aim_sentry\mujoco\rmuc2027\manifest.json --preflight-only --output D:\simulator\artifacts\integration\mujoco-ue-20261120\preflight-next
```

Use a fresh evidence directory each time, or omit --output for JSON on stdout.
Exit code 0 means preflight passed; exit code 1 includes a concrete failure.
Add --map-file for a selected map declaration; its arena-shell.json must be
beside it. The selected map must describe the same physical RMUC scene.

Preflight verifies the XML, current vehicle rig, independent drive profile,
backend configuration, all 15 vehicle and 14 map collision assets, original
vehicle convex exports, original RMUC collision GLB, source coordinate metadata,
geometry payloads and generator code. SHA256 records provenance; native runtime
bindings additionally carry MD5 and byte counts for UE FMD5 integration.

The physical scene identity includes the source STEP identity, collision asset
set and active perimeter dimensions. Map spawn/display fields and shell
lighting/presentation may change without regeneration. Collision geometry,
vehicle physics, units, coordinate convention and boundary changes require a
fresh model. The raw map/shell hashes remain historical provenance.

For another compile/initialization check, replace --preflight-only with --compile.
A successful preflight or compilation does not record runtime motion acceptance.

## Explicit flat-plane diagnostic

```powershell
& $MujocoPython D:\simulator\tools\mujoco\prepare_model.py --output D:\simulator\artifacts\integration\mujoco-ue-20261120\vehicle-diagnostic-next --vehicle-only
& $MujocoPython D:\simulator\tools\mujoco\verify_model.py --manifest D:\simulator\artifacts\integration\mujoco-ue-20261120\vehicle-diagnostic-next\manifest.json --allow-diagnostic --diagnostic-motion --output D:\simulator\artifacts\integration\mujoco-ue-20261120\vehicle-diagnostic-motion-next
```

The diagnostic retains the complete AIM vehicle, replaces the arena with a
declared flat plane, and requires explicit opt-in. Its 5 s maneuver settles,
accelerates to 0.5 m/s, and stops using 5,000 actual mj_step calls with no pose
overrides. It validates wheel propulsion and stopping on that plane. Native
full-field motion, terrain traversal, timing, sensor synchronization and UI
acceptance are recorded separately by the UE integration workflow.

## Vehicle contract

| Item | Model |
|---|---|
| Body names | The 15 current ue_mesh_rig.json part names |
| Root joint | chassis_free |
| Articulated joints | j_<part> |
| Wheel / steering / gimbal actuators | a_<part> |
| Passive suspension actuators | a_spring_<suspension_part> |
| Integration | 0.001 s, implicitfast, Newton, elliptic friction cone |
| UE scheduling | Native backend performs five real steps per 0.005 s frame |
| Contact | solref 0.002 1; solimp 0.99 0.999 0.0001 0.5 2 |
| Fixed terrain shell radius | 0.0001 m |
| Wheel geometry | Original convex hulls; radius 0.0425 m; source axis [0,1,0] |
| Suspension | 2,000 N/m, 140 N s/m, 50 N preload, 0.065 m total travel |
| Spring force law | -50 - 2000*q - 140*qdot, clamped to +/-1,000 N |
| Total configured mass | 25.4 kg |
| Original upper assembly mass | 20 kg split into body 19.8 kg, yaw 0.1 kg, pitch 0.1 kg |

The independent drive-profile.json is authoritative. The older embedded profile
snapshot in ue_mesh_rig.json is not used for dynamics parameters. Wheel and
steering limits remain those in the current independent profile; the new
backend.json records the initial gimbal motor settings.

Inertias come from the original homogeneous convex assemblies, rescaled to
configured link masses. The source model has no measured link inertias; both
backend and manifest preserve calibrated=false. The yaw/pitch split preserves
the existing total configured mass.

All 249 existing native vehicle convex hulls are reused, identified by the
SHA256 of the current collision.uasset and the matching previous native convex
export. No vehicle collision asset or visual model is regenerated.

## Complete RMUC collision geometry

The original source STEP identity is
a5ad3ede3b7674fc98f2e73b71717216383cb2b4b5dcdbe900c2038918bea8e0.
geometry.py reads the same full-map-collision.glb and mesh-source.json frame
used by the existing importer, applies source transforms, and reproduces the
float32 export coordinates. Cleanup removes exact zero-area faces and duplicate
faces and merges exact-coordinate duplicate vertices. No tolerance-based weld,
decimation or arena-wide convex hull is used.

The accepted field contains 14 world-attached rigid dim-2 flexes with
891,028 vertices and 1,608,500 triangles, plus four perimeter boxes whose inner
faces follow the active native platform bounds. This retains source ramps,
waves, openings and concavities.

The unmodified MuJoCo 4.0.1 solver updates all static-flex geometry every step;
the upstream performance investigation found this expensive at million-triangle
scale. The native SDK integration owns its validated static-world-flex cache
optimization and full-field timing acceptance. Geometry preparation preserves
the complete field independently of that cache.

Three geometry experiments are retained as audit tools:
simplify_static_geometry.py (Blender planar dissolution),
split_convex_geometry.py (strict closed convex components), and
retriangulate_planar.py (planar union with holes and constrained triangulation).
Their candidates were not selected for the formal field: dissolution changed
the surface beyond the 1 micrometre acceptance gate, convex splitting left
1,608,036 residual triangles, and strict planar union reduced very few triangles.
Failed and pending candidates remain in integration artifacts for reproducibility.

## Current recorded checks

The frozen model package was generated on 2026-11-20 with MuJoCo 4.0.1.
It compiles to nq=21, nv=20, nbody=16, njnt=15, nu=14, nmesh=249,
ngeom=253, nflex=14 and total mass 25.4 kg.

The reports under artifacts/integration/mujoco-ue-20261120 include:

- model-preflight-02/report.json: 33 runtime bindings and 35 source files passed.
- vehicle-model-03-motion/report.json: 5,000 real steps, zero pose overrides and
  zero MuJoCo warnings; forward speed 0.495895 m/s for a 0.5 m/s command,
  final speed 0.000466 m/s after stopping.
- model-contract-tests-01/report.json: all 11 allowed-change and stale-input cases passed.
- static-scene-audit-01/report.json: all 891,028 vertices and 1,608,500 triangle
  indices equal the source cache exactly; four current boundary boxes match.
- geometry-coplanar-probe-01: rejected surface-change evidence.
- geometry-convex-01 and geometry-planar-candidate-01: unselected geometry trials.

These records establish preparation and the explicit vehicle diagnostic scope.
The parent integration reports hold the actual UE/native runtime evidence.

The read-only scene audit can be reproduced with verify_static_scene.py using
--manifest and a fresh --output directory. It also checks the reviewed native
collider-construction code against native-static-review.json; a changed source
requires another explicit code review before that audit can pass. This review
gate is separate from ordinary launch preflight.
