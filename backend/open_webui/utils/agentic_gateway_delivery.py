"""Pinned planning snapshots, GitHub delivery and independent PR observation.

No model-selected URL, credential, branch, commit, shell or Git configuration.
Every remote mutation has a durable intent first. An unknown acknowledgment is
GET-only until the exact original object is positively found; it is never
permission to repeat a POST. Draft PRs remain unmerged and unapproved.
"""

from __future__ import annotations

import base64
import asyncio
import ctypes
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import time
from urllib.parse import quote, urlsplit

import httpx

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest
from open_webui.utils.agentic_artifacts import artifact_directory, local_bytes
from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable, _open_dir

TOKEN_FILE = Path('/var/lib/abit-ai-agentic-gateway/github-token')
SHA = re.compile(r'[a-f0-9]{40}')
REPO = re.compile(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)')
BRANCH = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,100}')
MAX_FILE = 196_608
MAX_FILES = 2000
MAX_CHANGED = 200


class RemoteUnknown(Exception):
    """Original remote intent may exist. Only its deterministic GET is safe."""


def git_hash(kind, data):
    return hashlib.sha1(
        kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data, usedforsecurity=False
    ).hexdigest()


def _excluded(parts):
    from open_webui.utils.agentic_gateway_operations import _private_path_part

    return any(p in {'.git', '.ssh', '.codex'} or _private_path_part(p) for p in parts)


def snapshot_workspace(row, packet, config):
    """Internal bytes plus a deterministic manifest; never return contents to a model."""
    from open_webui.utils.agentic_gateway_operations import _workspace

    root, profile = _workspace(row, packet, config)
    return _snapshot_at_root(root, profile)


def _snapshot_at_root(root, profile):
    from open_webui.utils.agentic_gateway_operations import _path, _parent, _read

    allowed = profile.get('allowed_paths')
    if not isinstance(allowed, list) or not allowed or len(allowed) > 100:
        raise CapabilityUnavailable('Repository delivery needs bounded approved source paths')
    contents, entries, total = {}, {}, 0

    def visit(relative):
        nonlocal total
        if _excluded(PurePosixPath(relative).parts):
            return
        path = _path({'path': relative}, profile)
        parent = _parent(root, path)
        try:
            try:
                before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return  # The remote base comparison records actual deletions.
            if stat.S_ISLNK(before.st_mode):
                raise ValueError('Delivery source cannot include a symlink')
            if stat.S_ISDIR(before.st_mode):
                folder = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    names = os.listdir(folder)
                finally:
                    os.close(folder)
                if len(names) > MAX_FILES:
                    raise ValueError('Delivery source directory exceeds its bound')
                for name in sorted(names):
                    visit(str(path / name))
                return
            if before.st_nlink != 1:
                raise ValueError('Delivery source cannot include hard-linked files')
            data, info = _read(parent, path.name)
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (before.st_ino, before.st_mtime_ns, before.st_size) != (info.st_ino, info.st_mtime_ns, info.st_size) or (
                info.st_ino,
                info.st_mtime_ns,
                info.st_size,
            ) != (after.st_ino, after.st_mtime_ns, after.st_size):
                raise ValueError('Delivery source changed during its snapshot')
        finally:
            os.close(parent)
        relative = str(path)
        if relative not in entries:
            total += len(data)
        if len(entries) >= MAX_FILES or total > 64_000_000:
            raise ValueError('Complete delivery source snapshot exceeds its bound')
        contents[relative] = data
        entries[relative] = {
            'path': relative,
            'sha256': hashlib.sha256(data).hexdigest(),
            'git_blob_sha1': git_hash('blob', data),
            'mode': '100755' if info.st_mode & 0o111 else '100644',
            'bytes': len(data),
        }

    for relative in allowed:
        if not isinstance(relative, str):
            raise ValueError('Invalid approved source path')
        visit(relative)
    manifest = [entries[name] for name in sorted(entries)]
    return {'sha256': digest(manifest), 'manifest': manifest, 'contents': contents}


def _token():
    folder = _open_dir(TOKEN_FILE.parent)
    try:
        fd = os.open(TOKEN_FILE.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 512 or info.st_mode & 0o027:
                raise CapabilityUnavailable('Protected repository credential is unavailable')
            value = os.read(fd, 513).decode('ascii').strip()
        finally:
            os.close(fd)
    finally:
        os.close(folder)
    if len(value) < 32:
        raise CapabilityUnavailable('Protected repository credential is unavailable')
    return value


class GitHubClient:
    def __init__(self, token, repository, *, _test_base=None):
        match = REPO.fullmatch(repository)
        if not match:
            raise ValueError('The approved repository must be an exact GitHub repository URL')
        self.repo = match.group(1) + '/' + match.group(2).removesuffix('.git')
        base = 'https://api.github.com'
        if _test_base is not None:
            parsed = urlsplit(_test_base)
            if (
                parsed.scheme != 'http'
                or parsed.hostname != '127.0.0.1'
                or not parsed.port
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
            ):
                raise ValueError('Controlled test transport must use its owned loopback server')
            base = _test_base
        self.http = httpx.AsyncClient(
            base_url=base,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            headers={
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2026-03-10',
            },
        )

    async def request(self, method, suffix, body=None):
        if method not in {'GET', 'POST'} or not suffix.startswith('/') or '://' in suffix or '\\' in suffix:
            raise ValueError('Unsupported fixed repository request')
        try:
            response = await self.http.request(method, '/repos/' + self.repo + suffix, json=body)
            if method == 'GET' and response.status_code == 404:
                return None
            if response.status_code not in ({200} if method == 'GET' else {201}):
                raise RemoteUnknown('GitHub original request is unconfirmed; reconcile its exact object')
            if len(response.content) > 8_000_000:
                raise RemoteUnknown('GitHub response exceeds the complete snapshot bound')
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise RemoteUnknown('GitHub original response is unavailable; no automatic write retry') from None

    async def close(self):
        await self.http.aclose()


def github_client(repository):
    return GitHubClient(_token(), repository)


@contextmanager
def _db(db_path=None):
    with ledger.connect(db_path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS governance_developer_delivery(
            invocation_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL,
            input_json TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT,created_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS governance_delivery_step(
            invocation_id TEXT NOT NULL,step_key TEXT NOT NULL,request_json TEXT NOT NULL,
            state TEXT NOT NULL,result_json TEXT,PRIMARY KEY(invocation_id,step_key));
          CREATE TABLE IF NOT EXISTS governance_qa_delivery(
            invocation_id TEXT PRIMARY KEY,packet_sha256 TEXT NOT NULL,delivery_sha256 TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL,facts_json TEXT NOT NULL,created_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS governance_repository_workspace(
            invocation_id TEXT PRIMARY KEY,scope_json TEXT NOT NULL,state TEXT NOT NULL,
            manifest_json TEXT,config_sha256 TEXT NOT NULL,snapshot_sha256 TEXT,
            created_at INTEGER NOT NULL);''')
        yield db


def _repository_profile(row, packet, config):
    if row['target_role'] not in {'solution_architect', 'project_manager', 'developer', 'qa'}:
        raise CapabilityUnavailable('This role has no repository bootstrap capability')
    profile = (config.get('agentic_repository_gateway') or {}).get('projects', {}).get(row['erp_project'])
    if (
        not isinstance(profile, dict)
        or profile.get('repository') != packet['repository']
        or profile.get('environment') != packet['environment']
        or not REPO.fullmatch(packet['repository'])
    ):
        raise CapabilityUnavailable('No approved repository/environment bootstrap profile exists')
    return profile


def _safe_workspace_root(profile):
    base = Path(profile.get('workspace_root', ''))
    if not base.is_absolute() or not str(base).startswith('/srv/abit-dev-linux/') or '..' in base.parts:
        raise CapabilityUnavailable('Repository bootstrap root is not approved')
    return base


def _workspace_target(row, profile):
    if any(
        not isinstance(row.get(k), str)
        or not re.fullmatch(r'[A-Za-z0-9_:@+\-.]{1,160}', row[k])
        or row[k] in {'.', '..'}
        for k in ('erp_task', 'invocation_id')
    ):
        raise ValueError('Invalid server-owned workspace identity')
    return _safe_workspace_root(profile) / row['erp_task'] / row['invocation_id'] / 'repository'


def _workspace_scope(row, packet, profile, commit):
    return {
        'invocation_id': row['invocation_id'],
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'role': row['target_role'],
        'repository': packet['repository'],
        'environment': packet['environment'],
        'commit': commit,
        'profile_sha256': digest(profile),
    }


def _workspace_config(repository):
    if not REPO.fullmatch(repository):
        raise ValueError('Invalid approved repository')
    return (
        '[core]\n\trepositoryformatversion = 0\n\tbare = false\n[remote "origin"]\n\turl = ' + repository + '\n'
    ).encode()


def workspace_receipt(row, packet, config, db_path=None, *, include_source_manifest=False):
    """Read-only server provenance for _workspace, not a model-selected config hash."""
    profile = _repository_profile(row, packet, config)
    root = _workspace_target(row, profile)
    with _db(db_path) as db:
        saved = db.execute(
            "SELECT * FROM governance_repository_workspace WHERE invocation_id=? AND state='completed'",
            (row['invocation_id'],),
        ).fetchone()
    if not saved:
        raise CapabilityUnavailable('Original repository workspace has not been prepared')
    scope = json.loads(saved['scope_json'])
    commit = (
        packet.get('implementation_delivery', {}).get('head_commit')
        if row['target_role'] == 'qa'
        else profile.get('base_commit')
    )
    if scope != _workspace_scope(row, packet, profile, commit):
        raise ValueError('Original workspace scope or approved baseline changed')
    directory = _open_dir(root / '.git')
    try:
        fd = os.open('config', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                raise ValueError('Invalid prepared Git metadata')
            data = os.read(fd, 32769)
        finally:
            os.close(fd)
    finally:
        os.close(directory)
    if hashlib.sha256(data).hexdigest() != saved['config_sha256'] or data != _workspace_config(packet['repository']):
        raise ValueError('Original server-created Git configuration changed')
    result = {
        **scope,
        'config_sha256': saved['config_sha256'],
        'snapshot_sha256': saved['snapshot_sha256'],
        'workspace_root': str(root),
        'api_materialized_checkout': True,
    }
    if row['target_role'] in {'solution_architect', 'project_manager'}:
        from open_webui.utils.agentic_gateway_operations import _repository_view

        manifest = json.loads(saved['manifest_json'])
        actual = _snapshot_at_root(root, _repository_view(row, profile))
        if actual['manifest'] != manifest or actual['sha256'] != saved['snapshot_sha256']:
            raise ValueError('Planning repository snapshot changed after its approved base was prepared')
        result.update(read_only_source=True, file_count=len(manifest))
        if include_source_manifest:
            result['_source_manifest'] = manifest
    return result


def _install_directory(parent, source, destination):
    """Linux atomic NOREPLACE; never overwrite an existing human/other job tree."""
    fd = _open_dir(parent)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise CapabilityUnavailable('Atomic no-replace workspace installation is unavailable')
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        if rename(fd, source.encode(), fd, destination.encode(), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        os.fsync(fd)
    finally:
        os.close(fd)


async def prepare_workspace(args, *, row, packet, config, db_path=None, **kwargs):
    if args != {}:
        raise ValueError('Repository preparation accepts no caller-selected source or path')
    profile = _repository_profile(row, packet, config)
    root = _workspace_target(row, profile)
    from open_webui.utils.agentic_gateway_operations import _repository_view

    view = _repository_view(row, profile)
    source_paths, test_paths = view.get('allowed_paths'), view.get('test_paths', [])
    if not isinstance(source_paths, list) or not isinstance(test_paths, list):
        raise CapabilityUnavailable('Approved workspace paths must be explicit lists')
    requested = source_paths + test_paths
    if not requested or len(requested) > 200 or any(not isinstance(p, str) for p in requested):
        raise CapabilityUnavailable('Workspace preparation needs bounded approved source/test paths')
    from open_webui.utils.agentic_gateway_operations import _path

    for path in requested:
        if not _excluded(PurePosixPath(path).parts):
            _path({'path': path}, {'allowed_paths': requested})
    client = github_client(packet['repository'])
    stage = None
    try:
        if row['target_role'] != 'qa':
            if (
                not isinstance(profile.get('base_branch'), str)
                or not BRANCH.fullmatch(profile['base_branch'])
                or '..' in profile['base_branch']
                or not SHA.fullmatch(profile.get('base_commit', ''))
            ):
                raise CapabilityUnavailable('Repository bootstrap needs a fixed approved base branch/commit')
            commit = profile['base_commit']
            _, leaves = await _base(client, profile)
        else:
            value = packet.get('implementation_delivery')
            from open_webui.utils.agentic_contracts.developer_delivery import validate_delivery

            if validate_delivery(value, packet['repository']):
                raise ValueError('QA bootstrap needs its exact Developer delivery')
            commit = value['head_commit']
            pr = await client.request('GET', '/pulls/' + value['pull_request_url'].rsplit('/', 1)[1])
            owner, repo = REPO.fullmatch(packet['repository']).groups()
            full = owner + '/' + repo.removesuffix('.git')
            if (
                not pr
                or pr.get('head', {}).get('sha') != commit
                or pr.get('head', {}).get('repo', {}).get('full_name') != full
                or pr.get('base', {}).get('repo', {}).get('full_name') != full
                or pr.get('base', {}).get('sha') != value['base_commit']
                or pr.get('html_url') != value['pull_request_url']
                or pr.get('state') != 'open'
            ):
                raise ValueError('QA bootstrap PR head/base no longer matches the implementation')
            obj = await client.request('GET', '/git/commits/' + commit)
            if not obj or obj.get('sha') != commit or not SHA.fullmatch(obj.get('tree', {}).get('sha', '')):
                raise ValueError('QA bootstrap commit did not verify')
            leaves = await _read_tree(client, obj['tree']['sha'])
        selected = {
            p: v
            for p, v in leaves.items()
            if not _excluded(PurePosixPath(p).parts)
            and any(p == x or p.startswith(x.rstrip('/') + '/') for x in requested)
        }
        if len(selected) > MAX_FILES or any(
            v['type'] != 'blob' or v['mode'] not in {'100644', '100755'} for v in selected.values()
        ):
            raise ValueError('Bootstrap source is too large or contains unsupported link/submodule entries')
        scope = _workspace_scope(row, packet, profile, commit)
        cfg = _workspace_config(packet['repository'])
        cfgsha = hashlib.sha256(cfg).hexdigest()
        with _db(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute(
                'SELECT * FROM governance_repository_workspace WHERE invocation_id=?', (row['invocation_id'],)
            ).fetchone()
            if old:
                if old['scope_json'] != canonical(scope) or old['config_sha256'] != cfgsha:
                    raise ValueError('Original bootstrap inputs are immutable')
                if old['state'] == 'completed':
                    db.commit()
                    return workspace_receipt(row, packet, config, db_path)
            else:
                if root.exists() or root.is_symlink():
                    raise ValueError('An existing workspace without original server provenance cannot be adopted')
                db.execute(
                    'INSERT INTO governance_repository_workspace VALUES(?,?,?,NULL,?,NULL,?)',
                    (row['invocation_id'], canonical(scope), 'preparing', cfgsha, int(time.time())),
                )
            db.commit()
        if root.exists() or root.is_symlink():
            if old is None or old['state'] != 'files_ready' or not old['manifest_json']:
                raise ValueError('Uncertain workspace installation needs exact original file provenance')
            current = _snapshot_at_root(root, {**profile, 'allowed_paths': requested})
            if current['manifest'] != json.loads(old['manifest_json']):
                raise ValueError('Original installed workspace bytes changed')
            with _db(db_path) as db:
                db.execute(
                    "UPDATE governance_repository_workspace SET state='completed' WHERE invocation_id=? AND state='files_ready'",
                    (row['invocation_id'],),
                )
                db.commit()
            return workspace_receipt(row, packet, config, db_path)
        # Open every existing/new ancestor without following links.
        base = _safe_workspace_root(profile)
        from open_webui.utils.agentic_artifacts import ensure_directory

        ensure_directory(base)
        for parent in (base / row['erp_task'], base / row['erp_task'] / row['invocation_id']):
            pfd = _open_dir(parent.parent)
            try:
                try:
                    os.mkdir(parent.name, 0o700, dir_fd=pfd)
                except FileExistsError:
                    pass
            finally:
                os.close(pfd)
            check = _open_dir(parent)
            os.close(check)
        stage = root.parent / ('.prepare-' + secrets.token_hex(16))
        stage.mkdir(mode=0o700)
        for path in requested:
            if _excluded(PurePosixPath(path).parts):
                continue
            directory = PurePosixPath(path).parent
            (stage / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
            if path not in leaves and not PurePosixPath(path).suffix:
                (stage / path).mkdir(parents=True, exist_ok=True, mode=0o700)
        semaphore = asyncio.Semaphore(8)
        total = 0

        async def download(path, item):
            nonlocal total
            async with semaphore:
                result = await client.request('GET', '/git/blobs/' + item['sha'])
                if not result or result.get('sha') != item['sha'] or result.get('encoding') != 'base64':
                    raise ValueError('Bootstrap blob identity did not verify')
                data = base64.b64decode(result.get('content', '').replace('\n', ''), validate=True)
                if len(data) > MAX_FILE or git_hash('blob', data) != item['sha']:
                    raise ValueError('Bootstrap source bytes exceed their bound or Git hash')
                total += len(data)
                if total > 64_000_000:
                    raise ValueError('Bootstrap source exceeds its 64MB bound')
                destination = stage / PurePosixPath(path)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with destination.open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                destination.chmod(0o700 if item['mode'] == '100755' else 0o600)

        tasks = [asyncio.create_task(download(path, item)) for path, item in selected.items()]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), 50)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        (stage / '.git').mkdir(mode=0o700)
        (stage / '.git/config').write_bytes(cfg)
        (stage / '.git/config').chmod(0o600)
        (stage / '.git/HEAD').write_text(commit + '\n')
        (stage / '.git/HEAD').chmod(0o600)
        source = _snapshot_at_root(stage, view)
        all_files = _snapshot_at_root(stage, {**profile, 'allowed_paths': requested})
        with _db(db_path) as db:
            db.execute(
                "UPDATE governance_repository_workspace SET state='files_ready',manifest_json=?,snapshot_sha256=? WHERE invocation_id=? AND state IN ('preparing','files_ready')",
                (canonical(all_files['manifest']), source['sha256'], row['invocation_id']),
            )
            db.commit()
        _install_directory(root.parent, stage.name, root.name)
        stage = None
        with _db(db_path) as db:
            db.execute(
                "UPDATE governance_repository_workspace SET state='completed' WHERE invocation_id=? AND state='files_ready'",
                (row['invocation_id'],),
            )
            db.commit()
        return workspace_receipt(row, packet, config, db_path)
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        await client.close()


def _profile(row, packet, config):
    from open_webui.utils.agentic_gateway_operations import _workspace

    _, p = _workspace(row, packet, config)
    if (
        not REPO.fullmatch(packet['repository'])
        or not isinstance(p.get('base_branch'), str)
        or not BRANCH.fullmatch(p['base_branch'])
        or '..' in p['base_branch']
        or not SHA.fullmatch(p.get('base_commit', ''))
    ):
        raise CapabilityUnavailable('Delivery requires the approved repository, base branch and full base commit')
    author = p.get('git_author')
    if (
        not isinstance(author, dict)
        or set(author) != {'name', 'email'}
        or not isinstance(author['name'], str)
        or not 1 <= len(author['name']) <= 100
        or any(c in author['name'] for c in '<>\r\n\0')
        or not isinstance(author['email'], str)
        or not re.fullmatch(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+', author['email'])
    ):
        raise CapabilityUnavailable('Delivery requires a fixed server-approved commit author')
    return p


def _tests(call_ids, row, packet, profile, snapshot, config, db_path):
    if (
        not isinstance(call_ids, list)
        or not 1 <= len(call_ids) <= 100
        or any(not isinstance(x, str) or len(x) > 160 for x in call_ids)
        or len(set(call_ids)) != len(call_ids)
    ):
        raise ValueError('Delivery requires exact original test call IDs')
    expected = {x['id']: x for x in packet['acceptance_tests']}
    covered, runs, evidence, observations = set(), [], [], []
    mapping = profile.get('test_acceptance_ids') or {}
    with _db(db_path) as db:
        for cid in sorted(call_ids):
            found = db.execute(
                '''SELECT c.* FROM governance_gateway_call c
              JOIN governance_gateway_session s ON s.session_id=c.session_id
              WHERE s.invocation_id=? AND c.call_id=? AND c.operation='repository.test' AND c.state='completed' ''',
                (row['invocation_id'], cid),
            ).fetchall()
            if len(found) != 1:
                raise ValueError('Test receipt does not belong to this original implementation')
            result = json.loads(found[0]['result_json'])
            ids = mapping.get(result.get('test_id'))
            if (
                type(result.get('exit_code')) is not int
                or result['exit_code'] != 0
                or result.get('repository_snapshot_sha256') != snapshot['sha256']
                or result.get('sandbox') != 'bubblewrap-no-network-sanitized-copy'
                or not isinstance(ids, list)
                or not ids
                or len(ids) != len(set(ids))
                or not set(ids) <= set(expected)
            ):
                raise ValueError('Successful tests must cover the exact current implementation snapshot')
            if result.get('command') != canonical((profile.get('tests') or {}).get(result['test_id'])):
                raise ValueError('The observed test command is no longer approved for this test identity')
            ref = result.get('evidence') or {}
            name, data = local_bytes(artifact_directory(row, config), ref.get('uri', ''))
            if hashlib.sha256(data).hexdigest() != ref.get('sha256'):
                raise ValueError('Actual original test log bytes changed')
            log = json.loads(data)
            if any(
                log.get(k) != result.get(k)
                for k in ('test_id', 'exit_code', 'command', 'sandbox', 'repository_snapshot_sha256')
            ):
                raise ValueError('Original command receipt does not match its actual log bytes')
            for stream in ('stdout', 'stderr'):
                text = log.get(stream)
                sha = result.get(stream + '_sha256')
                if (
                    not isinstance(text, str)
                    or hashlib.sha256(text.encode()).hexdigest() != sha
                    or log.get(stream + '_sha256') != sha
                    or result.get(stream) != text[:4096]
                ):
                    raise ValueError('Original full test output does not match its observed digest and preview')
            ident = 'TEST-' + digest(cid)[:32]
            requirements = sorted({r for tid in ids for r in expected[tid]['requirement_ids']})
            evidence.append(
                {
                    'id': ident,
                    'kind': 'test_log',
                    'uri': 'artifact://' + name,
                    'sha256': ref['sha256'],
                    'requirement_ids': requirements,
                }
            )
            runs.append({'test_ids': ids, 'command': result['command'], 'exit_code': 0, 'log_evidence_id': ident})
            for tid in ids:
                if tid not in covered:
                    observations.append(
                        {
                            'test_id': tid,
                            'status': 'pass',
                            'observed': 'Server-registered test command exited 0 on snapshot ' + snapshot['sha256'],
                            'evidence_ids': [ident],
                        }
                    )
            covered.update(ids)
    if covered != set(expected):
        raise ValueError('Every Task acceptance test needs an actual successful command receipt')
    return {'test_runs': runs, 'evidence': evidence, 'test_results': observations}


def _tree_hash(leaves):
    root = {}
    for path, item in leaves.items():
        parts = PurePosixPath(path).parts
        if (
            not parts
            or str(PurePosixPath(path)) != path
            or any(p in {'.', '..'} for p in parts)
            or path.startswith('/')
        ):
            raise ValueError('Unsafe remote Git tree path')
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict) or isinstance(child.get('sha'), str):
                raise ValueError('Conflicting remote Git tree paths')
            node = child
        node[parts[-1]] = item

    def encode(node):
        rows = []
        for name, value in node.items():
            directory = not isinstance(value.get('sha'), str)
            mode, sha = ('40000', encode(value)) if directory else (value['mode'], value['sha'])
            rows.append(
                (
                    (name + ('/' if directory else '')).encode(),
                    mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(sha),
                )
            )
        return git_hash('tree', b''.join(value for _, value in sorted(rows)))

    return encode(root)


def _commit_hash(body):
    def person(value):
        stamp = int(datetime.fromisoformat(value['date'].replace('Z', '+00:00')).timestamp())
        return value['name'] + ' <' + value['email'] + '> ' + str(stamp) + ' +0000'

    text = 'tree ' + body['tree'] + '\n' + ''.join('parent ' + x + '\n' for x in body['parents'])
    text += 'author ' + person(body['author']) + '\ncommitter ' + person(body['committer']) + '\n\n' + body['message']
    return git_hash('commit', text.encode())


async def _base(client, profile):
    ref = await client.request('GET', '/git/ref/heads/' + quote(profile['base_branch'], safe=''))
    if not ref or ref.get('object', {}).get('sha') != profile['base_commit']:
        raise ValueError('Approved base branch no longer matches its pinned commit')
    commit = await client.request('GET', '/git/commits/' + profile['base_commit'])
    if not commit or commit.get('sha') != profile['base_commit']:
        raise ValueError('Approved base commit identity did not verify')
    tree = (commit or {}).get('tree', {}).get('sha')
    if not isinstance(tree, str) or not SHA.fullmatch(tree):
        raise ValueError('Approved base commit has no exact Git tree')
    return tree, await _read_tree(client, tree)


async def _read_tree(client, tree):
    result = await client.request('GET', '/git/trees/' + tree + '?recursive=1')
    if (
        not result
        or result.get('sha') != tree
        or result.get('truncated') is not False
        or not isinstance(result.get('tree'), list)
        or len(result['tree']) > 20000
    ):
        raise ValueError('A complete bounded base tree is required for delivery')
    leaves = {}
    for item in result['tree']:
        if item.get('type') == 'tree':
            continue
        if (
            item.get('type') not in {'blob', 'commit'}
            or item.get('mode') not in {'100644', '100755', '120000', '160000'}
            or not SHA.fullmatch(item.get('sha', ''))
            or item['path'] in leaves
        ):
            raise ValueError('Invalid or duplicate remote Git tree entry')
        leaves[item['path']] = {k: item[k] for k in ('sha', 'mode', 'type')}
    if _tree_hash(leaves) != tree:
        raise ValueError('Remote complete tree fingerprint did not verify')
    return leaves


async def _step(client, invocation, key, suffix, body, recover, validate, db_path):
    request = canonical({'path': suffix, 'body': body})
    with _db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute(
            'SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key=?', (invocation, key)
        ).fetchone()
        if old:
            if old['request_json'] != request:
                raise ValueError('Original delivery mutation is immutable')
            if old['state'] == 'completed':
                return json.loads(old['result_json'])
            raise RemoteUnknown('Original delivery mutation needs read-only reconciliation')
        db.execute(
            'INSERT INTO governance_delivery_step VALUES(?,?,?, ?,NULL)', (invocation, key, request, 'uncertain')
        )
        db.commit()
    result = await client.request('POST', suffix, body)
    validate(result)
    with _db(db_path) as db:
        db.execute(
            "UPDATE governance_delivery_step SET state='completed',result_json=? WHERE invocation_id=? AND step_key=? AND state='uncertain'",
            (canonical(result), invocation, key),
        )
        db.commit()
    return result


def _sha_result(result, expected):
    if not isinstance(result, dict) or result.get('sha') != expected:
        raise RemoteUnknown('Original remote Git object hash did not verify')


async def _commit_result(client, result, body):
    sha = (result or {}).get('sha')
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise RemoteUnknown('Original commit reference is unavailable')
    current = await client.request('GET', '/git/commits/' + sha)
    if (
        not current
        or current.get('tree', {}).get('sha') != body['tree']
        or [x.get('sha') for x in current.get('parents', [])] != body['parents']
        or current.get('message') != body['message']
    ):
        raise RemoteUnknown('Original commit facts do not match the admitted delivery')
    return current


def _branch(row):
    return 'agov/' + re.sub('[^a-z0-9-]', '-', row['erp_task'].lower())[:50] + '-' + digest(row['invocation_id'])[:24]


def _pr_valid(pr, approved):
    owner, repo = REPO.fullmatch(approved['repository']).groups()
    repo = repo.removesuffix('.git')
    if (
        not isinstance(pr, dict)
        or type(pr.get('number')) is not int
        or pr['number'] < 1
        or pr.get('html_url') != approved['repository'].removesuffix('.git') + '/pull/' + str(pr['number'])
        or pr.get('head', {}).get('sha') != approved['head_commit']
        or pr.get('head', {}).get('ref') != approved['branch']
        or pr.get('base', {}).get('ref') != approved['base_branch']
        or pr.get('base', {}).get('sha') != approved['base_commit']
        or pr.get('head', {}).get('repo', {}).get('full_name') != owner + '/' + repo
        or pr.get('base', {}).get('repo', {}).get('full_name') != owner + '/' + repo
        or pr.get('draft') is not True
        or pr.get('state') != 'open'
    ):
        raise RemoteUnknown('Draft PR does not match this exact repository, branch and implementation head')
    return pr


async def _pipeline(client, original, row, packet, config, db_path):
    snapshot = snapshot_workspace(row, packet, config)
    if snapshot['sha256'] != original['snapshot_sha256']:
        raise ValueError('Implementation files changed after delivery was frozen')
    profile = _profile(row, packet, config)
    base_tree, leaves = await _base(client, profile)
    new = dict(leaves)
    changed = []
    allowed = profile['allowed_paths']
    for path, item in list(leaves.items()):
        if _excluded(PurePosixPath(path).parts):
            continue
        if any(path == x or path.startswith(x.rstrip('/') + '/') for x in allowed) and path not in snapshot['contents']:
            if item['type'] != 'blob' or item['mode'] == '120000':
                raise ValueError('Unsupported source deletion')
            changed.append({'path': path, 'mode': item['mode'], 'type': 'blob', 'sha': None})
            new.pop(path)
    for item in snapshot['manifest']:
        path = item['path']
        prior = leaves.get(path)
        value = {'mode': item['mode'], 'type': 'blob', 'sha': item['git_blob_sha1']}
        if prior == value:
            continue
        changed.append({'path': path, **value})
        new[path] = value
    if not 1 <= len(changed) <= MAX_CHANGED:
        raise ValueError('Delivery needs a bounded non-empty actual source diff')
    changed.sort(key=lambda x: x['path'])
    for item in changed:
        if item['sha'] is None:
            continue
        data = snapshot['contents'][item['path']]
        sha = item['sha']
        await _step(
            client,
            row['invocation_id'],
            'blob:' + sha,
            '/git/blobs',
            {'content': base64.b64encode(data).decode(), 'encoding': 'base64'},
            None,
            lambda r, s=sha: _sha_result(r, s),
            db_path,
        )
    tree_sha = _tree_hash(new)
    await _step(
        client,
        row['invocation_id'],
        'tree',
        '/git/trees',
        {'base_tree': base_tree, 'tree': changed},
        None,
        lambda r: _sha_result(r, tree_sha),
        db_path,
    )
    author = {**profile['git_author'], 'date': original['commit_date']}
    commit_body = {
        'message': 'ABIT governed implementation '
        + row['erp_task']
        + '\n\nInvocation: '
        + row['invocation_id']
        + '\nSnapshot: '
        + snapshot['sha256']
        + '\n',
        'tree': tree_sha,
        'parents': [profile['base_commit']],
        'author': author,
        'committer': author,
    }
    predicted = _commit_hash(commit_body)
    commit = await _step(
        client,
        row['invocation_id'],
        'commit',
        '/git/commits',
        commit_body,
        None,
        lambda r: _sha_result(r, predicted),
        db_path,
    )
    await _commit_result(client, commit, commit_body)
    branch = original['branch']
    head = commit['sha']

    def reference(result):
        if (
            not isinstance(result, dict)
            or result.get('ref') != 'refs/heads/' + branch
            or result.get('object', {}).get('sha') != head
        ):
            raise RemoteUnknown('Original feature branch does not match its implementation commit')

    await _step(
        client,
        row['invocation_id'],
        'reference',
        '/git/refs',
        {'ref': 'refs/heads/' + branch, 'sha': head},
        None,
        reference,
        db_path,
    )
    approved = {
        'repository': packet['repository'],
        'head_commit': head,
        'branch': branch,
        'base_branch': profile['base_branch'],
        'base_commit': profile['base_commit'],
    }
    body = {
        'title': '[AGOV] ' + row['erp_task'] + ' implementation',
        'head': branch,
        'base': profile['base_branch'],
        'draft': True,
        'body': 'Server-owned delivery for '
        + row['erp_task']
        + '.\n\nInvocation: '
        + row['invocation_id']
        + '\nSnapshot SHA256: '
        + snapshot['sha256']
        + '\nPacket SHA256: '
        + packet_digest(packet)
        + '\n',
        'maintainer_can_modify': False,
    }
    pr = await _step(
        client, row['invocation_id'], 'pull_request', '/pulls', body, None, lambda r: _pr_valid(r, approved), db_path
    )
    current = await client.request('GET', '/pulls/' + str(pr['number']))
    _pr_valid(current, approved)
    from open_webui.utils.agentic_gateway_operations import artifact_write
    from open_webui.utils.agentic_contracts.developer_delivery import delivery_manifest

    tested = original['tested']
    commit_id = 'COMMIT-' + digest(row['invocation_id'])[:32]
    delivery = {
        'repository': packet['repository'],
        'base_commit': profile['base_commit'],
        'head_commit': head,
        'pull_request_url': current['html_url'],
        'changed_paths': [x['path'] for x in changed],
        'commit_evidence_id': commit_id,
        'test_runs': tested['test_runs'],
    }
    manifest = await artifact_write(
        {
            'name': 'delivery-' + digest(row['invocation_id'])[:32] + '.json',
            'content_base64': base64.b64encode(canonical(delivery_manifest(delivery)).encode()).decode(),
        },
        row=row,
        config=config,
    )
    evidence = tested['evidence'] + [
        {
            'id': commit_id,
            'kind': 'commit',
            'uri': manifest['uri'],
            'sha256': manifest['sha256'],
            'requirement_ids': packet['requirement_ids'],
        }
    ]
    result = {
        'state': 'ready_for_review',
        'delivery': delivery,
        'evidence': evidence,
        'test_results': tested['test_results'],
        'repository_snapshot_sha256': snapshot['sha256'],
        'invocation_id': row['invocation_id'],
    }
    with _db(db_path) as db:
        db.execute(
            "UPDATE governance_developer_delivery SET state='completed',result_json=? WHERE invocation_id=?",
            (canonical(result), row['invocation_id']),
        )
        db.commit()
    return result


async def developer_deliver(args, *, row, packet, config, bridge=None, db_path=None, **kwargs):
    if row['target_role'] != 'developer' or not isinstance(args, dict) or set(args) != {'test_call_ids'}:
        raise ValueError('Only the bound Developer can publish an exact tested implementation')
    profile = _profile(row, packet, config)
    snapshot = snapshot_workspace(row, packet, config)
    tested = _tests(args['test_call_ids'], row, packet, profile, snapshot, config, db_path)
    frozen = {
        'packet_sha256': packet_digest(packet),
        'profile_sha256': digest(profile),
        'snapshot_sha256': snapshot['sha256'],
        'tested': tested,
        'branch': _branch(row),
    }
    with _db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute(
            'SELECT * FROM governance_developer_delivery WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        if old:
            original = json.loads(old['input_json'])
            if any(original.get(k) != v for k, v in frozen.items()):
                raise ValueError('Original delivery inputs, tests and target are immutable')
            if old['state'] == 'completed':
                return json.loads(old['result_json'])
            pending = old['state'] == 'pending_reconciliation'
        else:
            original = {**frozen, 'commit_date': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            db.execute(
                'INSERT INTO governance_developer_delivery VALUES(?,?,?,?,?,NULL,?)',
                (
                    row['invocation_id'],
                    row['erp_project'],
                    row['erp_task'],
                    canonical(original),
                    'prepared',
                    int(time.time()),
                ),
            )
            pending = False
        db.commit()
    if pending:
        return await reconcile_delivery({}, row=row, packet=packet, config=config, db_path=db_path)
    client = github_client(packet['repository'])
    try:
        return await _pipeline(client, original, row, packet, config, db_path)
    except RemoteUnknown:
        with _db(db_path) as db:
            db.execute(
                "UPDATE governance_developer_delivery SET state='pending_reconciliation' WHERE invocation_id=? AND state!='completed'",
                (row['invocation_id'],),
            )
            db.commit()
        return {
            'state': 'pending_reconciliation',
            'invocation_id': row['invocation_id'],
            'next_operation': 'delivery.reconcile',
            'reason': 'Original GitHub mutation is unconfirmed; only exact read-only reconciliation is allowed',
        }
    finally:
        await client.close()


async def reconcile_delivery(args, *, row, packet, config, db_path=None, **kwargs):
    if args != {} or row['target_role'] != 'developer':
        raise ValueError('Only exact original delivery reconciliation is supported')
    with _db(db_path) as db:
        original = db.execute(
            'SELECT * FROM governance_developer_delivery WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        steps = db.execute(
            "SELECT * FROM governance_delivery_step WHERE invocation_id=? AND state='uncertain'",
            (row['invocation_id'],),
        ).fetchall()
    if not original:
        raise ValueError('No original delivery exists')
    if original['state'] == 'completed':
        return json.loads(original['result_json'])
    frozen = json.loads(original['input_json'])
    if frozen['packet_sha256'] != packet_digest(packet) or frozen['profile_sha256'] != digest(
        _profile(row, packet, config)
    ):
        raise ValueError('Original delivery scope changed')
    client = github_client(packet['repository'])
    try:
        for step in steps:
            request = json.loads(step['request_json'])
            body = request['body']
            key = step['step_key']
            if key.startswith('blob:'):
                sha = key.split(':', 1)[1]
                result = await client.request('GET', '/git/blobs/' + sha)
                if result is None:
                    return {'state': 'pending_reconciliation', 'reason': 'Exact original blob is not yet visible'}
                _sha_result(result, sha)
                if base64.b64decode(result.get('content', '').replace('\n', ''), validate=True) != base64.b64decode(
                    body['content']
                ):
                    raise ValueError('Original blob bytes differ')
            elif key == 'tree':
                # Recompute the exact expected tree from the approved base and frozen source.
                snapshot = snapshot_workspace(row, packet, config)
                if snapshot['sha256'] != frozen['snapshot_sha256']:
                    raise ValueError('Frozen delivery source changed')
                _, leaves = await _base(client, _profile(row, packet, config))
                for item in body['tree']:
                    if item['sha'] is None:
                        leaves.pop(item['path'], None)
                    else:
                        leaves[item['path']] = {k: item[k] for k in ('sha', 'mode', 'type')}
                sha = _tree_hash(leaves)
                result = await client.request('GET', '/git/trees/' + sha)
                if result is None:
                    return {'state': 'pending_reconciliation', 'reason': 'Exact original tree is not yet visible'}
                _sha_result(result, sha)
            elif key == 'commit':
                sha = _commit_hash(body)
                result = await client.request('GET', '/git/commits/' + sha)
                if result is None:
                    return {'state': 'pending_reconciliation', 'reason': 'Exact original commit is not yet visible'}
                _sha_result(result, sha)
                await _commit_result(client, result, body)
            elif key == 'reference':
                result = await client.request(
                    'GET', '/git/ref/heads/' + quote(body['ref'][len('refs/heads/') :], safe='')
                )
                if result is None:
                    return {'state': 'pending_reconciliation', 'reason': 'Exact original branch is not yet visible'}
                if result.get('ref') != body['ref'] or result.get('object', {}).get('sha') != body['sha']:
                    raise ValueError('Original branch has conflicting content')
            elif key == 'pull_request':
                owner = REPO.fullmatch(packet['repository']).group(1)
                found = await client.request(
                    'GET',
                    '/pulls?state=all&head='
                    + quote(owner + ':' + body['head'], safe='')
                    + '&base='
                    + quote(body['base'], safe='')
                    + '&per_page=2',
                )
                if not found:
                    return {'state': 'pending_reconciliation', 'reason': 'Exact original PR is not yet visible'}
                if not isinstance(found, list) or len(found) != 1:
                    raise ValueError('Original PR reconciliation is ambiguous')
                with _db(db_path) as db:
                    commit = json.loads(
                        db.execute(
                            "SELECT result_json FROM governance_delivery_step WHERE invocation_id=? AND step_key='commit'",
                            (row['invocation_id'],),
                        ).fetchone()[0]
                    )
                result = _pr_valid(
                    found[0],
                    {
                        'repository': packet['repository'],
                        'head_commit': commit['sha'],
                        'branch': body['head'],
                        'base_branch': body['base'],
                        'base_commit': _profile(row, packet, config)['base_commit'],
                    },
                )
                if result.get('body') != body['body'] or result.get('title') != body['title']:
                    raise ValueError('Original PR marker changed')
            else:
                raise ValueError('Unsupported original mutation journal')
            with _db(db_path) as db:
                db.execute(
                    "UPDATE governance_delivery_step SET state='completed',result_json=? WHERE invocation_id=? AND step_key=? AND state='uncertain'",
                    (canonical(result), row['invocation_id'], key),
                )
                db.commit()
        with _db(db_path) as db:
            db.execute(
                "UPDATE governance_developer_delivery SET state='prepared' WHERE invocation_id=? AND state='pending_reconciliation'",
                (row['invocation_id'],),
            )
            db.commit()
        return {
            'state': 'reconciled',
            'invocation_id': row['invocation_id'],
            'next_operation': 'delivery.publish',
            'reason': 'Only original read receipts recorded; no new remote mutation executed',
        }
    finally:
        await client.close()


def _latest_test_calls(row, profile, db_path):
    latest = {}
    with _db(db_path) as db:
        rows = db.execute(
            '''SELECT c.call_id,c.result_json FROM governance_gateway_call c
          JOIN governance_gateway_session s ON s.session_id=c.session_id WHERE s.invocation_id=?
          AND c.operation='repository.test' AND c.state='completed' ORDER BY c.updated_at DESC,c.rowid DESC''',
            (row['invocation_id'],),
        ).fetchall()
    for found in rows:
        value = json.loads(found['result_json'])
        test = value.get('test_id')
        if test in (profile.get('test_acceptance_ids') or {}) and test not in latest:
            latest[test] = found['call_id']
    return sorted(latest.values())


def _source_matches_head(snapshot, leaves, profile):
    expected = {
        name: entry
        for name, entry in leaves.items()
        if not _excluded(PurePosixPath(name).parts)
        and any(name == x or name.startswith(x.rstrip('/') + '/') for x in profile['allowed_paths'])
    }
    actual = {entry['path']: entry for entry in snapshot['manifest']}
    if set(expected) != set(actual):
        raise ValueError('QA working source differs from the actual implementation PR head')
    for name, item in actual.items():
        if expected[name] != {'sha': item['git_blob_sha1'], 'mode': item['mode'], 'type': 'blob'}:
            raise ValueError('QA tests are not inspecting the exact implementation PR head bytes')


async def qa_verify_delivery(args, *, row, packet, config, db_path=None, **kwargs):
    if args != {} or row['target_role'] != 'qa':
        raise ValueError('Only independent QA can inspect the implementation PR head')
    value = packet.get('implementation_delivery')
    from open_webui.utils.agentic_contracts.developer_delivery import validate_delivery, delivery_digest

    if validate_delivery(value, packet['repository']):
        raise ValueError('QA requires the exact original Developer delivery')
    number = value['pull_request_url'].rsplit('/', 1)[1]
    client = github_client(packet['repository'])
    try:
        pr = await client.request('GET', '/pulls/' + number)
        owner, repo = REPO.fullmatch(packet['repository']).groups()
        repo = repo.removesuffix('.git')
        if (
            not pr
            or pr.get('head', {}).get('sha') != value['head_commit']
            or pr.get('head', {}).get('repo', {}).get('full_name') != owner + '/' + repo
            or pr.get('base', {}).get('repo', {}).get('full_name') != owner + '/' + repo
            or pr.get('base', {}).get('sha') != value['base_commit']
            or pr.get('html_url') != value['pull_request_url']
            or pr.get('state') != 'open'
        ):
            raise ValueError('Independent GitHub readback found a different PR head or repository')
        commit = await client.request('GET', '/git/commits/' + value['head_commit'])
        if (
            not commit
            or commit.get('sha') != value['head_commit']
            or not SHA.fullmatch(commit.get('tree', {}).get('sha', ''))
        ):
            raise ValueError('QA could not read the exact published implementation commit')
        from open_webui.utils.agentic_gateway_operations import _workspace

        _, profile = _workspace(row, packet, config)
        snapshot = snapshot_workspace(row, packet, config)
        _source_matches_head(snapshot, await _read_tree(client, commit['tree']['sha']), profile)
        facts = {
            'verified': True,
            'repository': packet['repository'],
            'pull_request_url': pr['html_url'],
            'head_commit': pr['head']['sha'],
            'base_commit': value['base_commit'],
            'reviewed_delivery_sha256': delivery_digest(value),
            'repository_snapshot_sha256': snapshot['sha256'],
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'read_only': True,
        }
        with _db(db_path) as db:
            old = db.execute(
                'SELECT * FROM governance_qa_delivery WHERE invocation_id=?', (row['invocation_id'],)
            ).fetchone()
            identity = (packet_digest(packet), delivery_digest(value), snapshot['sha256'])
            if old and tuple(old[k] for k in ('packet_sha256', 'delivery_sha256', 'snapshot_sha256')) != identity:
                raise ValueError('Original QA verification inputs are immutable')
            if not old:
                db.execute(
                    'INSERT INTO governance_qa_delivery VALUES(?,?,?,?,?,?)',
                    (row['invocation_id'], *identity, canonical(facts), int(time.time())),
                )
                db.commit()
        calls = _latest_test_calls(row, profile, db_path)
        if calls:
            try:
                tested = _tests(calls, row, packet, profile, snapshot, config, db_path)
                facts.update(test_results=tested['test_results'], evidence=tested['evidence'], tests_complete=True)
            except ValueError:
                facts.update(test_results=[], evidence=[], tests_complete=False)
        else:
            facts.update(test_results=[], evidence=[], tests_complete=False)
        return facts
    finally:
        await client.close()


def _assert_observations(result, tested):
    evidence = {item['id']: item for item in result.get('evidence', [])}
    for original in tested['evidence']:
        if evidence.get(original['id']) != original:
            raise ValueError('Result is missing the exact server-verified command/evidence receipt')
    observed = {item['test_id']: item for item in result.get('test_results', [])}
    for original in tested['test_results']:
        current = observed.get(original['test_id']) or {}
        if current.get('status') != 'pass' or not set(original['evidence_ids']) <= set(current.get('evidence_ids', [])):
            raise ValueError('Result acceptance matrix is not backed by actual gateway test receipts')


async def assert_gateway_result(row, packet, result, config, db_path=None, *, bridge=None):
    """Actual Dot acceptance hook, also used for saved-original reply recovery."""
    from open_webui.utils.agentic_gateway_sessions import CURRENT_DB

    context = CURRENT_DB.set(db_path)
    try:
        return await _assert_gateway_result(row, packet, result, config, db_path, bridge=bridge)
    finally:
        CURRENT_DB.reset(context)


async def _assert_gateway_result(row, packet, result, config, db_path=None, *, bridge=None):
    binding = packet['binding']
    if any(
        row.get(k) != v
        for k, v in {
            'erp_project': packet['project_id'],
            'erp_task': packet['task_id'],
            'target_role': binding['role'],
            'agent_id': binding['agent_id'],
        }.items()
    ):
        raise ValueError('Gateway acceptance belongs to another Task, project or agent')
    if row['target_role'] == 'developer' and result.get('outcome') == 'ready_for_review':
        with _db(db_path) as db:
            saved = db.execute(
                "SELECT input_json,result_json FROM governance_developer_delivery WHERE invocation_id=? AND state='completed'",
                (row['invocation_id'],),
            ).fetchone()
        if not saved:
            raise ValueError('Developer readiness requires its completed original gateway delivery')
        frozen, actual = json.loads(saved['input_json']), json.loads(saved['result_json'])
        if frozen['packet_sha256'] != packet_digest(packet) or actual['delivery'] != result.get('delivery'):
            raise ValueError('Developer result differs from its actual published PR, commit or test receipts')
        snapshot = snapshot_workspace(row, packet, config)
        if snapshot['sha256'] != frozen['snapshot_sha256']:
            raise ValueError('Developer source changed after its real delivery')
        _assert_observations(result, actual)
    if row['target_role'] == 'qa' and result.get('outcome') == 'pass':
        from open_webui.utils.agentic_it_qa import assert_it_qa_result

        if await assert_it_qa_result(row, packet, result, config, db_path, bridge=bridge):
            return
        from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest

        value = packet.get('implementation_delivery')
        if not isinstance(value, dict) or result.get('reviewed_delivery_sha256') != delivery_digest(value):
            raise ValueError('QA did not review the exact original Developer delivery')
        with _db(db_path) as db:
            saved = db.execute(
                'SELECT * FROM governance_qa_delivery WHERE invocation_id=?', (row['invocation_id'],)
            ).fetchone()
        if (
            not saved
            or saved['packet_sha256'] != packet_digest(packet)
            or saved['delivery_sha256'] != delivery_digest(value)
        ):
            raise ValueError('QA pass requires its own actual fixed GitHub/head verification receipt')
        # A fresh GET observes current head; it never changes a PR or reruns a model.
        verified = await qa_verify_delivery({}, row=row, packet=packet, config=config, db_path=db_path)
        if not verified['tests_complete'] or verified['repository_snapshot_sha256'] != saved['snapshot_sha256']:
            raise ValueError('QA pass requires independent current test receipts on the verified PR-head source')
        _assert_observations(result, verified)
