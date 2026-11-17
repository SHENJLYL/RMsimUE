---
doc_type: feature-design
feature: 2026-11-16-native-imu
status: approved
summary: Physical chassis IMU samples with SI specific force, axial-vector conversion, bounded transport and native ROS verification.
tags: [sensors, imu, ros2, physics]
authorization: Standing simulator and same-source sentry_ws goal; Windows first.
user_review: not_requested_under_standing_authorization
requirement: raw-chassis-imu
roadmap: rm-simulator
roadmap_item: native-imu
---

# Native IMU

## Scope and source contract

Continue toward actual sentry sensing. This slice implements raw six-axis IMUs mounted to the physical chassis; LiDAR/Point-LIO execution, AHRS, gimbal-mounted IMUs and hardware calibration remain later work. Do not emit a fabricated orientation estimate or localization TF. Raw Imu orientation covariance starts with-1; zero noise covariance means unknown, following the ROS message contract.

REP145 and sensor_msgs/Imu require sensor-frame SI specific force and angular velocity. Stationary upright acceleration is+g; free fall is approximately0 subject to actual vehicle damping/suspension forces. Source Point-LIO3099b9dd mid360.yaml uses acc_norm1 for g units and states9.81 for SI; adaptation must be explicit, not an unlabelled scale change.

## Placement and ownership

Optional RosImu plus a vehicle-local sensors/imu.json runtime profile, or explicit ImuProfile path. Profile contains up to4 named chassis-mounted IMUs, poses in the published ROS base_link frame, nominal rates, per-axis constant biases/noise and deterministic seeds. This supplements the existing physical mesh-rig adapter; it does not claim generic canonical URDF physics support. Default manual play incurs no sensor work.

RosImuComponent samples actual point velocity and angular velocity after synchronous Chaos physics. Point velocity includes lever-arm effects. Acceleration is finite difference over consecutive real sample windows minus actual world gravity, transformed to the current sensor frame; angular velocity uses axial-vector reflection into ROS. A first sample only initializes history; R/explicit pose resets clear history so teleporting does not fabricate a huge acceleration.

Sampling is driven by solver time, independently of50Hz transport and camera rate. Do not invent missing observations between physics frames. Report missed nominal periods, actual integration intervals and queue drops. Each sensor keeps at most32 samples; stream configuration and batches fit the bounded existing64KiB JSON. No-client periods discard pending transmission while preserving measurement history; new session sends no old backlog.

Reuse native ROS Imu and tf_static (base_link→sensor only), best-effort sensor messages, actual sample stamps and raw-orientation-unavailable semantics. Truth remains isolated. Optional generic SimStepSeconds configures fixed physics without requiring the LK controller; LK retains its1ms requirement. Ubuntu uses the same source, with offline syntax proof only.

## Verification

Actual Windows ROS receiver checks stationary gravity, positive yaw sign, rotated mounting coordinates, lever-arm acceleration, actual sample rate/batching, bias/noise configuration, reset/reconnect history and no localization truth TF. Compare paired sensors under the same motion to avoid using commanded velocity as measurement truth. A development-only freefall fixture may reposition the entire physical rig; the sampler never moves bodies or replaces measurements with expected values. Record the fixture and settling window.

Existing velocity/aim/manual paths receive focused regressions. Source/format/compile/runtime/calibration evidence remain separate. New sampling code belongs in RosImuComponent, physics access in VehicleRig and ROS conversion in a dedicated Python helper; no unrelated refactor.

## Integration and architecture

Mount points: GameMode's opt-in RosImu/generic fixed step; RosBridge Initialize/Advance/QueueState/session reset; VehicleRig actual point-motion access and ResetContactPose generation; Python protocol validation/ImuPublisher called after clock publication; Windows/Linux launch wrappers; development-only freefall/incline fixture. Removing the feature removes those optional hooks and sensor profile/schema/helper, while the existing velocity, motor and aim protocols remain intact. Actual references were searched during acceptance.

Architecture receives the sampler/transport/publisher flow, time/missing-sample contract and raw-only boundaries. A scoped raw-chassis-imu requirement is current; the broader realistic-sensors vision remains draft. Roadmap records native-imu separately from full sensor/control bridge and single-sentry closure.
