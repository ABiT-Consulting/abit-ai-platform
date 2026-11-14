"""Concrete capacity connector for a project-exclusive, offline ext4 image.

Never targets a block device, live mount, shared loop backing file or model path.
The operator pins file identity, filesystem UUID, geometry and maximum growth.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import stat
import time

from open_webui.utils.agentic_it_changes import LinuxReadOnlyReaders, TrustedCapacityConnector
from open_webui.utils.agentic_it_physical import PhysicalHandler
from open_webui.utils.agentic_tool_gateway import _open_dir


def require(value, message):
    if not value:
        raise ValueError(message)


class Ext4ImageConnector(TrustedCapacityConnector):
    def __init__(self, command_reader=None):
        self.command = command_reader or LinuxReadOnlyReaders()._command
        super().__init__('offline-ext4-image-v1', self.prepare_geometry)

    def identity(self, record, profile):
        value = record.get('image_path')
        require(isinstance(value, str) and value.startswith('/') and '..' not in Path(value).parts,
                'Pinned canonical image file required')
        path = Path(value)
        roots = profile.get('image_roots')
        require(isinstance(roots, list) and any(path.is_relative_to(Path(root)) for root in roots
            if isinstance(root, str) and root.startswith('/') and '..' not in Path(root).parts), 'Image outside registered roots')
        parent = _open_dir(path.parent)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(fd)
            finally:
                os.close(fd)
        finally:
            os.close(parent)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o022
                and info.st_uid == profile.get('expected_uid')
                and info.st_dev == record.get('image_device') and info.st_ino == record.get('image_inode'),
                'Image file no longer has its exclusive registered identity')
        maximum = record.get('maximum_image_bytes')
        require(type(maximum) is int and 4_194_304 <= info.st_size <= maximum <= 2_000_000_000,
                'Image size exceeds its reviewed capacity bound')
        # An image attached to any loop device is not an offline image, even if
        # the current process cannot see another namespace's mount table.
        for loop in Path('/sys/block').glob('loop*/loop/backing_file'):
            try:
                backing = loop.read_text().strip()
            except FileNotFoundError:
                continue
            require(not backing or Path('/' + backing.lstrip('/')).resolve() != path,
                    'A live loop device owns the image; offline expansion denied')
        return path, info

    async def read(self, record, profile):
        path, info = self.identity(record, profile)
        code, text = await self.command(('/usr/sbin/dumpe2fs', '-h', str(path)))
        require(code == 0, 'Exact ext4 geometry readback unavailable')
        fields = {}
        for line in text.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                require(key.strip() not in fields, 'Ambiguous filesystem geometry')
                fields[key.strip()] = value.strip()
        uuid = fields.get('Filesystem UUID')
        require(uuid == record.get('filesystem_uuid') and re.fullmatch('[a-f0-9-]{36}', uuid or '')
                and fields.get('Filesystem state') == 'clean' and 'extent' in fields.get('Filesystem features', '').split(),
                'Image filesystem identity/features/clean state differs')
        require(fields.get('Block count', '').isdigit() and fields.get('Block size', '').isdigit()
                and fields.get('Free blocks', '').isdigit(), 'Bounded exact ext4 block geometry required')
        count, block, free = int(fields['Block count']), int(fields['Block size']), int(fields['Free blocks'])
        require(block in {1024, 2048, 4096} and 0 <= free <= count and count * block <= info.st_size,
                'Image backing-file geometry is inconsistent')
        return {'host_id': socket.gethostname(), 'checked_at': int(time.time()), 'volume_id': record['volume_id'],
                'filesystem_id': 'ext4:' + uuid + ':' + str(info.st_dev) + ':' + str(info.st_ino),
                'filesystem': 'ext4', 'total_bytes': count * block, 'free_bytes': free * block,
                'image_bytes': info.st_size, 'block_size': block}

    async def prepare_geometry(self, record, profile, before):
        actual = await self.read(record, profile)
        require(all(actual.get(key) == before.get(key) for key in ('volume_id', 'filesystem_id', 'filesystem', 'total_bytes')),
                'Offline image geometry changed during preparation')
        target = record.get('target_bytes')
        require(type(target) is int and actual['total_bytes'] < target <= record['maximum_image_bytes']
                and target % 4096 == 0 and actual['image_bytes'] <= target, 'Only bounded offline image growth is supported')
        return {'volume_id': record['volume_id'], 'filesystem_id': actual['filesystem_id'], 'filesystem': 'ext4',
                'baseline_bytes': actual['total_bytes'], 'target_bytes': target,
                'fixed_argv': ['/usr/sbin/resize2fs', record['image_path'], str(target // 1024) + 'K']}

    def handler(self, resource_id, record, profile):
        async def prepare(plan):
            actual = await self.read(record, profile)
            geometry = await self.prepare_geometry(record, profile, actual)
            require(plan['target']['volume_id'] == record['volume_id'] and plan['target']['target_bytes'] == record['target_bytes']
                    and plan['baseline']['filesystem_id'] == actual['filesystem_id']
                    and plan['baseline']['total_bytes'] == actual['total_bytes']
                    and plan['fixed_argv'] == geometry['fixed_argv'], 'Approved image baseline/target changed')
            return [('/usr/bin/truncate', '--size', str(record['target_bytes']), '--', record['image_path']),
                    tuple(geometry['fixed_argv'])]

        async def verified(plan):
            actual = await self.read(record, profile)
            if not (actual['filesystem_id'] == plan['baseline']['filesystem_id']
                    and actual['total_bytes'] == actual['image_bytes'] == plan['target']['target_bytes']):
                return False
            code, _ = await self.command(('/usr/sbin/e2fsck', '-f', '-n', record['image_path']))
            return {'verified': code == 0, 'observed': {**actual, 'fsck_exit_code': code}}

        return PhysicalHandler('extend_capacity', resource_id, record['project_id'], record['repository'],
            record['environment'], profile['host_id'], profile['expected_uid'], prepare, verified,
            physical_identity='image:' + str(record['image_device']) + ':' + str(record['image_inode']))
