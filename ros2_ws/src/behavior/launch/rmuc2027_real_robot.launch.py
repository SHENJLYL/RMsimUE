"""Real-robot ROS 2 launch file for the unified RMUC 2027 C++ sentry behavior tree.

Subscribes directly to native dji_referee_protocol topics (/referee/common/* and
/referee/parsed/common/self_color), auto-detects Red/Blue side, and uses the unified
RMUC 2027 real-field map coordinates with zero simulator dependencies.

Usage on Ubuntu real sentry:
  ros2 launch behavior rmuc2027_real_robot.launch.py
"""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    share_dir = Path(get_package_share_directory("behavior"))
    trees_dir = str(share_dir / "behavior_trees")

    robot_id = LaunchConfiguration("robot_id")
    use_sim_time = LaunchConfiguration("use_sim_time")
    target_tree = LaunchConfiguration("target_tree")

    server_params = {
        "use_sim_time": use_sim_time,
        "action_name": "pb2025_sentry_behavior",
        "tick_frequency": 20,
        "groot2_port": 0,
        "ros_plugins_timeout": 1000,
        "use_cout_logger": True,
        "write_tree_model": False,
        "plugins": ["behavior/bt_plugins"],
        "behavior_trees": [trees_dir],
    }

    client_params = {
        "use_sim_time": use_sim_time,
        "target_tree": target_tree,
    }

    return LaunchDescription(
        [
            DeclareLaunchArgument("robot_id", default_value="aim_sentry"),
            DeclareLaunchArgument("use_sim_time", default_value="false"),
            DeclareLaunchArgument("target_tree", default_value="rmuc2027_match"),
            Node(
                package="behavior",
                executable="behavior_server",
                name="rmsim_sentry_bt_server",
                output="screen",
                parameters=[server_params],
                remappings=[
                    ("gimbal_scan_cmd", ["/", robot_id, "/gimbal_scan_cmd"]),
                    ("auto_aim_switch", ["/", robot_id, "/behavior/auto_aim_switch"]),
                    ("tactical_fire_permit", ["/", robot_id, "/behavior/fire_permit"]),
                    ("tactical_vision_track", ["/", robot_id, "/behavior/vision_track"]),
                    ("cmd_spin", ["/", robot_id, "/behavior/cmd_spin"]),
                    ("cmd_vel", ["/", robot_id, "/behavior/cmd_vel"]),
                    ("tactical_target_priority", ["/", robot_id, "/behavior/target_priority"]),
                    ("tactical_ammo_purchase", ["/", robot_id, "/behavior/ammo_purchase"]),
                    ("tactical_sentry_stance", ["/", robot_id, "/sentry_stance_cmd"]),
                    ("tactical_state", ["/", robot_id, "/behavior/tactical_state"]),
                ],
            ),
            Node(
                package="behavior",
                executable="behavior_client",
                name="rmsim_sentry_bt_client",
                output="screen",
                parameters=[client_params],
            ),
        ]
    )
