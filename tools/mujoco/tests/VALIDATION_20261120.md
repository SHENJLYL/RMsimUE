# Native MuJoCo 4.0.1 / RMsim static-world-flex .3 validation

Date: 2026-11-20. Scope: native C API dynamics/collision equivalence and CPU timing.
The model retains all 249 vehicle convex hulls, 14 static flexes, 1,608,500 map
triangles and 891,028 vertices. No solver setting, contact geometry or 1 ms
physical timestep was changed to obtain these results.

## Frozen identities

- Upstream tag 4.0.1 commit: `4ed093a9f06b170bd1564942fa37e1bb907d93e3`.
- Runtime: `4.0.1+rmsim.static-world-flex.3`; C API integer 4000001.
- Patch SHA256: `edfbc26d3fd5960e7bafafe1a26120f5538ba48ea3a6bb3d7ba53965f53d4839`.
- Official Windows DLL SHA256: `105a71108782b26fc9a088885f43044305197b2e89ce3a53914a5471be74df2e`.
- Custom Windows DLL SHA256: `544b51a2a79f5bd10341a977ddba7db22c8639db4f1c8b5d13081363af4019e4`.
- RMUC model SHA256: `db9b05391d880a9381fa975cbae51bb115283a787cd1d95b72d920b7cd326765`.
- Reference EXE SHA256: `95bb064a4d56f19e69b3627c30bc7aaa31bb8a3fb537444170a9b8de561bc7ab`.
- Profile EXE SHA256: `c4715a2c4b2757e739f4cdbdd71a365a0035363a5b2a67db713ffa0c0cdfbf53`.

The official and custom SDKs are separate directories under
`third_party/mujoco-sdk/`. Version .2 and its evidence were preserved.

## Equivalence

All commands exited zero, with the unchanged absolute gate 1e-12.
Every compared scalar was also bitwise equal.

| Run | Steps | Compared values | Bitwise differences | Accumulated contacts |
| --- | ---: | ---: | ---: | ---: |
| Windows nine fixtures | 9 × 2,000 | 695,764,792 | 0 | Per-case records |
| Windows frozen RMUC | 500 | 69,996,883 | 0 | 1,713 |
| Linux nine fixtures | 9 × 2,000 | 695,764,792 | 0 | Per-case records |

Full RMUC starts at the unmodified qpos0 and ctrl=0, including the model's
existing suspension/steering/wheel affine actuator biases. Source model hashes
before and after each command match. Contact identities, forces, qacc, state,
sensors and final derived geometry agree with stock MuJoCo.

Linux is a native C API cross-platform check; UE Ubuntu acceptance is separate.

## Residual collision bottleneck and exact fix

Version .2 had already cached static geometry and BVH updates. Official timer
profiling found a remaining broadphase scan in
`engine_collision_driver.c::hasAwakeDofs` calling
`engine_sleep.c::mj_sleepState(mjOBJ_FLEX)` for world-static flexes.

The version .2 Windows profile, with UE running concurrently, measured:

| Extra workers | Step mean (ms) | Broadphase mean (ms) | Midphase + narrowphase mean (ms) |
| ---: | ---: | ---: | ---: |
| 0 | 5.3709294 | 5.2079566 | 0.1000314 |
| 1 | 5.9985496 | 5.8253098 | 0.1049416 |
| 3 | 5.9135690 | 5.7393322 | 0.1076070 |
| 7 | 5.5681196 | 5.3983044 | 0.1091760 |

Threaded 1/3/7-worker runs each matched single-thread dynamics bitwise over
500 steps (69,996,883 values and 1,713 accumulated contacts per run).

Version .3 adds an O(1) guard using the existing strict eligibility metadata:
all vertex bodies are world, no interpolation nodes, no passive/deformation
mode, rigid dim-2 flex. The stock scan must return static for this set, so the
guard returns zero awake DOFs immediately. The candidate ordering, triangle
BVH tests, narrowphase and constraint solver remain unchanged.
Eligibility continues to use the versioned model metadata and existing
setConst / explicit invalidation lifecycle.

## Windows timing after .3

The UE process was stopped for the final measurements. The process snapshot
contains four resident MSBuild nodes (33236, 41048, 45112, 52832), with cumulative
CPU about 0.8–1.0 seconds each; no UnrealEditor, cl or link process was present.
Their interval CPU activity was not sampled.

100 warmup steps followed by 25 timed repetitions:

| API | Official 4.0.1 mean (ms) | Custom .3 mean (ms) |
| --- | ---: | ---: |
| mj_flex | 57.646012 | 0.000056 |
| mj_collision | 6.401696 | 0.037060 |
| mj_step | 61.414556 | 0.052096 |
| mj_step + mj_forward | 129.013224 | 0.112516 |

Both libraries finish at root z=-0.05934445790014979 with four contacts.

A separate zero-worker profile used 500 warmup steps and 3,000 measured steps:

| Metric | Observed value |
| --- | ---: |
| Step mean | 0.0810134 ms |
| Step p50 | 0.0510 ms |
| Step p95 | 0.0667 ms |
| Step p99 | 0.1678 ms |
| Step maximum | 59.9442 ms |
| Steps above the 1 ms physical timestep | 6 / 3,000 |
| Broadphase mean | 0.00623183333 ms |
| Midphase + narrowphase mean | 0.0605186 ms |
| Accumulated contacts | 12,000 |

The native mean and p99 have headroom for a 1 kHz physical timestep. The six
wall-time outliers are retained in the evidence; these measurements do not
establish a hard deadline for every Windows execution. Official timers use
wall time and include scheduling delays. UE's worker lag, catch-up behavior
and end-to-end sensor/frame synchronization require their own application run.

## Evidence

All paths below are relative to `D:\simulator\artifacts\integration\mujoco-ue-20261120\`:

- `native-reference-build-02/`: compiler environment and both EXE build logs, exit zero.
- `native-reference-win-rmsim3-01/verification.json`: all 12 .3 fixture, full model and benchmark records.
- `native-profile-win-rmsim3-01/verification.json`: timer breakdown, wall-time distribution and process snapshot.
- `native-profile-win-rmuc-01/verification.json`: version .2 broadphase diagnosis and 1/3/7-worker equivalence.
- `native-reference-win-rmuc-01/`, `native-reference-win-fixtures-01/`: earlier .2 validation.

Linux .3 regression record:
`/workspace/scratch/16a315187cad/static_flex_patch_test/equivalence-rmsim3.json`.
