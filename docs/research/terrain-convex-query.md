# Convex-query and wheel-support investigation

Initial read-only API/source investigation2026-11-15 was followed by the coordinator-authorized11-part native-contact preparation below. Current geometry has actually been imported and collision copies cooked; runtime contact behavior remains a separate verification.

## Current failure mechanism

VehicleTerrain.GetPartColliders still substitutes each complete visual part with its local bounding box. Narrow sloped surfaces and tyre corners can hit empty parts of those boxes. MoveOnTerrain additionally inflates every axis of the midpoint box by an angular bound; this can turn a valid tyre contact into a false floor hit. Convex proxies remove that particular representation error. They do not fix the independent ground-support model: four vertical wheel-centre rays plus a least-squares plane are not a finite-radius tyre contact or suspension solve.

## Installed UE5.8 source evidence

- `Engine/Source/Runtime/Engine/Classes/Engine/World.h:2456`: ComponentSweepMulti. The comment explicitly says rotation stays constant throughout a sweep. ByChannel overload at2470.
- `Engine/Source/Runtime/Engine/Private/Collision/WorldCollision.cpp:485`: ComponentSweepMultiByChannel iterates the component physics objects/shapes; non-convex geometry is skipped at the shape loop. A ComplexAsSimple triangle-mesh vehicle can therefore return no contacts and must not be treated as a valid movable collider.
- `Engine/Source/Runtime/Engine/Private/PhysicsEngine/BodyInstance.cpp:4382`: OverlapMulti requires a valid physics actor. It also skips non-convex shapes; each convex's geometry is queried at the supplied test pose.
- `Engine/Source/Runtime/Engine/Private/Components/PrimitiveComponent.cpp:2131`: registered component plus collision enabled (or explicit always-create flag) governs physics state creation. The existing NoCollision visual alone is not a reliable query shape source.
- `Engine/Source/Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h:310`: Blueprint/Python SetConvexDecompositionCollisions(mesh,HullCount,MaxHullVerts,HullPrecision). Implementation1473+ removes previous simple geometry, invokes GenerateConvexCollision, refreshes collision state and PostEditChange. GenerateConvexCollision at58 reads LOD0 collision-enabled sections and calls DecomposeMeshToHulls.
- `Engine/Source/Runtime/Engine/Private/PhysicsEngine/BodySetup.cpp:1604`: setting AggGeom through a property does not itself invalidate/cook convex geometry; it only recreates already-existing component states. An exact custom hull should use a small C++ editor helper calling InvalidatePhysicsData/CreatePhysicsMeshes explicitly.

## Minimal implementation recommendation

1. Generate independent copies under `/Game/RMSim/Collision/aim_sentry/`; source hash in the derived asset name prevents reusing old-scale geometry. Keep visual imported assets unchanged.
2. Body/yaw/pitch: first evaluate VHACD16 hulls,64 vertices,100000 precision. Those are an initial approximation budget, not a verified accuracy setting. Save each hull's vertices and the source/hull bounds excess; evaluate tight ground clearance and actual obstacle probes before accepting.
3. Tyres: one32-sided convex cylinder per wheel, radius from rig(current0.0425m), explicit width0.044m along local Y. The polygon's maximum radial deficit is about0.205mm, which should be recorded. It avoids a square tyre's false corner collision; omni-roller traction is still not modeled.
4. Runtime: create dedicated hidden UStaticMeshComponents for these assets, attach body/gimbal proxies to the matching links, use Movable+QueryOnly+BlockAll, disable overlap events, and wait for IsPhysicsStateCreated + valid BodyInstance before queries. Do not enable physics simulation just to obtain query geometry. Ignore the entire vehicle actor in queries. Tyre proxies attach in their reference axle frame, not the visually spinning mesh frame.
5. Use World.ComponentOverlapMultiByChannel and ComponentSweepMultiByChannel with FComponentQueryParams, collision channel Pawn and self ignored. Pass each part's origin/rotation at the candidate pose; component scale is already represented in its cooked/scaled physics geometry. Check at least one real convex shape exists before accepting a false/no-hit result. Record actual blocking actor/part/shape and impact normal.
6. Constant-rotation sweeps do not provide continuous rotational CCD. Subdivide by maximum surface-point travel (lever arm × angle), inspect start/mid/end rotations, and do translational convex sweeps at those orientations. This is a bounded approximation; do not silently claim an exact swept rotation. Avoid the current arbitrary all-axis box inflation which causes the wave-road false contacts.

## Ground support

Replace the centre-line contact ray with a downward sphere sweep at the actual tyre radius (or an axle-aligned capsule only after verifying tyre-width semantics). `FHitResult.Location` gives the swept wheel-centre position at contact; `ImpactPoint` is the surface contact and can be horizontally offset at a wave crest/edge. Fitting the support geometry must use the swept-centre positions with the wheel's local centre offsets, not subtract radius a second time.

Read all hits within allowed suspension travel, reject wall-like support normals, and record contact/drop state. A rigid four-wheel body can have only three supporting wheels over a crest; requiring all four to match one plane is a separate traversal limit. If the intended result is physical suspension and traction, use Chaos rigid-body state plus explicit wheel contact/spring/damper/drive forces. Convex-query movement alone remains kinematic and must be labeled accordingly. Neither gap inflation nor raising the5cm step threshold is a proof that the tyre can climb the obstacle.

## Offline preparation interface

`prepare_vehicle_collision.py` accepts CollisionRig/CollisionOutput and optional CollisionPart=body for the initial probe. It writes a mapping/artifact only and never changes ue_mesh_rig.json. Body/gimbal use editor convex decomposition. The wheel path requires `MapImportLibrary.set_convex_collision(mesh,vertices_cm)`, whose actual Python success return is the empty Error string and failure is None, plus `describe_convex_collision` for exact cooked-hull inspection. Missing helpers fail explicitly. Runtime validation remains false until the coordinator tests actual contact physics on the wave road.

## Actual11-part preparation

Native Windows UE5.8.4 visual import completed exit0 for body/yaw/pitch/four suspension carriers/four wheels. Convex preparation05 completed exit0, then the coordinator-authorized metadata mapping was applied only after matching original rig hash, source FBX identities, actual visual `.uasset` hashes and actual collider `.uasset` hashes. Backup and mapping proof are retained under `artifacts/assets/contact-physics`.

- `convex-preparation-01.log`: StaticMeshEditorSubsystem was absent in the bare commandlet. Explicit module load and constructing the subsystem supplied the actual offline API.
- `convex-preparation-02/03`: FKConvexElem.VertexData exists but is protected from Python. Native DescribeConvexCollision now returns actual transformed hull vertices/bounds, trace flag and primitive count as JSON.
- `convex-preparation-04`: exact wheel cooking succeeded but the script initially expected a tuple. UE treats bool-success plus one output as optional output, so successful SetConvexCollision returns an empty Error string; failed calls return None. Corrected and verified using native hull count/dimensions.
- `convex-preparation-05.json`: body16hulls, yaw16, pitch9, carriers5/13/14/11; each wheel1cylinder/64vertices. Original visual asset hashes were unchanged during collision preparation. All copies use SimpleAndComplex, enabling native convex dynamics.
- `rig-convex-mapping-05.json`:11/11parts mapped. `import_vehicle_assets.py` retains this metadata on later reimport only when both source FBX identity and saved collision asset hash agree. Geometry changes drop the mapping visibly.

The100k-precision VHACD result is approximate: body bounds extend up to0.477cm beyond visual bounds; yaw0.686cm, pitch0.316cm, carriers0.235..0.595cm. Body minimum collision Z is3.4989cm versus source3.9621cm, losing about4.6mm of clearance. This is recorded rather than hidden. If the actual wave-road test remains limited by these approximations, increase preparation precision/quality and remeasure instead of enlarging a fictitious floor gap. Wheel32-gon dimensions agree with radius4.25cm/width4.4cm to the checked0.001cm tolerance.
