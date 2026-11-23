"""Offline AMCL scan/map diagnostics; no ROS node, publisher, or control output.

The primary comparison uses exact-stamp published AMCL poses, original scan indices,
and the installed Nav2 1.1.20 map_update_cspace implementation. Explicit offline map
candidates preserve the runtime grid and bind a separate geometry manifest. Evaluation poses are
interpolated only inside recorded brackets and transformed with recorded body/scan TF.
This scores two observed poses, not the particle distribution or a new optimum.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import ctypes
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

import numpy as np
import yaml

UPSTREAM = {
    "tag": "1.1.20",
    "repository": "https://github.com/ros-navigation/navigation2",
    "files": {
        "nav2_amcl/src/sensors/laser/likelihood_field_model.cpp": "84a4542b6891cd665139fcc75dc6b71fa0d2175f167de5c11df463d2f6fe101d",
        "nav2_amcl/src/map/map_cspace.cpp": "e77abdeb1f20750de8d0a09027fe71479dba164970570773249bea41292a48ee",
        "nav2_amcl/include/nav2_amcl/map/map.hpp": "5aa7632d3705cd937e3a353c21a0aa4231ed44587affcdae88963ab2e37e709b",
        "nav2_amcl/src/amcl_node.cpp": "29a6402bf643fc8ea0d2dcea29619d4cbdcaf7014c811332ed4e86e593d3f1af",
        "nav2_map_server/src/map_io.cpp": "7f4e12d8c60891c64689bd0dab443a93800cdd280e9447e538d4458b1f68a57e",
    },
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    # Analysis outputs are immutable evidence; a rerun requires a fresh directory.
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def finite_vector(value, length):
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (length,) or not np.isfinite(result).all():
        raise ValueError("Malformed or nonfinite pose vector")
    return result


def quaternion(value):
    q = finite_vector(value, 4)
    norm = float(np.linalg.norm(q))
    if not .99 <= norm <= 1.01:
        raise ValueError("Recorded quaternion is not normalized")
    return q / norm


def rotation(q):
    x, y, z, w = quaternion(q)
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])


def slerp(q0, q1, fraction):
    q0, q1 = quaternion(q0), quaternion(q1)
    dot = float(np.dot(q0, q1))
    if dot < 0:
        q1, dot = -q1, -dot
    if dot > .9995:
        q = q0 + fraction*(q1-q0)
        return q / np.linalg.norm(q)
    theta = math.acos(max(-1., min(1., dot)))
    return (math.sin((1-fraction)*theta)*q0 + math.sin(fraction*theta)*q1)/math.sin(theta)


def matrix(position, orientation):
    value = np.eye(4)
    value[:3, :3] = rotation(orientation)
    value[:3, 3] = finite_vector(position, 3)
    return value


def planar(value):
    return [float(value[0, 3]), float(value[1, 3]), math.atan2(value[1, 0], value[0, 0])]


def tilt_degrees(value):
    return math.degrees(math.acos(float(np.clip(value[2, 2], -1., 1.))))


class Timeline:
    """No extrapolation, last-value hold, or timestamp substitution."""

    def __init__(self, rows, position_key, orientation_key):
        unique = {}
        self.conflicts = set()
        for row in rows:
            stamp = int(row["stamp_ns"])
            p, q = finite_vector(row[position_key], 3), quaternion(row[orientation_key])
            if stamp in unique:
                old_p, old_q = unique[stamp]
                if np.max(np.abs(old_p-p)) > 1.e-8 or abs(float(np.dot(old_q, q))) < 1-1.e-10:
                    self.conflicts.add(stamp)
            unique[stamp] = p, q
        self.stamps = sorted(unique)
        self.values = [unique[t] for t in self.stamps]

    def at(self, stamp, max_gap_ns):
        i = bisect.bisect_left(self.stamps, stamp)
        if i < len(self.stamps) and self.stamps[i] == stamp:
            if stamp in self.conflicts:
                raise ValueError("Conflicting poses at identical timestamp")
            p, q = self.values[i]
            return matrix(p, q), {"mode": "exact", "stamps_ns": [stamp], "fraction": 0.}
        if i == 0 or i == len(self.stamps):
            raise ValueError("Timestamp outside recorded interpolation bracket")
        a, b = self.stamps[i-1:i+1]
        if b-a > max_gap_ns or a in self.conflicts or b in self.conflicts:
            raise ValueError("Interpolation bracket too wide or conflicting")
        u = (stamp-a)/(b-a)
        p0, q0 = self.values[i-1]
        p1, q1 = self.values[i]
        return matrix(p0+u*(p1-p0), slerp(q0, q1, u)), {
            "mode": "bracketed", "stamps_ns": [a, b], "fraction": u,
        }


class RecordedTF:
    def __init__(self, rows, max_gap_ns):
        self.max_gap_ns = max_gap_ns
        grouped = collections.defaultdict(list)
        for row in rows:
            grouped[(row["frame_id"], row["child_frame_id"], row.get("source_topic"))].append(row)
        self.edges = {}
        for (parent, child, topic), values in grouped.items():
            timeline = Timeline(values, "translation_m", "rotation_xyzw")
            if topic == "/tf_static":
                first = matrix(values[0]["translation_m"], values[0]["rotation_xyzw"])
                if any(np.max(np.abs(first-matrix(v["translation_m"], v["rotation_xyzw"]))) > 1.e-8 for v in values):
                    raise ValueError("Static TF changes within trace")
                self.edges[parent, child] = (first, topic)
            elif topic == "/tf":
                self.edges[parent, child] = (timeline, topic)
            else:
                raise ValueError("Unrecognized recorded TF source")

    def lookup(self, parent, child, stamp):
        if parent == child:
            return np.eye(4), []
        queue = collections.deque([(parent, np.eye(4), [])])
        visited = {parent}
        while queue:
            current, transform, evidence = queue.popleft()
            for (a, b), (timeline, topic) in self.edges.items():
                if current not in (a, b):
                    continue
                target = b if current == a else a
                if target in visited:
                    continue
                try:
                    local, bracket = ((timeline, {"mode": "static"}) if topic == "/tf_static"
                                      else timeline.at(stamp, self.max_gap_ns))
                except ValueError:
                    continue
                if current == b:
                    local = np.linalg.inv(local)
                composed = transform @ local
                chain = evidence + [{"parent": a, "child": b, "inverse": current == b,
                                     "source_topic": topic, **bracket}]
                if target == child:
                    return composed, chain
                visited.add(target)
                queue.append((target, composed, chain))
        raise ValueError("No complete recorded TF chain at scan timestamp")


def read_pgm(path):
    raw = Path(path).read_bytes()
    token = re.compile(rb"(?:\s|\#[^\r\n]*(?:\r?\n|$))*([^\s#]+)")
    values, offset = [], 0
    for unused in range(4):
        match = token.match(raw, offset)
        if match is None:
            raise ValueError("Malformed PGM header")
        values.append(match.group(1))
        offset = match.end()
    magic, width, height, maximum = values
    width, height, maximum = int(width), int(height), int(maximum)
    if width <= 0 or height <= 0 or width*height > 25_000_000 or not 0 < maximum <= 255:
        raise ValueError("Unsupported PGM dimensions or depth")
    if magic != b"P5" or offset >= len(raw) or raw[offset:offset+1] not in b" \t\r\n":
        raise ValueError("Expected bounded 8-bit binary PGM")
    offset += 2 if raw[offset:offset+2] == b"\r\n" else 1
    pixels = np.frombuffer(raw[offset:], dtype=np.uint8)
    if len(pixels) != width*height:
        raise ValueError("PGM pixel length does not match dimensions")
    return np.flipud(pixels.reshape(height, width)).copy(), maximum


class MapCell(ctypes.Structure):
    _fields_ = [("occ_state", ctypes.c_int), ("occ_dist", ctypes.c_double)]


class AmclMap(ctypes.Structure):
    _fields_ = [("origin_x", ctypes.c_double), ("origin_y", ctypes.c_double),
                ("scale", ctypes.c_double), ("size_x", ctypes.c_int), ("size_y", ctypes.c_int),
                ("cells", ctypes.POINTER(MapCell)), ("max_occ_dist", ctypes.c_double)]


def find_map_library(explicit=None):
    if explicit:
        candidates = [Path(explicit)]
    else:
        repo = Path(__file__).resolve().parents[2]
        candidates = [repo/"artifacts/runtime/navigation/windows/map_lib.dll",
                      Path(sys.prefix)/"Library/bin/map_lib.dll", Path(sys.prefix)/"lib/libmap_lib.so"]
        for prefix in os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep):
            if prefix:
                candidates.extend([Path(prefix)/"lib/libmap_lib.so", Path(prefix)/"bin/map_lib.dll"])
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError("Installed Nav2 map_lib is required; provide --map-library")


class LikelihoodMap:
    def __init__(self, yaml_path, max_distance, library_path):
        self.yaml_path = Path(yaml_path).resolve()
        spec = yaml.safe_load(self.yaml_path.read_text(encoding="utf-8-sig"))
        if spec.get("mode", "trinary") != "trinary" or abs(float(spec["origin"][2])) > 1.e-12:
            raise ValueError("Only the current zero-yaw trinary AMCL map contract is supported")
        self.image_path = (self.yaml_path.parent/spec["image"]).resolve()
        pixels, maximum = read_pgm(self.image_path)
        self.height, self.width = pixels.shape
        self.resolution = float(np.float32(spec["resolution"]))  # OccupancyGrid float32 field.
        if not 0 < self.resolution < 10 or not 0 < max_distance < 100:
            raise ValueError("Invalid map scale or likelihood cutoff")
        self.origin = [float(spec["origin"][0]), float(spec["origin"][1])]
        self.center = [self.origin[0]+(self.width//2)*self.resolution,
                       self.origin[1]+(self.height//2)*self.resolution]
        occupancy = pixels.astype(float)/maximum
        if not spec.get("negate", 0):
            occupancy = 1.-occupancy
        self.states = np.where(occupancy > spec["occupied_thresh"], 1,
                               np.where(occupancy < spec["free_thresh"], -1, 0)).astype(np.int32)
        self.max_distance = float(max_distance)
        self.library_path = find_map_library(library_path)
        self._dll_directory = (os.add_dll_directory(str(self.library_path.parent)) if os.name == "nt" else None)
        self._library = ctypes.CDLL(str(self.library_path))
        function = self._library.map_update_cspace
        function.argtypes = [ctypes.POINTER(AmclMap), ctypes.c_double]
        function.restype = None
        dtype = np.dtype([("occ_state", np.int32), ("occ_dist", np.float64)], align=True)
        if dtype.itemsize != ctypes.sizeof(MapCell) or ctypes.sizeof(AmclMap) != 48:
            raise ValueError("Unexpected native AMCL map ABI; require verified 64-bit layout")
        self._cells = np.zeros(self.width*self.height, dtype=dtype)
        self._cells["occ_state"] = self.states.ravel()
        native = AmclMap(*self.center, self.resolution, self.width, self.height,
                         self._cells.ctypes.data_as(ctypes.POINTER(MapCell)), 0.)
        function(ctypes.byref(native), self.max_distance)
        self.distances = self._cells["occ_dist"].reshape(self.height, self.width).copy()
        if not np.isfinite(self.distances).all() or (self.distances < 0).any():
            raise ValueError("Native AMCL returned invalid distances")
        self.nearest_cache = {}

    def indices(self, xy):
        xy = np.asarray(xy, dtype=np.float64)
        indices = np.floor((xy-np.asarray(self.center))/self.resolution+.5).astype(np.int64)
        indices += np.array([self.width//2, self.height//2])
        return indices

    def evaluate(self, xy):
        indices = self.indices(xy)
        good = ((indices[:, 0] >= 0) & (indices[:, 0] < self.width) &
                (indices[:, 1] >= 0) & (indices[:, 1] < self.height))
        distance = np.full(len(xy), self.max_distance)
        distance[good] = self.distances[indices[good, 1], indices[good, 0]]
        return distance, indices, good

    def nearest_occupied(self, index):
        """Spatial annotation; primary score always uses native occ_dist above."""
        x, y = map(int, index)
        key = x, y
        if key in self.nearest_cache:
            return self.nearest_cache[key]
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        radius = math.ceil(self.max_distance/self.resolution)
        x0, x1 = max(0, x-radius), min(self.width, x+radius+1)
        y0, y1 = max(0, y-radius), min(self.height, y+radius+1)
        yy, xx = np.nonzero(self.states[y0:y1, x0:x1] == 1)
        if len(xx) == 0:
            result = None
        else:
            xx, yy = xx+x0, yy+y0
            squared = (xx-x)**2+(yy-y)**2
            i = int(np.argmin(squared))
            result = ([float(self.origin[0]+int(xx[i])*self.resolution),
                       float(self.origin[1]+int(yy[i])*self.resolution)]
                      if math.sqrt(float(squared[i]))*self.resolution <= self.max_distance else None)
        self.nearest_cache[key] = result
        return result


def beam_selection(scan, params):
    ranges = np.array([float(x) for x in scan["ranges"]], dtype=np.float64)
    if not 2 <= len(ranges) <= 100_000:
        raise ValueError("Invalid scan length")
    max_beams = int(params["max_beams"])
    if max_beams < 2:
        raise ValueError("AMCL max_beams must be at least two")
    maximum, minimum = float(scan["range_max"]), float(scan["range_min"])
    if params.get("laser_max_range", -1) > 0:
        maximum = min(maximum, float(np.float32(params["laser_max_range"])))
    if params.get("laser_min_range", -1) > 0:
        minimum = max(minimum, float(np.float32(params["laser_min_range"])))
    if not 0 <= minimum < maximum or not math.isfinite(maximum):
        raise ValueError("Invalid scan/model range limits")
    step = max(1, (len(ranges)-1)//(max_beams-1))
    sampled = np.arange(0, len(ranges), step)
    eligible = np.isfinite(ranges) & (ranges > minimum) & (ranges < maximum)
    indices = sampled[eligible[sampled]]
    angle_min, raw_increment = float(scan["angle_min"]), float(scan["angle_increment"])
    if not math.isfinite(angle_min) or not math.isfinite(raw_increment) or raw_increment <= 0:
        raise ValueError("Invalid recorded scan angles")
    # The source node transforms two QuaternionStamped values into its base frame.
    # Here scan_frame == base_frame; retain the float32 addition before getYaw/fmod.
    first_yaw = math.atan2(math.sin(angle_min), math.cos(angle_min))
    second_angle = float(np.float32(np.float32(angle_min)+np.float32(raw_increment)))
    second_yaw = math.atan2(math.sin(second_angle), math.cos(second_angle))
    increment = math.fmod(second_yaw-first_yaw+5*math.pi, 2*math.pi)-math.pi
    bearings = first_yaw + indices*increment
    return ranges[indices], bearings, indices, maximum, {
        "range_count": len(ranges), "finite_range_count": int(np.isfinite(ranges).sum()),
        "eligible_finite_count": int(eligible.sum()), "selection_step": step,
        "selected_index_count": len(sampled), "used_beam_count": len(indices),
        "effective_range_min": minimum, "effective_range_max": maximum,
        "effective_amcl_angle_min": first_yaw, "effective_amcl_angle_increment": increment,
    }


def score(field, pose, ranges, bearings, maximum, params):
    angles = bearings+pose[2]
    points = np.column_stack([pose[0]+ranges*np.cos(angles), pose[1]+ranges*np.sin(angles)])
    distances, indices, good = field.evaluate(points)
    probability = params["z_hit"]*np.exp(-(distances**2)/(2*params["sigma_hit"]**2)) + params["z_rand"]/maximum
    if not np.isfinite(probability).all() or (probability < 0).any() or (probability > 1+1.e-12).any():
        raise ValueError("AMCL model probability outside supported range")
    contribution = probability**3
    return {"score": float(1.+contribution.sum()), "points": points, "distances": distances,
            "indices": indices, "inside": good, "contribution": contribution}


def numeric_summary(values):
    values = np.asarray(values, dtype=float)
    return ({"count": len(values), "mean": float(values.mean()), "median": float(np.median(values)),
             "p95": float(np.quantile(values, .95)), "min": float(values.min()), "max": float(values.max())}
            if len(values) else {"count": 0})


def group_summary(beams, field):
    groups = collections.defaultdict(list)
    for beam in beams:
        groups[str(beam[field])].append(beam)
    result = []
    for key, rows in groups.items():
        delta = [b["score_estimate_minus_evaluation"] for b in rows]
        result.append({"group": key, "beams": len(rows), "scans": len({b["scan_stamp_ns"] for b in rows}),
                       "score_delta_sum": float(sum(delta)), "score_delta_absolute_sum": float(sum(abs(x) for x in delta)),
                       "estimated_distance_m": numeric_summary([b["estimated_distance_m"] for b in rows]),
                       "evaluation_distance_m": numeric_summary([b["evaluation_distance_m"] for b in rows])})
    return sorted(result, key=lambda x: x["score_delta_absolute_sum"], reverse=True)


def analyze(args):
    session = Path(args.session).resolve()
    trace_path = Path(args.trace).resolve() if args.trace else session/"actions/trace.json"
    provenance_path = session/"runtime-provenance.json"
    trace, provenance = load_json(trace_path), load_json(provenance_path)
    meta = trace.get("metadata", {})
    if meta.get("schema_version") != 2 or meta.get("runtime_provenance_sha256") != digest(provenance_path):
        raise ValueError("Rich trace schema or provenance hash mismatch")
    output = Path(args.output).resolve() if args.output else session/"scan-map-alignment"
    existing = [name for name in ("summary.json", "scan-scores.json", "beam-residuals.json")
                if (output/name).exists()]
    if existing:
        raise ValueError("Output already contains analysis evidence; use a fresh --output: " + ", ".join(existing))
    for key in ("module_sha256", "engine_config_sha256", "source_control_config_sha256"):
        if meta.get(key) != provenance.get(key):
            raise ValueError("Trace/runtime identity mismatch: " + key)
    if not args.evaluation_world_is_map:
        raise ValueError("Declare --evaluation-world-is-map only for the shared source-geometry coordinate contract")
    configurations = list((session/"navigation").glob("*/parameters.yaml"))
    if len(configurations) != 1:
        raise ValueError("Require one unambiguous native navigation parameter snapshot")
    config_path = configurations[0]
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    params = cfg["amcl"]["ros__parameters"]
    if params.get("laser_model_type") != "likelihood_field":
        raise ValueError("Only original likelihood_field, not likelihood_field_prob, is supported")
    if params["sigma_hit"] <= 0:
        raise ValueError("Invalid AMCL hit sigma")
    scene = provenance["scene"]
    yaml_path = Path(scene["map_yaml"])
    runtime_field = LikelihoodMap(yaml_path, params["laser_likelihood_max_dist"], args.map_library)
    if digest(runtime_field.image_path) != scene["raster_sha256"]:
        raise ValueError("Actual raster differs from runtime provenance")
    field = runtime_field
    candidate_yaml = getattr(args, "candidate_map_yaml", None)
    candidate_manifest = getattr(args, "candidate_manifest", None)
    if bool(candidate_yaml) != bool(candidate_manifest):
        raise ValueError("An offline candidate requires both --candidate-map-yaml and --candidate-manifest")
    candidate_record, candidate_path = None, None
    if candidate_yaml:
        if not args.output:
            raise ValueError("An offline candidate requires an explicit fresh --output directory")
        candidate_path = Path(candidate_manifest).resolve()
        candidate_record = load_json(candidate_path)
        candidate_yaml = Path(candidate_yaml).resolve()
        if (candidate_record.get("map_yaml_sha256") != digest(candidate_yaml) or
                candidate_record.get("reference_navigation_raster_sha256") != scene["raster_sha256"]):
            raise ValueError("Candidate manifest YAML or reference runtime raster hash mismatch")
        field = LikelihoodMap(candidate_yaml, params["laser_likelihood_max_dist"], args.map_library)
        if candidate_record.get("raster_sha256") != digest(field.image_path):
            raise ValueError("Candidate raster differs from its manifest")
        if ((field.width, field.height, field.resolution, field.origin) !=
                (runtime_field.width, runtime_field.height, runtime_field.resolution, runtime_field.origin)):
            raise ValueError("Offline candidate must preserve the runtime grid dimensions, scale, and origin")
    scans = trace.get("obstacle_scans", [])
    if not scans:
        raise ValueError("Trace contains no raw obstacle scans")
    identities = {(r.get("session"), r.get("epoch")) for r in scans if r.get("session") is not None}
    if len(identities) != 1:
        raise ValueError("Require one native session/epoch for scan analysis")
    identity = next(iter(identities))
    for name in ("selected_transforms", "truth_poses", "amcl_poses"):
        if any((r.get("session"), r.get("epoch")) != identity for r in trace[name] if r.get("session") is not None):
            raise ValueError("Cross-session evidence in " + name)
    truth_rows = trace["truth_poses"]
    if any(r["frame_id"] != args.evaluation_world_frame for r in truth_rows):
        raise ValueError("Unexpected independent evaluation world frame")
    truth = Timeline(truth_rows, "position_m", "orientation_xyzw")
    tf = RecordedTF(trace["selected_transforms"], round(args.max_bracket_seconds*1.e9))
    poses = collections.defaultdict(list)
    for row in trace["amcl_poses"]:
        if row["frame_id"] != params["global_frame_id"]:
            raise ValueError("AMCL pose is not in configured map frame")
        poses[int(row["stamp_ns"])].append(row)
    frames, beams, skipped = [], [], collections.Counter()
    for scan in scans:
        stamp = int(scan["stamp_ns"])
        ranges, bearings, selected, maximum, counts = beam_selection(scan, params)
        row = {"stamp_ns": stamp, "stamp_s": stamp*1.e-9, "probe_phase": scan.get("probe_phase"),
               "goal_request_id": scan.get("goal_request_id"), "frame_id": scan["frame_id"], **counts}
        frames.append(row)
        try:
            if scan["frame_id"] != params["base_frame_id"]:
                raise ValueError("Scan/base frames differ; cached laser mounting pose is not recorded")
            if stamp not in poses:
                raise ValueError("No exact-stamp published AMCL pose")
            if len(poses[stamp]) != 1:
                raise ValueError("Multiple published AMCL poses at scan stamp")
            if len(ranges) == 0:
                raise ValueError("No selected finite model beams")
            pose = poses[stamp][0]
            estimated = matrix(pose["position_m"], pose["orientation_xyzw"])
            body, bracket = truth.at(stamp, round(args.max_bracket_seconds*1.e9))
            mount, chain = tf.lookup(args.evaluation_body_frame, scan["frame_id"], stamp)
            evaluation = body @ mount
            est, ev = score(field, planar(estimated), ranges, bearings, maximum, params), score(field, planar(evaluation), ranges, bearings, maximum, params)
            row.update(status="scored", estimated_pose_xy_yaw=planar(estimated), evaluation_pose_xy_yaw=planar(evaluation),
                       evaluation_scan_tilt_degrees=tilt_degrees(evaluation), truth_interpolation=bracket,
                       body_to_scan_tf=chain, estimated_score=est["score"], evaluation_score=ev["score"],
                       score_estimate_minus_evaluation=est["score"]-ev["score"],
                       estimated_distances_m=numeric_summary(est["distances"]), evaluation_distances_m=numeric_summary(ev["distances"]),
                       estimated_off_map_beams=int((~est["inside"]).sum()), evaluation_off_map_beams=int((~ev["inside"]).sum()))
            raw_bearings = float(scan["angle_min"])+selected*float(scan["angle_increment"])
            local_points = np.column_stack([ranges*np.cos(raw_bearings), ranges*np.sin(raw_bearings), np.zeros(len(ranges)), np.ones(len(ranges))])
            full_points = (evaluation @ local_points.T).T[:, :2]
            full_distances = field.evaluate(full_points)[0]
            row["evaluation_full_transform_xy_distances_m"] = numeric_summary(full_distances)
            row["planar_vs_full_transform_xy_max_m"] = float(np.linalg.norm(full_points-ev["points"], axis=1).max())
            for i, beam_index in enumerate(selected):
                nearest = field.nearest_occupied(ev["indices"][i])
                tile = ([math.floor(x/.5)*.5 for x in nearest] if nearest is not None else None)
                degrees = math.degrees(bearings[i]+planar(evaluation)[2]) % 360
                sector = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")[int((degrees+22.5)//45)%8]
                beams.append({"scan_stamp_ns": stamp, "beam_index": int(beam_index), "range_m": float(ranges[i]),
                              "raw_scan_bearing_rad": float(raw_bearings[i]), "amcl_model_bearing_rad": float(bearings[i]), "evaluation_world_bearing_deg": degrees,
                              "world_sector": sector, "probe_phase": scan.get("probe_phase"),
                              "estimated_endpoint_xy_m": est["points"][i].tolist(), "evaluation_endpoint_xy_m": ev["points"][i].tolist(),
                              "estimated_distance_m": float(est["distances"][i]), "evaluation_distance_m": float(ev["distances"][i]),
                              "score_estimate_minus_evaluation": float(est["contribution"][i]-ev["contribution"][i]),
                              "evaluation_nearest_occupied_xy_m": nearest,
                              "occupied_structure_tile_lower_xy_m": tile if tile is not None else "none_within_cutoff_or_off_map"})
        except ValueError as error:
            row.update(status="unscored", reason=str(error))
            skipped[str(error)] += 1
    scored = [r for r in frames if r["status"] == "scored"]
    if not scored:
        raise ValueError("No exact-stamp scan/AMCL/evaluation pairs could be scored")
    output.mkdir(parents=True, exist_ok=True)
    beam_path, frames_path = output/"beam-residuals.json", output/"scan-scores.json"
    write_json(beam_path, beams)
    write_json(frames_path, frames)
    phases = {}
    for phase in sorted({r.get("probe_phase") or "unknown" for r in scored}):
        rows = [r for r in scored if (r.get("probe_phase") or "unknown") == phase]
        phases[phase] = {"scans": len(rows), "first_stamp_s": rows[0]["stamp_s"], "last_stamp_s": rows[-1]["stamp_s"],
                         "score_delta": numeric_summary([r["score_estimate_minus_evaluation"] for r in rows]),
                         "estimate_higher_score_scans": sum(r["score_estimate_minus_evaluation"] > 0 for r in rows),
                         "estimated_score": numeric_summary([r["estimated_score"] for r in rows]),
                         "evaluation_score": numeric_summary([r["evaluation_score"] for r in rows])}
    input_paths = [trace_path, provenance_path, config_path, runtime_field.yaml_path,
                   runtime_field.image_path, field.yaml_path, field.image_path, Path(__file__)]
    if candidate_path:
        input_paths.append(candidate_path)
    report = {
        "schema_version": 1, "status": "diagnostic_complete", "production_eligible": False,
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "session_directory": str(session),
        "session_identity": list(identity), "raw_scan_messages": len(scans), "scored_scan_messages": len(scored),
        "unscored_reasons": dict(skipped), "scored_beams": len(beams), "phases": phases,
        "model": {"name": "Nav2 1.1.20 likelihood_field", "formula": "1 + sum((z_hit * exp(-d*d/(2*sigma_hit*sigma_hit)) + z_rand/range_max)^3)",
                  "parameters": {k: params[k] for k in ["z_hit", "z_rand", "sigma_hit", "laser_likelihood_max_dist", "max_beams", "laser_min_range", "laser_max_range"]},
                  "beam_step": "max(1, floor((range_count-1)/(max_beams-1))); preserve original indices",
                  "native_distance_library": str(field.library_path), "native_distance_library_sha256": digest(field.library_path),
                  "distance_function": "map_update_cspace, called on private offline memory", "primary_sources": UPSTREAM},
        "map": {"role": "offline_geometry_candidate" if candidate_record is not None else "actual_runtime_map",
                "yaml": str(field.yaml_path), "image": str(field.image_path), "width": field.width, "height": field.height,
                "resolution_as_occupancy_grid_float32": field.resolution, "origin_xy": field.origin,
                "occupied_cells": int((field.states == 1).sum()), "unknown_cells": int((field.states == 0).sum()),
                "index_rule": "floor((world-origin_center)/scale+0.5)+size/2, exactly AMCL map.hpp"},
        "runtime_map_identity": {"yaml": str(runtime_field.yaml_path), "image": str(runtime_field.image_path),
                                 "yaml_sha256": digest(runtime_field.yaml_path), "raster_sha256": digest(runtime_field.image_path)},
        "candidate_manifest": ({"path": str(candidate_path), "sha256": digest(candidate_path), "contents": candidate_record}
                               if candidate_path else None),
        "direction_contributions": group_summary(beams, "world_sector"),
        "occupied_structure_tiles": group_summary(beams, "occupied_structure_tile_lower_xy_m")[:24],
        "largest_per_scan_score_differences": sorted(scored, key=lambda r: abs(r["score_estimate_minus_evaluation"]), reverse=True)[:12],
        "evaluation_scan_tilt_degrees": numeric_summary([r["evaluation_scan_tilt_degrees"] for r in scored]),
        "planar_vs_full_transform_xy_max_m": numeric_summary([r["planar_vs_full_transform_xy_max_m"] for r in scored]),
        "coordinate_contract": {"evaluation_world_frame": args.evaluation_world_frame, "evaluation_body_frame": args.evaluation_body_frame,
                                "map_frame": params["global_frame_id"], "evaluation_world_is_same_source_geometry_map": True,
                                "arbitrary_registration_or_offsets": False, "max_interpolation_bracket_seconds": args.max_bracket_seconds},
        "input_sha256": {str(p): digest(p) for p in input_paths},
        "detail_files": {"frames": {"path": str(frames_path), "sha256": digest(frames_path)},
                         "beams": {"path": str(beam_path), "sha256": digest(beam_path)}},
        "scope_boundaries": [
            "Only exact-stamp published AMCL pose/scan pairs are scored; other received scans retain counts and explicit rejection reasons.",
            "A published posterior pose is a representative estimate, not the entire particle set; scores do not reconstruct filter stochastic history.",
            "Two recorded poses are compared. No pose optimization, map translation, control correction, or truth input to localization is performed.",
            "An offline candidate scores the same recorded runtime AMCL/evaluation poses; it does not claim a new native localization result.",
            "Truth is independently recorded evaluation data; no timestamp extrapolation or last-value holding is allowed.",
            "Primary scoring is the same planar model at both poses; full-transform XY residuals and scan-plane tilt expose projection differences.",
            "Structure tiles locate nearby occupied PGM cells; they do not identify CAD materials or prove a physical object label.",
            "A score preference alone does not establish whether filtering noise, terrain projection, or map semantics caused localization error.",
            "Coverage is restricted to recorded finite beams and the declared session, with no full-field accuracy claim.",
        ],
    }
    write_json(output/"summary.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--trace")
    parser.add_argument("--output")
    parser.add_argument("--map-library", help="Installed same-version Nav2 map_lib DLL/shared library")
    parser.add_argument("--candidate-map-yaml", help="Optional geometry-derived offline map; runtime inputs remain unchanged")
    parser.add_argument("--candidate-manifest", help="Candidate YAML/raster/reference hashes and geometry provenance")
    parser.add_argument("--evaluation-body-frame", required=True)
    parser.add_argument("--evaluation-world-frame", default="rmsim_world")
    parser.add_argument("--evaluation-world-is-map", action="store_true")
    parser.add_argument("--max-bracket-seconds", type=float, default=.25)
    args = parser.parse_args(argv)
    if not 0 < args.max_bracket_seconds <= .5:
        parser.error("max bracket must be positive and at most .5 seconds")
    try:
        report = analyze(args)
    except (ValueError, OSError, KeyError, AttributeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({k: report[k] for k in ("status", "raw_scan_messages", "scored_scan_messages", "unscored_reasons", "scored_beams", "phases")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
