# Native Windows ROS 2 Humble baseline

This is an isolated **Windows-native** RoboStack Humble environment. It does not use WSL, Docker, or a Linux process. It has been verified with real two-process ROS 2 publication/subscription and a built/installed `ament_python` package. It is not a validation of `sentry_ws` or of cross-machine networking.

- Portable Pixi: `E:\simulator-tools\pixi\pixi.exe` (0.84.2).
- Manifest and exact package lock: `pixi.toml` and `pixi.lock` in this directory.
- Environment: `E:\simulator-cache\environments\rm-simulator-ros2-humble-9257817147700612234\envs\default`.
- Package cache, temporary files, ROS logs and colcon user data: `E:\simulator-cache`.
- Project-local `.pixi/config.toml` detaches the environment to E:. No global PATH/config was changed.
- Run commands through `run-env.ps1` to apply isolated paths and activation.

## Verified result

- Python 3.14.10, `sys.platform == "win32"`.
- ROS 2 Humble from `https://prefix.dev/robostack-humble`; rclpy 3.3.22.
- Native Fast DDS (`rmw_fastrtps_cpp`), ROS domain 71, localhost-only for this test.
- Two independent processes sent/received all five unique messages; both exited 0, with empty stderr.
- Imported `Image`, `Imu`, `PointCloud2`, `Twist`, `Odometry`, `Clock`, `String`.
- colcon-core 0.21.3 built the `native_humble_probe` package; its installed Windows executable ran successfully.

Evidence lives in `D:\simulator\artifacts\environment`, especially `native-pubsub-result.json`, `installed-packages.json`, `colcon-build.log`, `installed-entrypoint.log`, and `setup-log.md`.

## Repeat checks

```powershell
& D:\simulator\tools\environment\run-env.ps1 python D:\simulator\tools\environment\smoke_pubsub.py --evidence-dir D:\simulator\artifacts\environment
& D:\simulator\tools\environment\run-env.ps1 colcon --log-base D:\simulator\artifacts\environment\colcon-log build --base-paths D:\simulator\tools\environment\smoke_ws\src --build-base D:\simulator\tools\environment\smoke_ws\build --install-base D:\simulator\tools\environment\smoke_ws\install --merge-install
& D:\simulator\tools\environment\run-env.ps1 cmd.exe /d /c D:\simulator\tools\environment\verify-install.cmd
```

Windows colcon's Python build uses `relpath(build_base, source)`: source and build must be on the same drive. An initial D:-source/E:-build attempt exposed this limitation; its log is retained. The small probe build/install stay in this directory on D:, while all large environment/cache data stay on E:.

This RoboStack rebuild uses a newer Python than Ubuntu 22.04's usual Humble installation. Reuse source and message definitions across platforms; do not copy compiled Python extensions/DLLs between environments. The exact versions are captured in `pixi.lock`.

Point-LIO native addition (2026-11-16): PCL, pcl_conversions, tf2_ros and visualization_msgs plus their dependencies were added without replacing any previously locked package. The140 added package URLs and preserved lock are recorded under artifacts/sources/point_lio/provenance.json and artifacts/sensors/lidar/before. `Build-PointLio.cmd` builds the pinned original estimator and byte-identical Livox interfaces into install_lio. `Build-RosBridge.ps1` sources that underlay before installing the sidecar. `Start-PointLio.ps1` manages the original process and fresh localization state after simulator resets. None of these steps enables the Linux-only physical Livox SDK on Windows.
