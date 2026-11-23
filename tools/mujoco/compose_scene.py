"""Compose a decoupled MuJoCo scene from any canonical vehicle package and map package.

Produces separate map.xml and vehicle.xml files combined via MuJoCo <include> in
model.xml, plus a manifest.json with schema 'rmsim_mujoco_scene_v2'.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import itertools
import json
import math
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from geometry import digest, extract_rmuc, load_geometry
from prepare_model import add_static, height_at, load_json, numbers


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sub(parent: ET.Element, tag: str, **attributes) -> ET.Element:
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attributes.items()})


def rpy_to_quat_wxyz(rpy) -> list[float]:
    r, p, y = (float(rpy[0]), float(rpy[1]), float(rpy[2]))
    cr, sr = math.cos(r * 0.5), math.sin(r * 0.5)
    cp, sp = math.cos(p * 0.5), math.sin(p * 0.5)
    cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    yv = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    return [w, x, yv, z]


def rpy_to_rotmat(rpy) -> np.ndarray:
    r, p, y = (float(rpy[0]), float(rpy[1]), float(rpy[2]))
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]], dtype=float)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=float)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]], dtype=float)
    return rz @ ry @ rx


def resolve_canonical_vehicle(vehicle_arg: Path) -> tuple[dict, Path]:
    path = vehicle_arg.resolve()
    if path.is_dir():
        canonical = path / "vehicle.canonical.json"
        if canonical.is_file():
            return load_json(canonical), canonical
        manifest = path / "vehicle.json"
        if manifest.is_file():
            from rm_vehicle_tools.vehicle import load_vehicle

            return load_vehicle(manifest), manifest
        raise FileNotFoundError(f"No vehicle.canonical.json or vehicle.json in {path}")
    data = load_json(path)
    if data.get("kind") == "rm_vehicle_canonical":
        return data, path
    from rm_vehicle_tools.vehicle import load_vehicle

    return load_vehicle(path), path


def resolve_map(map_arg: Path) -> tuple[dict, Path]:
    path = map_arg.resolve()
    if path.is_dir():
        path = path / "map.json"
    if not path.is_file():
        raise FileNotFoundError(f"Map file not found: {path}")
    return load_json(path), path


def compute_lowest_z_in_root(vehicle: dict) -> float:
    links = {l["name"]: l for l in vehicle["links"]}
    root_name = vehicle["root_link"]
    children: dict[str, list[dict]] = {name: [] for name in links}
    for j in vehicle["joints"]:
        children[j["parent"]].append(j)

    transforms: dict[str, tuple[np.ndarray, np.ndarray]] = {
        root_name: (np.zeros(3), np.eye(3))
    }
    stack = [root_name]
    while stack:
        parent = stack.pop()
        p_pos, p_rot = transforms[parent]
        for j in children[parent]:
            o = j["origin"]
            l_pos = np.asarray(o["xyz"], dtype=float)
            l_rot = rpy_to_rotmat(o["rpy"])
            c_pos = p_pos + p_rot @ l_pos
            c_rot = p_rot @ l_rot
            transforms[j["child"]] = (c_pos, c_rot)
            stack.append(j["child"])

    lowest = 0.0
    for name, link in links.items():
        b_pos, b_rot = transforms[name]
        for col in link.get("collisions", []):
            o = col["origin"]
            g_pos = b_pos + b_rot @ np.asarray(o["xyz"], dtype=float)
            g_rot = b_rot @ rpy_to_rotmat(o["rpy"])
            geom = col["geometry"]
            gtype = geom["type"]
            if gtype == "box":
                half = np.asarray(geom["size"], dtype=float) * 0.5
                z_ext = float(np.sum(np.abs(g_rot[2, :]) * half))
            elif gtype == "cylinder":
                r = float(geom["radius"])
                hl = float(geom["length"]) * 0.5
                # Z extent of cylinder aligned with local Z
                az = abs(float(g_rot[2, 2]))
                sin_t = math.sqrt(max(0.0, 1.0 - az * az))
                z_ext = hl * az + r * sin_t
            elif gtype == "sphere":
                z_ext = float(geom["radius"])
            else:
                z_ext = 0.0
            lowest = min(lowest, float(g_pos[2]) - z_ext)
    return lowest


def build_flat_map_xml(map_spec: dict, contact_cfg: dict) -> tuple[ET.Element, list[dict]]:
    root = ET.Element("mujoco")
    world = sub(root, "worldbody")
    fric = numbers([
        contact_cfg["static_sliding_friction"],
        contact_cfg["torsional_friction"],
        contact_cfg["rolling_friction"],
    ])
    solref = numbers(contact_cfg["solref"])
    solimp = numbers(contact_cfg["solimp"])
    condim = contact_cfg["condim"]
    mappings: list[dict] = []

    floor = map_spec["floor"]
    fx, fy = float(floor["size_m"][0]), float(floor["size_m"][1])
    fz = float(floor["surface_z_m"])
    ft = float(floor["thickness_m"])
    sub(
        world,
        "geom",
        name="map_floor",
        type="box",
        pos=numbers([0.0, 0.0, fz - ft * 0.5]),
        size=numbers([fx * 0.5, fy * 0.5, ft * 0.5]),
        contype="2",
        conaffinity="1",
        priority="0",
        condim=condim,
        friction=fric,
        solref=solref,
        solimp=solimp,
        rgba="0.16 0.19 0.23 1",
    )
    mappings.append({"name": "map_floor", "kind": "flat_floor", "size_m": [fx, fy, ft], "surface_z_m": fz})

    bnd = map_spec["boundary"]
    bh = float(bnd["height_m"])
    bt = float(bnd["thickness_m"])
    walls = [
        ("north", [0.0, fy * 0.5 + bt * 0.5, fz + bh * 0.5], [fx * 0.5 + bt, bt * 0.5, bh * 0.5]),
        ("south", [0.0, -fy * 0.5 - bt * 0.5, fz + bh * 0.5], [fx * 0.5 + bt, bt * 0.5, bh * 0.5]),
        ("east", [fx * 0.5 + bt * 0.5, 0.0, fz + bh * 0.5], [bt * 0.5, fy * 0.5 + bt, bh * 0.5]),
        ("west", [-fx * 0.5 - bt * 0.5, 0.0, fz + bh * 0.5], [bt * 0.5, fy * 0.5 + bt, bh * 0.5]),
    ]
    for wname, pos, half in walls:
        sub(
            world,
            "geom",
            name=f"arena_{wname}",
            type="box",
            pos=numbers(pos),
            size=numbers(half),
            contype="2",
            conaffinity="1",
            priority="0",
            condim=condim,
            friction=fric,
            solref=solref,
            solimp=solimp,
            rgba="0.3 0.34 0.38 1",
        )
        mappings.append({"name": f"arena_{wname}", "kind": "boundary_box", "position_m": pos, "half_size_m": half})

    for obs in map_spec.get("obstacles", []):
        oid = obs["id"]
        pos = [float(v) for v in obs["center_m"]]
        half = [float(v) * 0.5 for v in obs["size_m"]]
        yaw = float(obs.get("yaw_rad", 0.0))
        q = [math.cos(yaw * 0.5), 0.0, 0.0, math.sin(yaw * 0.5)]
        sub(
            world,
            "geom",
            name=f"obs_{oid}",
            type="box",
            pos=numbers(pos),
            quat=numbers(q),
            size=numbers(half),
            contype="2",
            conaffinity="1",
            priority="0",
            condim=condim,
            friction=fric,
            solref=solref,
            solimp=solimp,
            rgba="0.28 0.38 0.48 1",
        )
        mappings.append({"name": f"obs_{oid}", "kind": "flat_obstacle", "position_m": pos, "half_size_m": half})

    return root, mappings


def build_mesh_map_xml(
    repo: Path,
    map_spec: dict,
    map_path: Path,
    geometry_dir: Path | None,
    output: Path,
    contact_cfg: dict,
) -> tuple[ET.Element, list[dict], list]:
    shell_path = map_path.parent / "arena-shell.json"
    if not shell_path.is_file():
        raise FileNotFoundError(f"Mesh map requires arena-shell.json: {shell_path}")
    shell = load_json(shell_path)
    if geometry_dir is None:
        # Reuse existing extracted RMUC geometry if available to stay fast.
        candidates = [
            repo / "artifacts/mujoco/rmuc2027-geometry",
            repo / "assets/vehicles/aim_sentry/mujoco/rmuc2027/raw_geometry",
        ]
        found = next((c for c in candidates if (c / "geometry.json").is_file()), None)
        if found is not None:
            geometry_dir = found
        else:
            geometry_dir = output / "raw_geometry"
            extract_rmuc(repo, geometry_dir)
    _, groups = load_geometry(geometry_dir, repo)
    root = ET.Element("mujoco")
    sub(root, "asset")
    sub(root, "worldbody")
    mappings = add_static(root, groups, shell, {"contact": contact_cfg})
    return root, mappings, groups


def build_vehicle_xml(
    vehicle: dict,
    spawn_pos: list[float],
    spawn_quat_wxyz: list[float],
    contact_cfg: dict,
) -> tuple[ET.Element, dict]:
    root = ET.Element("mujoco")
    world = sub(root, "worldbody")
    contact = sub(root, "contact")
    act = sub(root, "actuator")

    root_link = vehicle["root_link"]
    control = vehicle["control"]
    control_type = control["type"]
    wheel_joints = set(control.get("wheel_joints", []))
    gimbal = control.get("gimbal") or {}
    yaw_joint_name = gimbal.get("yaw_joint", "")
    pitch_joint_name = gimbal.get("pitch_joint", "")

    links = {l["name"]: l for l in vehicle["links"]}
    children: dict[str, list[dict]] = {name: [] for name in links}
    joint_by_child: dict[str, dict] = {}
    for j in vehicle["joints"]:
        children[j["parent"]].append(j)
        joint_by_child[j["child"]] = j

    solref = numbers(contact_cfg["solref"])
    solimp = numbers(contact_cfg["solimp"])
    condim = contact_cfg["condim"]

    wheel_records: list[dict] = []
    joint_map: dict[str, str] = {root_link: "chassis_free"}
    actuator_map: dict[str, str] = {}
    ordered_bodies: list[str] = []
    yaw_body = ""
    pitch_body = ""

    def emit_body(link_name: str, parent_el: ET.Element, parent_joint: dict | None, p_pos: np.ndarray, p_rot: np.ndarray):
        nonlocal yaw_body, pitch_body
        link = links[link_name]
        ordered_bodies.append(link_name)

        if parent_joint is None:
            pos = spawn_pos
            quat = spawn_quat_wxyz
            cur_pos = np.zeros(3)
            cur_rot = np.eye(3)
        else:
            o = parent_joint["origin"]
            l_pos = np.asarray(o["xyz"], dtype=float)
            l_rot = rpy_to_rotmat(o["rpy"])
            pos = l_pos.tolist()
            quat = rpy_to_quat_wxyz(o["rpy"])
            cur_pos = p_pos + p_rot @ l_pos
            cur_rot = p_rot @ l_rot

        body_el = sub(parent_el, "body", name=link_name, pos=numbers(pos), quat=numbers(quat))

        if parent_joint is None:
            sub(body_el, "freejoint", name="chassis_free")
        else:
            jname = parent_joint["name"]
            jtype = parent_joint["type"]
            if jtype in ("continuous", "revolute"):
                mj_jname = f"j_{link_name}"
                mj_aname = f"a_{link_name}"
                j_el = sub(
                    body_el,
                    "joint",
                    name=mj_jname,
                    type="hinge",
                    axis=numbers(parent_joint["axis"]),
                    limited="true" if jtype == "revolute" else "false",
                )
                limits = parent_joint.get("limits") or {}
                if jtype == "revolute":
                    j_el.set("range", numbers([limits["lower"], limits["upper"]]))
                effort = float(limits.get("effort", 8.0))
                if jname in wheel_joints:
                    kp, kd = 0.0, 2.0
                    # Extract wheel radius from cylinder/sphere collision or visual
                    radius = 0.05
                    for shape in link.get("collisions", []) + link.get("visuals", []):
                        g = shape.get("geometry", {})
                        if "radius" in g:
                            radius = float(g["radius"])
                            break
                    # Wheel spin axis in root_link frame (ROS right-handed)
                    axis_in_root = cur_rot @ np.asarray(parent_joint["axis"], dtype=float)
                    axis_norm = float(np.linalg.norm(axis_in_root))
                    if axis_norm > 1e-9:
                        axis_in_root = axis_in_root / axis_norm
                    wheel_records.append(
                        {
                            "part": link_name,
                            "joint": jname,
                            "radius_m": radius,
                            "position_m": cur_pos.tolist(),
                            "axis_in_root": axis_in_root.tolist(),
                            "effort_nm": effort,
                        }
                    )
                elif jname == yaw_joint_name:
                    kp, kd = 80.0, 8.0
                    yaw_body = link_name
                elif jname == pitch_joint_name:
                    kp, kd = 80.0, 8.0
                    pitch_body = link_name
                else:
                    kp, kd = 20.0, 2.0

                sub(
                    act,
                    "general",
                    name=mj_aname,
                    joint=mj_jname,
                    gainprm="1",
                    biastype="affine",
                    biasprm=numbers([0.0, -kp, -kd]),
                    forcelimited="true",
                    forcerange=numbers([-effort, effort]),
                )
                joint_map[link_name] = mj_jname
                actuator_map[link_name] = mj_aname

        inert = link["inertial"]
        it = inert["inertia"]
        io = inert["origin"]
        imat = np.array(
            [
                [float(it["ixx"]), float(it["ixy"]), float(it["ixz"])],
                [float(it["ixy"]), float(it["iyy"]), float(it["iyz"])],
                [float(it["ixz"]), float(it["iyz"]), float(it["izz"])],
            ],
            dtype=float,
        )
        irot = rpy_to_rotmat(io["rpy"])
        imat_body = irot @ imat @ irot.T
        sub(
            body_el,
            "inertial",
            mass=float(inert["mass"]),
            pos=numbers(io["xyz"]),
            fullinertia=numbers(
                [
                    imat_body[0, 0],
                    imat_body[1, 1],
                    imat_body[2, 2],
                    imat_body[0, 1],
                    imat_body[0, 2],
                    imat_body[1, 2],
                ]
            ),
        )

        is_wheel = parent_joint is not None and parent_joint["name"] in wheel_joints
        if is_wheel:
            # High traction for differential drive; low friction for omni wheels so planar holonomic motion glides cleanly.
            mu = 0.9 if control_type == "differential_drive" else 0.08
        elif link_name == root_link:
            # Low chassis skid friction so 2-wheel diff bots balance cleanly without dragging.
            mu = 0.02
        else:
            mu = 0.4

        for idx, col in enumerate(link.get("collisions", [])):
            co = col["origin"]
            g = col["geometry"]
            gtype = g["type"]
            g_attrs = {
                "name": f"g_{link_name}_{idx}",
                "pos": numbers(co["xyz"]),
                "quat": numbers(rpy_to_quat_wxyz(co["rpy"])),
                "contype": "1",
                "conaffinity": "3",
                "priority": "1",
                "condim": condim,
                "friction": numbers([mu, 0.001, 0.0001]),
                "solref": solref,
                "solimp": solimp,
                "rgba": "0.2 0.4 0.7 1" if not is_wheel else "0.08 0.08 0.08 1",
                "group": "1",
            }
            if gtype == "box":
                g_attrs["type"] = "box"
                g_attrs["size"] = numbers([float(v) * 0.5 for v in g["size"]])
            elif gtype == "cylinder":
                g_attrs["type"] = "cylinder"
                g_attrs["size"] = numbers([float(g["radius"]), float(g["length"]) * 0.5])
            elif gtype == "sphere":
                g_attrs["type"] = "sphere"
                g_attrs["size"] = numbers([float(g["radius"])])
            else:
                raise ValueError(f"Unsupported collision geometry in canonical MuJoCo composer: {gtype}")
            sub(body_el, "geom", **g_attrs)

        for cj in children[link_name]:
            emit_body(cj["child"], body_el, cj, cur_pos, cur_rot)

    emit_body(root_link, world, None, np.zeros(3), np.eye(3))

    for a, b in itertools.combinations(ordered_bodies, 2):
        sub(contact, "exclude", body1=a, body2=b)

    meta = {
        "root_body": root_link,
        "yaw_body": yaw_body,
        "pitch_body": pitch_body,
        "control_type": control_type,
        "limits": control.get("limits", {}),
        "body_names": ordered_bodies,
        "joints": joint_map,
        "actuators": actuator_map,
        "wheels": wheel_records,
    }
    return root, meta


def compose_scene(
    repo: Path,
    vehicle_arg: Path,
    map_arg: Path,
    output: Path,
    spawn_id: str = "default",
    clearance: float = 0.02,
    timestep: float = 0.001,
    geometry_dir: Path | None = None,
    overwrite: bool = False,
) -> dict:
    if mujoco.__version__ != "4.0.1" or mujoco.mj_version() != 4000001:
        raise RuntimeError(f"Pinned MuJoCo 4.0.1 is required, got {mujoco.__version__}")

    vehicle, vehicle_path = resolve_canonical_vehicle(vehicle_arg)
    map_spec, map_path = resolve_map(map_arg)

    if output.exists():
        if not overwrite:
            raise FileExistsError(f"Output directory already exists: {output} (pass --overwrite to replace)")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    contact_cfg = {
        "flex_radius_m": 0.0001,
        "solref": [0.002, 1.0],
        "solimp": [0.99, 0.999, 0.0001, 0.5, 2.0],
        "torsional_friction": 0.0,
        "rolling_friction": 0.0,
        "static_sliding_friction": 0.7,
        "condim": 3,
    }

    map_kind = map_spec["kind"]
    groups = None
    if map_kind == "rm_flat_map":
        map_root, static_mappings = build_flat_map_xml(map_spec, contact_cfg)
    elif map_kind == "rm_mesh_map":
        map_root, static_mappings, groups = build_mesh_map_xml(
            repo, map_spec, map_path, geometry_dir, output, contact_cfg
        )
    else:
        raise ValueError(f"Unsupported map kind for decoupled MuJoCo scene: {map_kind}")

    spawn_decl = next((s for s in map_spec.get("spawns", []) if s["id"] == spawn_id), None)
    if spawn_decl is None:
        raise ValueError(f"Spawn '{spawn_id}' not found in map '{map_spec['id']}'")
    sx, sy, sz = [float(v) for v in spawn_decl["position_m"]]
    syaw = float(spawn_decl.get("yaw_rad", 0.0))
    lowest_z = compute_lowest_z_in_root(vehicle)

    if map_kind == "rm_flat_map":
        ground_z = float(map_spec["floor"]["surface_z_m"])
    else:
        ground_z = height_at(groups, sx, sy, upper=sz + 0.5)

    root_pos = [sx, sy, ground_z - lowest_z + clearance]
    root_quat = [math.cos(syaw * 0.5), 0.0, 0.0, math.sin(syaw * 0.5)]

    vehicle_root, vehicle_meta = build_vehicle_xml(vehicle, root_pos, root_quat, contact_cfg)

    map_xml_path = output / "map.xml"
    vehicle_xml_path = output / "vehicle.xml"
    model_xml_path = output / "model.xml"

    ET.ElementTree(map_root).write(map_xml_path, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(vehicle_root).write(vehicle_xml_path, encoding="utf-8", xml_declaration=True)

    scene_root = ET.Element("mujoco", model=f"RMSim_{vehicle['id']}_{map_spec['id']}")
    sub(scene_root, "compiler", angle="radian", autolimits="true", inertiafromgeom="false")
    sub(
        scene_root,
        "option",
        timestep=timestep,
        integrator="implicitfast",
        solver="Newton",
        iterations="60",
        cone="elliptic",
        gravity="0 0 -9.8",
    )
    sub(scene_root, "size", memory="256M")
    sub(scene_root, "include", file="map.xml")
    sub(scene_root, "include", file="vehicle.xml")
    ET.ElementTree(scene_root).write(model_xml_path, encoding="utf-8", xml_declaration=True)

    t0 = time.perf_counter()
    model = mujoco.MjModel.from_xml_path(str(model_xml_path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    compile_s = time.perf_counter() - t0
    if any(int(x.number) for x in data.warning):
        raise ValueError("MuJoCo reported initialization warnings while compiling decoupled scene")

    manifest = {
        "schema": "rmsim_mujoco_scene_v2",
        "status": "compiled_ready",
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mujoco_version": mujoco.__version__,
        "model_xml": "model.xml",
        "vehicle_xml": "vehicle.xml",
        "map_xml": "map.xml",
        "vehicle_id": vehicle["id"],
        "vehicle_file": str(vehicle_path),
        "vehicle_sha256": digest(vehicle_path),
        "map_id": map_spec["id"],
        "map_kind": map_kind,
        "map_file": str(map_path),
        "map_sha256": digest(map_path),
        "diagnostic_plane_only": False,
        "timestep_s": timestep,
        "gravity_mps2": [0.0, 0.0, -9.8],
        "coordinate_system": "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
        "units": "SI",
        "calibrated": False,
        "root_body": vehicle_meta["root_body"],
        "yaw_body": vehicle_meta["yaw_body"],
        "pitch_body": vehicle_meta["pitch_body"],
        "control_type": vehicle_meta["control_type"],
        "limits": vehicle_meta["limits"],
        "body_names": vehicle_meta["body_names"],
        "joints": vehicle_meta["joints"],
        "actuators": vehicle_meta["actuators"],
        "wheels": vehicle_meta["wheels"],
        "gimbal_control": {
            "stiffness_nm_rad": 80.0,
            "damping_nm_s": 8.0,
            "max_torque_nm": 12.0,
            "yaw_rate_rad_s": 9.42477796076938,
            "pitch_rate_rad_s": 6.283185307179586,
        },
        "spawn": {
            "id": spawn_id,
            "declared_spawn": spawn_decl,
            "root_position_m": root_pos,
            "root_quaternion_wxyz": root_quat,
            "initial_clearance_m": clearance,
            "ground_z_m": ground_z,
            "lowest_local_z_m": lowest_z,
        },
        "static_objects": static_mappings,
        "model_counts": {
            n: int(getattr(model, n))
            for n in ("nq", "nv", "nbody", "njnt", "nu", "ngeom", "nmesh", "nflex", "nflexvert", "nflexelem")
        },
        "total_mass_kg": float(model.body_mass.sum()),
        "compile_seconds": compile_s,
        "model_sha256": digest(model_xml_path),
        "vehicle_xml_sha256": digest(vehicle_xml_path),
        "map_xml_sha256": digest(map_xml_path),
        "runtime_bindings": [
            {
                "role": "canonical_vehicle",
                "path": str(vehicle_path),
                "bytes": vehicle_path.stat().st_size,
                "md5": md5_file(vehicle_path),
                "sha256": digest(vehicle_path),
            },
            {
                "role": "map_manifest",
                "path": str(map_path),
                "bytes": map_path.stat().st_size,
                "md5": md5_file(map_path),
                "sha256": digest(map_path),
            },
            {
                "role": "model_xml",
                "path": "model.xml",
                "bytes": model_xml_path.stat().st_size,
                "md5": md5_file(model_xml_path),
                "sha256": digest(model_xml_path),
            },
            {
                "role": "vehicle_xml",
                "path": "vehicle.xml",
                "bytes": vehicle_xml_path.stat().st_size,
                "md5": md5_file(vehicle_xml_path),
                "sha256": digest(vehicle_xml_path),
            },
            {
                "role": "map_xml",
                "path": "map.xml",
                "bytes": map_xml_path.stat().st_size,
                "md5": md5_file(map_xml_path),
                "sha256": digest(map_xml_path),
            },
        ],
    }

    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose a decoupled MuJoCo scene from any vehicle + map package.")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--vehicle", type=Path, required=True, help="Vehicle directory, vehicle.json, or vehicle.canonical.json")
    parser.add_argument("--map", type=Path, required=True, help="Map directory or map.json")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for composed MuJoCo scene")
    parser.add_argument("--spawn", default="default")
    parser.add_argument("--clearance", type=float, default=0.02)
    parser.add_argument("--timestep", type=float, default=0.001)
    parser.add_argument("--geometry-directory", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    manifest = compose_scene(
        repo=args.repo.resolve(),
        vehicle_arg=args.vehicle,
        map_arg=args.map,
        output=args.output.resolve(),
        spawn_id=args.spawn,
        clearance=args.clearance,
        timestep=args.timestep,
        geometry_dir=args.geometry_directory.resolve() if args.geometry_directory else None,
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "vehicle_id": manifest["vehicle_id"],
                "map_id": manifest["map_id"],
                "control_type": manifest["control_type"],
                "manifest": str((args.output.resolve() / "manifest.json")),
                "counts": manifest["model_counts"],
                "total_mass_kg": manifest["total_mass_kg"],
                "compile_seconds": manifest["compile_seconds"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
