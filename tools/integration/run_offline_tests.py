"""Run source-level project tests with a fresh, compact evidence record."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for relative in ('src', 'ros2_ws/src/rmsim_bridge', 'ros2_ws/src/rmsim_bridge/test', 'tools/integration'):
        sys.path.insert(0, str(ROOT / relative))
    files = sorted((ROOT/'tests').rglob('test_*.py'))
    files += sorted((ROOT/'ros2_ws/src/rmsim_bridge/test').glob('test_*.py'))
    suite = unittest.TestSuite()
    evidence = []
    for index, file in enumerate(files):
        name = 'rmsim_offline_' + str(index)
        spec = importlib.util.spec_from_file_location(name, file)
        if spec is None or spec.loader is None:
            raise RuntimeError('Cannot import test file: ' + str(file))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        evidence.append({'path': file.relative_to(ROOT).as_posix(),
                         'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    with (args.output/'tests.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    report = {'status': 'passed' if result.wasSuccessful() else 'failed',
              'scope': 'offline source, protocol and configuration checks; native engine gates separate',
              'tests_run': result.testsRun,
              'passed': result.testsRun-len(result.skipped)-len(result.errors)-len(result.failures),
              'skipped': len(result.skipped), 'failures': len(result.failures), 'errors': len(result.errors),
              'failed_tests': [test.id() for test, _ in result.failures + result.errors],
              'skipped_tests': [test.id() for test, _ in result.skipped], 'files': evidence}
    (args.output/'verification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'files'}, indent=2), flush=True)
    return 0 if result.wasSuccessful() else 2

if __name__ == '__main__':
    raise SystemExit(main())
