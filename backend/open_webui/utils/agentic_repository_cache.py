"""Read only root-owned Git blobs; remote tree identities remain authoritative."""
import hashlib
import os
from pathlib import Path
import re
import stat

ROOT = Path('/var/lib/abit-ai-agentic-gateway/repository-objects')
MAX_SOURCE_BYTES = 2_000_000

def object_bytes(repository, object_id):
    if (not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
        or any(p in {'.', '..'} for p in repository.split('/'))
        or not isinstance(object_id, str) or not re.fullmatch(r'[a-f0-9]{40}', object_id)):
        raise ValueError('Invalid fixed repository object identity')
    paths = [ROOT, ROOT / hashlib.sha256(repository.encode()).hexdigest()]
    directory = None
    try:
        for path in paths:
            try:
                following = os.open(path if directory is None else path.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                return None
            info = os.fstat(following)
            if info.st_uid != 0 or info.st_mode & 0o022:
                os.close(following)
                raise ValueError('Repository object cache must be operator-owned and read-only')
            if directory is not None:
                os.close(directory)
            directory = following
        try:
            fd = os.open(object_id, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_size > MAX_SOURCE_BYTES):
                raise ValueError('Invalid operator repository object')
            data = os.read(fd, MAX_SOURCE_BYTES + 1)
            if len(data) != info.st_size or len(data) > MAX_SOURCE_BYTES:
                raise ValueError('Repository object exceeds its complete byte bound')
        finally:
            os.close(fd)
    finally:
        if directory is not None:
            os.close(directory)
    actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data, usedforsecurity=False).hexdigest()
    if actual != object_id:
        raise ValueError('Cached Git blob does not match the remote tree identity')
    return data
