# MuJoCo 4.0.1 world-fixed flex cache

Upstream: `google-deepmind/mujoco`, tag `4.0.1`, commit
`4ed093a9f06b170bd1564942fa37e1bb907d93e3`.

Runtime identifier: `4.0.1+rmsim.static-world-flex.3`.
Canonical patch: `third_party/patches/mujoco/mujoco_static_world_flex_4.0.1-rmsim3.patch`.
Patch SHA256: `edfbc26d3fd5960e7bafafe1a26120f5538ba48ea3a6bb3d7ba53965f53d4839`.
The integer API version remains `4000001`.
This is a separately built RMsim runtime, distributed beside the official SDK.

## Scope

Cache only non-interpolated 2D rigid flexes whose **every vertex body ID is 0**,
with no passive IPC mode or plastic mode. Moving rigid flexes, flexes attached
to mocap or non-world fixed bodies, and deformable flexes keep the stock update
path. Mesh topology, vertex coordinates, triangle collision, contact generation,
constraint assembly, integration, and solver code remain unchanged.

The public `mjModel`/`mjData` structs, their array layouts, and MJB format are
unchanged. Cache metadata lives in an aligned private tail of each engine-owned
model/data **buffer**. This also supports the stock Python recompile path that
allocates exactly `sizeof(mjModel)` externally and lets the engine allocate its
buffer. All function pointers and models/data must belong to the same runtime
library, as in the integration's single-runtime ownership design.

## Work removed from steady-state steps

1. Re-transforming immutable world vertices.
2. Recomputing every triangle AABB and every dynamic BVH node.
3. Scanning world vertices when computing broadphase center/covariance.
4. Projecting every world vertex into the changing SAP frame.
5. Clearing all world-fixed edge velocities on every velocity stage. They are
   initialized to zero on every cache refresh and remain identically zero.
6. Repeated static-world vertex scans in broadphase's `hasAwakeDofs` through
   `mj_sleepState(mjOBJ_FLEX)`. Version .3 returns zero awake DOFs directly for
   the existing strictly world-fixed eligibility set.

The original `mj_flexSkip(..., 1)` skipped rigid edge Jacobians, but retained
items 1–4. The original `mj_fwdVelocity` cleared every flex edge velocity even
when every edge was rigid and its velocity was zero. Version .2 retained item 6;
profiling the complete RMUC vehicle identified it as the residual bottleneck.
The .3 guard is equivalent to the original sleep-state scan: every vertex body
is world, and eligibility excludes interpolation nodes and deformation modes.
The world body has no dynamic tree, so the scan must return `mjS_STATIC`.

## Lifecycle and mutation contract

| Event | Cache action |
|---|---|
| Allocation / first forward | Private data cache starts invalid; normal full geometry update runs. |
| `mj_resetData`, debug reset, keyframe reset | Invalidate before buffer initialization. |
| `mj_copyData` / visualization copy | Invalidate destination; normal next forward reconstructs cache. |
| `mj_setState` | Invalidate; subsequent forward reconstructs geometry. |
| `mj_setConstGroups` / `mj_setConst` | Re-evaluate eligibility and increment model revision; every live `mjData` detects mismatch on its next update. |
| Model copy, including reuse of destination object | Rebuild destination eligibility and increment its own revision. |
| MJB load | Initialize private metadata from loaded public model arrays. |
| Recompile | New engine-owned buffers get fresh metadata; standard recompile state restoration applies. |
| Midphase re-enabled after first forward with it disabled | Rebuild BVH before using it. |
| Direct static geometry edits | Call `mj_setConst`, or `mjrmsim_invalidateStaticFlex(m)` when only the cached geometry changed. |

Direct writes through raw model pointers cannot be detected in constant time.
The runtime requires the explicit invalidation contract above. Structural edits
still require the existing MuJoCo recompile API. Pause all stepping workers
before modifying a shared model. Derived `mjData` arrays are engine outputs;
external code must not overwrite cached derived geometry or edge velocities.

No cache flag shares storage with Hessian validity, time, solver state, or other
unrelated fields. No process-wide pointer registry or global lock is introduced.

## Broadphase enclosure proof

The unchanged BVH root encloses the union of all triangle AABBs, each padded by
the flex collision radius. Let its world center be `c`, half size `h`, and one
SAP frame row be `a`. Every enclosed point has projected coordinate within

`dot(a, c) ± sum(abs(a[k]) * h[k])`.

The patch adds the current contact margin to those endpoints. Rotating this
axis-aligned enclosing box may enlarge the broadphase interval. It cannot
exclude any triangle-radius contact supported by the original intervals.
An outward roundoff allowance uses the absolute dot-product terms to include
cancellation at large coordinates. The original triangle BVH and narrowphase
reject added candidates. Contact ordering remains the existing sorted-pair
ordering; this is checked by the native trajectory comparison.

## Native validation

`tools/mujoco/tests/native_compare.cpp` dynamically loads both libraries in one
process, keeping their objects and function tables separate. It compares state,
warm starts, constraint forces, body/geom poses, sensors, flex-derived geometry,
edge velocities, and contact identity/position/frame/friction/solver parameters.
It rejects runtime warnings and records absolute and bitwise differences.

Cases cover a concave channel, raised wings, an open hole, sphere and convex
mesh probes, a rigid flex attached to a moving body, a dense planar grid, and
multiple bodies that change the SAP frame. Forces and yaw torque vary over
time. The 2,000-step lifecycle sequence includes debug reset/state restore,
data copy, model updates via a different live data object, in-place model copy,
MJB save/load, midphase toggles, explicit invalidation, and Python-style
external-struct recompile allocation.

### Final .3 results, 2026-11-20

All commands exited zero with the unchanged absolute comparison gate 1e-12.
Every compared scalar was also bitwise equal.

| Run | Steps | Compared values | Bitwise differences |
|---|---:|---:|---:|
| Windows nine fixtures | 9 × 2,000 | 695,764,792 | 0 |
| Windows complete RMUC model | 500 | 69,996,883 | 0 |
| Linux nine fixtures | 9 × 2,000 | 695,764,792 | 0 |

The complete RMUC model retains 249 vehicle convex hulls, 14 static flexes,
1,608,500 triangles and 891,028 vertices. Its SHA256 is
`db9b05391d880a9381fa975cbae51bb115283a787cd1d95b72d920b7cd326765`.
Source hashes match before and after testing. The full model comparison uses
unchanged qpos0 and zero ctrl, preserving existing affine actuator biases.
Dynamics, contacts and sensors are compared every step; immutable large
geometry arrays are compared at the initial and final states. It accumulated
1,713 contacts over 500 physical 1 ms steps.

Official Windows DLL SHA256:
`105a71108782b26fc9a088885f43044305197b2e89ce3a53914a5471be74df2e`.

Custom .3 Windows DLL SHA256:
`544b51a2a79f5bd10341a977ddba7db22c8639db4f1c8b5d13081363af4019e4`.

Linux validation establishes native C API equivalence on that platform;
UE Ubuntu integration requires its own application validation.

### Measured residual broadphase cost

Version .2 profiling of the complete vehicle, with UE running concurrently,
measured step mean 5.3709294 ms, broadphase 5.2079566 ms, and combined BVH
midphase plus narrowphase 0.1000314 ms. Official `mjTIMER_COL_NARROW` includes
both midphase and narrowphase; these figures do not separate them.

Official `mju_threadpool` configurations with 1, 3 and 7 extra workers did not
reduce this cost. Each configuration matched single-thread dynamics bitwise
over 500 steps (69,996,883 compared values and 1,713 accumulated contacts).
Production therefore retains zero extra workers. The .3 constant-time awake-DOF
guard removes the repeated scans without changing collision geometry, candidate
ordering, narrowphase or solver settings.

### Complete-model Windows performance

After stopping UE, a same-round comparison used 100 warmup steps and 25 timed
repetitions. These are per-call means.

| API | Official 4.0.1 | Custom .3 |
|---|---:|---:|
| `mj_flex` | 57.646012 ms | 0.000056 ms |
| `mj_collision` | 6.401696 ms | 0.037060 ms |
| `mj_step` | 61.414556 ms | 0.052096 ms |
| `mj_step + mj_forward` | 129.013224 ms | 0.112516 ms |

Both finish with four contacts and root z=-0.05934445790014979.

A separate official-timer profile used 500 warmup steps and 3,000 measured
steps, a 1 ms physical timestep, and no extra workers:

| Metric | Observed |
|---|---:|
| Step mean | 0.0810134 ms |
| p50 | 0.0510 ms |
| p95 | 0.0667 ms |
| p99 | 0.1678 ms |
| Maximum | 59.9442 ms |
| Steps exceeding 1 ms | 6 / 3,000 |
| Broadphase mean | 0.00623183333 ms |
| Midphase + narrowphase mean | 0.0605186 ms |
| Accumulated contacts | 12,000 |

The mean and p99 have compute headroom for a 1 kHz physical timestep. Six
wall-time overruns, including the 59.9442 ms maximum, are retained. Official
timers use wall time and include OS scheduling delays; this establishes native
throughput and observed tails, not a hard deadline for every Windows step.
UE worker/frame lag, catch-up and sensor synchronization have separate
application-level acceptance.

The profile's initial process snapshot contains no UnrealEditor, cl or link,
and four resident MSBuild nodes (33236, 41048, 45112, 52832), with cumulative CPU
about 0.8–1.0 seconds each. Their interval CPU activity was not sampled.
The record consequently does not assert a completely idle operating system.

Compilation and first forward/cache refresh still process all source triangles.
The full RMUC model reserves 256M arena memory. The synthetic 1.62-million-
triangle fixture uses 128M because stock BVH traversal reserves stack memory
proportional to node count.

### Reproducible evidence

Under `artifacts/integration/mujoco-ue-20261120/`:

- `native-reference-win-rmsim3-01/verification.json`: all 12 final Windows
  fixture/full-model/benchmark commands, hashes, exit codes and numerical results.
- `native-profile-win-rmsim3-01/verification.json`: timers, percentiles, all
  observed overruns and the process snapshot.
- `native-profile-win-rmuc-01/verification.json`: .2 broadphase diagnosis and
  threadpool equivalence.
- `native-reference-build-02/`: native reference/profile compiler logs and
  executables, both builds exited zero.

`tools/mujoco/tests/README.md` contains the runners' invocation examples.
`tools/mujoco/tests/VALIDATION_20261120.md` records the frozen identities and
all results in a compact review record. Earlier .2 evidence is retained.

## Building and review

`tools/mujoco/Build-MujocoRuntime.ps1` pins the upstream commit and final .3
patch SHA256. It applies the patch to an isolated source checkout, validates
tracked and untracked source against a temporary index, and installs into a
separate empty prefix. The official SDK and earlier .2 SDK remain available.

For this Windows build, the actual toolchain was Visual Studio 18.4 / 2026,
MSVC 19.50 (toolset 14.50.35717), and CMake 4.2.3; the script was called with
`-Generator 'Visual Studio 18 2026'`. Its default generator remains
`Visual Studio 17 2022` for hosts using that release.

Studio, Filament, simulate, examples, USD and tests are disabled for the
native C API library build. Upstream pinned native dependencies are used.
Private cache alignment uses the maximum alignment of its actual model/data
cache structs, with `__alignof` on MSVC and `_Alignof` elsewhere. This avoids
the MSVC C-mode dependency on `max_align_t` encountered in the earlier attempt.

The final independent Windows installation is
`third_party/mujoco-sdk/4.0.1-rmsim-static-world-flex.3/windows-x86_64/`.
`rmsim-build-manifest.json` records the source commit, patch identity and library
hashes. The runtime loader requires `mj_version()==4000001` and the exact .3
runtime string before enabling the full static-flex model.

Expose only the SDK's `mujoco/` C API header tree through `c-api/include`.
The full SDK's renderer header `math/quat.h` conflicts with UE
`Math/Quat.h` on case-insensitive Windows filesystems.
