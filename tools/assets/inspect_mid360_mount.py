"""Derive MID-360 datums from the read-only source mesh and official dimensions.

Run with Blender --background --factory-startup --python this_file -- --output ...
Optional --profiles-dir writes only sensor profiles and their source evidence.
"""
import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path

MANUAL = "https://terra-1-g.djicdn.com/851d20f7b9f64838a34cd02351370894/Livox/Livox_Mid-360_User_Manual_EN.pdf"
SPECS = "https://www.livoxtech.com/mid-360/specs"
IMU_IN_LIDAR_M = [0.011, 0.02329, -0.04412]
OPTICAL_HEIGHT_M = 0.047


def mesh_components(obj, local_points):
    """Keep source connectivity as evidence; never select connector by global yaw."""
    parent = list(range(len(local_points)))
    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    for edge in obj.data.edges:
        a, b = edge.vertices
        parent[root(a)] = root(b)
    groups = {}
    for index in range(len(local_points)):
        groups.setdefault(root(index), []).append(index)
    result = []
    for indices in groups.values():
        if len(indices) < 20:
            continue
        points = local_points[indices]
        lo, hi = points.min(axis=0), points.max(axis=0)
        result.append(dict(vertices=len(indices), first_vertex=min(indices),
                           lo=lo, hi=hi, midpoint=(lo + hi) / 2))
    return result


def inspect(source_directory):
    import bpy
    import numpy as np
    from mathutils import Matrix

    source = source_directory / "aim_sentry.blend"
    rig_path = source_directory / "rig_manifest.json"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    rig_bytes = rig_path.read_bytes()
    rig = json.loads(rig_bytes)
    scale = 1 / float(rig["linear_scale"])
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    obj = bpy.data.objects["radar_mid360"]
    if obj.parent.name != "bone_turret":
        raise ValueError("MID-360 source must remain attached to bone_turret")
    slot = next(i for i, material in enumerate(obj.material_slots) if "blue_glass" in material.name)
    faces = [face for face in obj.data.polygons if face.material_index == slot]
    indices = sorted({v for face in faces for v in face.vertices})
    all_points = np.array([list((obj.matrix_world @ v.co) * scale) for v in obj.data.vertices])
    points = all_points[indices]
    a = np.column_stack((2 * points, np.ones(len(points))))
    solution = np.linalg.lstsq(a, np.sum(points * points, axis=1), rcond=None)[0]
    sphere_center = solution[:3]
    radius = float(np.sqrt(solution[3] + sphere_center @ sphere_center))
    residual = np.linalg.norm(points - sphere_center, axis=1) - radius

    # Surface normals recover the physical dome axis; preserve its 45-degree installation.
    normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    z = np.sum([np.array(normal_matrix @ face.normal) * face.area for face in faces], axis=0)
    z = z / np.linalg.norm(z)
    if np.dot(z, points.mean(axis=0) - sphere_center) < 0:
        z = -z
    _, _, axes = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
    axis_agreement = float(abs(axes[-1] @ z))
    if abs(radius - 0.022) >= 0.0001 or float(np.max(np.abs(residual))) >= 0.0001 or axis_agreement <= 0.995:
        raise ValueError("Source optical surface no longer satisfies the dome fit")

    # This temporary basis indexes mesh regions only; final X comes from the connector.
    provisional_x = np.array([1., 0., 0.])
    provisional_x -= z * (provisional_x @ z)
    provisional_x /= np.linalg.norm(provisional_x)
    basis = np.column_stack((provisional_x, np.cross(z, provisional_x), z))
    local_points = (all_points - sphere_center) @ basis
    components = mesh_components(obj, local_points)
    housings = [c for c in components
                if 4000 <= c["vertices"] <= 5000
                and 0.014 < c["hi"][2] - c["lo"][2] < 0.018
                and np.linalg.norm(c["midpoint"][:2]) > 0.025
                and c["hi"][2] < 0]
    if len(housings) != 1:
        raise ValueError("Expected one independent side-connector housing")
    housing = housings[0]
    screws = [c for c in components
              if 1000 <= c["vertices"] <= 3000
              and 0.0038 < c["hi"][2] - c["lo"][2] < 0.0042
              and 0.0038 < min((c["hi"] - c["lo"])[:2]) < 0.0042
              and 0.0068 < max((c["hi"] - c["lo"])[:2]) < 0.0072
              and housing["lo"][2] < c["midpoint"][2] < housing["hi"][2]]
    if len(screws) != 4:
        raise ValueError("Expected four connector-face screws")
    centers = np.array([c["midpoint"] for c in screws])
    mean_center = centers.mean(axis=0)
    _, singular_values, normal_axes = np.linalg.svd(centers - mean_center, full_matrices=False)
    outward_local = normal_axes[-1]
    outward_local[2] = 0  # preserve optical +Z instead of tilting it toward a screw-fit error
    outward_local /= np.linalg.norm(outward_local)
    if outward_local @ housing["midpoint"] < 0:
        outward_local = -outward_local
    if singular_values[1] < 0.009 or singular_values[-1] > 0.00001:
        raise ValueError("Connector screws do not form the expected planar rectangle")
    connector_outward = basis @ outward_local
    x = -connector_outward
    y = np.cross(z, x)
    rotation = np.column_stack((x, y, z))
    if np.max(np.abs(rotation.T @ rotation - np.eye(3))) > 0.000001 or np.linalg.det(rotation) < 0.999999:
        raise ValueError("Sensor axes must remain a proper right-handed rotation")

    base_projection = float(local_points[:, 2].min())
    body_height = float(local_points[:, 2].max() - base_projection)
    if abs(body_height - 0.060) > 0.0005:
        raise ValueError("Source mounting base to top height disagrees with 60 +/- 0.5 mm drawing")
    # The optical datum is on the original symmetry axis, 47 mm above the mounting base.
    base_center = sphere_center + base_projection * z
    optical_center = base_center + OPTICAL_HEIGHT_M * z
    imu_center = optical_center + rotation @ np.array(IMU_IN_LIDAR_M)
    yaw_pivot = np.array(rig["yaw_pivot_m"]) * scale
    rpy = list(Matrix(rotation.tolist()).to_euler("XYZ"))
    origin = dict(xyz=(optical_center - yaw_pivot).tolist(), rpy=rpy)
    imu_origin = dict(xyz=(imu_center - yaw_pivot).tolist(), rpy=rpy)

    def evidence(component):
        return {key: value.tolist() if hasattr(value, "tolist") else value for key, value in component.items()}
    report = dict(
        schema="rm_mid360_source_v2",
        source_blend_sha256=before,
        source_rig_manifest_sha256=hashlib.sha256(rig_bytes).hexdigest(),
        source_object=obj.name, source_parent=obj.parent.name, source_linear_scale=1 / scale,
        optical_center_base_m=optical_center.tolist(), mount_parent_frame="yaw_link",
        origin_in_parent=origin, imu_origin_in_parent=imu_origin,
        optical_axis_base=z.tolist(), sensor_x_axis_base=x.tolist(),
        sensor_y_axis_base=y.tolist(), sensor_rotation_matrix_base=rotation.tolist(),
        dome_sphere_center_base_m=sphere_center.tolist(), sphere_radius_m=radius,
        sphere_max_fit_error_m=float(np.max(np.abs(residual))),
        axis_covariance_alignment=axis_agreement, fit_acceptance_tolerance_m=0.0001,
        optical_faces=len(faces), optical_vertices=len(indices), surface_vertices_checked=len(indices),
        sensor_geometry_bounds_m=dict(lo=all_points.min(axis=0).tolist(), hi=all_points.max(axis=0).tolist()),
        yaw_pivot_base_m=yaw_pivot.tolist(),
        mounting_base_center_base_m=base_center.tolist(),
        mounting_base_projection_from_dome_m=base_projection,
        measured_body_height_m=body_height, optical_origin_height_above_base_m=OPTICAL_HEIGHT_M,
        optical_origin_shift_from_dome_m=OPTICAL_HEIGHT_M + base_projection,
        connector_outward_axis_base=connector_outward.tolist(),
        connector_evidence=dict(
            coordinate_basis="temporary dome-centered basis; columns listed in basis_base",
            basis_base=basis.tolist(), housing=evidence(housing),
            face_screws=[evidence(c) for c in screws],
            screw_plane_singular_values_m=singular_values.tolist(),
            direction_method="plane through four connector-face screw bounding-box centers, projected perpendicular to optical +Z"),
        nominal_imu_in_lidar_translation_m=IMU_IN_LIDAR_M,
        nominal_lidar_in_imu_translation_m=[-v for v in IMU_IN_LIDAR_M],
        extrinsic_source="Livox MID-360 User Manual v1.2 (2024.04), IMU diagram: [11, 23.29, -44.12] mm; nominal, not calibrated",
        datum_source=MANUAL, device_spec_source=SPECS,
        scan_model="spherical_fibonacci_approx; uniform-coverage approximation, unpublished proprietary trajectory",
        legacy_profiles_retained=True,
        scope="Official optical datum and axes inferred from unchanged source geometry. Factory calibration and proprietary scan trajectory are not supplied.",
    )
    if hashlib.sha256(source.read_bytes()).hexdigest() != before or rig_path.read_bytes() != rig_bytes:
        raise RuntimeError("Read-only source changed during extraction")
    return report


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def update_profiles(directory, report):
    lidar_path, imu_path = directory / "mid360.json", directory / "mid360-imu.json"
    lidar = json.loads(lidar_path.read_text(encoding="utf-8"))
    imu = json.loads(imu_path.read_text(encoding="utf-8"))
    lidar["parent_frame"] = report["mount_parent_frame"]
    lidar["origin"] = copy.deepcopy(report["origin_in_parent"])
    for profile in imu["imus"]:
        if profile["id"] == "mid360_imu":
            profile["parent_frame"] = report["mount_parent_frame"]
            profile["origin"] = copy.deepcopy(report["imu_origin_in_parent"])
    if sum(p["id"] == "mid360_imu" for p in imu["imus"]) != 1:
        raise ValueError("Expected one mid360_imu in the active IMU profile")

    # Explicit deterministic comparison profiles keep the old zero-noise measurement model.
    ideal_lidar = copy.deepcopy(lidar)
    ideal_lidar.pop("mid360", None)
    ideal_lidar.update(schema="rm_lidar_runtime_v2", range_noise_std_m=0.0, max_range_m=40.0)
    ideal_imu = copy.deepcopy(imu)
    ideal_imu["schema"] = "rm_imu_runtime_v2"
    for profile in ideal_imu["imus"]:
        profile.pop("measurement", None)

    lidar.update(schema="rm_lidar_runtime_v3", calibrated=False, max_range_m=70.0, range_noise_std_m=0.02)
    lidar["mid360"] = dict(
        angular_noise_std_rad=math.radians(0.15), near_range_noise_std_m=0.03,
        noise_near_distance_m=0.2, noise_reference_distance_m=10.0,
        nir_default_reflectance=0.1, nir_material_reflectance={},
        range_reference_low_reflectance=0.1, range_reference_low_m=40.0,
        range_reference_high_reflectance=0.8, range_reference_high_m=70.0,
        incidence_cosine=True, pending_scan_limit=8, direction_sequence_file="",
        provenance=dict(
            document="docs/sensors/mid360-physical-model.md",
            measurement_calibrated=False,
            scan_pattern_fidelity="approximate_uniform_coverage",
            range_model="uncalibrated_two_anchor_reflectance_and_cosine",
            range_noise_model="uncalibrated_gaussian_interpolated_spec_limits",
            angular_noise_model="uncalibrated_gaussian_at_spec_upper_bound",
            reflectivity_model="uncalibrated_lambertian_linear_encoding",
            default_reflectance_source="assumed 0.1 NIR reflectance; UE visible RGB is not a NIR measurement",
            imu_measurement="200 Hz; assumed 16 g and 2000 deg/s full scales; bias, noise, quantization uncalibrated and disabled",
        ),
    )
    imu["schema"] = "rm_imu_runtime_v3"
    for profile in imu["imus"]:
        on_device = profile["id"] == "mid360_imu"
        profile["measurement"] = dict(
            accel_range_mps2=16 * 9.80665 if on_device else 0.0,
            gyro_range_rad_s=math.radians(2000) if on_device else 0.0,
            accel_quantization_mps2=0.0, gyro_quantization_rad_s=0.0,
        )
    for path, value in [(lidar_path, lidar), (imu_path, imu),
                        (directory / "mid360-ideal.json", ideal_lidar),
                        (directory / "mid360-imu-ideal.json", ideal_imu),
                        (directory / "mid360-source.json", report)]:
        write_json(path, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("E:/WarThunderModding/war_thunder_sentry"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profiles-dir", type=Path)
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(arguments)
    report = inspect(args.source)
    write_json(args.output, report)
    if args.profiles_dir is not None:
        update_profiles(args.profiles_dir, report)
    print("MID360_MOUNT", json.dumps(report, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
