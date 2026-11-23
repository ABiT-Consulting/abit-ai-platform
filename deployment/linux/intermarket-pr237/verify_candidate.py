"""Read-only offline inventory of the exact frozen PR237 payload."""
import hashlib
import json
import os
from pathlib import Path
import stat

MANIFEST = '3af33454471443a9248a9a725ef951acf5f6ff5f967e4f1ecd2a1ee6c47a53d8'
FROZEN = Path('/srv/abit-dev-linux/tmp/intermarket-frozen-08')


def verify(root):
    raw = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST:
        raise ValueError('Frozen manifest digest differs')
    manifest = json.loads(raw)
    counts = {}
    for component in ('backend', 'frontend'):
        expected = manifest['artifacts'][component]
        seen = set()
        base = root / component
        if base.is_symlink() or not base.is_dir():
            raise ValueError('Invalid component directory')
        for directory, dirs, files, directory_fd in os.fwalk(base, follow_symlinks=False):
            if any(not stat.S_ISDIR(os.stat(d, dir_fd=directory_fd, follow_symlinks=False).st_mode) for d in dirs):
                raise ValueError('Unexpected directory link')
            for name in files:
                relative = str((Path(directory) / name).relative_to(base))
                item = expected[relative]
                fd = os.open(name, os.O_NOFOLLOW | os.O_RDONLY | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size != item['bytes']:
                        raise ValueError('File type or size differs')
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                        raise ValueError('File digest differs')
                seen.add(relative)
        if seen != set(expected):
            raise ValueError('Incomplete component')
        counts[component] = len(seen)
    return counts


if __name__ == '__main__':
    print(json.dumps(verify(FROZEN), sort_keys=True))
