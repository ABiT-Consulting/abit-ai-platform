"""Focused V6 broker and shared source-contract checks. No provider or root action."""
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
PACKAGE = ROOT / "deployment/native-broker-v6-20261122"
BASELINE = "--baseline" in sys.argv
if BASELINE:
    sys.argv.remove("--baseline")
V6 = Path("/srv/abit-dev-linux/bigrro-dev/proj0202-native-v6-filter-composition-20261122")
BASE_RAW = (HERE / "v6-fixture-execution-bundle.json").read_bytes()
BASE = json.loads(BASE_RAW)
sha = lambda raw: hashlib.sha256(raw).hexdigest()
if sha(BASE_RAW) != "9d61e006a39606bf734ae795dd476217c4557b590ff90a252fc2d15e24ec04bc":
    raise RuntimeError("FROZEN_V6_BASE_CHANGED")
CONTROLS = {name: (V6 / "scripts/native-worker" / name).read_bytes() for name in BASE["control_files"]}
if any(sha(CONTROLS[name]) != expected for name, expected in BASE["control_files"].items()):
    raise RuntimeError("FROZEN_V6_CONTROL_CHANGED")
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

# Actual ordinary immutable V6 module bytes. No native functions that perform I/O are called.
inert = load("policy", V6 / "scripts/native-worker/policy.py")
native_boundary = load("linux_boundary", V6 / "scripts/native-worker/linux_boundary.py")
native_policy = load("worker_policy", V6 / "scripts/native-worker/worker_policy.py")
native_admission = load("native_admission_source_only", V6 / "scripts/native-worker/admission.py")
legacy_contract = load("legacy_native_contract", PACKAGE / "preimage-agentic_native_contract.py")
core_contract = load("candidate_core_contract", PACKAGE / "core/open_webui/utils/agentic_native_contract.py")

NOW = 1795370000
V6_PREFIX = Path("/var/lib/bigrro-verification/proj0202-native-worker-20261122-v6")
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
        stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.authority = {"schema_version": 4, "purpose": "bigrro-combined-disposable-verification",
            "nonce": NONCE, "head_sha": contract.WORKER_HEAD, "tree_sha": contract.WORKER_TREE,
            "plan_sha256": contract.PLAN_SHA, "executor_profile_sha256": contract.PROFILE_SHA,
            "runtime_manifest_sha256": contract.RUNTIME_SHA, "max_attempts": 1,
            "max_databases": 2, "publication_authorized": False, "additional_spend_usd": 0,
            "issued_at_utc": stamp(NOW - 60), "expires_at_utc": stamp(NOW + 120)}
        self.authority.update({"executor": "ABIT-DEV-LINUX", "repository": "asma2rjoub-dotcom/bigrro-platform",
            "base_sha": "eb89b7b6f5de8ff4f79550398384474e18dfb0ea", "port": 55451,
            "database": "bigrro_verify_" + NONCE, "connection_role": "bigrro_verify_" + NONCE,
            "upgrade_database": "bigrro_p20_disposable_" + NONCE, "postgres_major": 15,
            "allowed_operations": list(native_policy.OPERATIONS)})
        self.execution = {"nonce": NONCE, "authority_sha256": contract.digest(self.authority),
            "execution_manifest_sha256": self.manifest_hash, "worker_head": contract.WORKER_HEAD,
            "worker_tree": contract.WORKER_TREE, "plan_sha256": contract.PLAN_SHA,
            "executor_profile_sha256": contract.PROFILE_SHA, "runtime_manifest_sha256": contract.RUNTIME_SHA}
        self.scope = {"invocation_id": "fixture-native-v6", "run_id": "1" * 8 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 12,
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
        if path == V6_PREFIX / "EXECUTION.json":
            return self.manifest, self.manifest_hash
        if path == V6_PREFIX / "authority" / (NONCE + ".json"):
            if self.missing_authority:
                raise FileNotFoundError("fixture original authority absent")
            return self.authority, sha(contract.canonical(self.authority).encode())
        raise AssertionError("Unexpected provider path: " + str(path))
    def raw(self, path, *args, **kwargs):
        path = Path(path); self.reads.append(path)
        if path.parent != V6_PREFIX / "control/native-worker":
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

class V6BindingTests(unittest.TestCase):
    def test_exact_v6_inputs_accept_real_six_control_bytes(self):
        f = Fixture()
        self.assertEqual(f.verify(), f.execution)
        self.assertEqual(len(f.reads), 8)
        self.assertTrue(all(str(path).startswith(str(V6_PREFIX) + "/") for path in f.reads))
    def test_v5_manifest_cannot_pass_v6_pins(self):
        f = Fixture()
        f.manifest["control_files"]["controller.py"] = "7cb49a7d7805e5d0ecb4345d71c61ad19cd7170a7c982ccdbb3c109f3a918c59"
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
    def test_fixed_controller_argv_targets_only_v6_and_invalid_nonce_denies(self):
        argv = broker.controller_argv(NONCE, broker.ROOT / "attempts" / NONCE)
        self.assertIn(str(V6_PREFIX / "control/native-worker/controller.py"), argv)
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
    def test_runtime_function_asts_preserved_from_source_v5(self):
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

class V6CrossContractTests(unittest.TestCase):
    def test_actual_native_schema2_authority_core_and_root_accept_same_new_subject(self):
        f = Fixture()
        self.assertEqual(native_admission.validate_bundle(f.manifest), f.manifest)
        self.assertEqual(native_admission.validate_authority(f.authority, NOW), f.authority)
        self.assertEqual(core_contract.validate_gate(f.gate, f.scope, NOW), f.gate)
        self.assertEqual(contract.validate_gate(f.gate, f.scope, NOW), f.gate)
        self.assertEqual(f.verify(), f.execution)
        self.assertEqual(set(f.authority), native_admission.AUTHORITY_KEYS)
        self.assertEqual(set(f.manifest), native_admission.BUNDLE_KEYS)
        self.assertEqual(len(f.reads), 8)

    def test_previous_core_contract_denies_new_subject_before_any_root_call(self):
        f = Fixture()
        with self.assertRaisesRegex(ValueError, "NATIVE_WORKER_SCOPE_CHANGED"):
            legacy_contract.validate_gate(f.gate, f.scope, NOW)
        self.assertEqual(f.reads, [])

    def test_previous_root_subject_denies_even_with_new_prefix_and_control_pins(self):
        f = Fixture()
        with mock.patch.object(broker, "WORKER_HEAD", legacy_contract.WORKER_HEAD), \
             mock.patch.object(broker, "WORKER_TREE", legacy_contract.WORKER_TREE):
            with self.assertRaisesRegex(ValueError, "NATIVE_ROOT_V4_AUTHORITY_CHANGED"):
                f.verify()

    def test_candidate_core_and_native_authority_deny_previous_subject(self):
        f = Fixture()
        f.execution.update(worker_head=legacy_contract.WORKER_HEAD, worker_tree=legacy_contract.WORKER_TREE)
        with self.assertRaisesRegex(ValueError, "NATIVE_WORKER_SCOPE_CHANGED"):
            core_contract.validate_gate(f.gate, f.scope, NOW)
        f.authority.update(head_sha=legacy_contract.WORKER_HEAD, tree_sha=legacy_contract.WORKER_TREE)
        with self.assertRaisesRegex(native_policy.BoundaryError, "AUTHORITY_FIXED_SCOPE_INVALID"):
            native_admission.validate_authority(f.authority, NOW)

    def test_core_and_root_contract_copies_and_shared_validators_are_identical(self):
        raw = (ROOT / "backend/open_webui/utils/agentic_native_contract.py").read_bytes()
        self.assertEqual(raw, (PACKAGE / "payload/agentic_native_contract.py").read_bytes())
        self.assertEqual(raw, (PACKAGE / "core/open_webui/utils/agentic_native_contract.py").read_bytes())
        before = ast.parse((PACKAGE / "preimage-agentic_native_contract.py").read_bytes())
        after = ast.parse(raw)
        funcs = lambda tree: [ast.dump(n, include_attributes=False) for n in ast.walk(tree)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertEqual(funcs(before), funcs(after))
        for key in ("PLAN_SHA", "PROFILE_SHA", "RUNTIME_SHA", "PROFILE_ID", "OPERATION", "REPOSITORY"):
            self.assertEqual(getattr(contract, key), getattr(legacy_contract, key))

    def test_exact_literal_delta_only_for_broker_and_contract(self):
        expected = {"agentic_native_broker.py": {"NATIVE", "CONTROL_HASHES"},
                    "agentic_native_contract.py": {"WORKER_HEAD", "WORKER_TREE"}}
        for name, changed in expected.items():
            old = ast.parse((PACKAGE / ("preimage-" + name)).read_bytes())
            new = ast.parse((ROOT / "backend/open_webui/utils" / name).read_bytes())
            self.assertEqual(len(old.body), len(new.body))
            actual = set()
            for a, b in zip(old.body, new.body):
                if ast.dump(a, include_attributes=False) != ast.dump(b, include_attributes=False):
                    self.assertIsInstance(a, ast.Assign)
                    self.assertIsInstance(b, ast.Assign)
                    self.assertEqual(ast.dump(a.targets[0]), ast.dump(b.targets[0]))
                    actual.add(a.targets[0].id)
            self.assertEqual(actual, changed)

    def test_source_snapshot_change_invalidates_all_four_native_gate_subjects(self):
        f = Fixture()
        self.assertEqual(set(f.manifest["gates"]), set(native_admission.GATE_NAMES))
        old_subject = native_admission.review_subject(f.manifest)
        f.manifest["source_snapshot"]["readback_sha256"] = "d" * 64
        self.assertNotEqual(old_subject, native_admission.review_subject(f.manifest))
        with self.assertRaisesRegex(native_policy.BoundaryError, "INDEPENDENT_GATE_SOURCE_MISMATCH"):
            native_admission.validate_bundle(f.manifest)

    def test_complete_native_authority_preserves_operations_database_and_port_limits(self):
        for key, value, code in (("allowed_operations", ["migrate"], "AUTHORITY_OPERATIONS_INVALID"),
                ("database", "business_database", "AUTHORITY_DATABASE_INVALID"),
                ("port", 55449, "AUTHORITY_PORT_INVALID"),
                ("executor", "another-host", "AUTHORITY_FIXED_SCOPE_INVALID")):
            with self.subTest(field=key):
                f = Fixture(); f.authority[key] = value
                with self.assertRaisesRegex(native_policy.BoundaryError, code):
                    native_admission.validate_authority(f.authority, NOW)

    def test_native_pending_manifest_denies_before_any_protected_read(self):
        self.assertEqual(native_policy.EXECUTION_MANIFEST_SHA256, "PENDING_DENY")
        with mock.patch.object(native_boundary, "read_regular", side_effect=AssertionError("protected read")):
            with self.assertRaises(native_policy.BoundaryError):
                native_admission.read_execution_bundle(NOW)

    def test_scope_environment_role_and_maintenance_identity_remain_fixed(self):
        for key, val in (("environment", "native-verification"), ("role", "developer"),
                         ("repository", "https://example.invalid/other")):
            f = Fixture(); f.scope[key] = val
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "NATIVE_SCOPE_FIXED_LANE_REQUIRED"):
                contract.validate_gate(f.gate, f.scope, NOW)
        f = Fixture()
        with self.assertRaisesRegex(ValueError, "NATIVE_MAINTENANCE_SCOPE_CHANGED"):
            contract.validate_maintenance({"lease_id": "a" * 48, "kind": "governed",
                "reference": "different-invocation", "epoch": 0}, f.scope["invocation_id"])


class V6BoundaryTests(unittest.TestCase):
    def test_old_v5_control_policy_rejected_before_provider_byte_reads(self):
        f = Fixture()
        f.manifest["control_files"]["worker_policy.py"] = "2da646c633577c02fa2438482a89f6e4444241f21c09f54770123b4362240d03"
        with self.assertRaisesRegex(ValueError, "NATIVE_CONTROL_MANIFEST_CHANGED"):
            f.verify()
        self.assertEqual(len(f.reads), 1)

    def test_mixed_v5_head_or_tree_denied_by_core_and_native_authority(self):
        for ek, ak, old in (("worker_head", "head_sha", legacy_contract.WORKER_HEAD),
                            ("worker_tree", "tree_sha", legacy_contract.WORKER_TREE)):
            with self.subTest(field=ek):
                f = Fixture(); f.execution[ek] = old
                with self.assertRaisesRegex(ValueError, "NATIVE_WORKER_SCOPE_CHANGED"):
                    core_contract.validate_gate(f.gate, f.scope, NOW)
                f.authority[ak] = old
                with self.assertRaisesRegex(native_policy.BoundaryError, "AUTHORITY_FIXED_SCOPE_INVALID"):
                    native_admission.validate_authority(f.authority, NOW)

    def test_real_old_v5_native_bundle_rejected_by_new_native_scope(self):
        old = json.loads((ROOT / "docs/evidence/2026-11-22-native-broker-v5/v5-fixture-execution-bundle.json").read_bytes())
        with self.assertRaisesRegex(native_policy.BoundaryError, "EXECUTION_BUNDLE_SCOPE_INVALID"):
            native_admission.validate_bundle(old)

    def test_future_registration_remains_disabled_without_peer_identity(self):
        doc = json.loads((PACKAGE / "registration.disabled.example.json").read_bytes())
        self.assertIs(doc["enabled"], False)
        self.assertEqual(doc["core_peer"]["pid"], 0)
        with self.assertRaisesRegex(ValueError, "NATIVE_ROOT_REGISTRATION_REQUIRED"):
            service.parse_registration(json.dumps(doc).encode())

    def test_native_snapshot_contract_and_broker_subject_are_coherent(self):
        doc = json.loads((HERE / "native-source-snapshot-contract.reference.json").read_bytes())
        self.assertEqual(doc["worker_source"]["commit"], contract.WORKER_HEAD)
        self.assertEqual(doc["worker_source"]["tree"], contract.WORKER_TREE)
        self.assertEqual(doc["worker_source"]["commit"], native_policy.WORKER_HEAD)
        self.assertEqual(doc["worker_source"]["tree"], native_policy.WORKER_TREE)
        self.assertEqual(doc["physical_prefix"], str(native_policy.SOURCE_SNAPSHOT_PREFIX))
        self.assertIs(doc["execution_authorized"], False)


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
