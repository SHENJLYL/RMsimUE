---
doc_type: feature-acceptance
feature: 2026-11-13-vehicle-package-v1
status: passed
summary: Bounded vehicle-package tooling passed Windows tests and reproducible exports.
tags: [vehicles, urdf, acceptance]
date: 2026-11-13
user_review: not_requested_under_standing_authorization
---

# Vehicle package v1 acceptance

This accepts the offline importer slice only. Standing authorization is not a claim of individual user review.

## 1. Interface contract

load_vehicle and CLI implement vehicle.json v1 to canonical SI/right-handed links, joints, sensors, control and source. Both examples validate/export. Source paths use one shared package-root function. CLI returns 0 on success, 2 on input/filesystem failure.

## 2. Behavior and decisions

Packages are data only; no scripts execute. Paths, tree structure, inertial tensors and control/sensor references are validated before export. Atomic temporary-file replacement protects source manifest/URDF/assets, including in-package manifest symlinks.

Mount points: Python package entry, schema, sample assets and regression discovery. No Unreal/ROS runtime is imported by this tool. Removing these mounting points removes the feature without changing upstream assets.

## 3. Scenarios and evidence

Parent ran python tools/vehicles/verify_samples.py on native Windows 11 / Python 3.14.2. Exact evidence: artifacts/vehicles/verification.json.

| Check | Result |
|---|---|
| S1: two distinct samples | Pass: sentry_omni 7 links/6 joints; training_diff 5 links/4 joints; three sensors each |
| S2: deterministic canonical output | Pass: source hashes and repeatable structures tested |
| S3: malformed/unsafe packages | Pass: paths, graph, tensors, fields, DTDs and portable names checked |
| S4: source/failed output protection | Pass: atomic output and internal-manifest-link regressions |
| S5: CLI/current-host execution | Pass: parent re-executed suite and both validate/export commands |

Suite: 26 tests, 24 passed, 2 skipped. Skipped file-symlink cases require Windows privilege. A real directory-junction escape test passed; internal-manifest-link regression uses mocked resolution without that privilege. No Python 3.10 execution or native Ubuntu run is claimed.

## 4. Terminology

Vehicle package, canonical description and schema_version are consistent across code/schema/roadmap. Mesh metadata validation is distinct from runtime mesh loading.

## 5. Architecture integration

Current architecture records offline loader, CLI, schema, examples and evidence. UE controls/display remain a separate unaccepted feature.

## 6. Requirement update

extensible-worlds-vehicles retains broader draft status because engine imports/maps are incomplete. Offline tooling is its implemented subset, not complete fulfillment.

## 7. Roadmap update

vehicle-package-v1 is done for this slice. manual-aim-sentry is in progress under later user steering. Autonomous integration is pending.

## 8. Persistent notes

Shared resolve_package must remain authoritative for package-root interpretation. JSON/XML size limits apply before reading the whole file. Corrections are recorded in the change journal.

## 9. Remaining limits

No calibrated dynamics, simulator rendering, arbitrary URDF support, real camera/LiDAR model, full sentry algorithm, native Ubuntu or multiplayer is established by this acceptance.
