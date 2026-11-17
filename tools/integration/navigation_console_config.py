"""Portable scene preparation and RViz configuration for the native console."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

def atomic_json(path: Path, data: dict, attempts: int = 8) -> bool:
    """Readers may temporarily deny rename on Windows. Keep the live process alive."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, indent=2) + "\n"
    for _ in range(attempts):
        try:
            with temporary.open("w",encoding="utf-8",newline="\n") as stream:
                stream.write(text);stream.flush();os.fsync(stream.fileno())
            temporary.replace(path)
            return True
        except PermissionError:
            time.sleep(.02)
    return False


ROOT = Path(__file__).resolve().parents[2]

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def scene_geometry(scene: dict) -> str:
    """Whitespace and spawn-independent identity; geometry and asset references stay strict."""
    data = {k: v for k, v in scene.items() if k not in ('spawns', 'display_name', 'limitations', 'regions')}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def prepare_scene(scene_name: str, output: Path) -> dict:
    if scene_name not in ('rmuc2027', 'import_lab'):
        raise ValueError('Unknown console scene')
    output.mkdir(parents=True, exist_ok=False)
    nav = output / 'navigation'
    if scene_name == 'rmuc2027':
        source = ROOT / 'assets/maps/rmuc2027/navigation-console'
        specification = json.loads((source / 'SOURCE.json').read_text(encoding='utf-8-sig'))
        scene = json.loads((source / 'scene.json').read_text(encoding='utf-8-sig'))
        if scene_geometry(scene) != specification['scene_geometry_sha256']:
            raise ValueError('RMUC navigation raster does not match the declared scene geometry')
        raster = source / 'map.pgm'
        if not raster.is_file() or digest(raster) != specification['raster_sha256']:
            raise ValueError('RMUC navigation raster is missing or changed; restore the local raster recorded in assets/maps/rmuc2027/navigation-console/SOURCE.json')
        nav.mkdir()
        shutil.copyfile(raster, nav / 'map.pgm')
        shutil.copyfile(source / 'map.yaml', nav / 'map.yaml')
        metadata = copy.deepcopy(specification['map_metadata'])
        initial = [6.4, 6.0, .15]
        view = dict(x=7.5, y=6.4, scale=65.)
    else:
        scene = json.loads((ROOT / 'assets/maps/import_lab/map.json').read_text(encoding='utf-8-sig'))
        initial = [.6, -.4, .15]
        view = dict(x=1., y=0., scale=45.)
    scene_file = output / 'scene.json'
    scene_file.write_text(json.dumps(scene, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    if scene_name == 'import_lab':
        from rm_map_tools.map import load_map
        from rm_map_tools.__main__ import main as map_main
        if map_main(['export', str(scene_file), '--output', str(nav), '--resolution', '.05']) != 0:
            raise RuntimeError('Cannot export the selected flat scene')
    else:
        metadata['source_md5'] = hashlib.md5(scene_file.read_bytes()).hexdigest()
        metadata['source_sha256'] = digest(scene_file)
        metadata['scope'] = 'Geometry-derived RMUC raster; native validation covers the red wave route. Other regions remain experimental.'
        (nav / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    return dict(scene=scene_name, scene_file=str(scene_file), map_yaml=str(nav / 'map.yaml'),
                initial_pose=initial, view=view, scene_sha256=digest(scene_file), raster_sha256=digest(nav / 'map.pgm'))

def rviz_configuration(view: dict) -> dict:
    def topic(name, transient=False, sensor=False):
        return {'Value': name, 'Depth': 5, 'History Policy': 'Keep Last',
                'Reliability Policy': 'Best Effort' if sensor else 'Reliable',
                'Durability Policy': 'Transient Local' if transient else 'Volatile'}
    def grid(name, channel, alpha=1., scheme='map'):
        return {'Class':'rviz_default_plugins/Map', 'Name':name, 'Enabled':True, 'Value':True,
                'Topic':topic(channel, True), 'Update Topic':topic(channel+'_updates'),
                'Alpha':alpha, 'Color Scheme':scheme, 'Draw Behind':True, 'Use Timestamp':False}
    displays = [grid('Navigation map', '/map'), grid('Local obstacle costs', '/local_costmap/costmap', .45, 'costmap'),
        {'Class':'rviz_default_plugins/Axes','Name':'Estimated robot pose','Enabled':True,
         'Reference Frame':'aim_sentry/base_link','Length':.55,'Radius':.04},
        {'Class':'rviz_default_plugins/Axes','Name':'Measured gimbal heading','Enabled':True,'Reference Frame':'aim_sentry/yaw_link','Length':.7,'Radius':.018},
        {'Class':'rviz_default_plugins/Axes','Name':'Mid-360 optical mount','Enabled':True,'Reference Frame':'aim_sentry/lidar_link','Length':.25,'Radius':.01},
        {'Class':'rviz_default_plugins/Polygon','Name':'Robot footprint','Enabled':True,
         'Topic':topic('/local_costmap/published_footprint'),'Color':'0; 190; 110','Alpha':1.},
        {'Class':'rviz_default_plugins/Path','Name':'Planned route','Enabled':True,'Value':True,
         'Topic':topic('/plan'),'Color':'0; 160; 255','Line Style':'Billboards','Line Width':.06,
         'Buffer Length':1,'Pose Style':'None'},
        {'Class':'rviz_default_plugins/LaserScan','Name':'Live obstacle scan','Enabled':True,'Value':True,
         'Topic':topic('/obstacle_scan', sensor=True),'Size (Pixels)':2,'Size (m)':.025,
         'Style':'Points','Color Transformer':'FlatColor','Color':'245; 130; 30','Position Transformer':'XYZ','Decay Time':0},
        {'Class':'rviz_default_plugins/PointCloud2','Name':'Point-LIO registered cloud','Enabled':True,'Value':True,
         'Topic':topic('/registered_scan', sensor=True),'Size (Pixels)':1,'Size (m)':.012,
         'Style':'Points','Color Transformer':'FlatColor','Color':'90; 160; 170','Position Transformer':'XYZ',
         'Decay Time':2.,'Alpha':.45},
        {'Class':'rviz_default_plugins/MarkerArray','Name':'Selected waypoints','Enabled':True,'Value':True,
         'Topic':topic('/waypoints')},
        {'Class':'rviz_default_plugins/TF','Name':'Coordinate frames','Enabled':False,'Show Names':True,'Marker Scale':.25}]
    return {'Panels':[{'Class':'rviz_common/Displays','Name':'Displays'},
                      {'Class':'nav2_rviz_plugins/Navigation 2','Name':'Navigation 2'},
                      {'Class':'rviz_common/Views','Name':'Views'}],
        'Visualization Manager':{'Class':'','Name':'root','Enabled':True,'Displays':displays,
            'Global Options':{'Fixed Frame':'aim_sentry/map','Frame Rate':20,'Background Color':'40; 45; 52'},
            'Tools':[{'Class':'rviz_default_plugins/MoveCamera'}, {'Class':'rviz_default_plugins/Select'},
                     {'Class':'rviz_default_plugins/FocusCamera'},
                     {'Class':'rviz_default_plugins/SetInitialPose','Topic':topic('/initialpose'),
                      'Covariance x':.25,'Covariance y':.25,'Covariance yaw':.09},
                     {'Class':'nav2_rviz_plugins/GoalTool'}],
            'Transformation':{'Current':{'Class':'rviz_default_plugins/TF'}},
            'Views':{'Current':{'Class':'rviz_default_plugins/TopDownOrtho','Name':'Current View',
                    'Target Frame':'<Fixed Frame>','Angle':0.,'X':view['x'],'Y':view['y'],'Scale':view['scale']}}},
        'Window Geometry':{'Width':1280,'Height':940,'X':1200,'Y':40,'Hide Left Dock':False,'Hide Right Dock':True}}
