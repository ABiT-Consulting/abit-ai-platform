"""Run only finite activation checks; preserve every terminal attempt."""
from pathlib import Path
import datetime
import hashlib
import importlib
import io
import json
import os
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
paths = sorted((ROOT / 'deployment/linux/native-verification-activation-20261122').glob('*.py'))
paths += [HERE / 'test_graceful_lifecycle.py', HERE / 'test_native_activation.py']
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
before = {str(p.relative_to(ROOT)): digest(p) for p in paths}
suite = unittest.TestSuite()
for name in ('test_graceful_lifecycle', 'test_native_activation'):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
log = io.StringIO()
result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
stem = ('optimized-' if sys.flags.optimize else 'normal-') + stamp
(HERE / (stem + '.log')).write_text(log.getvalue())
after = {str(p.relative_to(ROOT)): digest(p) for p in paths}
report = {'schema_version': 1, 'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'python_optimized': bool(sys.flags.optimize),
          'tests': result.testsRun, 'failures': len(result.failures),
          'errors': len(result.errors), 'skips': len(result.skipped),
          'source_before': before, 'source_after': after, 'source_drift': before != after,
          'log': str((HERE / (stem + '.log')).relative_to(ROOT)),
          'log_sha256': digest(HERE / (stem + '.log')),
          'native_or_service_action': False, 'production_database_access': False,
          'actual_fixture_files_and_eleven_sqlite_backups': True}
(HERE / (stem + '.json')).write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: report[k] for k in ('tests','failures','errors','skips','source_drift')},
                 sort_keys=True))
print(str(HERE / (stem + '.json')))
if not result.wasSuccessful() or before != after:
    print(log.getvalue())
    raise SystemExit(1)
