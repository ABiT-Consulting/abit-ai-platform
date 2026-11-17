"""Offline candidate check; never imports the app or touches live state."""
from pathlib import Path
import datetime, hashlib, importlib.util, importlib.abc, json, logging, os, sys, tempfile, unittest
sys.dont_write_bytecode = True
logging.disable(logging.CRITICAL)
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
MANIFEST = json.loads((HERE / 'manifest.json').read_text())
LIVE = Path(MANIFEST['live_root'])
CANDIDATE = HERE.parent / 'candidate'
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
for entry in MANIFEST['files']:
    assert digest(LIVE / entry['relative_path']) == entry['live_sha256'], 'Live drift'
    assert digest(CANDIDATE / entry['relative_path']) == entry['candidate_sha256'], 'Candidate drift'
class NoApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'open_webui.main', 'open_webui.config', 'open_webui.internal.db'}:
            raise AssertionError('Application import prohibited: ' + fullname)
        return None
sys.meta_path.insert(0, NoApplication())
source = REPO / 'backend/tests/test_agentic_executor_flow.py'
spec = importlib.util.spec_from_file_location('independent_overlay_fixture', source)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
names = [
 'test_invalid_developer_original_is_held_once_without_success_or_publication',
 'test_contract_rejection_preserves_stop_and_cancellation_reason',
 'test_contract_rejection_refuses_changed_identity_receipt_and_existing_result',
 'test_parse_rejection_is_typed_but_unrelated_validation_errors_stay_uncertain',
 'test_valid_developer_blocked_result_keeps_genuine_failing_test_evidence',
 'test_developer_then_separate_qa_pass_with_real_artifact_bytes_and_receipt',
 'test_forged_artifact_hash_cannot_publish_ready_or_approval',
]
with tempfile.TemporaryDirectory(prefix='agov-independent-overlay-') as tmp:
    utils = Path(tmp) / 'open_webui/utils'
    utils.mkdir(parents=True)
    for item in (LIVE / 'open_webui/utils').iterdir():
        if item.name == '__pycache__': continue
        os.symlink(item, utils / item.name, target_is_directory=item.is_dir())
    for entry in MANIFEST['files']:
        dst = Path(tmp) / entry['relative_path']
        dst.unlink()
        dst.write_bytes((CANDIDATE / entry['relative_path']).read_bytes())
    fixture.UTILS = utils
    suite = unittest.TestSuite(fixture.ExecutorFlow(name) for name in names)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
for entry in MANIFEST['files']:
    assert digest(LIVE / entry['relative_path']) == entry['live_sha256'], 'Live changed during check'
    assert digest(CANDIDATE / entry['relative_path']) == entry['candidate_sha256'], 'Candidate changed during check'
report = {'observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'scope': 'Three exact candidate leaves over current installed live module view; fixture data isolated in temporary directories',
          'source_test_sha256': digest(source), 'tests_run': result.testsRun,
          'failures': len(result.failures), 'errors': len(result.errors), 'successful': result.wasSuccessful(),
          'live_and_candidate_hashes_unchanged': True, 'application_import_blocked': True,
          'live_state_changed': False, 'model_submissions': 0, 'test_names': names}
(HERE / 'overlay-check.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
sys.exit(0 if result.wasSuccessful() else 1)
