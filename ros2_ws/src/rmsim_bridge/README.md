# RMSim local ROS 2 sidecar

Same Python source for native Windows and Ubuntu. `ament_python` package; no new Python dependencies beyond ROS 2 message packages and rclpy. Native Windows UE5.8.4/Humble end-to-end gates passed on 2026-11-16. Ubuntu is prepared but has not been built or run. See [native launch guide](../../../docs/guides/ros2-native-bridge.md) for the exact local environment and evidence.

```bash
colcon build --packages-select rmsim_bridge
# Source install/setup.bash on Ubuntu or install/local_setup.ps1 on Windows.
ros2 run rmsim_bridge bridge
# In a separate, equally sourced terminal, with UE started in RosControl mode:
ros2 run rmsim_bridge drive_demo --ros-args -p vx:=0.2 -p duration_s:=2.0
```

Bridge parameters: `robot_id=aim_sentry`, `port=9451`. Demo parameters: `robot_id`, `vx`, `vy`, `wz`, `duration_s` (maximum30s). The demo uses simulator time for stamps and wall time for its bounded duration, waits for fresh state, and publishes zeros on exit. The TCP endpoint is always 127.0.0.1; this is not a multiplayer protocol or a remote-control listener. I/O uses a steady-clock timer even if `use_sim_time=true` is set. The sidecar does not generate a clock, image or TF until it receives a validated simulator packet.

Optional `control_backend=lk` on both bridge and drive_demo uses the supplied native LK controller and original custom_msgs instead of the velocity path. UE must run with `-RosMotorControl`, and `install_control` must be sourced. That mode uses fixed1ms physics and may run slower than wall time; see [original LK guide](../../../docs/guides/sentry-lk-motor-loop.md). It has its own22 native checks, independent of full sentry_ws acceptance. Ordinary velocity mode remains the default.

LiDAR adds the original `livox_ros_driver2` interfaces. On this Windows workspace, build `Build-PointLio.cmd`, then run `Build-RosBridge.ps1` through `run-env.ps1` so colcon sees the `install_lio` underlay. Source `install_lio` and `install_bridge` when consuming LiDAR. Native Ubuntu can build these same source packages together; it has not yet been run.

| ROS interface | Meaning / QoS |
|---|---|
| `/aim_sentry/cmd_vel` TwistStamped | Reliable, volatile, depth1; header frame must be `aim_sentry/base_link`, units m/s and rad/s. Only linear x/y and angular z are accepted. |
| `/clock` Clock | Simulator-owned time; reliable, volatile, depth1. |
| `/aim_sentry/camera/image_raw` Image | Actual RGB8 frame, best-effort depth2, timestamp and optical frame provided by UE. |
| `/aim_sentry/camera/camera_info` CameraInfo | Pinhole intrinsics from actual width/height/HFOV; zero distortion is the ideal renderer model, not calibrated hardware. |
| `/tf` TFMessage | Dynamic base_link→aim_camera_optical_frame only, reliable. NO map/world→base truth transform. |
| `/aim_sentry/imu/data_raw` Imu | Optional physical six-axis samples, SI sensor frame, best-effort; orientation is unavailable. Other configured IMU IDs get their own topic. |
| `/tf_static` TFMessage | Optional base_link→IMU/LiDAR mounting transforms only; reliable, transient-local. |
| `/aim_sentry/lidar/livox` CustomMsg | Optional actual rolling ray returns, original Livox message fields with acquisition offsets in ns; best-effort. |
| `/aim_sentry/lidar/points` PointCloud2 | Same measured returns with xyz, offset_time, reflectivity/tag/line;20bytes per point. |
| `/rmsim/evaluation/aim_sentry/ground_truth` PoseStamped | Independent evaluation truth in `rmsim_world`; never substituted for Point-LIO/Nav2 localization. |
| `/rmsim/aim_sentry/state` String | Connection, session/epoch/tick, real command acknowledgement and rejection counters. |

Changing robot_id changes corresponding topic and frame prefixes; it must match the server robot identity. `TwistStamped` uses the latest simulator clock. Commands older than250ms or >50ms into the future are rejected, as are commands when simulator state has been absent for250ms. The UE server independently enforces profile speed limits, sequence/epoch and a monotonic250ms command lease before physics. Sidecar disconnect discards all partial inbound/outbound frames and unsent commands; it never replays those on reconnection.

UE `-RosBridge` enables observation while manual controls retain ownership. `-RosControl` explicitly gives ROS the chassis; WASD cannot compete, focus loss does not interrupt external control, Space is an operator brake and R resets physics/session epoch. Independent `-RosAimControl` grants gimbal/shooter control; the bridge then needs `aim_control:=true` and the original sp_msgs/pb_rm_interfaces overlay. Shooting also requires TrainingMode. Without a ROS flag there is no bridge socket or sensor capture.

See [aim/scan/fire guide](../../../docs/guides/ros-aim-fire.md). Original AutoAimCommandMsg, GimbalCmd and Int32 permission feed a separate200ms aim lease. Gimbal JointState and own training state are published; target health/hits stay on an evaluation topic. This is a rate-limited kinematic gimbal and training shooter, not the original hardware Hub/electrical loop or a running vision detector. The installed `aim_demo` emits the original messages with a bounded duration.

UE `-RosImu` adds chassis-mounted raw IMUs from the vehicle's `sensors/imu.json`; the sidecar publishes them automatically. `-SimStepSeconds=0.001` supplies enough physical observations for the default200Hz sampler, though wall-clock playback can be slower. Actual sample timestamps are preserved across50Hz transport batches. Missed physical periods and bounded-buffer drops are reported in diagnostics; no samples or attitude estimates are invented. See [IMU guide](../../../docs/guides/imu-runtime.md) for mounting, noise, reset and Point-LIO units. The model is uncalibrated and does not run Point-LIO.

Clock comes from the synchronous Chaos solver, relative to reset. R causes a backwards time jump with a new epoch/session; consuming algorithms must handle ROS clock jumps. The training round timer remains a separate demo timer. The dedicated sensor is RGB8, default1280x720/10Hz/HFOV60deg, independent of observer zoom. UE flags `-RosCameraWidth=`, `-RosCameraHeight=`, `-RosCameraHz=`, `-RosCameraHFov=` configure it. Synchronous GPU readback adds cost; nominal frequency is not a real-time or calibrated sensor guarantee.

Wire UE→sidecar: little-endian uint32 JSON length, uint32 binary length, UTF-8 JSON, optional tightly packed RGB8. JSON≤64KiB; image≤16MiB. TCP fragmentation/coalescing is handled without dropping partial packets. State JSON fields are documented by `protocol.validate_state`; camera pose and camera timestamp must describe the same observation. State-only camera TF uses its supplied timestamp. New session/epoch permits simulation time reset; time cannot regress within an epoch.

With `-RosLidar`, state protocol is v2 and the binary body contains `camera_bytes` RGB bytes followed by contiguous bounded scan blocks. Each point is little-endian uint32 offset_ns, float32 xyz, uint8 reflectivity/tag/line/padding. The total binary cap stays16MiB, JSON64KiB; metadata declares exact offsets/counts. Without LiDAR, v1 remains unchanged. Commands remain v1. Old sidecars must be rebuilt before receiving v2.

`ros2 run rmsim_bridge point_lio` supervises the original native estimator. It derives extrinsics from actual published IMU/LiDAR mount configuration, supplies SI parameters, and restarts the process/map on a new sensor session. This is required because the pinned original node rejects backwards timestamps without resetting its map. It never consumes the evaluation truth topic. See [LiDAR and Point-LIO guide](../../../docs/guides/lidar-pointlio.md); ray pattern, reflectivity and sensor parameters are explicitly uncalibrated, not a Mid-360 optical replica.

Wire sidecar→UE: newline JSON≤4096bytes with `v,kind,session,epoch,seq,stamp_ns,frame_id,vx,vy,wz`. Full-queue or malformed server data closes the local connection and lets the authoritative watchdog stop motion. No stale command is queued for a future connection.

LK mode extends v1 with `kind=motor_current`: the same seven identity/time fields plus integer `brake`0/1 and `steer0..3`/`wheel0..3` in[-2000,2000], exactly16 flat fields. State adds `motors` containing FL/FR/BL/BR shaft feedback and the configured torque constants. Velocity/current commands are mutually exclusive. The adapter requires fresh chassis intent, current messages and physical feedback; it sends an explicit bounded brake when they expire. The controller's original messages remain unchanged.

Pure protocol/transport tests (do not constitute ROS/UE integration proof):

```bash
PYTHONPATH=. python -m unittest discover -s test -v
```

Windows equivalent from the package root: run the same unittest command. The tests use actual loopback sockets for fragmentation/reconnect behavior but do not import or mock rclpy. Actual UE/ROS proof is under `artifacts/ros2/`: `final-cli-demo-02` (11 native ROS checks including installed ros2 run demo), `final-tcp-contract`, `final-observe`, `final-stall`, `ros-topics-03-visible-reset`, and `manual-regression` (19 manual checks). IMU and LiDAR/original Point-LIO have separate evidence in `artifacts/sensors/imu` and `artifacts/sensors/lidar`. Full sentry_ws/Nav2, AHRS, calibration and native Ubuntu remain unverified; original LK current mode has its separate scoped acceptance.
