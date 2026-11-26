"""Unified ROS 2 launch file for sp_vision_25 (supports both UE5 simulation and real robot)."""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    share_dir = Path(get_package_share_directory("sp_vision_25"))
    sim_cfg = str(share_dir / "config" / "sim_rmuc2027.yaml")
    real_cfg = str(share_dir / "config" / "real_robot.yaml")

    mode = LaunchConfiguration("mode")
    enemy_color = LaunchConfiguration("enemy_color")
    require_bt = LaunchConfiguration("require_behavior_switch")
    debug_image = LaunchConfiguration("debug_image")

    params_file = PythonExpression(
        [f"'{real_cfg}' if '", mode, "' == 'real' else '", sim_cfg, "'"]
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("mode", default_value="real", description="Launch profile: 'real' or 'sim'"),
            DeclareLaunchArgument("enemy_color", default_value="auto", description="'auto' | 'red' | 'blue'"),
            DeclareLaunchArgument("require_behavior_switch", default_value="true", description="Gate aiming on behavior tree"),
            DeclareLaunchArgument("debug_image", default_value="false", description="Publish /sp_vision/debug_image"),
            Node(
                package="sp_vision_25",
                executable="vision_node",
                name="sp_vision_25_sim",
                output="screen",
                parameters=[
                    params_file,
                    {
                        "enemy_color": enemy_color,
                        "require_behavior_switch": require_bt,
                        "debug_image": debug_image,
                    },
                ],
            ),
        ]
    )
