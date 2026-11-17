---
doc_type: feature-design
feature: 2026-11-15-wt-existing-map
status: approved
summary: Decode an existing Eastern Europe or Flanders game map, retaining its original terrain, object layout and resource references for UE import.
tags: [maps, war-thunder, extraction, provenance]
authorization: User explicitly requested unpacking and then selected existing Eastern Europe/Flanders maps instead of the CDK crop or football/training examples.
user_review: not_requested_under_standing_authorization
---

# Existing War Thunder map

## 0. Scope

Use installed avg_eastern_europe.bin or avg_vlaanderen.bin as the source. Preserve source terrain, instances and texture/material references. Decode and convert for UE; do not substitute a handmade map or treat the previous CDK crop as satisfying this request. RMUC remains the first/default map.

Original game/CDK and vehicle source files remain read-only. Local extraction tools, their source versions, generated assets and evidence are kept separately. No online client modification, game patching or publishing is involved.

## 1. Flow

Audit original DBLD chunks and resource names → consult official Dagor source and inspect an existing asset-export tool → extract terrain/instance layout/referenced geometry and textures → convert coordinates/materials/instances to UE map package → actual Windows loading and driving with source-versus-import checks. Every unsupported format or missing asset remains explicit.

The native ROS2 bridge investigation remains part of the long-term goal; this user correction makes the real game-map import the current priority. No bridge implementation has been claimed complete.

## 2. Acceptance

- Original map/source hashes recorded, source files unmodified.
- Terrain and object positions come from the selected map's actual data, at the original scale.
- Referenced assets/textures are resolved and missing items enumerated; no silent placeholder-success claim.
- Actual UE scene and collision/vehicle run verified, with screenshots and bounds/placement checks.
- Current map/vehicle/control behavior retained; updates recorded in CodeStable and progress docs.

## 3. Stop condition and limits

Continue until a concrete original-map sample works or an evidenced external dependency/format barrier prevents progress. A standalone terrain extraction is useful progress but does not by itself satisfy full-map import. Existing render/material approximations and dynamics limitations must be reported.
