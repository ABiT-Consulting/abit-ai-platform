"""Per-job cgroup budget within the service's systemd delegation, no sudo API.

The service must start in DelegateSubgroup=controller. Only its own delegated
descendants are writable. No caller-selected cgroup, PID or limit is accepted.
"""

from contextlib import asynccontextmanager
import asyncio
import errno
import time
import os
from pathlib import Path
import secrets

from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable, _open_dir

_HIGH_MEMORY = asyncio.Semaphore(1)


def _delegation():
    entries = Path('/proc/self/cgroup').read_text().splitlines()
    if len(entries) != 1 or not entries[0].startswith('0::/'):
        raise CapabilityUnavailable('Repository tests require a delegated cgroup-v2 service')
    relative = entries[0][3:]
    current = Path('/sys/fs/cgroup') / relative.lstrip('/')
    if current.name != 'controller' or not current.parent.name.startswith(('abit-ai', 'abit-agov-')):
        raise CapabilityUnavailable('Repository tests require the approved service controller subgroup')
    root = current.parent
    fd = _open_dir(root)
    os.close(fd)
    if root.stat().st_uid != os.geteuid():
        raise CapabilityUnavailable('Repository test cgroup is not delegated to this service identity')
    available = (root / 'cgroup.controllers').read_text().split()
    if not {'memory', 'pids'} <= set(available):
        raise CapabilityUnavailable('Required memory/process controllers are not delegated')
    (root / 'cgroup.subtree_control').write_text('+memory +pids')
    return root


@asynccontextmanager
async def budget(*, memory_bytes=536870912, processes=64):
    if type(memory_bytes) is not int or memory_bytes not in {536870912, 1073741824, 1610612736, 2147483648, 3221225472, 4294967296} or type(processes) is not int or not 64 <= processes <= 128:
        raise CapabilityUnavailable('Repository resource budget is outside operator bounds')
    high = memory_bytes > 536870912
    if high:
        await _HIGH_MEMORY.acquire()
        available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
        if available < memory_bytes + 1073741824:
            _HIGH_MEMORY.release()
            raise CapabilityUnavailable('Insufficient measured memory for the registered test budget')
    root = None
    group = None
    try:
        root = _delegation()
        group = root / ('job-' + secrets.token_hex(16))
        group.mkdir(mode=0o700)
    except BaseException:
        if high:
            _HIGH_MEMORY.release()
        raise
    # The complete execution and cleanup remain inside the resource lease.
    try:
        (group / 'memory.max').write_text(str(memory_bytes))
        (group / 'memory.swap.max').write_text('0')
        (group / 'pids.max').write_text(str(processes))
        if (group / 'memory.max').read_text().strip() != str(memory_bytes) or (
            group / 'pids.max'
        ).read_text().strip() != str(processes):
            raise CapabilityUnavailable('Repository test resource budget did not verify')
        # A fresh fixed launcher joins before exec, avoiding Python preexec_fn
        # in the multithreaded application. This path never comes from a model.
        yield str(group / 'cgroup.procs')
    finally:
        try:
            if group.exists():
                # Kill descendants even if a test detached or the HTTP caller left.
                (group / 'cgroup.kill').write_text('1')
                deadline = time.monotonic() + 5
                while True:
                    if not (group / 'cgroup.procs').read_text().strip():
                        try:
                            group.rmdir()
                            break
                        except OSError as error:
                            if error.errno not in {errno.EBUSY, errno.ENOTEMPTY}:
                                raise
                    if time.monotonic() >= deadline:
                        raise CapabilityUnavailable('Owned test cgroup cleanup did not finish')
                    await asyncio.sleep(0.02)
        finally:
            if high:
                _HIGH_MEMORY.release()
