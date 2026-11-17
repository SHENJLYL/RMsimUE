---
doc_type: feature-design
feature: 2026-11-15-source-mesh-maps
status: approved
summary: Use the DJI RMUC2027 source-derived field as the first map, and a read-only War Thunder CDK terrain crop as a second map.
tags: [maps, unreal, terrain, provenance]
authorization: Explicit user request for RMUC2027 first and a small War Thunder map attempt; standing implementation authorization.
user_review: not_requested_under_standing_authorization
---

# Source mesh maps

## 0. Scope and terms

Mesh map: paired visual/triangle collision assets with source hashes, internal ROS frame and checked spawns. Terrain follower: bounded kinematic wheel-support queries, not calibrated suspension or tire dynamics. The first map uses DJI RMUC2027 V1.0.0 CAD-derived geometry; no referee rule or official surface-artwork claim. The secondary map is a 512 m crop of the locally installed official War Thunder CDK location_sample heightfield; compiled shipped maps have no verified converter yet.

Windows native runtime is the current acceptance target. Ubuntu receives portable source/scripts and an offline handoff, not a claimed native runtime pass. Source asset directories remain read-only.

## 1. Implementation

- tools/maps: source verification and visual/collision FBX preparation; assets/maps/{id}: reusable packages; artifacts/sources: downloaded immutable sources; artifacts/maps: logs/captures/checks.
- tools/unreal/import_mesh_map.py imports source-color PBR materials and complex-as-simple collision; no substitute blocking boxes over the field.
- MapStage/MeshMap loads rm_mesh_map through the existing MapFile seam. VehicleTerrain adds wheel support, bounded slope/step acceptance, collision checks and fail-closed missing-ground handling to MoveManual. Flat-map movement remains available for existing checks.
- GameMode enables terrain following only for mesh maps, snaps after collision bodies are ready, and computes gimbal angles in chassis coordinates on tilted ground.
- Native terrain checks exercise the same controller and actual collision scene, preserve start/end poses, support samples and screenshots. Set RMUC2027 as the default only after successful runtime proof.

## 2. Acceptance

1. Source provenance/hashes and coordinate conversion are recorded; original inputs are unchanged.
2. RMUC2027 renders its real field geometry and the vehicle spawns without overlap on supported terrain.
3. Shared manual input moves/turns/brakes, updates height/orientation on slopes, and rejects unsupported terrain or blocked motion. Actual wheel support and collision evidence is saved.
4. WT CDK crop loads with the same runtime without rebuilding and has its own honest scope label.
5. Existing flat-map controls remain passing; Windows default and optional map launch paths work.

## 3. Stop condition

Deliver the two concrete map samples, first-map launcher, evidence and current records. Full competition mechanisms, original game-map objects/textures, physical calibration, ROS sensor closure and multiplayer stay tracked independently. Do not claim the overall long-term goal complete.
