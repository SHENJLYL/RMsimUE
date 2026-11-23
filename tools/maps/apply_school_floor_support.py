"""Uniformly raise source school gym, exterior and lamps; leave RMUC physics fixed.

Default is a candidate only. Pass --apply after the measured field bottom is known.
Only the 69 center.Z numeric tokens and metadata.floor_support are edited.
No native geometry, XY, dimensions, rotations, materials or light power is changed.
"""
import argparse
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r'D:\simulator')
DEFAULT_LAYOUT = ROOT/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
DEFAULT_OUT = ROOT/'artifacts/fixes/school-floor-contact-20261120/floor-support-apply-01'
AUDIT = ROOT/'artifacts/fixes/school-floor-contact-20261120/source-floor-audit-01/report.json'
SHA = lambda b: hashlib.sha256(b).hexdigest()

def json_spans(text):
    """Record source-character spans without reformatting any JSON tokens."""
    dec = json.JSONDecoder()
    spans = {}
    def ws(i):
        while i < len(text) and text[i].isspace():
            i += 1
        return i
    def parse(i, path):
        i = ws(i)
        start = i
        if text[i] == '{':
            i = ws(i+1)
            if text[i] != '}':
                while True:
                    key, i = dec.raw_decode(text, i)
                    i = ws(i)
                    if text[i] != ':':
                        raise ValueError('Expected JSON colon')
                    i = ws(parse(i+1, path+(key,)))
                    if text[i] == '}':
                        break
                    if text[i] != ',':
                        raise ValueError('Expected JSON comma')
                    i = ws(i+1)
            i += 1
        elif text[i] == '[':
            i = ws(i+1)
            n = 0
            if text[i] != ']':
                while True:
                    i = ws(parse(i, path+(n,)))
                    n += 1
                    if text[i] == ']':
                        break
                    if text[i] != ',':
                        raise ValueError('Expected JSON comma')
                    i = ws(i+1)
            i += 1
        else:
            _, i = dec.raw_decode(text, i)
        spans[path] = (start, i)
        return i
    end = parse(0, ())
    if ws(end) != len(text):
        raise ValueError('Unexpected trailing text')
    return spans

def get_path(data, path):
    for part in path:
        data = data[part]
    return data

def set_path(data, path, value):
    parent = get_path(data, path[:-1])
    parent[path[-1]] = value

def build_candidate(original, delta, audit, measured_bottom, evidence):
    if not math.isfinite(delta):
        raise ValueError('Delta must be finite')
    bom = original.startswith(b'\xef\xbb\xbf')
    text = original.decode('utf-8-sig')
    old = json.loads(text)
    if old.get('metadata', {}).get('floor_support'):
        raise ValueError('floor_support already recorded; avoid applying offset twice')
    instance_count = sum(len(g['instances']) for g in old['groups'])
    if (len(old['native_meshes']), instance_count, len(old['lights'])) != (23, 57, 12):
        raise ValueError('Expected 23 native meshes, 57 instances, and 12 lights')
    if SHA(original) != audit['sources']['layout_sha256']:
        raise ValueError('Layout differs from the geometry-audited snapshot; audit the new snapshot first')
    paths = [('groups', gi, 'instances', ii, 'center', 2)
             for gi, group in enumerate(old['groups'])
             for ii, instance in enumerate(group['instances'])]
    paths += [('lights', li, 'center', 2) for li in range(len(old['lights']))]
    spans = json_spans(text)
    edits = []
    changes = []
    intended = copy.deepcopy(old)
    for path in paths:
        before = get_path(old, path)
        after = before + delta
        if not math.isfinite(after):
            raise ValueError('Invalid resulting Z')
        start, end = spans[path]
        edits.append((start, end, repr(after)))
        changes.append({'path':list(path), 'old_z_m':before, 'new_z_m':after, 'original_numeric_token':text[start:end]})
        set_path(intended, path, after)
    floor_before = audit['conclusion']['support_top_world_z_m']
    floor_after = floor_before + delta
    if measured_bottom is not None and abs(floor_after-measured_bottom) > 1e-9:
        raise ValueError('Delta does not align source floor with the supplied measured field bottom')
    support = {
        'schema':'uniform_source_floor_support_v1',
        'applied_at_utc':datetime.now(timezone.utc).isoformat(),
        'vertical_delta_m':delta,
        'source_floor_top_before_m':floor_before,
        'source_floor_top_after_m':floor_after,
        'measured_rmuc_physical_bottom_m':measured_bottom,
        'measured_rmuc_evidence':evidence,
        'source_floor_audit':str(AUDIT),
        'source_floor_audit_sha256':SHA(AUDIT.read_bytes()),
        'layout_before_sha256':SHA(original),
        'source_instances_translated':57,
        'rect_lights_translated':12,
        'translation_axis':'ROS/UE positive Z only',
        'invariants':['RMUC geometry and physics fixed','all XY, sizes and rotations byte-preserved','source geometry and native assets unchanged','relative source gym/exterior/light positions unchanged'],
    }
    metadata = copy.deepcopy(old.get('metadata', {}))
    metadata['floor_support'] = support
    intended['metadata'] = metadata
    metadata_text = json.dumps(metadata, indent=2, ensure_ascii=False).replace('\n','\n  ')
    if 'metadata' in old:
        start, end = spans[('metadata',)]
        edits.append((start, end, metadata_text))
    else:
        close = spans[()][1]-1
        while close > 0 and text[close-1].isspace():
            close -= 1
        edits.append((close, close, ',\n  "metadata": '+metadata_text))
    edits.sort(key=lambda x:x[0])
    if any(a[1] > b[0] for a,b in zip(edits,edits[1:])):
        raise ValueError('Overlapping replacements')
    chunks = []
    pos = 0
    for start, end, replacement in edits:
        chunks.extend([text[pos:start], replacement])
        pos = end
    chunks.append(text[pos:])
    candidate_text = ''.join(chunks)
    result = json.loads(candidate_text)
    if result != intended:
        raise ValueError('Unexpected semantic change')
    normalized = copy.deepcopy(result)
    for path in paths:
        set_path(normalized,path,get_path(old,path))
    if 'metadata' in old:
        normalized['metadata'] = old['metadata']
    else:
        normalized.pop('metadata')
    if normalized != old:
        raise ValueError('Unexpected non-Z or non-metadata change')
    candidate = (b'\xef\xbb\xbf' if bom else b'')+candidate_text.encode('utf-8')
    report = {
        'schema':'uniform_source_floor_support_apply_v1',
        'status':'candidate_not_applied',
        'layout_path':str(DEFAULT_LAYOUT),
        'audit_path':str(AUDIT),
        'delta_m':delta,
        'source_floor_top_after_m':floor_after,
        'measured_field_bottom_m':measured_bottom,
        'before_sha256':SHA(original),
        'candidate_sha256':SHA(candidate),
        'counts':{'native_assets':23,'instances':57,'lights':12,'z_tokens_replaced':len(paths)},
        'all_unchanged_bytes_preserved_except_z_tokens_and_metadata':True,
        'non_z_semantic_invariants_verified':True,
        'changes':changes,
        'metadata_added':support,
    }
    return candidate, report

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--delta-m','--delta',type=float,required=True)
    ap.add_argument('--measured-field-bottom-m',type=float)
    ap.add_argument('--field-evidence',default='')
    ap.add_argument('--layout',type=Path,default=DEFAULT_LAYOUT)
    ap.add_argument('--out',type=Path,default=DEFAULT_OUT)
    ap.add_argument('--expected-layout-sha256')
    ap.add_argument('--apply',action='store_true')
    args = ap.parse_args()
    original = args.layout.read_bytes()
    if args.expected_layout_sha256 and SHA(original) != args.expected_layout_sha256:
        raise ValueError('Current layout SHA does not match --expected-layout-sha256')
    audit = json.loads(AUDIT.read_text(encoding='utf-8'))
    candidate, report = build_candidate(original,args.delta_m,audit,args.measured_field_bottom_m,args.field_evidence)
    report['layout_path'] = str(args.layout)
    args.out.mkdir(parents=True,exist_ok=True)
    backup = args.out/'layout.before.json'
    if backup.exists() and backup.read_bytes() != original:
        raise ValueError('Existing backup has different original bytes; use a new output directory')
    backup.write_bytes(original)
    (args.out/'layout.candidate.json').write_bytes(candidate)
    if args.apply:
        if args.layout.read_bytes() != original:
            raise ValueError('Layout changed during preparation; aborted before active write')
        temporary = args.layout.with_name(args.layout.name+'.floor-support.tmp')
        temporary.write_bytes(candidate)
        temporary.replace(args.layout)
        if args.layout.read_bytes() != candidate:
            raise ValueError('Active layout post-write verification failed')
        report['status'] = 'applied_and_byte_verified'
    report['backup_path'] = str(backup)
    report['candidate_path'] = str(args.out/'layout.candidate.json')
    (args.out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['status','layout_path','delta_m','source_floor_top_after_m','before_sha256','candidate_sha256','counts','backup_path','candidate_path']},indent=2))

if __name__ == '__main__':
    main()
