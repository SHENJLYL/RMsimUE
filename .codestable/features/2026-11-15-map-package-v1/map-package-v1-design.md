---
doc_type: feature-design
feature: 2026-11-15-map-package-v1
status: approved
summary: Load flat map packages in the existing vehicle runtime, with coherent collision, spawns, semantic areas and occupancy export.
tags: [maps, unreal, navigation]
requirement: extensible-worlds-vehicles
roadmap: rm-simulator
roadmap_item: map-package-v1
authorization: Standing user instruction to implement extensible maps and keep working.
user_review: not_requested_under_standing_authorization
---

# Flat map package v1

## 0. Terms

Map package: map.json data only. Spawn pose: map-frame ground pose, not assumed robot origin. Semantic area: rectangular ground region with a role/team, not a complete referee implementation. Occupancy export: 2D raster of the same declared blocking geometry.

## 1. Goal and limits

Replace hardcoded test geometry with two selectable packages using the same vehicle and executable. Current vehicle is flat-ground kinematic; maps with ramps, stacked floors, arbitrary mesh collision and physical material calibration remain future work. This does not claim official RM field/rule fidelity.

The existing map roadmap dependency on a completed referee is unnecessary: static region contracts and navigation maps can be provided before rules consume them. Keep full referee work separate and record this implementation order change.

## 2. Contract and flow

- schema_version=1, kind=rm_flat_map, id/display_name, units=m, coordinate_system=ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP.
- floor: size_m[2], surface_z_m, thickness_m, color_rgb[3]; boundary: height_m, thickness_m.
- obstacles: id, center_m[3], size_m[3], yaw_rad, color_rgb[3]. All blocking boxes, supported by the existing swept vehicle proxy.
- spawns: id, team, position_m[3], yaw_rad. Pose lies on the declared ground and within walls; reject points inside obstacles, and validate the entire vehicle at runtime.
- regions: id, kind (spawn/supply/capture), team (neutral/red/blue), center_m[2], size_m[2]. Metadata and ground markers; no gain/score claims.
- inspection_obstacle_id names a box for the actual collision probe when that map supports its test route.

Flow: data validation → floor/walls/obstacles/markers → selected spawn transform → initial collision guard → vehicle controls. Python occupancy export samples the identical geometry in map coordinates, including yawed boxes and walls; no true robot pose is fed to algorithms.

## 3. Acceptance

- Both packages validate, and the same runtime can select either without recompilation.
- Actual UE records identify the map/spawn and each obstacle's bounds; invalid data or an overlapping spawn fails visibly.
- Existing controls/collision regression remains valid on the default map.
- Different map produces visibly different geometry/markers, and actual vehicle blocking is exercised.
- Occupancy cells match walls/boxes at known coordinates and spawn cells are free. Exports include source hash, resolution and origin.

## 4. Structure and mount points

assets/maps holds data; schemas holds contract; src/rm_map_tools holds offline validation/export; MapStage.cpp/h owns UE geometry, leaving control code responsible for the vehicle. Tools and tests remain under their existing type-specific directories. Removing the MapFile option/MapStage call restores the previous runtime boundary; no external source files are edited.
