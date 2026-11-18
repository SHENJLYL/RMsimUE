"""Read-only inventory of UE5.8 reflected collision enums; no asset/world load."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import unreal

match = re.search(r'-CollisionEnums=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
if not match:
    raise ValueError('pass -CollisionEnums=<fresh report directory>')
output = Path(match.group(1) or match.group(2)).resolve()
output.mkdir(parents=True, exist_ok=False)


def entry(value):
    record = dict(type=type(value).__name__, representation=repr(value))
    for field in ('name', 'value'):
        try:
            record[field] = getattr(value, field)
        except Exception as error:
            record[field+'_error'] = str(error)
    try:
        record['display_name'] = str(value.get_display_name())
    except Exception as error:
        record['display_name_error'] = str(error)
    return record


report = dict(schema='wt_collision_native_enum_inventory_v1', status='running',
              started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              engine=unreal.SystemLibrary.get_engine_version(), enums={},
              script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
for name in ('CollisionChannel', 'TraceTypeQuery', 'CollisionResponseType', 'CollisionResponse'):
    enum_type = getattr(unreal, name)
    record = dict(type=repr(enum_type), public_dir=[x for x in dir(enum_type) if not x.startswith('_')],
                  attributes={}, casts={}, iteration=[])
    for attribute in record['public_dir']:
        try:
            value = getattr(enum_type, attribute)
            if isinstance(value, enum_type):
                record['attributes'][attribute] = entry(value)
        except Exception as error:
            record['attributes'][attribute] = dict(error=str(error))
    try:
        record['iteration'] = [entry(x) for x in enum_type]
    except Exception as error:
        record['iteration_error'] = str(error)
    for value in (0, 1, 2, 3, 4, 8, 14):
        try:
            record['casts'][str(value)] = entry(enum_type.cast(value))
        except Exception as error:
            record['casts'][str(value)] = dict(error=str(error))
    report['enums'][name] = record
report.update(status='passed', finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              assets_loaded=0, world_loaded=False)
(output/'verification.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
unreal.log('WT_COLLISION_ENUM_INVENTORY '+str(output/'verification.json'))
