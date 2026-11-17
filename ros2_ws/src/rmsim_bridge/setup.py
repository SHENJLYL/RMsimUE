from setuptools import find_packages, setup

setup(
    name='rmsim_bridge', version='0.1.0', packages=find_packages(exclude=['test']),
    data_files=[('share/ament_index/resource_index/packages', ['resource/rmsim_bridge']),
                ('share/rmsim_bridge', ['package.xml', 'README.md']),
                ('share/rmsim_bridge/config', ['config/fastdds-loopback-udp.xml'])],
    install_requires=['setuptools'], zip_safe=True,
    maintainer='RMSim development', maintainer_email='local@example.invalid',
    description='Native local ROS 2 simulator sidecar', license='Apache-2.0',
    entry_points={'console_scripts': ['bridge = rmsim_bridge.node:main','drive_demo = rmsim_bridge.drive_demo:main','aim_demo = rmsim_bridge.aim_demo:main','point_lio = rmsim_bridge.point_lio_runner:main','navigation = rmsim_bridge.navigation_runner:main']},
)
