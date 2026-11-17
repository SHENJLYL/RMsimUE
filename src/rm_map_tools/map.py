"""Validate map.json and derive an occupancy grid from its declared geometry."""
import hashlib
import json
import math
from pathlib import Path
import re


class MapError(ValueError):
    pass


def require(value, message):
    if not value:
        raise MapError(message)


def keys(value, expected, label, optional=()):
    required = set(expected.split())
    require(isinstance(value, dict) and required <= set(value) <= required | set(optional), f'{label}: unexpected/missing fields')


def number(value, label, low=None, high=None):
    require(type(value) in (int, float), f'{label}: expected number')
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise MapError(f'{label}: nonfinite value') from exc
    require(math.isfinite(result), f'{label}: nonfinite value')
    require(low is None or result >= low, f'{label}: below {low}')
    require(high is None or result <= high, f'{label}: above {high}')
    return result


def vector(value, count, label, low=None, high=None):
    require(isinstance(value, list) and len(value) == count, f'{label}: expected {count} components')
    return [number(v, label, low, high) for v in value]


def name(value, label):
    require(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]*', value), f'{label}: invalid identifier')
    return value


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON field: {key}')
        result[key] = value
    return result


def blocked(data, x, y):
    """Ground-position collision for the flat-map raster, without robot inflation."""
    width, depth = data['floor']['size_m']
    wall = data['boundary']['thickness_m']
    if abs(x) >= width / 2 - wall or abs(y) >= depth / 2 - wall:
        return True
    for box in data['obstacles']:
        dx, dy = x - box['center_m'][0], y - box['center_m'][1]
        c, s = math.cos(box['yaw_rad']), math.sin(box['yaw_rad'])
        if abs(c * dx + s * dy) <= box['size_m'][0] / 2 and abs(-s * dx + c * dy) <= box['size_m'][1] / 2:
            return True
    return False


def load_map(filename):
    path = Path(filename)
    if path.is_dir():
        path = path / 'map.json'
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, 'map exceeds 1 MiB')
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise MapError(str(exc)) from exc
    keys(data, 'schema_version kind id display_name units coordinate_system floor boundary obstacles spawns regions', 'map', ('inspection_obstacle_id',))
    require(type(data['schema_version']) is int and data['schema_version'] == 1, 'unsupported map version')
    require(data['kind'] == 'rm_flat_map' and data['units'] == 'm' and
            data['coordinate_system'] == 'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP', 'unsupported map kind/units/coordinates')
    name(data['id'], 'map.id')
    require(isinstance(data['display_name'], str) and data['display_name'].strip(), 'display_name is empty')
    floor = data['floor']
    keys(floor, 'size_m surface_z_m thickness_m color_rgb', 'floor')
    vector(floor['size_m'], 2, 'floor.size_m', 2, 100)
    number(floor['surface_z_m'], 'floor.surface_z_m', -100, 100)
    number(floor['thickness_m'], 'floor.thickness_m', 0.01, 5)
    vector(floor['color_rgb'], 3, 'floor.color_rgb', 0, 1)
    keys(data['boundary'], 'height_m thickness_m', 'boundary')
    number(data['boundary']['height_m'], 'boundary.height_m', 0.1, 10)
    number(data['boundary']['thickness_m'], 'boundary.thickness_m', 0.05, min(floor['size_m']) / 4)
    ids = {}
    for category in ('obstacles', 'spawns', 'regions'):
        require(isinstance(data[category], list) and len(data[category]) <= 256, f'{category}: array of at most 256 required')
        ids[category] = set()
        for entry in data[category]:
            require(isinstance(entry, dict), f'{category}: object required')
            entry_id = name(entry.get('id'), category + '.id')
            require(entry_id not in ids[category], f'{category}: duplicate id')
            ids[category].add(entry_id)
            if category == 'obstacles':
                keys(entry, 'id center_m size_m yaw_rad color_rgb', 'obstacle')
                center = vector(entry['center_m'], 3, 'obstacle.center', -100, 100)
                size = vector(entry['size_m'], 3, 'obstacle.size', 0.02, 100)
                angle = number(entry['yaw_rad'], 'obstacle.yaw', -math.pi, math.pi)
                vector(entry['color_rgb'], 3, 'obstacle.color', 0, 1)
                require(abs(center[2] - size[2] / 2 - floor['surface_z_m']) < 1.e-6, 'flat obstacles must rest on floor')
                c, s = abs(math.cos(angle)), abs(math.sin(angle))
                extents = [(c*size[0]+s*size[1])/2, (s*size[0]+c*size[1])/2]
                require(all(abs(center[i])+extents[i] <= floor['size_m'][i]/2-data['boundary']['thickness_m'] for i in range(2)), 'obstacle outside inner walls')
            elif category == 'spawns':
                keys(entry, 'id team position_m yaw_rad', 'spawn')
                position = vector(entry['position_m'], 3, 'spawn.position', -100, 100)
                number(entry['yaw_rad'], 'spawn.yaw', -math.pi, math.pi)
                require(entry['team'] in ('neutral', 'red', 'blue'), 'invalid spawn team')
                require(abs(position[2]-floor['surface_z_m']) < 1.e-6, 'spawn must specify the ground surface')
                require(not blocked(data, position[0], position[1]), 'spawn point is blocked')
            else:
                keys(entry, 'id kind team center_m size_m', 'region')
                center = vector(entry['center_m'], 2, 'region.center', -100, 100)
                size = vector(entry['size_m'], 2, 'region.size', 0.05, 100)
                require(entry['kind'] in ('spawn','supply','capture') and entry['team'] in ('neutral','red','blue'), 'invalid region kind/team')
                require(all(abs(center[i])+size[i]/2 <= floor['size_m'][i]/2-data['boundary']['thickness_m'] for i in range(2)), 'region outside inner walls')
    require(data['spawns'], 'at least one spawn required')
    if data.get('inspection_obstacle_id', '') != '':
        name(data['inspection_obstacle_id'], 'inspection_obstacle_id')
        require(data['inspection_obstacle_id'] in ids['obstacles'], 'unknown inspection_obstacle_id')
    return data, hashlib.sha256(raw).hexdigest()


def occupancy(data, resolution):
    resolution = number(resolution, 'resolution', 0.01, 1)
    width_m, height_m = data['floor']['size_m']
    width, height = math.ceil(width_m / resolution), math.ceil(height_m / resolution)
    require(width * height <= 4_000_000, 'occupancy grid exceeds 4 million cells')
    origin = [-width_m / 2, -height_m / 2, 0]
    # PGM top row is highest world Y. Centres beyond ceil-rounded field edges stay occupied.
    pixels = bytearray(width * height)
    for row in range(height):
        y = origin[1] + (height - row - 0.5) * resolution
        for col in range(width):
            x = origin[0] + (col + 0.5) * resolution
            pixels[row * width + col] = 0 if blocked(data, x, y) else 254
    return width, height, origin, pixels
