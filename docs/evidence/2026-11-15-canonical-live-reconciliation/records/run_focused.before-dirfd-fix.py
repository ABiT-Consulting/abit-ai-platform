from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, sys, tempfile, unittest, importlib, traceback
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
MODE = sys.argv[1]
if MODE not in {'baseline', 'focused'}:
    raise SystemExit('Unknown isolated mode')
os.umask(0o077)
FIXTURES = EVIDENCE / (MODE + '-fixtures')
FIXTURES.mkdir(mode=0o700, exist_ok=False)
FIXTURES.chmod(0o700)
tempfile.tempdir = str(FIXTURES)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT/'backend/tests'))
sources = {
    str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in [*(ROOT/'backend/open_webui/utils').glob('agentic_*.py'),
              ROOT/'backend/open_webui/routers/agentic_governance.py',
              *(ROOT/'backend/tests').glob('test_*preflight*.py'),
              ROOT/'backend/tests/test_optional_action_schema.py',
              ROOT/'backend/tests/test_agentic_control_route_contract.py',
              ROOT/'backend/tests/test_governance_background_diagnostics.py',
              ROOT/'backend/tests/test_agentic_queued_wakeup.py',
              ROOT/'backend/tests/test_delivery_control_recovery.py',
              ROOT/'backend/tests/test_returned_reply_format_correction.py']
}
origins = {}
denials = []
def denied(event, reason):
    denials.append({'event':event,'reason':reason})
    raise PermissionError(reason)
def aspath(value):
    if isinstance(value, int):
        return None
    try: return Path(os.fsdecode(value)).absolute()
    except (TypeError, ValueError): return None
def audit(event, args):
    if event in {'socket.connect','socket.getaddrinfo','socket.gethostbyname',
                 'socket.gethostbyaddr','subprocess.Popen','os.system','os.posix_spawn'}:
        denied(event, 'Isolated regression forbids network/DNS/process actions')
    if event == 'sqlite3.connect':
        value = str(args[0])
        if value == ':memory:': return
        p = aspath(unquote(urlsplit(value).path) if value.startswith('file:') else value)
        if p is None or not p.is_relative_to(FIXTURES):
            denied(event, 'SQLite must remain in private test fixtures')
    if event == 'open':
        p = aspath(args[0])
        if p is None: return
        if (p.is_relative_to('/srv/abit-dev-linux/abit-ai') and not p.is_relative_to('/srv/abit-dev-linux/abit-ai/venv')) or p.is_relative_to('/var/lib/abit-ai-agentic-gateway') or p.is_relative_to('/var/lib/abit-ai-erp-rollouts'):
            denied(event, 'Production runtime and protected evidence reads forbidden')
        mode = args[1] or ''
        flags = args[2] if len(args)>2 and type(args[2]) is int else 0
        writing = any(c in mode for c in 'wax+') or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if writing and not p.is_relative_to(EVIDENCE):
            denied(event, 'Test writes must remain in private evidence fixtures')
    if event == 'exec':
        file = str(args[0].co_filename)
        if file.startswith(str(ROOT/'backend/open_webui')):
            p = Path(file)
            if p.name in {'main.py','env.py','config.py'} or p.is_relative_to(ROOT/'backend/open_webui/internal'):
                denied(event, 'Full application and live configuration imports forbidden')
            if p.is_file():
                origins[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
        if '/open_webui/' in file and file.endswith('.py') and not file.startswith(str(ROOT/'backend/open_webui')):
            denied(event, 'Product module origin must be the canonical checkout')
sys.addaudithook(audit)
baseline = [
 'test_agentic_control_route_contract.ControlRouteContract.test_missing_noninteger_and_boolean_sequence_rejected_before_route',
 'test_qa_preflight_retry.QAPreflightRetry.test_newer_ack_pending_review_one_successor_preserves_original_and_duplicate',
 'test_governance_background_diagnostics.Diagnostics.test_actual_http_callback_preserves_status_and_server_diagnostic',
]
focused = [
 'test_agentic_control_route_contract.ControlRouteContract',
 'test_qa_preflight_retry.QAPreflightRetry',
 'test_optional_action_schema.OptionalActionSchema',
 'test_governance_background_diagnostics.Diagnostics',
 'test_qa_preflight_router.QAPreflightRouting',
 'test_agentic_queued_wakeup.QueuedWakeup',
 'test_delivery_control_recovery.DeliveryControlRecovery.test_signed_task_retry_creates_one_audited_unit_then_finishes_original_delivery',
 'test_returned_reply_format_correction.ReturnedFormat.test_unsigned_retry_cursor_cannot_authorize_corrected_bytes',
 'test_returned_reply_format_correction.ReturnedFormat.test_old_unbound_v1_and_missing_creation_records_deny_without_replacement',
 'test_returned_reply_format_correction.ReturnedFormat.test_maintenance_closing_during_packet_read_holds_before_selection_and_completion',
]
class Result(unittest.TextTestResult):
    def __init__(self,*a,**k):
        super().__init__(*a,**k);self.ids=[];self.passed=[]
    def startTest(self,test):
        self.ids.append(test.id());super().startTest(test)
    def addSuccess(self,test):
        self.passed.append(test.id());super().addSuccess(test)
started=datetime.now(timezone.utc).isoformat()
log=EVIDENCE/(MODE+'.log')
with log.open('w') as stream:
    suite=unittest.defaultTestLoader.loadTestsFromNames(baseline if MODE=='baseline' else focused)
    result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
after={rel:hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() for rel in sources}
record={'schema_version':1,'mode':MODE,'started_at_utc':started,'finished_at_utc':datetime.now(timezone.utc).isoformat(),
 'selection':baseline if MODE=='baseline' else focused,'test_ids':result.ids,'passed_ids':result.passed,
 'tests_run':result.testsRun,'unique_test_ids':len(set(result.ids)),'failures':len(result.failures),
 'errors':len(result.errors),'skips':len(result.skipped),'successful':result.wasSuccessful(),
 'pins_unchanged':sources==after,'source_pins':sources,'executed_product_origins':origins,
 'guard_denials':denials,'isolation':'Private mode0700 temp fixtures; canonical source modules; network/DNS/subprocess and live runtime/database reads denied; no full app imports.',
 'log':{'path':str(log.relative_to(ROOT)),'sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'bytes':log.stat().st_size}}
out=EVIDENCE/(MODE+'-receipt.json')
out.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
print(json.dumps({'receipt':str(out.relative_to(ROOT)),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
 'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
 'source_pins_unchanged':sources==after,'guard_denials':len(denials),'exit_code':0 if result.wasSuccessful() and sources==after else 1}))
raise SystemExit(0 if result.wasSuccessful() and sources==after else 1)
