"""Resolve model references to real, run-scoped bytes and private ERP Files."""

from __future__ import annotations
from hashlib import sha256, md5
from pathlib import Path
import os
import re
import stat
from urllib.parse import urlsplit, parse_qs, quote

MAX_BYTES = 2_000_000
EXTENSIONS = {'.png', '.txt', '.md', '.json', '.svg'}


def artifact_directory(row, config):
    # Only a server-owned allowlist selects roots. Task/model strings cannot
    # turn a filesystem path or URL into an authorized artifact location.
    configured = (config.get('agentic_artifact_roots') or {}).get(row['erp_project'])
    if configured is None:
        configured = '/srv/abit-dev-linux/abit-ai/data/governance-artifacts/' + row['erp_project']
    if not isinstance(configured, str) or not configured.startswith('/srv/abit-dev-linux/'):
        raise ValueError('Project artifact root is not configured on ABIT-DEV-LINUX')
    if '..' in Path(configured).parts:
        raise ValueError('Artifact root cannot traverse directories')
    return Path(configured) / row['erp_task'] / row['invocation_id']


def ensure_directory(path):
    """Create only server-derived artifact directories, with no symlink traversal."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode=0o2770, dir_fd=fd)
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.fchmod(next_fd, 0o2770)
            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)


def local_bytes(root, uri):
    parsed = urlsplit(uri)
    if (
        parsed.scheme != 'artifact'
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}', parsed.netloc)
    ):
        raise ValueError('Artifact must reference one safe run-scoped filename')
    file = Path(parsed.netloc)
    if file.suffix.lower() not in EXTENSIONS:
        raise ValueError('Unsupported evidence file type')
    fds = []
    try:
        # Open every component relative to its verified directory; reject links
        # and replacement of any ancestor while resolving model artifact names.
        fds.append(os.open('/', os.O_RDONLY | os.O_DIRECTORY))
        for part in root.parts[1:]:
            fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
        fd = os.open(file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                raise ValueError('Evidence must be a protected regular file')
            data = handle.read(MAX_BYTES + 1)
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return validate_bytes(file.name, data)


def validate_bytes(name, data):
    """Validate before creating a gateway file as well as before publication."""
    file = Path(name)
    if file.suffix.lower() not in EXTENSIONS:
        raise ValueError('Unsupported evidence file type')
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Evidence exceeds the artifact limit')
    if file.suffix.lower() == '.png':
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(data), formats=['PNG']) as image:
            if image.width * image.height > 12_000_000 or image.is_animated:
                raise ValueError('Invalid screenshot dimensions')
            image.verify()
    if file.suffix.lower() == '.svg':
        import xml.etree.ElementTree as ET

        if re.search(rb'url\s*\(|@import|<!DOCTYPE|<!ENTITY|<\?(?!xml\s)', data, re.I):
            raise ValueError('External SVG content is forbidden')
        tree = ET.fromstring(data)
        allowed = {
            'svg',
            'g',
            'path',
            'rect',
            'circle',
            'ellipse',
            'line',
            'polyline',
            'polygon',
            'text',
            'tspan',
            'title',
            'desc',
            'defs',
            'marker',
            'linearGradient',
            'radialGradient',
            'stop',
            'clipPath',
        }
        for node in tree.iter():
            if node.tag.split('}')[-1] not in allowed or any(
                key.split('}')[-1].lower().startswith('on') or key.split('}')[-1].lower() in {'href', 'src'}
                # Inspect decoded XML values as well as source bytes. Numeric
                # entities and CSS escapes must not hide an external reference.
                or '\\' in value or re.search(r'url\s*\(|@import|(?:https?|data|javascript|file|ftp):', value, re.I)
                for key, value in node.attrib.items()
            ):
                raise ValueError('Active or external SVG content is forbidden')
        if re.search(rb'url\s*\(|@import|<!DOCTYPE|<!ENTITY', data, re.I):
            raise ValueError('External SVG content is forbidden')
    return file.name, data


def verified_file_url(doc, task, expected_sha, content, *, attachment_target=None):
    target = attachment_target or {'doctype': 'Task', 'name': task}
    if (
        doc.get('attached_to_doctype') != target['doctype']
        or doc.get('attached_to_name') != target['name']
        or doc.get('is_private') != 1
        or doc.get('content_hash') != md5(content, usedforsecurity=False).hexdigest()
        or doc.get('file_size') != len(content)
        or sha256(content).hexdigest() != expected_sha
    ):
        raise ValueError('ERP did not confirm the exact private evidence attachment')
    path = doc.get('file_url', '')
    ident = doc.get('name', '')
    if not re.fullmatch(r'/private/files/[A-Za-z0-9_.-]+', path) or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}', ident):
        raise ValueError('ERP returned an invalid private evidence reference')
    return 'https://erp.abit.ai' + path + '?fid=' + quote(ident, safe='')


def verify_developer_manifest(row, config, result):
    """Check exact run-owned commit manifest bytes before any ERP publication."""
    if result['actor']['role'] != 'developer' or result['outcome'] != 'ready_for_review':
        return
    from open_webui.utils.agentic_contracts.developer_delivery import verify_manifest_bytes

    delivery = result['delivery']
    evidence = next((e for e in result['evidence'] if e['id'] == delivery['commit_evidence_id']), None)
    if not evidence or evidence['kind'] != 'commit':
        raise ValueError('Developer commit manifest evidence is missing')
    _, content = local_bytes(artifact_directory(row, config), evidence['uri'])
    if sha256(content).hexdigest() != evidence['sha256']:
        raise ValueError('Developer commit manifest hash differs from actual bytes')
    verify_manifest_bytes(delivery, content)


async def publish_artifacts(erp, row, config, evidence, *, before_write=None, attachment_target=None, bridge=None):
    if not isinstance(evidence, list) or len(evidence) > 30:
        raise ValueError('Too many evidence artifacts')
    root = artifact_directory(row, config)
    target = attachment_target or {'doctype': 'Task', 'name': row['erp_task']}
    if attachment_target:
        from open_webui.utils.agentic_pm_review import artifact_target as verified_target

        target = verified_target(row, {'artifact_target': attachment_target})
        if bridge is None or bridge.erp is not erp:
            raise ValueError('PM evidence requires its authenticated original ERP bridge')
    results = []
    author = (await erp.request('GET', '/api/method/frappe.auth.get_logged_user'))['message']
    for item in evidence:
        if before_write:
            await before_write()
        name, data = local_bytes(root, item['uri'])
        if sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Evidence hash differs from actual bytes')
        stem = (
            'agov-' + sha256((row['invocation_id'] + item['id']).encode()).hexdigest()[:20] + '-' + item['sha256'][:16]
        )
        filename = stem + Path(name).suffix
        if attachment_target:
            doc = await bridge.upload_review_evidence(row, item['id'], filename, data, item['sha256'])
            url = verified_file_url(doc, row['erp_task'], item['sha256'], data, attachment_target=target)
            if doc.get('owner') != author:
                raise ValueError('Evidence attachment identity changed')
            results.append({key: item[key] for key in ('id', 'kind', 'sha256', 'requirement_ids')} | {'url': url})
            continue
        files = [
            f
            async for f in erp.rows(
                'File',
                [
                    'name',
                    'owner',
                    'file_url',
                    'file_size',
                    'content_hash',
                    'is_private',
                    'attached_to_doctype',
                    'attached_to_name',
                ],
                [
                    ['attached_to_doctype', '=', target['doctype']],
                    ['attached_to_name', '=', target['name']],
                    ['file_name', '=', filename],
                    ['owner', '=', author],
                ],
            )
        ]
        if len(files) > 1:
            raise ValueError('Multiple private evidence files require reconciliation')
        if files:
            doc = files[0]
        else:
            response = await erp.request(
                'POST',
                '/api/method/upload_file',
                data={'doctype': target['doctype'], 'docname': target['name'], 'is_private': '1'},
                files={'file': (filename, data, 'image/png' if filename.endswith('.png') else 'text/plain')},
            )
            ident = (response.get('message') or {}).get('name')
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}', ident):
                raise ValueError('Evidence upload acknowledgment unknown; reconcile its original digest')
            doc = await erp.get('File', ident)
        url = verified_file_url(doc, row['erp_task'], item['sha256'], data, attachment_target=attachment_target)
        if doc.get('owner') != author:
            raise ValueError('Evidence attachment identity changed')
        results.append(
            {
                'id': item['id'],
                'kind': item['kind'],
                'url': url,
                'sha256': item['sha256'],
                'requirement_ids': item['requirement_ids'],
            }
        )
    return results
