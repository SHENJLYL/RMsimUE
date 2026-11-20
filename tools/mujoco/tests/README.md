# Full RMUC MuJoCo sensor acceptance

Run after the native backend, the complete original RMUC model and the installed native Windows ROS bridge have passed their respective preparation gates. Coordinate the shared UE project with other users of the project; this wrapper acquires the same named mutex as the build/import/launch tools and rejects any existing project process.

```powershell
powershell.exe -NoProfile -File tools/environment/run-env.ps1 powershell.exe -NoProfile -File tools/environment/Build-RosBridge.ps1
powershell.exe -NoProfile -File tools/mujoco/tests/Test-MujocoSensors.ps1 `
  -EngineRoot E:/simulator-tools/UE_5.8 `
  -EvidenceDirectory D:/simulator/artifacts/integration/mujoco-ue-20261120/formal-sensors-01 `
  -Port 19671
```

Use a fresh evidence directory on every attempt. The default model is `assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json`; a diagnostic plane is rejected. `-SkipRos` produces a TCP-only result and does not claim native ROS2 acceptance.

The wrapper verifies the formal model against the active map, records SHA256 hashes of its inputs and testing code, then starts one owned UE process with the actual rendering profile, 1 ms MuJoCo substeps and 5 ms sensor/control updates. It uses the unchanged Mid360 and Mid360 IMU profiles. It stops its own processes and releases the mutex on success or failure. Other project processes are never stopped.

## Evidence

`tcp/verification.json` records actual MuJoCo clock metadata, both raw IMUs at 200 Hz, rest specific force, a physical gimbal sweep, sensor origin and angular velocity, an open-loop drive/brake segment, LiDAR payloads, acquisition times, ray budgets, opaque self occlusion, real RGB frames, and a reconnect with fresh sensor queues. The evaluator reads vehicle truth only for independent measurements; commands are open loop and no estimator receives truth.

`ros/verification.json` uses the installed native Humble bridge and native Livox messages in isolated ROS domain 173. It checks `/clock`, both raw IMU topics, the articulated TF tree, exact Livox/PointCloud2 point equality, acquisition offsets, matching camera image/CameraInfo/TF timestamps, real RGB pixels and intrinsics. The observer has no evaluation-truth subscription. The bridge is started without optional aim-controller adapters.

The runtime log must independently pass the existing native serial-versus-parallel LiDAR query comparison. Camera PNGs contain the actual received RGB bytes. JSON traces retain sample timestamps and metrics for inspection. All bound files must still have their original hashes at the end of the gate.

At a 5 ms real sensor update interval, a 100 ms LiDAR scan contains 19 acquisition times in its first window and 20 thereafter; each group of rays shares the actual sampled pose and time. This gate does not invent 1 ms ray poses. It reports measured simulation/wall time ratio separately; `real_time_gate` remains false because this is a sensor correctness gate.

## Native navigation integration smoke

`Test-MujocoNavigation.ps1 -EngineRoot E:\simulator-tools\UE_5.8 -SessionDirectory <fresh-directory>` acquires the shared RMSim project mutex, then runs the public `Start-NavigationConsole.ps1` entry with the full RMUC MuJoCo model, native Windows ROS2 overlays, Point-LIO, AMCL and Nav2. The declared scene spawn determines a 1 m forward goal. The return goal is canceled after AMCL observes more than 0.15 m displacement. Simulator truth is subscribed only for independent endpoint/braking/drift evaluation; the existing strict limits remain 0.25 m, 0.2 m and 0.02 m.

The coordinator reuses the unchanged `measure_console_clock.py` production observer for 60 s (10 s stable warmup, 0.98–1.02 overall and rolling clock ratios, 200,000 LiDAR rays/s, 200 Hz IMU and 60 Hz native scene Draw calls within 5%). Action evidence must fit wholly inside that measurement window. Camera acquisition remains at the public navigation entry's default 0 Hz; the renderer stays enabled offscreen. It changes no user navigation YAML or vehicle profile. Domain 174 and TCP port 9474 are isolated defaults.

Reports live under `<session>/actions`, `<session>/clock` and `<session>-orchestration`. The owned console exits through its `stop.request` file; forced shutdown is reported as a failed cleanup. Run only after other UE/build/sensor owners release the shared project lock.

`probe_contact_orientation.py --output <fresh-directory>` provides three compact official MuJoCo C API contact-normal fixtures (vehicle as geom 0, vehicle as geom 1, and a compound-body center-of-mass counterexample). Its report records its actual host/runtime; a Linux native run is separate evidence from a Windows UE run.
