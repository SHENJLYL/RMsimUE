"""Verify native Windows ROS 2 using two independent Python processes."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid


def worker(role, topic, token):
    import rclpy
    from std_msgs.msg import String

    rclpy.init()
    node = rclpy.create_node(f"native_smoke_{role}")
    received = set()
    publisher = None
    sent = 0
    last_send = 0.0
    completed_at = None
    if role == "subscriber":
        def receive(msg):
            if msg.data.startswith(token + ":"):
                received.add(int(msg.data.rsplit(":", 1)[1]))
        subscription = node.create_subscription(String, topic, receive, 10)
    else:
        publisher = node.create_publisher(String, topic, 10)
    start = time.monotonic()
    while time.monotonic() - start < 20:
        rclpy.spin_once(node, timeout_sec=0.05)
        now = time.monotonic()
        if role == "publisher" and publisher.get_subscription_count() > 0:
            if sent < 5 and now - last_send >= 0.1:
                publisher.publish(String(data=f"{token}:{sent}"))
                print(f"sent {sent}", flush=True)
                sent += 1
                last_send = now
            if sent == 5:
                if completed_at is None:
                    completed_at = now
                if now - completed_at > 0.5:
                    break
        if role == "subscriber" and received == set(range(5)):
            break
    success = sent == 5 if role == "publisher" else received == set(range(5))
    print(json.dumps({"role": role, "pid": os.getpid(), "success": success,
                      "sent": sent, "received": sorted(received)}), flush=True)
    node.destroy_node()
    rclpy.shutdown()
    return 0 if success else 1


def main(evidence):
    import rclpy
    from sensor_msgs.msg import Image, Imu, PointCloud2
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    from rosgraph_msgs.msg import Clock
    from std_msgs.msg import String

    evidence.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    topic = f"/native_humble_smoke_{token}"
    report = {
        "platform": sys.platform, "platform_detail": platform.platform(),
        "native_windows": sys.platform == "win32",
        "python": sys.version, "python_executable": sys.executable,
        "rclpy_module": rclpy.__file__, "ros_distro": os.getenv("ROS_DISTRO"),
        "rmw": os.getenv("RMW_IMPLEMENTATION"),
        "ros_domain_id": os.getenv("ROS_DOMAIN_ID"), "topic": topic,
        "message_types": [type(t()).__name__ for t in
                          (Image, Imu, PointCloud2, Twist, Odometry, Clock, String)],
        "colcon_core_version": importlib.metadata.version("colcon-core"),
        "processes": {},
    }
    processes = {}
    try:
        for role in ("subscriber", "publisher"):
            command = [sys.executable, str(Path(__file__).resolve()), "--role", role,
                       "--topic", topic, "--token", token]
            processes[role] = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        for role, process in processes.items():
            stdout, stderr = process.communicate(timeout=30)
            (evidence / f"{role}.stdout.log").write_text(stdout, encoding="utf-8")
            (evidence / f"{role}.stderr.log").write_text(stderr, encoding="utf-8")
            report["processes"][role] = {"pid": process.pid, "returncode": process.returncode,
                                        "stdout": stdout, "stderr": stderr}
        report["success"] = (report["native_windows"] and report["ros_distro"] == "humble"
                             and all(p.returncode == 0 for p in processes.values()))
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.kill()
                process.wait()
        (evidence / "native-pubsub-result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("publisher", "subscriber"))
    parser.add_argument("--topic")
    parser.add_argument("--token")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    if args.role:
        sys.exit(worker(args.role, args.topic, args.token))
    if args.evidence_dir is None:
        parser.error("--evidence-dir is required for the parent process")
    sys.exit(main(args.evidence_dir))
