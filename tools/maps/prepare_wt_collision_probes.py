"""Offline source-derived ray, streaming and spawn-corridor fixtures (NumPy).

Reads original NPZ and original RMI/full-affine transforms independently of the
derived FBX. Does not start UE or mutate any source/prepared mesh.
"""
import argparse
import collections
import json
import math
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'unreal'))
from wt_collision import load_contract, read, save, digest, local, rmi_rows, COUNTS

C = np.array(((1, 0, 0), (0, 0, -1), (0, 1, 0)), dtype=float)
SPAWN = np.array((1028., -1020., -1.3805753))


def rotation(row):
    x, y, z, w = np.asarray(row[3:7])/np.linalg.norm(row[3:7])
    return np.array(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
                     (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
                     (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))))


class Source:
    def __init__(self, package, original):
        source = read(package/'scene-source.json')
        self.models, self.items, self.hashes = {}, [], {}
        for name in COUNTS:
            folder = original/'models-all-07'/name
            metadata = read(folder/'model.json')
            if digest(folder/'mesh.npz') != metadata['mesh_sha256']:
                raise ValueError('source NPZ hash differs')
            with np.load(folder/'mesh.npz') as a:
                self.models[name] = (a['vertices'].astype(float) @ C.T,
                                     a['triangles'].astype(int))
            self.hashes[name] = metadata['mesh_sha256']
        for spec in source['meshes']:
            name = spec.get('source')
            if name not in COUNTS:
                continue
            for index, row in enumerate(rmi_rows(local(package, spec['instances_file']))):
                self.add(name, rotation(row) @ np.diag(row[7:10]), np.array(row[:3]),
                         dict(kind='rigid', row=index, rmi=spec['instances_file']))
        for batch in read(package/'affine-bake-map.json')['batches']:
            for index, item in enumerate(batch['instances']):
                if item['model'] in COUNTS:
                    self.add(item['model'], C @ np.array(item['matrix3x3']) @ C.T,
                             C @ np.array(item['translation_dagor_m']),
                             dict(kind='affine', tile=batch['tile'], tile_index=index))
        actual = collections.Counter(item['model'] for item in self.items)
        if dict(actual) != COUNTS:
            raise ValueError('independent source instance counts differ')
        self.lo = np.array([i['lo'] for i in self.items])
        self.hi = np.array([i['hi'] for i in self.items])
        self.positions = np.array([i['position'] for i in self.items])

    def add(self, name, matrix, position, identity):
        points = self.models[name][0] @ matrix.T + position
        self.items.append(dict(model=name, matrix=matrix, position=position,
                               identity=identity, lo=points.min(axis=0), hi=points.max(axis=0)))

    def geometry(self, index):
        item = self.items[index]
        vertices, faces = self.models[item['model']]
        return (vertices @ item['matrix'].T + item['position'])[faces]

    def provenance(self, index):
        item = self.items[index]
        return dict(model=item['model'], source_instance=item['identity'],
                    source_npz_sha256=self.hashes[item['model']],
                    position_ros_m=item['position'].tolist())

    def candidates(self, low, high):
        return np.flatnonzero(np.all(self.hi >= low-1e-8, axis=1)
                              & np.all(self.lo <= high+1e-8, axis=1))

    def ray(self, start, end):
        start, end = np.array(start, float), np.array(end, float)
        direction = end-start
        best, result = 2., None
        for index in self.candidates(np.minimum(start, end), np.maximum(start, end)):
            triangles = self.geometry(index)
            e1, e2 = triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]
            p = np.cross(np.broadcast_to(direction, e2.shape), e2)
            det = np.einsum('ij,ij->i', e1, p)
            valid = abs(det) > 1e-12
            inv = np.divide(1., det, out=np.zeros_like(det), where=valid)
            offset = start-triangles[:, 0]
            u = np.einsum('ij,ij->i', offset, p)*inv
            q = np.cross(offset, e1)
            v = q @ direction*inv
            t = np.einsum('ij,ij->i', e2, q)*inv
            valid &= (u >= -1e-9) & (v >= -1e-9) & (u+v <= 1+1e-9) & (t >= 0) & (t <= 1)
            choices = np.flatnonzero(valid)
            if len(choices):
                face = choices[np.argmin(t[choices])]
                if t[face] < best:
                    best = float(t[face])
                    result = dict(hit=True, impact_ros_m=(start+direction*best).tolist(),
                                  source_face=int(face), **self.provenance(int(index)))
        return result or dict(hit=False)

    def fixture(self, name, start, end, anchor, kind):
        return dict(name=name, kind=kind, start_ros_m=np.asarray(start).tolist(),
                    end_ros_m=np.asarray(end).tolist(), anchor_ros_m=np.asarray(anchor).tolist(),
                    expected=self.ray(start, end), tolerance_cm=.05)


def hull2(points):
    points = sorted(set(map(tuple, points)))
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.asarray(lower[:-1]+upper[:-1])


def inside_convex(p, hull):
    edges = np.roll(hull, -1, axis=0)-hull
    vectors = p-hull
    return np.all(edges[:, 0]*vectors[:, 1]-edges[:, 1]*vectors[:, 0] >= 1e-6)


def point_segment_distance(points, a, b):
    edge = b-a
    t = np.clip((points-a) @ edge/max(float(edge @ edge), 1e-30), 0, 1)
    return np.linalg.norm(points-(a+t[:, None]*edge), axis=1)


def projected_segment_distance(triangles, a, b):
    points = triangles[:, :, :2]
    result = float(point_segment_distance(points.reshape(-1, 2), a, b).min())
    edges = np.roll(points, -1, axis=1)-points
    projected_area = abs(edges[:, 0, 0]*edges[:, 1, 1]-edges[:, 0, 1]*edges[:, 1, 0])
    for endpoint in (a, b):
        delta = endpoint-points
        cross = edges[:, :, 0]*delta[:, :, 1]-edges[:, :, 1]*delta[:, :, 0]
        if np.any((projected_area > 1e-12) &
                  (np.all(cross >= -1e-10, axis=1) | np.all(cross <= 1e-10, axis=1))):
            return 0.
    for start, end in ((a, b), (b, a)):
        for j in range(3):
            x, y = points[:, j], points[:, (j+1)%3]
            edge = y-x
            t = np.clip(np.einsum('ij,ij->i', start-x, edge)/
                        np.maximum(np.einsum('ij,ij->i', edge, edge), 1e-30), 0, 1)
            result = min(result, float(np.linalg.norm(start-(x+t[:, None]*edge), axis=1).min()))
            # Segment/edge intersection in the plane.
            d = end-start
            denominator = d[0]*edge[:, 1]-d[1]*edge[:, 0]
            delta = x-start
            valid = abs(denominator) > 1e-12
            u = np.divide(delta[:, 0]*edge[:, 1]-delta[:, 1]*edge[:, 0],
                          denominator, out=np.zeros_like(denominator), where=valid)
            v = np.divide(delta[:, 0]*d[1]-delta[:, 1]*d[0],
                          denominator, out=np.zeros_like(denominator), where=valid)
            if np.any(valid & (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)):
                return 0.
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene-source', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root, manifest, contract = load_contract(args.scene_source)
    source = Source(args.scene_source.resolve().parent, args.source_root.resolve())
    probes, selected = [], []
    # Top surfaces for all fourteen resources, including full-affine sources.
    for name in sorted(COUNTS):
        indices = [i for i, item in enumerate(source.items) if item['model'] == name]
        index = min(indices, key=lambda i: np.linalg.norm(source.positions[i, :2]-SPAWN[:2]))
        selected.append(index)
        triangles = source.geometry(index)
        area_xy = abs(np.cross(triangles[:, 1]-triangles[:, 0],
                               triangles[:, 2]-triangles[:, 0])[:, 2])
        face = int(np.argmax(area_xy))
        center = triangles[face].mean(axis=0)
        top, bottom = center.copy(), center.copy()
        top[2] = source.hi[index, 2]+.5
        bottom[2] = source.lo[index, 2]-.5
        probe = source.fixture(name+'_top', top, bottom, source.positions[index], 'source_surface')
        if not probe['expected']['hit']:
            raise ValueError('source top fixture has no hit')
        probes.append(probe)
    sleepers = np.array([i for i, x in enumerate(source.items)
                        if x['model'] == 'russia_rails_sleeper_a'], dtype=int)
    first = min(sleepers, key=lambda i: np.linalg.norm(source.positions[i, :2]-SPAWN[:2]))
    distances = np.linalg.norm(source.positions[sleepers, :2]-source.positions[first, :2], axis=1)
    gap = None
    for second in sleepers[np.argsort(distances)[1:12]]:
        center = (source.positions[first]+source.positions[second])/2
        top, bottom = center.copy(), center.copy()
        top[2] = max(source.hi[first, 2], source.hi[second, 2])+.5
        bottom[2] = min(source.lo[first, 2], source.lo[second, 2])-.5
        candidate = source.fixture('between_sleepers', top, bottom, center, 'source_air_gap')
        if not candidate['expected']['hit'] and np.linalg.norm(source.positions[first, :2]-source.positions[second, :2]) < 2:
            gap = candidate
            gap['source_neighbours'] = [source.provenance(int(first)), source.provenance(int(second))]
            break
    if gap is None:
        raise ValueError('no verified original sleeper gap found')
    probes.append(gap)
    curve_index = next(i for i in selected if source.items[i]['model'] == 'pavement_border_g')
    triangle = source.geometry(curve_index)
    hull = hull2(triangle[:, :, :2].reshape(-1, 2))
    curved_gap = None
    for x in np.linspace(source.lo[curve_index, 0], source.hi[curve_index, 0], 35)[1:-1]:
        for y in np.linspace(source.lo[curve_index, 1], source.hi[curve_index, 1], 35)[1:-1]:
            if not inside_convex(np.array((x, y)), hull):
                continue
            start = [x, y, source.hi[curve_index, 2]+.5]
            end = [x, y, source.lo[curve_index, 2]-.5]
            candidate = source.fixture('curved_curb_hull_void', start, end,
                                       source.positions[curve_index], 'source_concavity')
            if not candidate['expected']['hit']:
                curved_gap = candidate
                break
        if curved_gap:
            break
    if curved_gap is None:
        raise ValueError('no verified curved curb hull void found')
    probes.append(curved_gap)
    corridor_a, corridor_b = SPAWN[:2], SPAWN[:2]+[3.4, 0]
    near = source.candidates(np.r_[np.minimum(corridor_a, corridor_b)-10, -10000],
                             np.r_[np.maximum(corridor_a, corridor_b)+10, 10000])
    clearance = []
    for index in near:
        triangle = source.geometry(index)
        clearance.append(dict(**source.provenance(int(index)),
                              projected_distance_to_340cm_centerline_m=projected_segment_distance(
                                  triangle, corridor_a, corridor_b),
                              source_bounds_ros_m=dict(lo=source.lo[index].tolist(), hi=source.hi[index].tolist())))
    clearance.sort(key=lambda x: x['projected_distance_to_340cm_centerline_m'])
    # Exercise coverage at 7.99 m with initial cells, then after the 8 m trigger,
    # and far enough to retire non-anchor cells before returning to the spawn.
    boundary = [dict(name='initial', position_ros_m=SPAWN.tolist(), refresh=True),
                dict(name='before_8m_trigger', position_ros_m=(SPAWN+[7.99, 0, 0]).tolist(), refresh=False),
                dict(name='after_8m_trigger', position_ros_m=(SPAWN+[8.01, 0, 0]).tolist(), refresh=True),
                dict(name='outside_spawn_footprint', position_ros_m=(SPAWN+[160, 0, 0]).tolist(), refresh=True),
                dict(name='return_to_spawn', position_ros_m=SPAWN.tolist(), refresh=True)]
    for stage in boundary:
        position = np.array(stage['position_ros_m'])
        candidates = np.flatnonzero(np.linalg.norm(source.positions[:, :2]-position[:2], axis=1) < 38.)
        ranked = sorted(candidates, key=lambda i: abs(np.linalg.norm(source.positions[i, :2]-position[:2])-35))
        stage['probes'] = []
        for index in ranked[:3]:
            triangles = source.geometry(index)
            area = abs(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])[:, 2])
            center = triangles[int(np.argmax(area))].mean(axis=0)
            start = position+np.array((0., 0., .5))
            direction = center-start
            if np.linalg.norm(direction) >= 39.9:
                continue
            end = start+direction/np.linalg.norm(direction)*40.
            probe = source.fixture(stage['name']+'_'+str(index), start, end, position, 'range_boundary')
            if not probe['expected']['hit']:
                raise ValueError('40m source query fixture has no hit')
            stage['probes'].append(probe)
        if not stage['probes']:
            raise ValueError('boundary stage needs real source geometry')
    fixtures = dict(schema='wt_generated_collision_source_probes_v1', status='passed',
                    contract=contract, script_sha256=digest(Path(__file__)),
                    source_npz_sha256=source.hashes, spawn_ros_m=SPAWN.tolist(),
                    query_range_m=40., probes=probes, boundary_stages=boundary,
                    corridor_nearest=clearance[:12])
    save(args.output/'fixtures.json', fixtures)
    save(args.output/'summary.json', dict(
        schema='wt_generated_collision_source_probe_summary_v1', status='passed',
        fixtures_sha256=digest(args.output/'fixtures.json'), script_sha256=digest(Path(__file__)),
        source_instances=len(source.items), fixture_count=len(probes),
        represented_models=len(COUNTS), verified_original_gaps=2,
        query_range_m=40., boundary_stages=len(boundary),
        boundary_rays=sum(len(x['probes']) for x in boundary),
        corridor_centerline_ros_m=[corridor_a.tolist(), corridor_b.tolist()],
        corridor_nearest=clearance[:5],
        collision_geometry_classification='derived from original visual geometry, source double-sided surfaces',
        native_queries_pending=True))
    print(json.dumps(read(args.output/'summary.json')), flush=True)


if __name__ == '__main__':
    main()
