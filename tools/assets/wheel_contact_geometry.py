"""Portable validation/identity for source-derived wheel hulls (ROS local metres)."""
import hashlib
import json
import math


def source_vertices(value):
    if value is None:
        return None
    if not isinstance(value, list) or not 4 <= len(value) <= 1024:
        raise ValueError('wheel hull requires 4..1024 vertices')
    result = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 3 or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or
                not math.isfinite(v) or abs(v) > 1 for v in point):
            raise ValueError('wheel hull requires finite local metre XYZ values within one metre')
        result.append([float(v) for v in point])
    if any(max(p[i] for p in result)-min(p[i] for p in result) < 1e-6 for i in range(3)):
        raise ValueError('wheel hull has a degenerate axis')
    return result


def geometry_hash(value):
    vertices = source_vertices(value)
    if vertices is None:
        return ''
    return hashlib.sha256(json.dumps(vertices, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
