def main():
    import json
    import os
    import sys
    import rclpy
    assert sys.platform == "win32", sys.platform
    assert os.environ["ROS_DISTRO"] == "humble"
    print(json.dumps({"colcon_installed_package": "native_humble_probe",
                      "platform": sys.platform, "python": sys.executable,
                      "rclpy": rclpy.__file__}))
