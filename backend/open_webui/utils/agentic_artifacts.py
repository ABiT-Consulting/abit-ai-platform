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
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Evidence exceeds the artifact limit')
    if file.suffix == '.png':
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(data), formats=['PNG']) as image:
            if image.width * image.height > 12_000_000 or image.is_animated:
                raise ValueError('Invalid screenshot dimensions')
            image.verify()
    if file.suffix == '.svg':
        import xml.etree.ElementTree as ET

        if re.search(rb'url\s*\(|@import|<!DOCTYPE|<!ENTITY', data, re.I):
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
                key.lower().startswith('on') or key.split('}')[-1] in {'href', 'src'} for key in node.attrib
            ):
                raise ValueError('Active or external SVG content is forbidden')
        if re.search(rb'url\s*\(|@import|<!DOCTYPE|<!ENTITY', data, re.I):
            raise ValueError('External SVG content is forbidden')
    return file.name, data


def verified_file_url(doc, task, expected_sha, content):
    if (
        doc.get('attached_to_doctype') != 'Task'
        or doc.get('attached_to_name') != task
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


async def publish_artifacts(erp, row, config, evidence, *, before_write=None):
    if not isinstance(evidence, list) or len(evidence) > 30:
        raise ValueError('Too many evidence artifacts')
    root = artifact_directory(row, config)
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
                    ['attached_to_doctype', '=', 'Task'],
                    ['attached_to_name', '=', row['erp_task']],
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
                data={'doctype': 'Task', 'docname': row['erp_task'], 'is_private': '1'},
                files={'file': (filename, data, 'image/png' if filename.endswith('.png') else 'text/plain')},
            )
            ident = (response.get('message') or {}).get('name')
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}', ident):
                raise ValueError('Evidence upload acknowledgment unknown; reconcile its original digest')
            doc = await erp.get('File', ident)
        url = verified_file_url(doc, row['erp_task'], item['sha256'], data)
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
