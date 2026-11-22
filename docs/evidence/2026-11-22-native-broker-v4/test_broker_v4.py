"""Focused v4 broker binding and startup denial checks. No provider or root action."""
import ast
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PACKAGE = ROOT / "deployment/native-broker-v4-20261122"
BASELINE = "--baseline" in sys.argv
if BASELINE:
    sys.argv.remove("--baseline")
V4 = Path("/srv/abit-dev-linux/bigrro-dev/proj0202-native-worker-v4-platform-20261122")
BASE_RAW = (HERE / "v4-base-execution-bundle.json").read_bytes()
BASE = json.loads(BASE_RAW)
sha = lambda raw: hashlib.sha256(raw).hexdigest()
if sha(BASE_RAW) != "787766861cd3b065e438aa64557111739d2766996da56a4750e17e200b62e061":
    raise RuntimeError("FROZEN_V4_BASE_CHANGED")
CONTROLS = {name: (V4 / "scripts/native-worker" / name).read_bytes() for name in BASE["control_files"]}
if any(sha(CONTROLS[name]) != expected for name, expected in BASE["control_files"].items()):
    raise RuntimeError("FROZEN_V4_CONTROL_CHANGED")
def load(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module
contract = load("agentic_native_contract", ROOT / "backend/open_webui/utils/agentic_native_contract.py")
broker_source = PACKAGE / "preimage-agentic_native_broker.py" if BASELINE else ROOT / "backend/open_webui/utils/agentic_native_broker.py"
broker = load("review_native_broker", broker_source)
service = load("review_native_service", ROOT / "backend/open_webui/utils/agentic_native_service.py")

NOW = 1795370000
V4_PREFIX = Path("/var/lib/bigrro-verification/proj0202-native-worker-20261122-v4")
NONCE = "7" * 32
class Fixture:
    def __init__(self):
        self.reads = []
        self.manifest = copy.deepcopy(BASE)
        self.manifest_hash = sha(BASE_RAW)
        self.controls = dict(CONTROLS)
        literal = b'EXECUTION_MANIFEST_SHA256 = "PENDING_DENY"'
        if self.controls["worker_policy.py"].count(literal) != 1:
            raise RuntimeError("FROZEN_EXECUTION_LITERAL_CHANGED")
        self.controls["worker_policy.py"] = self.controls["worker_policy.py"].replace(
            literal, ('EXECUTION_MANIFEST_SHA256 = "' + self.manifest_hash + '"').encode())
        from datetime import datetime, timezone
        stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
        self.authority = {"schema_version": 4, "purpose": "bigrro-combined-disposable-verification",
            "nonce": NONCE, "head_sha": contract.WORKER_HEAD, "tree_sha": contract.WORKER_TREE,
            "plan_sha256": contract.PLAN_SHA, "executor_profile_sha256": contract.PROFILE_SHA,
            "runtime_manifest_sha256": contract.RUNTIME_SHA, "max_attempts": 1,
            "max_databases": 2, "publication_authorized": False, "additional_spend_usd": 0,
            "issued_at_utc": stamp(NOW - 60), "expires_at_utc": stamp(NOW + 120)}
        self.execution = {"nonce": NONCE, "authority_sha256": contract.digest(self.authority),
            "execution_manifest_sha256": self.manifest_hash, "worker_head": contract.WORKER_HEAD,
            "worker_tree": contract.WORKER_TREE, "plan_sha256": contract.PLAN_SHA,
            "executor_profile_sha256": contract.PROFILE_SHA, "runtime_manifest_sha256": contract.RUNTIME_SHA}
        self.scope = {"invocation_id": "fixture-native-v4", "run_id": "1" * 8 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 12,
            "project_id": "PROJ-0202", "task_id": "TASK-FIXTURE-ONLY", "task_revision": "fixture-task-revision",
            "project_revision": "fixture-project-revision", "agent_id": "2" * 8 + "-" + "2" * 4 + "-" + "2" * 4 + "-" + "2" * 4 + "-" + "2" * 12,
            "erp_user": "fixture-it@example.invalid", "role": "it_manager", "packet_sha256": "3" * 64,
            "context_sha256": "4" * 64, "srs_revision": "fixture-revision", "srs_sha256": "5" * 64,
            "lease_fence": 1, "repository": contract.REPOSITORY, "environment": "ABIT-DEV-LINUX",
            "profile_id": contract.PROFILE_ID}
        self.gate = {"schema_version": 1, "kind": "abit_native_verification_gate",
            "scope": self.scope, "execution": self.execution, "issued_at": NOW - 30, "expires_at": NOW + 90}
        self.missing_authority = False
    def document(self, path, *args, **kwargs):
        path = Path(path); self.reads.append(path)
        if path == V4_PREFIX / "EXECUTION.json":
            return self.manifest, self.manifest_hash
        if path == V4_PREFIX / "authority" / (NONCE + ".json"):
            if self.missing_authority:
                raise FileNotFoundError("fixture original authority absent")
            return self.authority, sha(contract.canonical(self.authority).encode())
        raise AssertionError("Unexpected provider path: " + str(path))
    def raw(self, path, *args, **kwargs):
        path = Path(path); self.reads.append(path)
        if path.parent != V4_PREFIX / "control/native-worker":
            raise AssertionError("Unexpected control directory")
        return self.controls[path.name]
    def verify(self):
        with mock.patch.object(broker, "root_document", side_effect=self.document), \
             mock.patch.object(broker, "root_bytes", side_effect=self.raw), \
             mock.patch.object(broker.time, "time", return_value=NOW):
            return broker.verify_inputs(self.gate)

def registration():
    return {"schema_version": 1, "enabled": True,
        "module_sha256": {"agentic_native_contract.py": sha((ROOT / "backend/open_webui/utils/agentic_native_contract.py").read_bytes()),
                         "agentic_native_broker.py": sha(broker_source.read_bytes())},
        "core_peer": {"pid": 12345, "uid": 999, "gid": 988, "start_ticks": 123456,
                     "cmdline_sha256": "a" * 64, "boot_id": "b" * 8 + "-" + "b" * 4 + "-" + "b" * 4 + "-" + "b" * 4 + "-" + "b" * 12}}

class V4BindingTests(unittest.TestCase):
    def test_exact_v4_inputs_accept_real_six_control_bytes(self):
        f = Fixture()
        self.assertEqual(f.verify(), f.execution)
        self.assertEqual(len(f.reads), 8)
        self.assertTrue(all(str(path).startswith(str(V4_PREFIX) + "/") for path in f.reads))
    def test_v1_manifest_cannot_pass_v4_pins(self):
        f = Fixture()
        f.manifest["control_files"]["controller.py"] = "773bf0adea5ef2c885adbd0124050a302066c1a41513c65c063d94625c03894b"
        with self.assertRaisesRegex(ValueError, "NATIVE_CONTROL_MANIFEST_CHANGED"):
            f.verify()
        self.assertEqual(len(f.reads), 1)
    def test_modified_controller_is_rejected(self):
        f = Fixture(); f.controls["controller.py"] += b"\n# changed\n"
        with self.assertRaisesRegex(ValueError, "NATIVE_CONTROL_BYTES_CHANGED"):
            f.verify()
    def test_unchanged_other_control_still_pinned(self):
        f = Fixture(); f.controls["bootstrap.py"] += b"\n# changed\n"
        with self.assertRaisesRegex(ValueError, "NATIVE_CONTROL_BYTES_CHANGED"):
            f.verify()
    def test_policy_requires_exact_execution_hash_binding(self):
        f = Fixture()
        f.controls["worker_policy.py"] = CONTROLS["worker_policy.py"]
        with self.assertRaisesRegex(ValueError, "NATIVE_EXECUTION_MANIFEST_UNBOUND"):
            f.verify()
    def test_policy_normalization_does_not_allow_other_edits(self):
        f = Fixture(); f.controls["worker_policy.py"] += b"\n# changed\n"
        with self.assertRaisesRegex(ValueError, "NATIVE_CONTROL_BYTES_CHANGED"):
            f.verify()
    def test_manifest_digest_mismatch_is_rejected(self):
        f = Fixture(); f.execution["execution_manifest_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "NATIVE_EXECUTION_MANIFEST_CHANGED"):
            f.verify()
    def test_missing_authority_denies_without_launch(self):
        f = Fixture(); f.missing_authority = True
        with self.assertRaises(FileNotFoundError):
            f.verify()
    def test_expired_authority_denies(self):
        f = Fixture()
        f.authority["expires_at_utc"] = f.authority["issued_at_utc"]
        f.execution["authority_sha256"] = contract.digest(f.authority)
        with self.assertRaisesRegex(ValueError, "NATIVE_V4_AUTHORITY_EXPIRED"):
            f.verify()
    def test_retry_spend_publication_and_worker_scope_still_denied(self):
        for key, value in (("max_attempts", 2), ("additional_spend_usd", 1),
                           ("publication_authorized", True), ("head_sha", "0" * 40)):
            with self.subTest(field=key):
                f = Fixture(); f.authority[key] = value
                f.execution["authority_sha256"] = contract.digest(f.authority)
                with self.assertRaisesRegex(ValueError, "NATIVE_ROOT_V4_AUTHORITY_CHANGED"):
                    f.verify()
    def test_fixed_controller_argv_targets_only_v4_and_invalid_nonce_denies(self):
        argv = broker.controller_argv(NONCE, broker.ROOT / "attempts" / NONCE)
        self.assertIn(str(V4_PREFIX / "control/native-worker/controller.py"), argv)
        self.assertEqual(argv[-3:], ["--execute", "--nonce", NONCE])
        self.assertNotIn("/bin/sh", argv)
        with self.assertRaises(ValueError):
            broker.controller_argv("../other", broker.ROOT / "attempts" / NONCE)
    def test_missing_per_task_gate_denies_prepare(self):
        native = object.__new__(broker.RootNativeService)
        f = Fixture()
        with mock.patch.object(broker, "root_document", side_effect=FileNotFoundError), \
             mock.patch.object(broker, "verify_inputs", side_effect=AssertionError("provider read after missing gate")):
            with self.assertRaises(FileNotFoundError):
                asyncio.run(native.prepare({"scope": f.scope}))
    def test_expired_per_task_gate_denies_before_provider_reads(self):
        native = object.__new__(broker.RootNativeService)
        f = Fixture(); f.gate["expires_at"] = NOW - 1
        with mock.patch.object(broker, "root_document", return_value=(f.gate, contract.digest(f.gate))), \
             mock.patch.object(broker.time, "time", return_value=NOW), \
             mock.patch.object(broker, "verify_inputs", side_effect=AssertionError("provider read after expired gate")):
            with self.assertRaisesRegex(ValueError, "NATIVE_GATE_EXPIRED"):
                asyncio.run(native.prepare({"scope": f.scope}))
    def test_runtime_function_asts_preserved_from_staged_v1(self):
        original = ast.parse((PACKAGE / "preimage-agentic_native_broker.py").read_bytes())
        changed = ast.parse(broker_source.read_bytes())
        funcs = lambda tree: [ast.dump(n, include_attributes=False) for n in ast.walk(tree)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertEqual(funcs(original), funcs(changed))
    def test_startup_constructor_needs_no_business_gate_or_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            os.chmod(temp, 0o700)
            seen = []
            def opened(path):
                seen.append(path)
                return os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            with mock.patch.object(broker.os, "geteuid", return_value=0), \
                 mock.patch.object(broker, "root_directory", side_effect=opened), \
                 mock.patch.object(broker, "root_document", side_effect=AssertionError("startup read a business gate")), \
                 mock.patch.object(broker, "verify_inputs", side_effect=AssertionError("startup consumed authority")):
                broker.RootNativeService()
            self.assertEqual(seen, [broker.ROOT, broker.ROOT / "gates", broker.ROOT / "attempts"])
    def test_bound_registration_needs_only_module_and_kernel_peer_fields(self):
        value = registration()
        self.assertEqual(service.parse_registration(json.dumps(value).encode()), value)
    def test_disabled_registration_and_zero_process_are_rejected(self):
        value = registration(); value["enabled"] = False
        with self.assertRaisesRegex(ValueError, "NATIVE_ROOT_REGISTRATION_REQUIRED"):
            service.parse_registration(json.dumps(value).encode())
        value = registration(); value["core_peer"]["pid"] = 0
        with self.assertRaisesRegex(ValueError, "NATIVE_CORE_PROCESS_PIN_REQUIRED"):
            service.parse_registration(json.dumps(value).encode())
    def test_old_module_pin_is_denied_before_compile(self):
        value = registration()
        value["module_sha256"]["agentic_native_broker.py"] = "702568f7f12ce420dbac407736cef3081cb8ac5f8869c7cf33c21d2aff348eb4"
        sources = {name: (ROOT / "backend/open_webui/utils" / name).read_bytes()
                   for name in service.MODULE_NAMES}
        with mock.patch.object(service, "source_bytes", side_effect=lambda path, limit: sources[path.name]):
            with self.assertRaisesRegex(ValueError, "NATIVE_ROOT_BUNDLE_BYTES_CHANGED"):
                service.load_modules(value)
    def test_key_owner_and_mode_are_independent_of_matching_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "fixture-key"; p.write_bytes(b"fixture-key-only" * 2); p.chmod(0o600)
            self.assertEqual(len(broker._key(p, os.geteuid())), 32)
            with self.assertRaisesRegex(ValueError, "Broker key owner/mode differs"):
                broker._key(p, os.geteuid() + 1)
            p.chmod(0o640)
            with self.assertRaisesRegex(ValueError, "Broker key owner/mode differs"):
                broker._key(p, os.geteuid())
    def test_existing_core_config_loader_observes_atomic_new_bytes(self):
        tree = ast.parse((ROOT / "backend/open_webui/routers/agentic_governance.py").read_bytes())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_config")
        scope = {"os": os, "json": json, "Path": Path, "HTTPException": RuntimeError}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<exact-existing-config-loader>", "exec"), scope)
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "config.json"; p.write_text('{"agentic_native_verification":{"enabled":false}}')
            with mock.patch.dict(os.environ, {"ABIT_ERP_TASK_RESOLUTION_CONFIG": str(p)}):
                self.assertFalse(scope["_config"]()["agentic_native_verification"]["enabled"])
                q = Path(temp) / "candidate.json"; q.write_text('{"agentic_native_verification":{"enabled":true}}')
                os.replace(q, p)
                self.assertTrue(scope["_config"]()["agentic_native_verification"]["enabled"])

def deny(*_args, **_kwargs):
    raise AssertionError("External execution/network is forbidden in source validation")
socket_create = socket.socket
def local_only_socket(family=socket.AF_INET, *args, **kwargs):
    if family != socket.AF_UNIX:
        return deny()
    return socket_create(family, *args, **kwargs)
if __name__ == "__main__":
    with mock.patch("subprocess.Popen", side_effect=deny), \
         mock.patch("os.system", side_effect=deny), \
         mock.patch("socket.create_connection", side_effect=deny), \
         mock.patch("socket.socket", side_effect=local_only_socket), \
         mock.patch("asyncio.create_subprocess_exec", side_effect=deny):
        unittest.main()
