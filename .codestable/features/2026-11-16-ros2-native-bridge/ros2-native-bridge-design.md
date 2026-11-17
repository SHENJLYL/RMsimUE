---
doc_type: feature-design
feature: 2026-11-16-ros2-native-bridge
status: approved
summary: Connect native UE contact vehicle and rendered camera to the existing native ROS2 sidecar with explicit ownership and bounded command lifetime.
tags: [ros2, sensors, control, native]
authorization: Standing autonomous simulator implementation goal; Windows first and Ubuntu offline preparation.
user_review: not_requested_under_standing_authorization
---

# Native ROS2 bridge

## Scope and boundaries

Use the existing rmsim_bridge Python package and its version1 loopback TCP contract. Add an optional UE component, enabled by -RosBridge; -RosControl explicitly gives ROS chassis ownership. Default manual play remains unchanged. This slice is real native ROS/UE transport, not full sentry_ws, Livox/Point-LIO, official referee or multiplayer acceptance.

The UE server owns state and binds only127.0.0.1. One client, bounded buffers/nonblocking I/O, strict flat JSON command parsing, session/epoch/sequence/frame/time checks. Commands use body-frame SI vx/vy/wz; unsupported or out-of-profile requests are rejected. A250ms monotonic lease checked by the rig before physics brakes on disconnect, silence or invalid input. Restart/reset invalidates stale commands; a stalled game tick closes the session before reading buffered input.

Clock comes from the actual synchronous Chaos solver after physics, relative to the current reset epoch. Do not label render/wall time as physics time. Async physics is not enabled by this component. Images use a dedicated fixed-FOV camera at the real aim-camera pose, sampled at a configured simulation rate, with matching optical TF and timestamp. Human sight zoom must not change sensor intrinsics. Capture only with a connected reader and available bounded output buffer.

## Implementation

URosBridgeComponent owns socket, parser, session and camera acquisition. GameMode wires lifetime, reset and control ownership; VehicleRig owns the pre-physics lease. Sockets and PhysicsCore/Chaos are the native platform abstraction. Existing Python transport keeps fragmentation/backpressure behavior. /clock, RGB8 Image/CameraInfo and base_link->optical TF use real server observations. Evaluation truth remains under /rmsim/evaluation and is never published as localization TF.

Robot camera output is separate from the observer/sight view. ROS-controlled motion continues when the viewport is unfocused; keyboard movement/gimbal commands do not compete. Space is an operator brake, R resets the epoch. Manual mode accepts observation connections but rejects remote drive commands.

## Acceptance

- Native Windows message imports and package build/run, same Python source retained for Ubuntu.
- Actual UE TCP state and nonblank rendered RGB, matching dimensions/intrinsics/optical frame/timestamp and a projected real landmark.
- Real ROS2 TwistStamped moves the physical vehicle with correct axes; actual clock/image/info/TF observed by an independent ROS node.
- Stopped publisher/disconnect, stale/future/duplicate/wrong-session/frame/nonfinite/oversized commands cannot sustain movement; explicit acknowledgement and actual stop evidence.
- Reset/session and backpressure do not replay stale inputs. No world->base truth TF masquerades as localization.
- Manual controls and optional sensor-off rendering remain intact; Windows proof is not counted as Ubuntu native proof.

Architecture, bridge README, progress, environment lock and evidence must be updated. Capture/readback cost and uncalibrated ideal optics are recorded limits.
