"""Fixed real package/service readers and handlers for reviewed host resources."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import time

from open_webui.utils.agentic_it_changes import LinuxReadOnlyReaders, _read_protected, digest
from open_webui.utils.agentic_it_physical import PhysicalHandler
from open_webui.utils.agentic_it_signature import protected_bytes


def require(value, message):
    if not value:
        raise ValueError(message)


class OwnedContainerPackages:
    """Official package mutation in one immutable-identity private canary container.

    No host namespace, socket, credential or writable host mount is allowed. The
    fixed read-only package cache is the sole allowed bind mount.
    """

    FORMAT = ('{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},'
        '"network":{{json .HostConfig.NetworkMode}},"privileged":{{json .HostConfig.Privileged}},'
        '"pid_mode":{{json .HostConfig.PidMode}},"ipc_mode":{{json .HostConfig.IpcMode}},'
        '"memory":{{json .HostConfig.Memory}},"pids_limit":{{json .HostConfig.PidsLimit}},"nano_cpus":{{json .HostConfig.NanoCpus}},'
        '"cap_drop":{{json .HostConfig.CapDrop}},"cap_add":{{json .HostConfig.CapAdd}},'
        '"security":{{json .HostConfig.SecurityOpt}},"mounts":{{json .Mounts}},"labels":{{json .Config.Labels}}}')

    def __init__(self, command_reader=None):
        self.command = command_reader or LinuxReadOnlyReaders()._command

    async def identity(self, profile):
        context = profile.get('package_context')
        require(isinstance(context, dict) and context.get('driver') == 'owned_container', 'Owned-container package context required')
        container = context.get('container_id')
        image = context.get('image_id')
        require(re.fullmatch('[a-f0-9]{64}', str(container)) and re.fullmatch('sha256:[a-f0-9]{64}', str(image)),
                'Exact immutable container/image identities required')
        code, text = await self.command(('/usr/bin/docker', 'inspect', '--format', self.FORMAT, '--', container))
        require(code == 0, 'Owned package container readback unavailable')
        current = json.loads(text)
        require(current.get('id') == container and current.get('image') == image and current.get('running') is True
                and current.get('network') == 'none' and current.get('privileged') is False
                and current.get('pid_mode') == '' and current.get('ipc_mode') == 'private', 'Package container isolation/identity differs')
        security = set(current.get('security') or [])
        require(set(current.get('cap_drop') or []) == {'ALL'}
                and {name.removeprefix('CAP_') for name in current.get('cap_add') or []} <= {'CHOWN', 'FOWNER', 'DAC_OVERRIDE'}
                and security and security <= {'no-new-privileges', 'no-new-privileges:true'},
                'Package container capability policy differs')
        require(type(current.get('memory')) is int and 134_217_728 <= current['memory'] <= 536_870_912
                and type(current.get('pids_limit')) is int and 16 <= current['pids_limit'] <= 128
                and type(current.get('nano_cpus')) is int and 100_000_000<=current['nano_cpus']<=2_000_000_000,
                'Package container lacks bounded memory/process budgets')
        expected = {key: context[key] for key in ('project_id', 'repository', 'environment')}
        scope = digest(expected)
        labels = current.get('labels') or {}
        require(labels.get('com.abit.agentic.scope') == scope
                and labels.get('com.abit.agentic.purpose') == 'physical-it-canary', 'Container is not exclusive to the registered project')
        roots = profile.get('package_cache_roots', [])
        cache = context.get('artifact_root')
        require(isinstance(cache, str) and cache.startswith('/') and '..' not in Path(cache).parts
                and Path(cache).resolve() == Path(cache) and cache in roots, 'Fixed protected package mount required')
        mounts = current.get('mounts')
        require(isinstance(mounts, list) and len(mounts) == 1 and mounts[0].get('Type') == 'bind'
                and mounts[0].get('Source') == cache and mounts[0].get('Destination') == cache
                and mounts[0].get('RW') is False, 'Package container has an unapproved host mount')
        return container

    async def package(self, record, profile):
        container = await self.identity(profile)
        name = record['package']
        require(re.fullmatch('[a-z0-9][a-z0-9+.-]{0,99}', name), 'Invalid package pin')
        code, text = await self.command(('/usr/bin/docker', 'exec', '--user', '0', '--', container,
            '/usr/bin/dpkg-query', '--show', '--showformat=${Package}\t${Version}\t${Architecture}\t${db:Status-Status}', '--', name))
        if code == 1:
            return {'host_id': socket.gethostname(), 'checked_at': int(time.time()), 'package': name,
                    'installed': False, 'version': None, 'architecture': None}
        require(code == 0 and len(text.splitlines()) == 1, 'Package container metadata query failed')
        fields = text.split('\t')
        require(len(fields) == 4 and fields[0] == name, 'Package container metadata identity differs')
        return {'host_id': socket.gethostname(), 'checked_at': int(time.time()), 'package': name,
                'installed': fields[3] == 'installed', 'version': fields[1], 'architecture': fields[2]}

    def handler(self, resource_id, record, profile):
        async def prepare(plan):
            container = await self.identity(profile)
            scope = profile['package_context']
            require(all(scope.get(key) == record.get(key) for key in ('project_id', 'repository', 'environment')),
                    'Package container project scope differs')
            artifact = _read_protected(record['artifact_path'], profile['package_cache_roots'], profile['expected_uid'])
            require(all(artifact.get(key) == plan['artifact'].get(key) for key in artifact), 'Original cached package changed')
            current = await self.package(record, profile)
            require(all(current.get(key) == plan['baseline'].get(key) for key in ('package', 'installed', 'version', 'architecture')),
                    'Original package baseline changed immediately before dispatch')
            expected = ['/usr/bin/dpkg', '--install', '--', record['artifact_path']]
            require(plan['fixed_argv'] == expected and all(plan['target'][key] == record[key]
                for key in ('package', 'version', 'architecture', 'artifact_sha256')), 'Approved package target differs')
            for field, expected_value in [('Package', record['package']), ('Version', record['version']), ('Architecture', record['architecture'])]:
                code, text = await self.command(('/usr/bin/dpkg-deb', '--field', record['artifact_path'], field))
                require(code == 0 and text.strip() == expected_value, 'Actual Debian package control metadata differs')
            return [('/usr/bin/docker', 'exec', '--user', '0', '--', container, *expected)]

        async def verified(plan):
            actual = await self.package(record, profile)
            if not (actual['installed'] and all(actual[key] == plan['target'][key] for key in ('package', 'version', 'architecture'))):
                return False
            container = await self.identity(profile)
            code, text = await self.command(('/usr/bin/docker', 'exec', '--user', '0', '--', container,
                                              '/usr/bin/dpkg', '--verify', '--', record['package']))
            return {'verified': code == 0 and not text.strip(), 'observed': actual}

        return PhysicalHandler('install_official_package', resource_id, record['project_id'], record['repository'],
            record['environment'], profile['host_id'], profile['expected_uid'], prepare, verified,
            physical_identity='container-dpkg:' + profile['package_context']['container_id'])


class PinnedService:
    """Restart only an exact reviewed service, with actual PID/invocation health."""

    def __init__(self, command_reader=None):
        self.command = command_reader or LinuxReadOnlyReaders()._command
        self.reader = LinuxReadOnlyReaders(health=self.healthy, command_reader=self.command)

    async def healthy(self, record, profile):
        program = _read_protected(record['health_program'], profile.get('service_program_roots', []),
                                  profile['expected_uid'], 1_000_000)
        require(program['sha256'] == record.get('health_program_sha256'), 'Pinned service health program changed')
        path = record.get('health_state_path')
        require(isinstance(path, str), 'Pinned private service health state required')
        raw, _ = protected_bytes(path, profile.get('service_health_roots', []), record['service_uid'], 16384)
        health = json.loads(raw)
        pid = health.get('pid')
        require(type(pid) is int and pid > 1 and isinstance(health.get('invocation_id'), str), 'Actual service process health identity required')
        process = Path('/proc') / str(pid)
        try:
            uid = process.stat().st_uid
            argv = (process / 'cmdline').read_bytes().split(b'\0')
            ticks = (process / 'stat').read_text().rsplit(') ', 1)[1].split()[19]
            executable = (process / 'exe').resolve()
        except FileNotFoundError:
            return False
        return uid == record['service_uid'] and executable == Path(record['health_executable']).resolve() \
            and record['health_program'].encode() in argv \
            and ticks == str(health.get('process_start_ticks')) and health.get('ready') is True

    async def read(self, record, profile):
        value = await self.reader._service(record, profile)
        raw, _ = protected_bytes(record['health_state_path'], profile.get('service_health_roots', []), record['service_uid'], 16384)
        health = json.loads(raw)
        value['healthy'] = value['healthy'] and value['main_pid'] == health.get('pid') \
            and value['invocation_id'] == health.get('invocation_id')
        return value

    def handler(self, resource_id, record, profile):
        async def prepare(plan):
            actual = await self.read(record, profile)
            require(all(actual.get(key) == plan['baseline'].get(key) for key in
                ('unit', 'unit_sha256', 'active_state', 'sub_state', 'main_pid', 'invocation_id', 'healthy')),
                'Actual service changed immediately before restart')
            require(actual['healthy'] and plan['fixed_argv'] == ['/usr/bin/systemctl', 'restart', '--', record['unit']],
                    'Exact healthy service/restart command required')
            return [tuple(plan['fixed_argv'])]

        async def verified(plan):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    actual = await self.read(record, profile)
                    if actual['healthy'] and actual['active_state'] == 'active' \
                            and actual['unit_sha256'] == plan['target']['unit_sha256'] \
                            and actual['invocation_id'] != plan['baseline']['invocation_id']:
                        return {'verified': True, 'observed': actual}
                except (ValueError, FileNotFoundError):
                    pass
                import asyncio
                await asyncio.sleep(0.1)
            return False

        return PhysicalHandler('restart_service', resource_id, record['project_id'], record['repository'],
            record['environment'], profile['host_id'], profile['expected_uid'], prepare, verified,
            physical_identity='systemd:' + record['unit'])
