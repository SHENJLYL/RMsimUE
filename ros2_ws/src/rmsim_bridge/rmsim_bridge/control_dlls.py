"""Keep scoped Windows DLL directories alive for generated ROS message type support."""
import os
from pathlib import Path

_handles={}
_fastdds_typesupport={}


def load_control_dlls():
    load_package_dlls('custom_msgs','chassis_controllers')


def load_package_dlls(*packages):
    if os.name!='nt':return
    from ament_index_python.packages import get_package_prefix
    for name in packages:
        path=Path(get_package_prefix(name))/'bin'
        if str(path) not in _handles:_handles[str(path)]=os.add_dll_directory(str(path))
        library=path/(name+'__rosidl_typesupport_fastrtps_c.dll')
        if name not in _fastdds_typesupport and library.is_file():
            import ctypes
            _fastdds_typesupport[name]=ctypes.WinDLL(str(library))
