"""Offline proof of independent LC/RVT microdetail effects and exact sample reuse.

Uses original prepared 12-slot pixels. GPU filtering, shader compilation and native
socket connections are checked separately by the read-only native material gate.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import traceback

import numpy as np
from PIL import Image


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unit(value):
    return value / np.linalg.norm(value)


def bilinear(image, uv):
    h, w = image.shape[:2]
    texel = np.asarray(uv) * [w, h] - .5
    lo = np.floor(texel).astype(int)
    f = texel - lo
    x, y = lo
    return ((image[y % h, x % w] * (1 - f[0]) + image[y % h, (x + 1) % w] * f[0]) * (1 - f[1])
            + (image[(y + 1) % h, x % w] * (1 - f[0])
               + image[(y + 1) % h, (x + 1) % w] * f[0]) * f[1])


BASE_COLOR = np.array([.31, .43, .19])
BASE_NORMAL = unit(np.array([.12, -.08, 1.0]))
BASE_ROUGHNESS = .71


def apply_one(micro):
    factor = (2 * micro[0]) ** 2
    roughness = 1 - np.clip((1 - BASE_ROUGHNESS) * micro[0] * 2, 0, 1)
    xy = micro[[3, 1]] * 2 - 1
    detail = unit(np.r_[xy, np.sqrt(np.clip(1 - np.dot(xy, xy), 0, 1))])
    t = BASE_NORMAL + [0, 0, 1]
    u = detail * [-1, -1, 1]
    normal = unit(t * (np.dot(t, u) / max(t[2], 1.e-5)) - u)
    return factor, roughness, normal


def effects(images, uv, indices, strengths, cached):
    cache = {}
    reads = 0
    neutral = max(1 - sum(strengths), 0)
    color_factor = neutral
    roughness = BASE_ROUGHNESS * neutral
    normal = BASE_NORMAL * neutral
    if sum(strengths) <= 0:
        return np.r_[BASE_COLOR, BASE_ROUGHNESS, BASE_NORMAL], reads
    for index, strength in zip(indices, strengths):
        if strength <= 0:
            continue
        index = np.clip(index, 0, len(images) - 1)
        lower = int(np.floor(index))
        upper = min(lower + 1, len(images) - 1)
        part = index - lower
        micro = np.zeros(4)
        for slot, weight in ((lower, 1 - part), (upper, part)):
            if weight <= 0:
                continue
            if not cached or slot not in cache:
                value = bilinear(images[slot], uv)
                reads += 1
                if cached:
                    cache[slot] = value
            else:
                value = cache[slot]
            micro += weight * value
        factor, modified_roughness, combined_normal = apply_one(micro)
        color_factor += strength * factor
        roughness += strength * modified_roughness
        normal += strength * combined_normal
    return np.r_[np.maximum(BASE_COLOR * color_factor, 0), np.clip(roughness, .04, 1), unit(normal)], reads


def decode(clear, source_scale, base_raw_id, base_valid, mask, payload, inside=True):
    coverage = np.clip((mask - clear) / (1 - clear), 0, 1) * int(inside)
    base_index = np.clip(base_raw_id * source_scale, 0, 11)
    decal_valid = 0.0
    decal_index = 0.0
    if coverage > 0:
        raw = max((payload - clear * (1 - coverage)) / max(coverage, 1.e-8), 0)
        decal_valid = np.clip(raw * 13, 0, 1)
        decal_index = np.clip(raw * 13 - 1, 0, 11)
    return [base_index, decal_index], [(1 - coverage) * np.clip(base_valid, 0, 1), coverage * decal_valid]


def run(package, module_file):
    spec = importlib.util.spec_from_file_location('checked_microdetail_module', module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.validate(package)
    base = package / Path(module.DEFINITION_FILE).parent
    images = [np.asarray(Image.open(base / slot['texture_file']).convert('RGBA'), dtype=np.float64) / 255
              for slot in data['slots']]
    checks = []

    def check(name, passed, **details):
        checks.append(dict(name=name, passed=bool(passed), **details))
        if not passed:
            raise AssertionError(name + ': ' + json.dumps(details))

    code = module.shader_code(data)
    metadata = json.loads(module.post_adapter_metadata(data))
    check('source_and_shader_binding', metadata['source_definition_sha256'] == sha(package / module.DEFINITION_FILE)
          and metadata['hlsl_sha256'] == hashlib.sha256(code.encode()).hexdigest()
          and metadata['inputs'] == list(module.POST_INPUTS)
          and 'Texture2DArraySampleGrad' in code and 'SampleLevel' not in code,
          source_definition_sha256=sha(package / module.DEFINITION_FILE), hlsl_sha256=metadata['hlsl_sha256'],
          slot_count=len(images), unique_textures=len({slot['texture_sha256'] for slot in data['slots']}))
    rng = np.random.default_rng(20261119)
    max_error = 0.0
    uncached_reads = cached_reads = max_reads = 0
    decoder_error = 0.0
    records = []
    for i in range(1024):
        uv = rng.uniform(-2, 2, 2)
        base_index, decal_index = rng.uniform(0, 11, 2)
        if i % 5 == 0:
            decal_index = base_index
        coverage = [0, .05, .25, .5, .75, 1][i % 6]
        base_valid = [0, .3, 1][i % 3]
        decal_valid = 0 if i % 7 == 0 else 1
        mask = module.CLEAR_VALUE * (1 - coverage) + coverage
        payload = module.CLEAR_VALUE * (1 - coverage) + coverage * decal_valid * (decal_index + 1) / 13
        indices, strengths = decode(module.CLEAR_VALUE, data['id_count_scale'], base_index / data['id_count_scale'],
                                    base_valid, mask, payload)
        expected_strengths = [(1 - coverage) * base_valid, coverage * decal_valid]
        decoder_error = max(decoder_error, float(np.max(np.abs(np.asarray(strengths) - expected_strengths))))
        if strengths[1] > 0:
            decoder_error = max(decoder_error, abs(indices[1] - decal_index))
        direct, direct_reads = effects(images, uv, indices, strengths, False)
        reused, reused_reads = effects(images, uv, indices, strengths, True)
        max_error = max(max_error, float(np.max(np.abs(direct - reused))))
        uncached_reads += direct_reads
        cached_reads += reused_reads
        max_reads = max(max_reads, reused_reads)
        if i < 8:
            records.append(dict(uv=uv.tolist(), indices=indices, strengths=strengths,
                                result=reused.tolist(), reads=reused_reads))
    check('original_pixels_cached_equals_direct', max_error == 0 and max_reads <= 4 and cached_reads < uncached_reads,
          cases=1024, max_abs_error=max_error, uncached_requests=uncached_reads, cached_requests=cached_reads,
          maximum_requests=max_reads, sampling_scope='original highest mip wrap bilinear CPU reference; hardware filtering separate')
    check('rvt_clear_decode_and_source_validity', decoder_error < 2.e-13, maximum_error=float(decoder_error),
          clear_value=module.CLEAR_VALUE, id_count_scale=data['id_count_scale'])

    synthetic = [np.broadcast_to(np.array([.1, .5, .4, .5]), (2, 2, 4)).copy() for _ in range(12)]
    synthetic[1][:] = [.2, .5, .4, .7]
    synthetic[9][:] = [.8, .7, .4, .5]
    actual, reads = effects(synthetic, [.25, .25], [1, 9], [.5, .5], True)
    a, _ = effects(synthetic, [.25, .25], [1, 0], [1, 0], False)
    b, _ = effects(synthetic, [.25, .25], [9, 0], [1, 0], False)
    expected = np.r_[(a[:3] + b[:3]) / 2, (a[3] + b[3]) / 2, unit(a[4:] + b[4:])]
    incorrect_averaged_id, _ = effects(synthetic, [.25, .25], [5, 0], [1, 0], False)
    check('surface_identities_not_averaged', np.max(np.abs(actual - expected)) < 1.e-14
          and np.linalg.norm(actual - incorrect_averaged_id) > .1 and reads == 2,
          separate_result=actual.tolist(), incorrect_averaged_id_result=incorrect_averaged_id.tolist())

    endpoint_error = 0.0
    for base_valid, coverage, decal_valid in ((1, 0, 1), (1, 1, 1), (0, 1, 1), (0, 0, 0), (1, 1, 0)):
        indices, strengths = decode(module.CLEAR_VALUE, data['id_count_scale'], 1.25 / data['id_count_scale'],
                                    base_valid, module.CLEAR_VALUE * (1 - coverage) + coverage,
                                    module.CLEAR_VALUE * (1 - coverage) + coverage * decal_valid * 9.75 / 13)
        got, _ = effects(images, [.128, .694], indices, strengths, True)
        if coverage == 0 and base_valid:
            want, _ = effects(images, [.128, .694], [1.25, 0], [1, 0], False)
        elif coverage == 1 and decal_valid:
            want, _ = effects(images, [.128, .694], [0, 8.75], [0, 1], False)
        else:
            want = np.r_[BASE_COLOR, BASE_ROUGHNESS, BASE_NORMAL]
        endpoint_error = max(endpoint_error, float(np.max(np.abs(got - want))))
    outside, _ = decode(module.CLEAR_VALUE, data['id_count_scale'], .2, 1, 1, .8, inside=False)
    _, outside_strengths = decode(module.CLEAR_VALUE, data['id_count_scale'], .2, 1, 1, .8, inside=False)
    check('coverage_and_validity_endpoints', endpoint_error < 1.e-13 and outside_strengths == [1, 0],
          maximum_error=endpoint_error, outside_rvt_weights=outside_strengths,
          opaque_invalid_decal='neutral effect; underlying LC identity does not leak through')

    rvtonly_error = 0.0
    for index in np.linspace(0, 11, 33):
        for coverage in (.05, .3, .7, 1):
            lower = int(np.floor(index)); upper = min(lower + 1, 11); part = index - lower
            micro = bilinear(images[lower], [.314, .159]) * (1 - part) + bilinear(images[upper], [.314, .159]) * part
            factor, modified_roughness, combined_normal = apply_one(micro)
            old = np.r_[BASE_COLOR * (1 - coverage + coverage * factor),
                        np.clip(BASE_ROUGHNESS * (1 - coverage) + coverage * modified_roughness, .04, 1),
                        unit(BASE_NORMAL * (1 - coverage) + coverage * combined_normal)]
            got, _ = effects(images, [.314, .159], [0, index], [0, coverage], True)
            rvtonly_error = max(rvtonly_error, float(np.max(np.abs(got - old))))
    check('old_rvt_only_results_preserved', rvtonly_error < 1.e-14, cases=132, maximum_error=rvtonly_error)

    epsilon = 1.e-7
    continuity_error = 0.0
    for boundary in range(1, 11):
        left, _ = effects(images, [.182, .613], [boundary - epsilon, 7.25], [.7, .3], True)
        right, _ = effects(images, [.182, .613], [boundary + epsilon, 7.25], [.7, .3], True)
        continuity_error = max(continuity_error, float(np.max(np.abs(left - right))))
    edge_error = 0.0
    for coverage in (0, .5, 1):
        lo, hi = max(0, coverage - epsilon), min(1, coverage + epsilon)
        a, _ = effects(images, [.182, .613], [1.25, 8.75], [1 - lo, lo], True)
        b, _ = effects(images, [.182, .613], [1.25, 8.75], [1 - hi, hi], True)
        edge_error = max(edge_error, float(np.max(np.abs(a - b))))
    check('continuous_per_surface_floor_next_and_coverage', continuity_error < 16 * epsilon and edge_error < 16 * epsilon,
          epsilon=epsilon, integer_boundary_max_delta=continuity_error, coverage_max_delta=edge_error)

    _, one = effects(images, [.31, .74], [2, 2], [.4, .6], True)
    _, two = effects(images, [.31, .74], [2.3, 2.3], [.4, .6], True)
    _, three = effects(images, [.31, .74], [2.3, 3.8], [.4, .6], True)
    _, four = effects(images, [.31, .74], [2.3, 8.7], [.4, .6], True)
    _, none = effects(images, [.31, .74], [2.3, 8.7], [0, 0], True)
    check('sample_reuse_exact_integer_slice_keys', (none, one, two, three, four) == (0, 1, 2, 3, 4),
          inactive=none, same_integer=one, same_fractional=two, overlapping_endpoints=three, distinct_endpoints=four)

    failures = 0
    for sockets in ({}, {'id': (object(), 'Id')}, {'id': (None, 'Id'), 'valid': (object(), 'Valid')},
                    {'id': (object(), ''), 'valid': (object(), 'Valid')},
                    {'id': (object(), 'Id'), 'valid': (object(), 'Valid'), 'extra': 1}):
        try:
            module._source_microdetail_sockets(sockets)
        except ValueError:
            failures += 1
    valid_socket = {'id': (object(), 'MicrodetailIdOut'), 'valid': (object(), 'MicrodetailValidOut')}
    check('real_socket_contract_fails_closed', failures == 5 and module._source_microdetail_sockets(None) is None
          and module._source_microdetail_sockets(valid_socket) is valid_socket, rejected_invalid_contracts=failures,
          native_missing_pin_behavior='wt_materials._wire must raise; validated separately with native graph')
    return dict(status='passed', checks=checks, source_slots=[dict(index=s['index'], sha256=s['texture_sha256']) for s in data['slots']],
                metadata=metadata, examples=records,
                scope='CPU source-pixel composition and cache equivalence; native compiled graph and GPU performance pending')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--module', type=Path, default=Path(__file__).resolve().parents[1] / 'unreal/wt_ground_microdetails.py')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(schema='wt_ground_microdetail_composition_audit_v1', status='failed',
                  script_sha256=sha(__file__), module_file=str(args.module.resolve()), module_sha256=sha(args.module))
    try:
        report.update(run(args.package.resolve(), args.module.resolve()))
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        (args.output / 'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps({key: report[key] for key in ('status', 'script_sha256', 'module_sha256')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
