# Original sentry behavior runtime

`tools/environment/Build-Behavior.ps1` imports these sources into local checkouts under `ros2_ws/src`. The checked-in patches contain the Windows and simulation compatibility changes.

| Package | Revision | License declared upstream |
|---|---|---|
| [behavior](https://github.com/unnc-aim/27UL_Sentry_behavior/tree/aa4d82b73ae3c404b3037f2630eb2950dc0bbea4) | `aa4d82b73ae3c404b3037f2630eb2950dc0bbea4` | Apache-2.0 |
| [behaviortree_ros2 and btcpp_ros2_interfaces](https://github.com/unnc-aim/BehaviorTree.ROS2/tree/5e3979e16bcfd7369c8df6d2c903e89e86f9a80b) | `5e3979e16bcfd7369c8df6d2c903e89e86f9a80b` | MIT |
| [auto_aim_interfaces](https://github.com/SMBU-PolarBear-Robotics-Team/auto_aim_interfaces/tree/5eab0e0464b6cfae7cf8ae0599109b3f5b623e3d) | `5eab0e0464b6cfae7cf8ae0599109b3f5b623e3d` | MIT |
| [dji_referee_protocol](https://github.com/unnc-aim/26UL_dji_referee_protocol/tree/cf28347bfc284930cc68b206c41f171b0fe32bfc) | `cf28347bfc284930cc68b206c41f171b0fe32bfc` | MIT |

The four revisions match the submodule entries of [27UL_Sentry_ws](https://github.com/unnc-aim/27UL_Sentry_ws/tree/e990d98041e0787ab6abfb0b8038130f34fe1ca6) at `e990d98041e0787ab6abfb0b8038130f34fe1ca6`. BehaviorTree.CPP 4.10.0 and generate_parameter_library 0.7.7 use the Windows Humble builds in `tools/environment/pixi.lock`. Nav2 continues using BehaviorTree.CPP v3 from the same Pixi environment.

The simulator passes per-robot map frame IDs to the original navigation action plugins. They default to `map`. The simulation build generates `Armor`, `Armors`, and `Target` for the existing vision subscriptions. Their ROS message definitions and the installed upstream XML are identical to the pinned originals.
