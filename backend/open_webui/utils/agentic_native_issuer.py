"""Root-owned, fixed-subject per-invocation issuer. Disabled without registration.

The caller cannot select a command, filesystem path, product subject, nonce,
port or capacity measurement. Creation is serialised by the same native flock
as the controller. Partial records remain an unresolved original, never a retry.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import types

if __package__ == "open_webui.utils":
    from open_webui.utils.agentic_native_contract import (
        require, exact, sha, digest, canonical, validate_gate, nonce,
        WORKER_HEAD, WORKER_TREE, PLAN_SHA, PROFILE_SHA, RUNTIME_SHA)
    from open_webui.utils.agentic_native_admission_contract import (
        MODE, PROJECT, validate_attestation, validate_admission_grant, readiness, observation)
else:
    from agentic_native_contract import (
        require, exact, sha, digest, canonical, validate_gate, nonce,
        WORKER_HEAD, WORKER_TREE, PLAN_SHA, PROFILE_SHA, RUNTIME_SHA)
    from agentic_native_admission_contract import (
        MODE, PROJECT, validate_attestation, validate_admission_grant, readiness, observation)

NATIVE = Path("/var/lib/bigrro-verification/proj0202-native-per-run-20261123")
CONTROL = NATIVE / "control/native-worker"
LOCK = Path("/var/lib/bigrro-verification/bigrro-proj0202-native-worker.lock")
CGROUPS = Path("/sys/fs/cgroup/system.slice")
MODULES = ("policy.py", "linux_boundary.py", "worker_policy.py", "admission.py", "bootstrap.py", "controller.py")
MEMORY_REQUIRED = 2147483648 + 134217728
MAX_INDEX = 4096


def identity(item):
    return (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
            item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)


def validate_registration(value):
    exact(value, {"mode", "subject_sha256", "control_sha256"}, "NATIVE_ISSUER_REGISTRATION_SCHEMA")
    require(value["mode"] == MODE, "NATIVE_ISSUER_MODE_DENIED")
    sha(value["subject_sha256"])
    exact(value["control_sha256"], MODULES, "NATIVE_ISSUER_CONTROL_SET")
    for item in value["control_sha256"].values():
        sha(item)
    return value


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def kernel_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) <= limit, "NATIVE_ISSUER_KERNEL_BOUNDS")
        return bytes(data)
    finally:
        os.close(fd)


class NativeSubject:
    """Exact hash-pinned native controls, loaded only in the dedicated root service."""
    def __init__(self, io, registration):
        self.io, self.registration = io, validate_registration(registration)
        self.controls = None
        self.provider_proof = None

    def current_controls(self):
        sources = {}
        for filename in MODULES:
            raw = self.io.root_bytes(CONTROL / filename, 256 * 1024)
            normalized = raw
            if filename == "worker_policy.py":
                literal = ('SUBJECT_MANIFEST_SHA256 = "' + self.registration["subject_sha256"] + '"').encode()
                require(raw.count(literal) == 1, "NATIVE_SUBJECT_POLICY_UNBOUND")
                normalized = raw.replace(literal, b'SUBJECT_MANIFEST_SHA256 = "PENDING_DENY"', 1)
            require(hashlib.sha256(normalized).hexdigest() == self.registration["control_sha256"][filename],
                    "NATIVE_ISSUER_CONTROL_DRIFT")
            sources[filename] = raw
        # Every module is verified before any module executes. Native controls
        # contain only fixed imports and have no import-time mutation.
        if self.controls is None:
            loaded = {}
            # bootstrap is an isolated-unit entrypoint and controller is a
            # separately invoked administrator entrypoint. Both stay hashed
            # above, but importing them into this host process is forbidden.
            for filename in ("policy.py", "linux_boundary.py", "worker_policy.py", "admission.py"):
                name = filename[:-3]
                require(name not in sys.modules, "NATIVE_ISSUER_MODULE_NAMESPACE_OCCUPIED")
                module = types.ModuleType(name)
                module.__file__, module.__package__ = str(CONTROL / filename), ""
                sys.modules[name] = module
                exec(compile(sources[filename], module.__file__, "exec"), module.__dict__)
                loaded[name] = module
            self.controls = loaded
        return self.controls

    def subject_document(self):
        subject, raw_sha = self.io.root_document(NATIVE / "SUBJECT.json", 65536)
        require(raw_sha == self.registration["subject_sha256"] == digest(subject),
                "NATIVE_ISSUER_SUBJECT_DRIFT")
        require(subject["payload"]["control_files"] == self.registration["control_sha256"],
                "NATIVE_ISSUER_SUBJECT_CONTROLS_CHANGED")
        return subject

    def load(self):
        self.provider_proof = None
        controls = self.current_controls()
        subject = self.subject_document()
        verified, provider = controls["admission"].read_static_subject_with_provider()
        require(verified == subject, "NATIVE_ISSUER_SUBJECT_READER_CHANGED")
        self.provider_proof = (digest(subject), provider)
        return subject

    def read_run(self, value, expected, now, *, admission=True):
        controls = self.current_controls()
        subject = self.subject_document()
        # The native reader already verifies the complete static subject,
        # source, reviews and provider once. Repeating load() here would scan
        # them twice inside the same short authenticated grant.
        payload = controls["admission"].read_per_run_bundle(value, expected, now, admission=admission)
        require(payload == subject["payload"], "NATIVE_ISSUER_SUBJECT_READER_CHANGED")
        return payload

    @contextmanager
    def retained_reader(self, subject):
        """Authenticate prior records using only this admission's fresh proof.

        This read-only scope never admits a worker or survives a second load.
        New-run publication and launch retain the full native current reader.
        """
        proof = self.provider_proof
        require(proof is not None and proof[0] == digest(subject) == self.registration["subject_sha256"],
                "NATIVE_RETAINED_STATIC_PROOF_REQUIRED")
        controls = self.current_controls()
        a, b, p = (controls[name] for name in ("admission", "linux_boundary", "worker_policy"))
        require(Path(p.PREFIX) == NATIVE, "NATIVE_RETAINED_PREFIX_CHANGED")
        provider = proof[1]
        frozen = digest(subject), digest(provider)
        opened = True

        def read(value, expected, now, *, admission=False):
            require(opened and admission is False and self.provider_proof is proof and
                    (digest(subject), digest(provider)) == frozen,
                    "NATIVE_RETAINED_READ_SCOPE_CHANGED")
            p.nonce(value)
            p.inert.valid_digest(expected)
            path = NATIVE / "executions" / value
            owners = []
            try:
                for directory in (path.parent, path):
                    fd = b.open_directory(directory)
                    owners.append((directory, fd, os.fstat(fd)))
                    item = owners[-1][2]
                    require(item.st_uid == item.st_gid == 0 and stat.S_IMODE(item.st_mode) == 0o700,
                            "NATIVE_RETAINED_DIRECTORY_NOT_ROOT_PRIVATE")
                execution, actual = a.read_private_json(path / "EXECUTION.json", limit=32768)
                require(actual == expected, "NATIVE_RETAINED_EXECUTION_DRIFT")
                attestation, _ = a.read_private_json(path / "ADMISSION.json")
                capacity, _ = a.read_private_json(path / "CAPACITY.json", limit=32768)
                authority, _ = a.read_private_json(p.authority_input(value), limit=32768)
                result = a.validate_per_run_files(subject, execution, attestation, capacity, authority,
                                                  value, expected, provider, now, admission=False)
                for directory, fd, before in owners:
                    require(b.identity(before) == b.identity(os.fstat(fd)) == b.identity(directory.lstat()),
                            "NATIVE_RETAINED_DIRECTORY_CHANGED")
                require(self.provider_proof is proof and (digest(subject), digest(provider)) == frozen,
                        "NATIVE_RETAINED_STATIC_PROOF_CHANGED")
                return result
            finally:
                for _, fd, _ in reversed(owners):
                    os.close(fd)
        try:
            yield read
        finally:
            opened = False

    def template_size(self, subject):
        # This is the exact provider proof returned by this admission's load;
        # final read_run verifies it all again before gate publication. No
        # cross-admission cache substitutes for the native current reader.
        require(self.provider_proof is not None and self.provider_proof[0] == digest(subject),
                "NATIVE_ISSUER_PROVIDER_PROOF_REQUIRED")
        proof = self.provider_proof[1]
        require(proof["manifest_sha256"] == subject["payload"]["rootfs_manifest_sha256"] and
                type(proof["bytes"]) is int and type(proof["entries"]) is int and
                proof["bytes"] >= 0 and proof["entries"] > 0, "NATIVE_ISSUER_ROOTFS_CHANGED")
        return proof["bytes"], proof["entries"]

    def authority(self, value, issued, expires):
        p = self.controls["worker_policy"]
        port = 49152 + int(value[:4], 16) % 16384
        if port == 55449:
            port += 1
        result = {"schema_version": 4, "purpose": "bigrro-combined-disposable-verification",
            "executor": "ABIT-DEV-LINUX", "repository": "asma2rjoub-dotcom/bigrro-platform",
            "head_sha": WORKER_HEAD, "tree_sha": WORKER_TREE, "base_sha": p.BASE_SHA,
            "nonce": value, "port": port, "database": "bigrro_verify_" + value,
            "connection_role": "bigrro_verify_" + value,
            "upgrade_database": "bigrro_p20_disposable_" + value,
            "max_databases": 2, "plan_sha256": PLAN_SHA, "executor_profile_sha256": PROFILE_SHA,
            "runtime_manifest_sha256": RUNTIME_SHA, "postgres_major": 15,
            "issued_at_utc": utc(issued), "expires_at_utc": utc(expires), "max_attempts": 1,
            "allowed_operations": list(p.OPERATIONS), "additional_spend_usd": 0,
            "publication_authorized": False}
        return self.controls["admission"].validate_authority(result, issued)


class PerRunIssuer:
    def __init__(self, io, registration, peer_check):
        require(os.geteuid() == 0 and callable(peer_check), "NATIVE_ISSUER_ROOT_PEER_REQUIRED")
        self.io, self.registration, self.peer_check = io, validate_registration(registration), peer_check
        self.native = NativeSubject(io, registration)
        self.index = io.ROOT / "admissions"
        for path in (self.index, NATIVE / "executions", NATIVE / "authority"):
            fd = io.root_directory(path)
            try:
                require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "NATIVE_ISSUER_PRIVATE_DIRECTORY")
            finally:
                os.close(fd)

    @contextmanager
    def locked(self):
        parent = self.io.root_directory(LOCK.parent)
        fd = None
        try:
            # The reviewed installer creates this exact shared lock. Admission
            # cannot substitute a new inode for an existing controller lock.
            fd = os.open(LOCK.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and
                    before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600,
                    "NATIVE_ISSUER_LOCK_METADATA")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(identity(before) == identity(os.stat(LOCK.name, dir_fd=parent, follow_symlinks=False)),
                    "NATIVE_ISSUER_LOCK_REPLACED")
            yield
            require(identity(before) == identity(os.fstat(fd)) ==
                    identity(os.stat(LOCK.name, dir_fd=parent, follow_symlinks=False)),
                    "NATIVE_ISSUER_LOCK_CHANGED")
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)

    def path(self, attestation):
        return self.index / hashlib.sha256(attestation["scope"]["invocation_id"].encode()).hexdigest()

    def mkdir_once(self, path):
        parent = self.io.root_directory(path.parent)
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)

    def names(self):
        fd = self.io.root_directory(self.index)
        try:
            before = os.fstat(fd)
            result = os.listdir(fd)
            require(len(result) <= MAX_INDEX and all(re.fullmatch(r"[a-f0-9]{64}", n) for n in result),
                    "NATIVE_ADMISSION_INDEX_BOUNDS")
            named = self.io.root_directory(self.index)
            try:
                require(identity(before) == identity(os.fstat(fd)) == identity(os.fstat(named)),
                        "NATIVE_ADMISSION_INDEX_CHANGED")
            finally:
                os.close(named)
            return result
        finally:
            os.close(fd)

    def no_pending(self, incoming, read_prior):
        for name in self.names():
            old, _ = self.io.root_document(self.index / name / "ATTESTATION.json")
            validate_attestation(old, 0, current=False)
            require(self.path(old).name == name, "NATIVE_ADMISSION_INDEX_SCOPE")
            require(old["consumption"]["receipt_id"] != incoming["consumption"]["receipt_id"] and
                    old["action"]["action_id"] != incoming["action"]["action_id"] and
                    old["scope"]["run_id"] != incoming["scope"]["run_id"],
                    "NATIVE_ADMISSION_APPROVAL_ALREADY_USED")
            # Unknown/partial admission or launch is not cleared by timeout.
            prior = self.observe({"attestation": old}, read_prior=read_prior)
            require(prior["state"] == "admitted", "NATIVE_PRIOR_ADMISSION_UNRESOLVED")
            value = prior["gate"]["execution"]["nonce"]
            binding, _ = self.io.root_document(self.io.ROOT / "attempts" / value / "BINDING.json")
            require(binding["scope"] == old["scope"] and binding["gate_sha256"] == digest(prior["gate"]),
                    "NATIVE_PRIOR_BINDING_CHANGED")
            outcome = self.io.observe_worker(binding, native=NATIVE)
            require(outcome["state"] in {"completed", "failed_retained"} and outcome["cleanup_confirmed"] is True,
                    "NATIVE_PRIOR_ATTEMPT_UNRESOLVED")

    def active_cgroups(self):
        fd = self.io.root_directory(CGROUPS)
        try:
            names = os.listdir(fd)
            require(len(names) <= 4096, "NATIVE_CGROUP_INVENTORY_BOUNDS")
        finally:
            os.close(fd)
        for name in names:
            if not re.fullmatch(r"(?:bigrro-proj0202-native|abit-native-controller)-[a-f0-9]{32}\.service", name):
                continue
            path = CGROUPS / name
            child = self.io.root_directory(path)
            os.close(child)
            events = {}
            for line in kernel_bytes(path / "cgroup.events", 4096).decode("ascii").splitlines():
                key, value = line.split()
                require(key not in events, "NATIVE_CGROUP_DUPLICATE_EVENT")
                events[key] = value
            require(events.get("populated") == "0" and not kernel_bytes(path / "cgroup.procs", 8192).strip(),
                    "NATIVE_CONFLICTING_ACTIVE_ATTEMPT")
        return 0

    def capacity(self, subject, value, attestation, issued, expires):
        mem = {}
        for line in kernel_bytes(Path("/proc/meminfo"), 65536).decode("ascii").splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                key, count, unit = line.split()
                require(key not in mem and count.isdigit() and unit == "kB", "NATIVE_MEMORY_METADATA")
                mem[key] = int(count) * 1024
        require(set(mem) == {"MemTotal:", "MemAvailable:"} and
                MEMORY_REQUIRED <= mem["MemAvailable:"] <= mem["MemTotal:"], "NATIVE_CURRENT_CAPACITY_DENIED")
        active = self.active_cgroups()
        file_bytes, entries = self.native.template_size(subject)
        disk = os.statvfs(NATIVE)
        required_inodes = entries + 16
        archive = self.native.controls["worker_policy"].MAX_ARCHIVE_BYTES
        required = archive + file_bytes + required_inodes * disk.f_frsize
        require(disk.f_frsize > 0 and disk.f_bavail * disk.f_frsize >= required and
                disk.f_favail >= required_inodes, "NATIVE_CURRENT_DISK_CAPACITY_DENIED")
        return {"schema_version": 1, "kind": "bigrro_native_per_run_capacity", "nonce": value,
            "subject_sha256": self.registration["subject_sha256"], "admission_sha256": digest(attestation),
            "observed_at": issued, "expires_at": expires, "memory_available_bytes": mem["MemAvailable:"],
            "memory_required_bytes": MEMORY_REQUIRED, "free_output_capture_bytes": disk.f_bavail * disk.f_frsize,
            "required_output_capture_bytes": required, "concurrent_native_attempts": active,
            "rootfs_copy_bytes": file_bytes, "required_copy_inodes": required_inodes,
            "available_inodes": disk.f_favail, "filesystem_block_bytes": disk.f_frsize}

    def prepare(self, payload):
        exact(payload, {"scope"}, "NATIVE_ISSUER_PREFLIGHT_SCOPE")
        self.native.load()
        return readiness(payload["scope"], self.registration["subject_sha256"])

    def admit(self, payload, grant, peer):
        now = int(time.time())
        validate_admission_grant(grant, payload, now)
        attestation = payload["attestation"]
        require(attestation["subject_sha256"] == self.registration["subject_sha256"],
                "NATIVE_ADMISSION_SUBJECT_REPLACED")
        with self.locked():
            # Even exact repeat admission is only observation of its original.
            if self.path(attestation).name in self.names():
                return self.observe(payload)
            subject = self.native.load()
            with self.native.retained_reader(subject) as read_prior:
                self.no_pending(attestation, read_prior)
            value = nonce(secrets.token_hex(16))
            issued = int(time.time())
            # Dispatch ownership is current at issue and the later launch challenge.
            # Its renewable120s lease is not a worker-runtime deadline. This
            # immutable delegation stays within the original human approval;
            # the native fixed worker retains its separate3600s runtime cap.
            expires = min(issued + 7200, attestation["approval"]["expires_at"])
            capacity_expires = min(issued + 900, expires)
            require(issued < expires, "NATIVE_ADMISSION_WINDOW_EXPIRED")
            capacity = self.capacity(subject, value, attestation, issued, capacity_expires)
            authority = self.native.authority(value, issued, expires)
            validate_admission_grant(grant, payload, int(time.time()))
            require(self.peer_check() == peer, "NATIVE_ADMISSION_CORE_PROCESS_CHANGED")
            index = self.path(attestation)
            self.mkdir_once(index)
            self.io.write_once(index, "ATTESTATION.json", attestation)
            self.io.write_once(index, "INTENT.json", {"nonce": value, "attestation_sha256": digest(attestation),
                "state": "admission_may_have_been_published", "automatic_replay_permitted": False})
            self.io.write_once(index, "AUTHENTICATED_PEER.json", peer)
            run = NATIVE / "executions" / value
            self.mkdir_once(run)
            self.io.write_once(run, "ADMISSION.json", attestation)
            self.io.write_once(run, "CAPACITY.json", capacity)
            self.io.write_once(NATIVE / "authority", value + ".json", authority)
            execution = {"schema_version": 3, "kind": "bigrro_native_per_run_execution", "nonce": value,
                "subject_sha256": self.registration["subject_sha256"], "admission_sha256": digest(attestation),
                "authority_sha256": digest(authority), "capacity_sha256": digest(capacity),
                "issued_at": issued, "expires_at": expires}
            self.io.write_once(run, "EXECUTION.json", execution)
            self.native.read_run(value, digest(execution), int(time.time()))
            validate_admission_grant(grant, payload, int(time.time()))
            require(self.peer_check() == peer, "NATIVE_ADMISSION_CORE_PROCESS_CHANGED")
            gate = {"schema_version": 2, "kind": "abit_native_per_invocation_gate",
                "scope": attestation["scope"], "admission_sha256": digest(attestation),
                "subject_sha256": self.registration["subject_sha256"],
                "execution": {"nonce": value, "authority_sha256": digest(authority),
                    "execution_manifest_sha256": digest(execution), "worker_head": WORKER_HEAD,
                    "worker_tree": WORKER_TREE, "plan_sha256": PLAN_SHA,
                    "executor_profile_sha256": PROFILE_SHA, "runtime_manifest_sha256": RUNTIME_SHA},
                "issued_at": issued, "expires_at": expires}
            validate_gate(gate, attestation["scope"], int(time.time()))
            self.io.write_once(self.io.ROOT / "gates", self.io.gate_file(attestation["scope"]).name, gate)
            result = observation(attestation, gate)
            self.io.write_once(index, "ADMITTED.json", result)
            return result

    def observe(self, payload, *, read_prior=None):
        exact(payload, {"attestation"}, "NATIVE_ADMISSION_OBSERVE_SCHEMA")
        attestation = validate_attestation(payload["attestation"], 0, current=False)
        index = self.path(attestation)
        try:
            saved, _ = self.io.root_document(index / "ATTESTATION.json")
        except FileNotFoundError:
            return observation(attestation)
        require(saved == attestation, "NATIVE_ADMISSION_ORIGINAL_CONFLICT")
        try:
            gate = self.io.load_gate(attestation["scope"], admission=False)
        except FileNotFoundError:
            return observation(attestation)
        require(gate.get("admission_sha256") == digest(attestation) and
                gate.get("subject_sha256") == self.registration["subject_sha256"],
                "NATIVE_ADMISSION_ORIGINAL_GATE_CONFLICT")
        reader = self.native.read_run if read_prior is None else read_prior
        reader(gate["execution"]["nonce"], gate["execution"]["execution_manifest_sha256"],
               int(time.time()), admission=False)
        return observation(attestation, gate)

    def verify(self, gate, *, admission=True, binding=None):
        require(gate.get("schema_version") == 2 and gate.get("subject_sha256") == self.registration["subject_sha256"],
                "NATIVE_PER_RUN_GATE_REQUIRED")
        execution = gate["execution"]
        self.native.read_run(execution["nonce"], execution["execution_manifest_sha256"],
                             int(time.time()), admission=admission)
        attestation, _ = self.io.root_document(NATIVE / "executions" / execution["nonce"] / "ADMISSION.json")
        require(digest(attestation) == gate["admission_sha256"] and attestation["scope"] == gate["scope"],
                "NATIVE_PER_RUN_GATE_ATTESTATION_CHANGED")
        if binding is not None:
            require(binding["action_id"] == attestation["action"]["action_id"] and
                    binding["native_approval_sha256"] == digest(attestation["consumption"]) and
                    all(binding[key] == attestation[key] for key in ("lease_token_sha256",
                        "start_receipt_sha256", "erp_baseline_sha256", "maintenance_admission")),
                    "NATIVE_PER_RUN_LAUNCH_BINDING_CHANGED")
        return execution
