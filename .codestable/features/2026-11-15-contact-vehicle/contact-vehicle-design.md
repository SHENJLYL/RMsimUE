---
doc_type: feature-design
feature: 2026-11-15-contact-vehicle
status: approved
summary: Replace rigid height-following as the playable vehicle model with actual chassis, suspension and wheel contact dynamics.
tags: [physics, vehicle, terrain, regression]
authorization: User reports wave terrain still blocked and requires physically passable regions to be traversable; standing autonomous implementation authorization.
user_review: not_requested_under_standing_authorization
---

# Contact vehicle

## 0. Why the previous model is insufficient

The RMUC wave section has≈69.9mm height variation and42.29° local faces, while rigid axle pitch stays≈14°. The old35° individual ray gate wrongly rejects the road; a vertical ray plus radius also underestimates rolling tire support by14.2mm. Actual front overhang can contact terrain by≈35.2mm under the enforced four-wheel plane, so disabling that gate or shrinking boxes cannot prove passage. Wheels must unload and suspension/chassis contacts must resolve forces.

## 1. Model and scope

Use native UE Chaos rigid bodies/constraints: fixed chassis, four sliding wheel assemblies, four cylindrical wheel bodies. Preserve original normalized geometry and movable modules from the source; visual wheel spin and suspension follow physical bodies. Chassis movement comes from motor/contact forces and gravity, not imposed XY/Z poses or support-plane teleportation. Keep original static scene collision; no ghosting, global floor-ignore or artificial lift to pass.

Prepare independent convex collision assets from source meshes. Exact-radius32-sided cylinders represent tires; non-wheel components use documented convex decomposition. Preserve hashes, counts and approximation limits. Mass, friction, spring/damper, travel and drive limits are explicit demonstration parameters until measured robot values are supplied; source WT animation travel is not calibration.

## 2. Integration

Initialize at the validated spawn, then let physics settle. Common manual input drives forward/reverse differential wheel commands and brake; actual velocity feedsHUD. Camera/gimbal/trainingshots follow the true body transform. Existing kinematic probes remain diagnostic tests for that older mode and cannot be claimed as contact-physics proof. New asynchronous probes advance actual world ticks and record body/wheel poses and contact responses.

ROS sidecar continues offline preparation in parallel; physical command interface will be the shared input boundary. Original Flanders conversion remains separate. Windows native is the acceptance target; Ubuntu same-source preparation remains unverified natively.

## 3. Acceptance

- Real normalized body and all wheel assemblies remain correctly posed at rest, with finite stable rigid bodies.
- Actual forward/reverse/brake/steer responds to input without forced position updates.
- Complete red and blue RMUC wave routes are driven through, not merely one ramp endpoint; pose/contact evidence and screenshots retained.
- Chassis/wheel contacts with real walls/too-high obstacles still stop or deflect naturally; no tunnelling exemption.
- Driving/aim/fire integration works and original source remains unchanged.
- Claims match measured coverage; unknown friction/COM/motor properties and untested routes remain explicit, not hidden behind a global claim that all geometry is passable.
