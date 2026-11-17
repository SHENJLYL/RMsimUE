"""Stage RViz's native DLL closure locally; leave the installed environment untouched."""
from pathlib import Path
import os
from native_runtime import stage_runtime

root = Path(__file__).resolve().parents[2]
prefix = Path(os.environ['CONDA_PREFIX']) / 'Library'
ogre = prefix / 'opt/rviz_ogre_vendor/bin'
plugins = [prefix/'bin/rviz_default_plugins.dll', prefix/'bin/nav2_rviz_plugins.dll']
plugins += list(ogre.glob('*.dll'))
qt = prefix/'plugins/platforms/qwindows.dll'
if qt.is_file(): plugins.append(qt)
for path in plugins:
    if not path.is_file(): raise FileNotFoundError(path)
report = stage_runtime(root/'artifacts/runtime/rviz/windows', [prefix/'bin/rviz2.exe'],
                       [root/'artifacts/runtime/navigation/windows', prefix/'bin', ogre], plugins)
print('RViz runtime ready; Qt plugins remain in their original installed directory.')
