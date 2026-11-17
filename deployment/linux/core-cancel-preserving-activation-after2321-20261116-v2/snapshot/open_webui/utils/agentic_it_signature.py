"""Verify exact Debian/Ubuntu archive metadata and cached package bytes offline.

Trust chain: pinned archive key -> signed InRelease -> indexed Packages SHA256
-> exact package/version/architecture/size/SHA256. No download, key installation,
trust override or model-provided success boolean is accepted.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

from open_webui.utils.agentic_it_changes import OfficialSignatureVerifier, _read_protected


def require(value, message):
    if not value:
        raise ValueError(message)


def protected_bytes(path, roots, uid, maximum):
    identity = _read_protected(path, roots, uid, maximum)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == tuple(identity[key] for key in ('device', 'inode', 'size', 'mtime_ns')), 'Protected source identity changed')
        value = bytearray()
        while chunk := os.read(fd, 65536):
            value.extend(chunk)
            require(len(value) <= maximum, 'Protected source exceeds its bound')
        require(hashlib.sha256(value).hexdigest() == identity['sha256'], 'Protected source bytes changed')
        return bytes(value), identity
    finally:
        os.close(fd)


def deb822(text):
    """Bounded Debian control fields, retaining continuation lines for checksums."""
    fields = {}
    current = None
    for line in text.splitlines():
        if line.startswith((' ', '\t')):
            require(current is not None, 'Orphan Debian field continuation')
            fields[current] += '\n' + line[1:]
            continue
        require(':' in line, 'Malformed Debian metadata field')
        key, value = line.split(':', 1)
        require(key not in fields and re.fullmatch(r'[A-Za-z0-9-]+', key), 'Duplicate or malformed Debian metadata field')
        fields[key] = value.strip()
        current = key
    return fields


def signed_payload(data):
    text = data.decode('utf-8')
    require(text.startswith('-----BEGIN PGP SIGNED MESSAGE-----\n')
            or text.startswith('-----BEGIN PGP SIGNED MESSAGE-----\r\n'), 'Clearsigned InRelease required')
    lines = text.splitlines()
    start = lines.index('') + 1
    end = lines.index('-----BEGIN PGP SIGNATURE-----')
    require(start < end and end < len(lines) - 1, 'Signed release framing differs')
    return '\n'.join(line[2:] if line.startswith('- ') else line for line in lines[start:end]).rstrip('\n')


def unpack_index(data, name, maximum=64_000_000):
    if name.endswith('.xz'):
        decoder = lzma.LZMADecompressor()
        content = decoder.decompress(data, max_length=maximum + 1)
        require(decoder.eof and not decoder.unused_data, 'Incomplete or concatenated package index')
    elif name.endswith('.gz'):
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            content = stream.read(maximum + 1)
    else:
        content = data
    require(len(content) <= maximum, 'Uncompressed package index exceeds its bound')
    return content.decode('utf-8')


class DebianArchiveVerifier(OfficialSignatureVerifier):
    def __init__(self):
        super().__init__(self._verify)

    async def _verify(self, record, source, artifact, profile):
        roots = profile.get('package_cache_roots', [])
        uid = profile['expected_uid']
        catalog, catalog_identity = protected_bytes(source['catalog_path'], roots, uid, 4_000_000)
        require(catalog_identity['sha256'] == source['catalog_sha256'], 'Pinned signed catalog hash differs')
        index_path = source['index_relative_path']
        relative = PurePosixPath(index_path)
        require(not relative.is_absolute() and str(relative) == index_path and '..' not in relative.parts,
                'Exact repository-relative package index required')
        index, index_identity = protected_bytes(source['index_path'], roots, uid, 32_000_000)
        keyring = source['keyring_path']
        keyring_roots = profile.get('keyring_roots', [])
        _read_protected(keyring, keyring_roots, profile.get('keyring_uid', 0), 8_000_000)
        process = await asyncio.create_subprocess_exec('/usr/bin/gpgv', '--status-fd=1', '--keyring', keyring,
            '--', source['catalog_path'], stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        try:
            status, _ = await asyncio.wait_for(process.communicate(), 10)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        require(process.returncode == 0 and len(status) <= 16384, 'Archive signature verification failed')
        lines = [line.split() for line in status.decode('ascii').splitlines() if line.startswith('[GNUPG:] ')]
        invalid = {'BADSIG', 'ERRSIG', 'REVKEYSIG', 'EXPKEYSIG', 'EXPSIG', 'KEYEXPIRED', 'SIGEXPIRED', 'NODATA', 'FAILURE'}
        require(not any(line[1] in invalid for line in lines), 'Archive signature is invalid, revoked or expired')
        fingerprint = source['signer_fingerprint'].upper()
        valid = [line for line in lines if line[1] == 'VALIDSIG']
        require(len(valid) == 1 and fingerprint in {valid[0][2], valid[0][-1]}, 'Archive signer fingerprint differs')
        require(len(valid[0]) >= 11 and valid[0][9] in {'8', '9', '10'}, 'Archive signature needs SHA256 or stronger')
        # The path verified by gpgv must still hold exactly the pinned bytes.
        require(protected_bytes(source['catalog_path'], roots, uid, 4_000_000)[1] == catalog_identity,
                'Signed catalog changed during signature verification')
        release = deb822(signed_payload(catalog))
        require(release.get('Origin') == source['release_origin']
                and release.get('Codename') == source['codename'], 'Official archive identity differs')
        now = int(time.time())
        issued = parsedate_to_datetime(release['Date'])
        require(issued.tzinfo is not None and issued.timestamp() <= now + 300, 'Archive release date is invalid')
        expiry = source.get('catalog_expires_at')
        require(type(expiry) is int and now < expiry <= now + 7 * 86400, 'Explicit bounded catalog freshness pin required')
        if 'Valid-Until' in release:
            signed_expiry = parsedate_to_datetime(release['Valid-Until'])
            require(signed_expiry.tzinfo is not None and now < signed_expiry.timestamp(), 'Signed archive metadata expired')
            expiry = min(expiry, int(signed_expiry.timestamp()))
        hashes = []
        for line in release.get('SHA256', '').splitlines():
            if not line.strip():
                continue
            parts = line.split()
            require(len(parts) == 3 and re.fullmatch('[a-f0-9]{64}', parts[0]) and parts[1].isdigit(), 'Malformed signed index checksum')
            if parts[2] == index_path:
                hashes.append(parts)
        require(len(hashes) == 1 and hashes[0][0] == index_identity['sha256']
                and int(hashes[0][1]) == len(index), 'Packages index is not authenticated by the signed release')
        matches = []
        content = unpack_index(index, index_path)
        for paragraph in re.split(r'\n\s*\n', content.strip()):
            fields = deb822(paragraph)
            if fields.get('Package') == record['package'] and fields.get('Version') == record['version'] \
                    and fields.get('Architecture') == record['architecture']:
                matches.append(fields)
        require(len(matches) == 1, 'Exact package/version/architecture must appear once in the authenticated index')
        package = matches[0]
        require(package.get('SHA256') == record['artifact_sha256'] == artifact['sha256']
                and package.get('Size', '').isdigit() and int(package['Size']) == artifact['size']
                and package.get('Filename') == record['archive_filename'], 'Official package bytes or index filename differ')
        return {'verified': True, 'package': record['package'], 'version': record['version'],
                'architecture': record['architecture'], 'artifact_sha256': artifact['sha256'],
                'official_source_id': record['official_source_id'], 'signer_fingerprint': source['signer_fingerprint'],
                'catalog_sha256': catalog_identity['sha256'], 'valid_until': expiry}
