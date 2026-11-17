"""Keep Nav2 and original sentry programs in one local DLL directory under an MSIX parent."""
import json,os
from pathlib import Path
from native_runtime import stage_runtime

root=Path(__file__).resolve().parents[2];prefix=Path(os.environ['CONDA_PREFIX']);install=root/'ros2_ws/install_navigation'
programs=[];plugins=[];sources=[]
custom=['loam_interface','sensor_scan_generation','fake_vel_transform','terrain_analysis','terrain_analysis_ext','pointcloud_to_laserscan','pb_omni_pid_pursuit_controller','pb_nav2_plugins']
for package in custom:
    directory=install/package;sources.append(directory/'bin')
    programs.extend((directory/'lib'/package).glob('*.exe'));plugins.extend((directory/'bin').glob('*.dll'))
vendor={'nav2_amcl':'amcl','nav2_map_server':'map_server','nav2_lifecycle_manager':'lifecycle_manager',
    'nav2_controller':'controller_server','nav2_planner':'planner_server','nav2_smoother':'smoother_server',
    'nav2_behaviors':'behavior_server','nav2_bt_navigator':'bt_navigator','nav2_waypoint_follower':'waypoint_follower','nav2_velocity_smoother':'velocity_smoother'}
native_overlays={p.name:p for p in install.iterdir() if p.is_dir() and (p.name.startswith('nav2_') or p.name=='diagnostic_updater') and (p/'bin').is_dir()}
for directory in native_overlays.values():sources.append(directory/'bin');plugins.extend((directory/'bin').glob('*.dll'))
for package,executable in vendor.items():
    owner=native_overlays.get(package,prefix/'Library')
    programs.append(owner/'lib'/package/(executable+'.exe'))
# Pluginlib/BT plugins are loaded at runtime, not necessarily visible in EXE imports.
for metadata in (prefix/'conda-meta').glob('ros2-nav2-*.json'):
    package=json.loads(metadata.read_text(encoding='utf-8'))
    # GUI plugins have their own audited RViz/Ogre runtime; do not inject them into headless Nav2.
    if package['name']=='ros2-nav2-rviz-plugins':continue
    if package['name'].removeprefix('ros2-').replace('-','_') in native_overlays:continue
    plugins.extend(prefix/file for file in package['files'] if file.lower().endswith('.dll'))
assert len(programs)>=16 and plugins
stage_runtime(root/'artifacts/runtime/navigation/windows',programs,sources,plugins)
