# Livox interface-only simulator projection

CustomMsg.msg and CustomPoint.msg are byte-identical to unnc-aim/26UL_Sentry_livox_ros_driver2 commit a8230b02c1f862371c554673cc4f7d029d8f1928. The complete original checkout remains at third_party/livox_driver_original, including its license and Linux hardware implementation.

This package generates those same ROS interface names on Windows and Ubuntu. It does **not** implement a hardware driver or link the Livox SDK. Use one livox_ros_driver2 implementation per overlay; do not discover this and the hardware package under the same colcon source tree.
