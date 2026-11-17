"""Per-job cgroup budget within the service's systemd delegation, no sudo API.

The service must start in DelegateSubgroup=controller. Only its own delegated
descendants are writable. No caller-selected cgroup, PID or limit is accepted.
"""

from contextlib import asynccontextmanager
import asyncio
import os
from pathlib import Path
import secrets

from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable, _open_dir


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
async def budget():
    root = _delegation()
    group = root / ('job-' + secrets.token_hex(16))
    group.mkdir(mode=0o700)
    try:
        (group / 'memory.max').write_text('536870912')
        (group / 'memory.swap.max').write_text('0')
        (group / 'pids.max').write_text('64')
        if (group / 'memory.max').read_text().strip() != '536870912' or (
            group / 'pids.max'
        ).read_text().strip() != '64':
            raise CapabilityUnavailable('Repository test resource budget did not verify')
        # A fresh fixed launcher joins before exec, avoiding Python preexec_fn
        # in the multithreaded application. This path never comes from a model.
        yield str(group / 'cgroup.procs')
    finally:
        if group.exists():
            # Kill descendants even if a test detached or the HTTP caller left.
            (group / 'cgroup.kill').write_text('1')
            for _ in range(100):
                if not (group / 'cgroup.procs').read_text().strip():
                    break
                await asyncio.sleep(0.01)
            group.rmdir()
