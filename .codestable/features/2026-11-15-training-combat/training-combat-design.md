---
doc_type: feature-design
feature: 2026-11-15-training-combat
status: approved
summary: Add playable manual training combat while original-map conversion continues in parallel.
tags: [gameplay, projectile, training, validation]
authorization: User asked to prioritize simulation functionality while processing maps; standing implementation authorization.
user_review: not_requested_under_standing_authorization
---

# Manual training combat

## 0. Scope

Left mouse fires from the actual rig's aim/muzzle preview transform. Swept ballistic projectiles collide with map or target geometry; only armor hits reduce target health. A training round owns ammo, heat/cooling, cadence, target HP, score, time and reset. These explicit demo values are not official RMUL/RMUC rule certification or calibrated ballistics. The local authoritative runtime owns state; networking/ROS interfaces remain separate work.

## 1. Flow and ownership

Existing common ApplyManualInput → ATrainingRange Step (simulation time, trigger/reset) → ballistic swept segments → collision hit → armor damage/disable/round victory. All projectiles are removed at round end/reset; a new epoch prevents stale events. Focus loss stops firing while time/cooling/projectiles continue. R resets the range, not vehicle movement. Explicit TrainingMode opt-in keeps the original driving checks reproducible.

TrainingRange.cpp/h owns gameplay state and visual targets/projectiles. TrainingProbe uses real scene queries and the same input path; no test-only hit counters. HUD reports training status and bindings. Config uses DefaultGame.ini; changed values are recorded in runtime evidence.

## 2. Acceptance

- Build and actually play the existing vehicle with range targets in Windows.
- Actual shared-input shot consumes ammo/heats once; cooldown, overheat and empty magazine reject further shots.
- Swept projectiles hit real armor, damage exactly once and disable at zero HP; geometry occlusion blocks damage.
- Round victory/timeout locks firing; reset clears projectiles, heat and scores, restores target HP and advances epoch.
- Focus loss doesn't fire. Existing movement/aim remains passing.
- Native result JSON and screenshots prove above behavior; record limits (static targets, simple gravity, no complete referee/network/ROS closure).

## 3. Validation order

Compile → independent combat mechanics probe using runtime state/queries → common input and aim probe → visual review → document. Map workers continue preparing Flanders concurrently; do not label that scene complete until it runs.
