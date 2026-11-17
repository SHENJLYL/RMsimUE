# Navigation-follow source and scope

Target-shaping rules follow `unnc-aim/universal_controller` commit `95c66c28717e0b4d0f586c2ebf8df99d47a24ab0`, specifically the navigation input readiness and yaw-follow functions in `src/controllers/gimbal_controller.cpp`, Hub arbitration and controller configuration. Exact source URLs and SHA256 are retained in provenance.json. The local reference is read-only and excluded from Git.

`navigation_follow.py` implements the same heading, acceleration, rate, braking and freshness rules in Python at the simulator message boundary. It uses actual joint feedback and estimated base odometry. The original hardware current/PID loop is a separately tracked implementation boundary. Navigation does not acquire firing permission.

The gravity-aligned moving-mount adapter extends the existing Apache-2.0 loam_interface work copy. `third_party/patches/sentry-console-loam-mount.patch` records the delta against RMSim commit 0a83cc4, after the earlier navigation-native and reconnect patches. Original package notices stay with the source.
