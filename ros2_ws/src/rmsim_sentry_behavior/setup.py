from setuptools import setup

package_name = "rmsim_sentry_behavior"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="UNNC AIM",
    maintainer_email="ssyjs16@nottingham.edu.cn",
    description="RMUC 2027 simulator vision frontend",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "vision_autoaim = rmsim_sentry_behavior.vision_autoaim_node:main",
        ],
    },
)
