from glob import glob
import os
from setuptools import find_packages, setup

package_name = "sp_vision_25"

setup(
    name=package_name,
    version="0.2.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/assets", glob("assets/*")),
        (f"share/{package_name}/config", glob("config/*.yaml")),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="UNNC AIM",
    maintainer_email="ssyjs16@nottingham.edu.cn",
    description="RMUC 2027 sp_vision_25 ROS 2 auto-aim node for simulation and real robot deployment.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "vision_node = sp_vision_25.vision_node:main",
        ],
    },
)
