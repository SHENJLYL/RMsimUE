"""Restricted URDF to engine-neutral JSON. All output lengths/angles are SI/ROS."""

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET


class VehicleError(ValueError):
    """A package violates the manifest or restricted URDF contract."""


def require(condition, message):
    if not condition:
        raise VehicleError(message)


def keys(obj, required, optional, label):
    require(isinstance(obj, dict), f"{label}: expected object")
    require(set(required) <= obj.keys(), f"{label}: missing {sorted(set(required) - obj.keys())}")
    require(obj.keys() <= set(required) | set(optional),
            f"{label}: unsupported keys {sorted(obj.keys() - set(required) - set(optional))}")


def name(value, label, slug=False):
    pattern = r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*" if slug else r"[A-Za-z_][A-Za-z0-9_.-]*"
    require(isinstance(value, str) and re.fullmatch(pattern, value), f"{label}: invalid name")
    return value


def number(value, label, positive=False, nonnegative=False):
    require(type(value) in (int, float), f"{label}: expected finite number")
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise VehicleError(f"{label}: expected finite number") from exc
    require(math.isfinite(value), f"{label}: expected finite number")
    require(not positive or value > 0, f"{label}: must be positive")
    require(not nonnegative or value >= 0, f"{label}: must be nonnegative")
    return value


def vector(value, length, label, positive=False):
    require(isinstance(value, list) and len(value) == length, f"{label}: expected {length} numbers")
    return [number(v, label, positive=positive) for v in value]


def origin(obj, label):
    keys(obj, ["xyz", "rpy"], [], label)
    return {k: vector(obj[k], 3, f"{label}.{k}") for k in ("xyz", "rpy")}


def local_file(root, value, label, relative_to=None):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value,
            f"{label}: use a package-local relative POSIX path")
    require(not any(ord(c) < 32 or c in '<>"|?*' for c in value),
            f"{label}: invalid portable path characters")
    parts = value.split("/")
    require(not PurePosixPath(value).is_absolute() and all(p not in ("", ".", "..") for p in parts),
            f"{label}: absolute paths and traversal are forbidden")
    path = ((relative_to or root) / value).resolve()
    require(path.is_relative_to(root), f"{label}: path escapes package (including symlinks)")
    require(path.is_file(), f"{label}: file not found: {value}")
    return path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_limited(path, limit, label):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, f"{label}: exceeds {limit // (1024 * 1024)} MiB size limit")
    return raw


def manifest(data):
    keys(data, ["schema_version", "id", "display_name", "model", "control", "sensors"], [], "manifest")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1,
            "schema_version: only integer 1 is supported")
    name(data["id"], "id", slug=True)
    require(isinstance(data["display_name"], str) and data["display_name"].strip(),
            "display_name: expected nonempty string")
    keys(data["model"], ["format", "path"], [], "model")
    require(data["model"]["format"] == "urdf", "model.format: only urdf is supported")
    control = data["control"]
    keys(control, ["type", "base_link", "wheel_joints", "limits"], ["gimbal"], "control")
    require(control["type"] in ("differential_drive", "holonomic"), "control.type: unsupported")
    name(control["base_link"], "control.base_link")
    require(isinstance(control["wheel_joints"], list) and len(control["wheel_joints"]) >= 2,
            "control.wheel_joints: at least two joints required")
    wheel_names = [name(v, "wheel_joint") for v in control["wheel_joints"]]
    require(len(set(wheel_names)) == len(wheel_names), "control.wheel_joints: duplicate name")
    keys(control["limits"], ["linear_mps", "angular_rps"], [], "control.limits")
    for k in control["limits"]:
        control["limits"][k] = number(control["limits"][k], f"control.limits.{k}", positive=True)
    if "gimbal" in control:
        keys(control["gimbal"], ["yaw_joint", "pitch_joint"], [], "control.gimbal")
        for k, v in control["gimbal"].items():
            name(v, f"control.gimbal.{k}")
        require(len(set(control["gimbal"].values())) == 2, "control.gimbal: joints must differ")
    require(isinstance(data["sensors"], list), "sensors: expected array")
    ids = set()
    for sensor in data["sensors"]:
        keys(sensor, ["id", "type", "parent_link", "origin", "rate_hz", "parameters"], [], "sensor")
        sid = name(sensor["id"], "sensor.id", slug=True)
        require(sid not in ids, f"sensor.id: duplicate {sid}")
        ids.add(sid)
        name(sensor["parent_link"], "sensor.parent_link")
        sensor["origin"] = origin(sensor["origin"], f"sensor {sid}.origin")
        sensor["rate_hz"] = number(sensor["rate_hz"], f"sensor {sid}.rate_hz", positive=True)
        params = sensor["parameters"]
        fields = {"camera": ["width", "height", "horizontal_fov_rad"],
                  "imu": [], "lidar": ["horizontal_samples", "range_min_m", "range_max_m"]}
        require(isinstance(sensor["type"], str) and sensor["type"] in fields, "sensor.type: unsupported")
        keys(params, fields[sensor["type"]], [], f"sensor {sid}.parameters")
        for k in ("width", "height", "horizontal_samples"):
            if k in params:
                require(type(params[k]) is int and params[k] > 0, f"sensor {sid}.{k}: positive integer required")
        if sensor["type"] == "camera":
            fov = number(params["horizontal_fov_rad"], f"sensor {sid}.horizontal_fov_rad")
            require(0 < fov < math.pi, f"sensor {sid}: horizontal FOV must be in (0, pi)")
        if sensor["type"] == "lidar":
            low = number(params["range_min_m"], f"sensor {sid}.range_min_m", nonnegative=True)
            high = number(params["range_max_m"], f"sensor {sid}.range_max_m", positive=True)
            require(low < high, f"sensor {sid}: range_min_m must be less than range_max_m")
    return data


def xml_shape(node, attrs, children=()):
    require(set(node.attrib) <= set(attrs), f"<{node.tag}>: unsupported attributes")
    require(all(c.tag in children for c in node), f"<{node.tag}>: unsupported child element")
    require(not (node.text or "").strip() and all(not (c.tail or "").strip() for c in node),
            f"<{node.tag}>: unexpected text")


def one(node, tag, required=False):
    matches = node.findall(tag)
    require(len(matches) <= 1, f"<{node.tag}>: duplicate <{tag}>")
    require(bool(matches) or not required, f"<{node.tag}>: missing <{tag}>")
    return matches[0] if matches else None


def xnumber(value, label, positive=False, nonnegative=False):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VehicleError(f"{label}: expected finite number") from exc
    return number(value, label, positive, nonnegative)


def xvector(value, length, label, positive=False):
    require(isinstance(value, str), f"{label}: expected numeric vector")
    parts = value.split()
    require(len(parts) == length, f"{label}: expected {length} numbers")
    return [xnumber(v, label, positive) for v in parts]


def xorigin(node):
    entry = one(node, "origin")
    if entry is None:
        return {"xyz": [0.0, 0.0, 0.0], "rpy": [0.0, 0.0, 0.0]}
    xml_shape(entry, ["xyz", "rpy"])
    return {k: xvector(entry.get(k, "0 0 0"), 3, f"origin.{k}") for k in ("xyz", "rpy")}


def inertia(node):
    entry = one(node, "inertial", required=True)
    xml_shape(entry, [], ["origin", "mass", "inertia"])
    mass = one(entry, "mass", required=True)
    xml_shape(mass, ["value"])
    mass_value = xnumber(mass.get("value"), "mass", positive=True)
    tensor = one(entry, "inertia", required=True)
    names = ["ixx", "ixy", "ixz", "iyy", "iyz", "izz"]
    xml_shape(tensor, names)
    values = {k: xnumber(tensor.get(k), f"inertia.{k}") for k in names}
    a, b, c, d, e, f = (values[k] for k in names)
    # A positive definite inertia tensor and positive semidefinite second moment
    # enforce physically possible principal moments without a numerical dependency.
    scale = max(abs(v) for v in values.values())
    require(scale > 0, "inertia: tensor must be positive definite")
    a, b, c, d, e, f = (v / scale for v in (a, b, c, d, e, f))
    determinant = a * d * f + 2 * b * c * e - a * e * e - d * c * c - f * b * b
    require(a > 0 and a * d - b * b > 0 and determinant > 0,
            "inertia: tensor must be positive definite")
    p, q, r = (d + f - a) / 2, (a + f - d) / 2, (a + d - f) / 2
    tolerance = 1e-10
    moments = [p, q, r, p*q-b*b, p*r-c*c, q*r-e*e,
               p*q*r-2*b*c*e-p*e*e-q*c*c-r*b*b]
    require(all(v >= -tolerance for v in moments), "inertia: violates physical moment inequalities")
    return {"mass": mass_value, "origin": xorigin(entry), "inertia": values}


def material(node, materials):
    xml_shape(node, ["name"], ["color"])
    result = {}
    if "name" in node.attrib:
        result["name"] = name(node.get("name"), "material.name")
    color = one(node, "color")
    if color is not None:
        xml_shape(color, ["rgba"])
        rgba = xvector(color.get("rgba"), 4, "material.rgba")
        require(all(0 <= v <= 1 for v in rgba), "material.rgba: values must be in [0, 1]")
        result["rgba"] = rgba
    elif result.get("name") in materials:
        result = dict(materials[result["name"]])
    else:
        raise VehicleError("material: requires color or an existing named color material")
    return result


def geometry(node, root, model_dir, assets):
    xml_shape(node, [], ["box", "cylinder", "sphere", "mesh"])
    require(len(node) == 1, "geometry: exactly one shape required")
    shape = node[0]
    kind = shape.tag
    result = {"type": kind}
    if kind == "box":
        xml_shape(shape, ["size"])
        result["size"] = xvector(shape.get("size"), 3, "box.size", positive=True)
    elif kind in ("cylinder", "sphere"):
        xml_shape(shape, ["radius", "length"] if kind == "cylinder" else ["radius"])
        result["radius"] = xnumber(shape.get("radius"), f"{kind}.radius", positive=True)
        if kind == "cylinder":
            result["length"] = xnumber(shape.get("length"), "cylinder.length", positive=True)
    else:
        xml_shape(shape, ["filename", "scale"])
        path = local_file(root, shape.get("filename"), "mesh.filename", model_dir)
        package_path = path.relative_to(root).as_posix()
        assets[package_path] = sha256(path)
        result.update(path=package_path,
                      scale=xvector(shape.get("scale", "1 1 1"), 3, "mesh.scale", positive=True))
    return result


def urdf(root, model_path):
    raw = read_limited(model_path, 16 * 1024 * 1024, "URDF")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise VehicleError("URDF: only UTF-8 XML is supported") from exc
    require(not re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.I), "URDF: DTD and entities are forbidden")
    try:
        robot = ET.fromstring(text)
    except ET.ParseError as exc:
        raise VehicleError(f"URDF XML: {exc}") from exc
    require(robot.tag == "robot", "URDF: root must be <robot>")
    xml_shape(robot, ["name"], ["link", "joint", "material"])
    name(robot.get("name"), "robot.name")
    materials, assets, links, joints = {}, {}, [], []
    for item in robot.findall("material"):
        mid = name(item.get("name"), "material.name")
        require(mid not in materials, f"material: duplicate {mid}")
        materials[mid] = material(item, materials)
    link_names, joint_names = set(), set()
    for item in robot.findall("link"):
        xml_shape(item, ["name"], ["inertial", "visual", "collision"])
        lname = name(item.get("name"), "link.name")
        require(lname not in link_names, f"link: duplicate {lname}")
        link_names.add(lname)
        link = {"name": lname, "inertial": inertia(item), "visuals": [], "collisions": []}
        for tag in ("visual", "collision"):
            shape_names = set()
            for shape in item.findall(tag):
                xml_shape(shape, ["name"], ["origin", "geometry", "material"] if tag == "visual" else ["origin", "geometry"])
                entry = {"origin": xorigin(shape), "geometry": geometry(one(shape, "geometry", True), root, model_path.parent, assets)}
                if shape.get("name") is not None:
                    sname = name(shape.get("name"), f"{tag}.name")
                    require(sname not in shape_names, f"{tag}: duplicate name {sname}")
                    shape_names.add(sname)
                    entry["name"] = sname
                mat = one(shape, "material")
                if mat is not None:
                    entry["material"] = material(mat, materials)
                link[tag + "s"].append(entry)
        links.append(link)
    require(links, "URDF: at least one link required")
    children = {}
    for item in robot.findall("joint"):
        xml_shape(item, ["name", "type"], ["parent", "child", "origin", "axis", "limit"])
        jname = name(item.get("name"), "joint.name")
        require(jname not in joint_names, f"joint: duplicate {jname}")
        joint_names.add(jname)
        kind = item.get("type")
        require(kind in ("fixed", "continuous", "revolute"), f"joint {jname}: unsupported type")
        joint = {"name": jname, "type": kind, "origin": xorigin(item)}
        for relation in ("parent", "child"):
            entry = one(item, relation, True)
            xml_shape(entry, ["link"])
            value = entry.get("link")
            require(value in link_names, f"joint {jname}: unknown {relation} link {value}")
            joint[relation] = value
        require(joint["child"] not in children, f"link {joint['child']}: multiple parent joints")
        children[joint["child"]] = joint["parent"]
        axis = one(item, "axis")
        if axis is not None:
            xml_shape(axis, ["xyz"])
        xyz = xvector(axis.get("xyz") if axis is not None else "1 0 0", 3, f"joint {jname}.axis")
        magnitude = math.hypot(*xyz)
        require(magnitude > 0 and math.isfinite(magnitude), f"joint {jname}: invalid axis")
        joint["axis"] = [v / magnitude for v in xyz]
        limit = one(item, "limit", kind != "fixed")
        if kind == "fixed":
            require(limit is None and axis is None, f"joint {jname}: fixed joint must not have axis/limit")
        else:
            allowed = ["effort", "velocity", "lower", "upper"] if kind == "revolute" else ["effort", "velocity"]
            xml_shape(limit, allowed)
            values = {k: xnumber(limit.get(k), f"joint {jname}.{k}", positive=True) for k in ("effort", "velocity")}
            if kind == "revolute":
                values.update({k: xnumber(limit.get(k), f"joint {jname}.{k}") for k in ("lower", "upper")})
                require(values["lower"] < values["upper"], f"joint {jname}: lower must be less than upper")
            joint["limits"] = values
        joints.append(joint)
    roots = link_names - children.keys()
    require(len(roots) == 1, "URDF: exactly one root link required")
    root_link = next(iter(roots))
    for lname in link_names:
        visited = set()
        current = lname
        while current in children:
            require(current not in visited, f"URDF: joint cycle at {current}")
            visited.add(current)
            current = children[current]
        require(current == root_link, "URDF: disconnected link tree")
    return root_link, links, joints, [{"path": p, "sha256": s} for p, s in sorted(assets.items())]


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"JSON: duplicate key {key}")
        result[key] = value
    return result


def resolve_package(package):
    """Keep the entry directory as package root, even for an internal manifest link."""
    path = Path(package).absolute()
    if path.is_dir():
        path = path.resolve() / "vehicle.json"
    require(path.name == "vehicle.json" and path.is_file(), "package: expected directory or vehicle.json")
    root = path.parent.resolve()
    return root, local_file(root, "vehicle.json", "manifest")


def load_vehicle(package):
    """Validate a directory or vehicle.json and return canonical data (no writes)."""
    try:
        root, path = resolve_package(package)
        raw = read_limited(path, 1024 * 1024, "manifest")
        try:
            document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_json)
        except ValueError as exc:
            raise VehicleError(f"manifest JSON: {exc}") from exc
        data = manifest(document)
        model_path = local_file(root, data["model"]["path"], "model.path")
        root_link, links, joints, assets = urdf(root, model_path)
        require(data["control"]["base_link"] == root_link, "control.base_link must be the URDF root link")
        lookup = {j["name"]: j for j in joints}
        wheel_names = data["control"]["wheel_joints"]
        for jname in wheel_names:
            require(jname in lookup and lookup[jname]["type"] == "continuous",
                    f"control.wheel_joints: {jname} must name a continuous joint")
        gimbal = data["control"].get("gimbal", {})
        for jname in gimbal.values():
            require(jname in lookup and lookup[jname]["type"] in ("continuous", "revolute"),
                    f"control.gimbal: {jname} must name a moving joint")
            require(jname not in wheel_names, f"control.gimbal: {jname} is already a wheel joint")
        link_names = {link["name"] for link in links}
        for sensor in data["sensors"]:
            require(sensor["parent_link"] in link_names, f"sensor {sensor['id']}: unknown parent_link")
        return {
            "schema_version": 1, "kind": "rm_vehicle_canonical", "id": data["id"],
            "display_name": data["display_name"],
            "units": {"length": "m", "angle": "rad", "mass": "kg", "inertia": "kg*m^2"},
            "coordinate_system": "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
            "root_link": root_link, "links": links, "joints": joints,
            "sensors": data["sensors"], "control": data["control"],
            "source": {"manifest_sha256": hashlib.sha256(raw).hexdigest(),
                       "urdf_path": model_path.relative_to(root).as_posix(),
                       "urdf_sha256": sha256(model_path), "assets": assets},
        }
    except VehicleError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise VehicleError(f"package: {exc}") from exc
