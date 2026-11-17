from setuptools import setup

setup(
    name="native_humble_probe", version="0.1.0", packages=["native_humble_probe"],
    data_files=[("share/ament_index/resource_index/packages", ["resource/native_humble_probe"]),
                ("share/native_humble_probe", ["package.xml"])],
    entry_points={"console_scripts": ["native_probe = native_humble_probe:main"]},
)
