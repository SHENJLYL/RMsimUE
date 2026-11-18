"""Validate optional localization maps against the selected physical map package.

This module reads map artifacts only. It does not use simulator or estimated poses.
The same identity is used before launching native processes and on estimator reload.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct

import yaml


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _document(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read map metadata {path}: {error}") from error
    if not isinstance(result, dict):
        raise ValueError(f"Map metadata must be an object: {path}")
    return result


def _sha256(value, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"Invalid map identity {field}")
    return value


def _pgm(raw: bytes) -> tuple[int, int, bytes]:
    cursor = 0
    tokens = []
    for _ in range(4):
        while cursor < len(raw):
            if raw[cursor] in b" \t\r\n":
                cursor += 1
            elif raw[cursor] == 35:
                end = raw.find(b"\n", cursor)
                if end < 0:
                    raise ValueError("Unterminated PGM comment")
                cursor = end + 1
            else:
                break
        start = cursor
        while cursor < len(raw) and raw[cursor] not in b" \t\r\n":
            cursor += 1
        tokens.append(raw[start:cursor])
    if tokens[0] != b"P5" or tokens[3] != b"255":
        raise ValueError("Localization map package requires P5/255 grayscale images")
    try:
        width, height = int(tokens[1]), int(tokens[2])
    except ValueError as error:
        raise ValueError("Invalid PGM dimensions") from error
    if cursor >= len(raw) or raw[cursor] not in b" \t\r\n":
        raise ValueError("PGM header lacks binary-data separator")
    cursor += 2 if raw[cursor:cursor+2] == b"\r\n" else 1
    pixels = raw[cursor:]
    if width <= 0 or height <= 0 or len(pixels) != width * height:
        raise ValueError("PGM payload does not match dimensions")
    return width, height, pixels


def _grid(path: Path) -> tuple[dict, dict]:
    path = path.resolve()
    try:
        yaml_raw = path.read_bytes()
        spec = yaml.safe_load(yaml_raw.decode("utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise ValueError(f"Cannot read map YAML {path}: {error}") from error
    if not isinstance(spec, dict) or not isinstance(spec.get("image"), str):
        raise ValueError(f"Map YAML requires an image: {path}")
    image = (path.parent / spec["image"]).resolve()
    if image.parent != path.parent:
        raise ValueError("Map image must be stored beside its YAML")
    try:
        raster = image.read_bytes()
        resolution = float(spec["resolution"])
        origin = [float(value) for value in spec["origin"]]
        occupied = float(spec["occupied_thresh"])
        free = float(spec["free_thresh"])
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid map spatial declaration: {path}") from error
    if not math.isfinite(resolution) or resolution <= 0 or len(origin) != 3 or not all(math.isfinite(v) for v in origin):
        raise ValueError("Map resolution/origin must be finite metric values")
    if not (0 <= free < occupied <= 1) or spec.get("negate", 0) not in (0, 1, False, True):
        raise ValueError("Invalid trinary occupancy thresholds")
    if spec.get("mode", "trinary") != "trinary":
        raise ValueError("Localization map identity currently requires trinary map mode")
    width, height, pixels = _pgm(raster)
    negate = bool(spec.get("negate", 0))
    # Native Nav2 map_io classifies grayscale pixels, then flips rows into map order.
    lookup = []
    for value in range(256):
        probability = value / 255.0 if negate else 1.0-value / 255.0
        lookup.append(100 if probability > occupied else 0 if probability < free else 255)
    data = b"".join(pixels[row*width:(row+1)*width] for row in range(height-1, -1, -1)).translate(bytes(lookup))
    metadata_path = path.parent / "metadata.json"
    metadata = _document(metadata_path)
    identity = {
        "yaml_path": str(path), "yaml_sha256": _digest(yaml_raw),
        "raster_path": str(image), "raster_sha256": _digest(raster),
        "metadata_path": str(metadata_path), "metadata_sha256": _digest(metadata_path.read_bytes()),
        "width": width, "height": height,
        "resolution": struct.unpack("<f", struct.pack("<f", resolution))[0],
        "yaml_resolution": resolution, "origin": origin,
        "occupancy_sha256": _digest(data),
    }
    return identity, metadata


def validate_localization_map(navigation_map_yaml, localization_map_yaml) -> dict:
    """Return an immutable-by-content map-pair identity or raise before any launch."""
    navigation, nav_meta = _grid(Path(navigation_map_yaml))
    localization, local_meta = _grid(Path(localization_map_yaml))
    if local_meta.get("role") not in ("offline_geometry_candidate", "geometric_localization_surface_map"):
        raise ValueError("Localization map lacks its explicit physical-surface role")
    map_id = nav_meta.get("map_id")
    if not isinstance(map_id, str) or local_meta.get("map_id") != map_id:
        raise ValueError("Localization map_id differs from the selected navigation map")
    for key, actual in (("map_yaml_sha256", localization["yaml_sha256"]),
                        ("raster_sha256", localization["raster_sha256"]),
                        ("reference_navigation_raster_sha256", navigation["raster_sha256"])):
        if _sha256(local_meta.get(key), key) != actual:
            raise ValueError(f"Localization map hash mismatch: {key}")
    for key in ("width", "height", "resolution", "origin"):
        if localization[key] != navigation[key]:
            raise ValueError(f"Localization and navigation map spatial declarations differ: {key}")
    if local_meta.get("width") != localization["width"] or local_meta.get("height") != localization["height"]:
        raise ValueError("Localization map metadata dimensions mismatch")
    try:
        declared_resolution = struct.unpack("<f", struct.pack("<f", float(local_meta["resolution"])))[0]
        declared_origin = [float(v) for v in local_meta["origin"]]
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("Invalid localization metadata spatial declaration") from error
    if declared_resolution != localization["resolution"] or declared_origin != localization["origin"]:
        raise ValueError("Localization map metadata resolution/origin mismatch")
    scene_geometry = _sha256(nav_meta.get("scene_geometry_sha256"), "navigation.scene_geometry_sha256")
    if _sha256(local_meta.get("scene_geometry_sha256"), "localization.scene_geometry_sha256") != scene_geometry:
        raise ValueError("Localization map belongs to different scene geometry")
    collision = _sha256(nav_meta.get("collision_sha256"), "navigation.collision_sha256")
    if _sha256(local_meta.get("source_collision_sha256"), "source_collision_sha256") != collision:
        raise ValueError("Localization map collision geometry identity differs")
    descriptor = _sha256(nav_meta.get("descriptor_sha256"), "navigation.descriptor_sha256")
    if _sha256(local_meta.get("descriptor_sha256"), "localization.descriptor_sha256") != descriptor:
        raise ValueError("Localization map descriptor identity differs")
    translation = local_meta.get("source_to_sim_translation_m")
    if not isinstance(translation, list) or len(translation) != 3 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in translation):
        raise ValueError("Invalid localization source-to-simulation transform")
    if translation != nav_meta.get("source_to_sim_translation_m"):
        raise ValueError("Localization map source-to-simulation transform differs")
    if local_meta.get("coordinate_system") != "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP" or local_meta.get("units") != "m":
        raise ValueError("Localization map axes/units differ from the native map contract")
    return {
        "schema": "rmsim_localization_map_identity_v1",
        "map_id": map_id,
        "scene_geometry_sha256": scene_geometry,
        "source_collision_sha256": collision,
        "descriptor_sha256": descriptor,
        "source_to_sim_translation_m": translation,
        "coordinate_system": local_meta["coordinate_system"],
        "units": "m",
        "source_role": local_meta["role"],
        "source_status": local_meta.get("status"),
        "navigation": navigation,
        "localization": localization,
    }


def map_file_signatures(identity: dict) -> dict:
    """Cheap change detector; full hashes are checked on changes and on every reload."""
    signatures = {}
    for role in ("navigation", "localization"):
        for kind in ("yaml", "raster", "metadata"):
            path = identity[role][kind + "_path"]
            try:
                stat = Path(path).stat()
            except OSError as error:
                raise ValueError(f"Map input disappeared: {path}") from error
            signatures[path] = [stat.st_size, stat.st_mtime_ns]
    return signatures


def verify_occupancy_message(message, expected: dict, frame: str) -> None:
    """Check what the original map_server actually published, including reloads."""
    info = message.info
    if message.header.frame_id != frame:
        raise ValueError("Published map frame does not match the selected robot")
    if int(info.width) != expected["width"] or int(info.height) != expected["height"]:
        raise ValueError("Published map dimensions differ")
    if float(info.resolution) != expected["resolution"]:
        raise ValueError("Published map resolution differs")
    x, y, yaw = expected["origin"]
    position, orientation = info.origin.position, info.origin.orientation
    actual = [position.x, position.y, position.z, orientation.x, orientation.y, orientation.z, orientation.w]
    required = [x, y, 0.0, 0.0, 0.0, math.sin(yaw/2), math.cos(yaw/2)]
    if any(not math.isclose(float(a), float(b), rel_tol=0., abs_tol=1e-8) for a, b in zip(actual, required)):
        raise ValueError("Published map origin differs")
    try:
        data = memoryview(message.data).cast("B").tobytes()
    except (TypeError, ValueError):
        data = bytes(int(value) & 255 for value in message.data)
    if len(data) != expected["width"] * expected["height"] or _digest(data) != expected["occupancy_sha256"]:
        raise ValueError("Published occupancy differs from the validated map file")


def package_relative_path(base: Path, value, field: str) -> Path:
    """Resolve portable package/repository paths while rejecting path escapes."""
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError(f"{field} must be a canonical relative POSIX path")
    base = Path(base).resolve()
    result = (base / value).resolve()
    try:
        result.relative_to(base)
    except ValueError as error:
        raise ValueError(f"{field} escapes its package") from error
    return result


def packaged_localization_map(package_directory, specification: dict, allow_missing_derived=False):
    """Validate an opt-in package default; missing derived binaries may be rebuilt.

    YAML and metadata remain immutable tracked inputs. Existing mismatched files
    always fail, including when rebuilding is allowed.
    """
    declaration = specification.get("localization_map")
    if declaration is None:
        return None
    if not isinstance(declaration, dict) or declaration.get("schema") != "rmsim_packaged_localization_map_v1":
        raise ValueError("Invalid packaged localization-map declaration")
    if not isinstance(declaration.get("default"), bool):
        raise ValueError("Packaged localization-map default must be boolean")
    if not declaration["default"]:
        return None
    files = declaration.get("files")
    required = {"yaml", "raster", "metadata", "surface_provenance"}
    if not isinstance(files, dict) or set(files) != required:
        raise ValueError("Packaged localization map requires YAML, raster, metadata and surface provenance")
    paths, missing = {}, []
    for role in sorted(required):
        item = files[role]
        if not isinstance(item, dict):
            raise ValueError(f"Invalid packaged localization file: {role}")
        path = package_relative_path(Path(package_directory), item.get("path"), "localization_map."+role)
        expected = _sha256(item.get("sha256"), "localization_map."+role+".sha256")
        if not path.is_file():
            if allow_missing_derived and role in ("raster", "surface_provenance"):
                missing.append(role)
            else:
                raise ValueError(f"Packaged localization file is missing: {path}")
        elif _digest(path.read_bytes()) != expected:
            raise ValueError(f"Packaged localization file hash mismatch: {role}")
        paths[role] = {"path": str(path), "sha256": expected}
    if len({Path(value["path"]).parent for value in paths.values()}) != 1:
        raise ValueError("Packaged localization files must share one directory")
    metadata = _document(Path(paths["metadata"]["path"]))
    for meta_key, role in (("map_yaml_sha256", "yaml"), ("raster_sha256", "raster"),
                           ("surface_provenance_sha256", "surface_provenance")):
        if _sha256(metadata.get(meta_key), meta_key) != paths[role]["sha256"]:
            raise ValueError(f"Packaged metadata/file declaration mismatch: {meta_key}")
    if (metadata.get("scene_geometry_sha256") != specification.get("scene_geometry_sha256")
            or metadata.get("source_collision_sha256") != specification.get("map_metadata", {}).get("collision_sha256")
            or metadata.get("reference_navigation_raster_sha256") != specification.get("raster_sha256")):
        raise ValueError("Packaged localization map belongs to different navigation geometry")
    recipe = declaration.get("export_recipe")
    if not isinstance(recipe, dict) or recipe.get("schema") != "rmsim_localization_surface_export_recipe_v1":
        raise ValueError("Packaged localization map lacks a supported export recipe")
    return {"yaml_path": paths["yaml"]["path"], "files": paths, "missing_derived": missing,
            "export_recipe": recipe, "metadata": metadata,
            "status": declaration.get("status"), "selection": "package_default"}
