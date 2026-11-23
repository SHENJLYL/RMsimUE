"""Independent no-wind leaf-coordinate verification; does not run Unreal.

Compares the official Dagor normalized-instance-basis expansion with the UE WPO
adapter for the recovered raw half4 payload, several camera rotations, and
positive uniform instance scales. Native shader and mesh checks are separate.
"""
import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import struct
import traceback
import zipfile


DAGOR_COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'
IDENTITY = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
SCALE_RANGES = {
    'kh_gol_euraspen_fall_03_rendinst': (0.21875, 0.9375),
    'kh_gol_liveoak_03_rendinst': (0.25, 0.4375),
    'kh_gol_tree_simple_a': (1.28125, 2.59375),
}


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def scale(a, s):
    return tuple(x * s for x in a)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    return scale(a, 1. / norm(a))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def transform(columns, vector):
    return tuple(sum(columns[j][i] * vector[j] for j in range(3)) for i in range(3))


def rotation(roll, pitch, yaw):
    """Generic orthogonal source frame, returned as matrix columns."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return ((cy*cp, sy*cp, -sp),
            (cy*sp*sr-sy*cr, sy*sp*sr+cy*cr, cp*sr),
            (cy*sp*cr+sy*sr, sy*sp*cr-cy*sr, cp*cr))


def source_to_ue(vector):
    """Dagor -> ROS (x,-z,y) -> UE (x,-y,z), without unit scaling."""
    return (vector[0], vector[2], vector[1])


def ue_camera(source_camera):
    """Convert the same camera forward/up, then construct UE's proper basis."""
    forward = unit(source_to_ue(source_camera[2]))
    up_hint = unit(source_to_ue(source_camera[1]))
    right = unit(cross(up_hint, forward))
    up = unit(cross(forward, right))
    return right, up, forward


def npy(archive, name):
    data = archive.read(name + '.npy')
    if data[:6] != b'\x93NUMPY':
        raise ValueError('invalid NPY magic: ' + name)
    version = data[6]
    if version == 1:
        size, offset = struct.unpack_from('<H', data, 8)[0], 10
    elif version in (2, 3):
        size, offset = struct.unpack_from('<I', data, 8)[0], 12
    else:
        raise ValueError('unsupported NPY version')
    header = ast.literal_eval(data[offset:offset+size].decode('latin1'))
    if header['fortran_order']:
        raise ValueError('unexpected Fortran-order array')
    formats = {'<f4': 'f', '|b1': '?', '<i4': 'i', '|u1': 'B'}
    fmt = formats[header['descr']]
    shape = header['shape']
    count = math.prod(shape)
    values = struct.unpack('<' + str(count) + fmt, data[offset+size:])
    if len(shape) == 1:
        return values
    if len(shape) == 2:
        return tuple(values[i:i+shape[1]] for i in range(0, count, shape[1]))
    raise ValueError('unsupported array rank')


def source_expansion(pivot, delta, camera, instance_rotation, instance_scale, translation):
    """Direct official wind-array sum + dot(normalized local axes) + world matrix."""
    right, up, forward = camera
    delta_world = add(add(scale(right, delta[0]), scale(up, delta[1])), scale(forward, -delta[2]))
    instance_basis = tuple(scale(axis, instance_scale) for axis in instance_rotation)
    delta_local = tuple(dot(unit(axis), delta_world) for axis in instance_basis)
    return add(translation, transform(instance_basis, add(pivot, delta_local)))


def ue_expansion(pivot, delta, camera, instance_rotation, instance_scale, translation, x_sign=-1.):
    """Actual reference-geometry plus WPO equation, expressed in UE centimetres."""
    def instance_vector(vector):
        return scale(source_to_ue(transform(instance_rotation, source_to_ue(vector))), instance_scale)

    reference = scale((delta[0], -delta[2], delta[1]), 100.)
    base_local = add(scale(source_to_ue(pivot), 100.), reference)
    base_world = add(scale(source_to_ue(translation), 100.), instance_vector(base_local))
    camera_vector = scale((x_sign * delta[0], delta[1], -delta[2]), 100.)
    instance_unit_x = instance_vector((1., 0., 0.))
    desired_world = scale(transform(ue_camera(camera), camera_vector), norm(instance_unit_x))
    wpo = sub(desired_world, instance_vector(reference))
    return add(base_world, wpo), wpo


def verify(raw_directory):
    camera_cases = {
        'source_identity_to_ue_yaw_90': IDENTITY,
        'source_yaw_90': rotation(0., 0., math.pi / 2),
        'source_pitch_90': rotation(0., math.pi / 2, 0.),
        'source_roll_90': rotation(math.pi / 2, 0., 0.),
        'source_oblique': rotation(.31, -.73, 1.17),
    }
    instance_cases = (IDENTITY, rotation(0., 0., math.pi / 2), rotation(-.17, .29, -.61))
    translation = (16368., -27.9447632, -16384.)
    report = dict(
        schema='wt_facing_leaves_math_v1', status='running',
        scope='independent_source_basis_vs_ue_wpo_equivalence',
        unreal_executed=False, native_shader_compiled=False,
        dagor_commit=DAGOR_COMMIT,
        source_evidence=[
            'prog/engine/libFx/leavesWind.cpp:setShaderVars/setNoAnimShaderVars',
            'prog/engine/coreUtil/flyMode.cpp:keys.fwd/right/cameraUp/getInvViewMatrix',
            'prog/gameLibs/ecs/camera/sphereCam.cpp.inl:SphereCamera.updateItm',
            'prog/gameLibs/bvh/shaders/bvh_process_leaves_vertices.dshl:tc2/delta_w/normalized-instance-basis'],
        ue_evidence=[
            'Engine/Source/Runtime/Engine/Private/SceneView.cpp:UpdateViewMatrix/CameraViewToTranslatedWorld',
            'Engine/Source/Runtime/Engine/Private/Materials/HLSLMaterialTranslator.cpp:MCB_Camera/MCB_Instance',
            'Engine/Shaders/Private/MaterialTemplate.ush:GetInstanceToWorldDF'],
        dagor_to_ue_axes='(x,z,y), determinant -1',
        ue_reference_delta_cm='100*(dx,-dz,dy)',
        ue_camera_delta_cm='100*(-dx,dy,-dz)',
        ue_wpo='length(InstanceToWorld(1,0,0))*CameraToWorld(ue_camera_delta_cm)-InstanceToWorld(ue_reference_delta_cm)',
        identity_source_camera_ue_axes=dict(right=(-1,0,0), up=(0,0,1), forward=(0,1,0)),
        camera_cases=list(camera_cases), instance_rotations=len(instance_cases),
        large_world_translation_dagor_m=translation,
        max_error_tolerance_cm=1.e-7, max_abs_error_cm=0.,
        reference_camera_max_wpo_cm=0., wrong_right_axis_max_error_cm=0.,
        cases=0, source_vertices=0, wind_groups=[], models=[])
    wind_groups = set()
    for name, scales in SCALE_RANGES.items():
        path = raw_directory / (name + '.npz')
        with zipfile.ZipFile(path) as data:
            pivots, deltas, mask = npy(data, 'positions'), npy(data, 'delta_xyzw'), npy(data, 'leaf_mask')
        if not len(pivots) == len(deltas) == len(mask):
            raise AssertionError('payload array lengths differ')
        leaf_vertices = [(pivot, delta) for pivot, delta, leaf in zip(pivots, deltas, mask) if leaf]
        item = dict(model=name, raw_payload_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    source_leaf_vertices=len(leaf_vertices), instance_scale_range=scales,
                    max_abs_error_cm=0., cases=0)
        for pivot, delta in leaf_vertices:
            if any(not math.isfinite(v) for v in (*pivot, *delta)):
                raise AssertionError('non-finite leaf payload')
            if delta[3] not in (0.,3.,6.,9.):
                raise AssertionError('unexpected source wind group')
            wind_groups.add(int(delta[3]))
            for camera in camera_cases.values():
                for instance_rotation in instance_cases:
                    for instance_scale in scales:
                        source = source_expansion(pivot, delta, camera, instance_rotation, instance_scale, translation)
                        expected = scale(source_to_ue(source), 100.)
                        actual, wpo = ue_expansion(pivot, delta, camera, instance_rotation, instance_scale, translation)
                        error = max(abs(v) for v in sub(actual, expected))
                        if error > report['max_error_tolerance_cm']:
                            raise AssertionError(f'{name}: source/UE disagreement {error} cm')
                        item['cases'] += 1
                        item['max_abs_error_cm'] = max(item['max_abs_error_cm'], error)
                        if camera == IDENTITY and instance_rotation == IDENTITY:
                            report['reference_camera_max_wpo_cm'] = max(report['reference_camera_max_wpo_cm'], norm(wpo))
                            wrong, _ = ue_expansion(pivot, delta, camera, instance_rotation, instance_scale, translation, x_sign=1.)
                            report['wrong_right_axis_max_error_cm'] = max(report['wrong_right_axis_max_error_cm'], norm(sub(wrong, expected)))
        report['source_vertices'] += len(leaf_vertices)
        report['cases'] += item['cases']
        report['max_abs_error_cm'] = max(report['max_abs_error_cm'], item['max_abs_error_cm'])
        report['models'].append(item)
    if report['source_vertices'] != 364:
        raise AssertionError('expected 364 original vertices in 91 leaf cards')
    if report['reference_camera_max_wpo_cm'] > 1.e-10:
        raise AssertionError('reference camera must leave reference geometry unchanged')
    if report['wrong_right_axis_max_error_cm'] < 1.:
        raise AssertionError('test failed to distinguish the mirrored camera-right sign')
    report['wind_groups'] = sorted(wind_groups)
    report['status'] = 'passed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-payload-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('verification output already exists: ' + str(args.output))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    failure = None
    try:
        result = verify(args.raw_payload_dir.resolve())
        material = Path(__file__).with_name('wt_materials.py')
        result['material_source_sha256'] = hashlib.sha256(material.read_bytes()).hexdigest()
    except Exception:
        failure = traceback.format_exc()
        result = dict(schema='wt_facing_leaves_math_v1', status='failed', error=failure,
                      unreal_executed=False, native_shader_compiled=False)
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if failure:
        raise RuntimeError(failure)


if __name__ == '__main__':
    main()
