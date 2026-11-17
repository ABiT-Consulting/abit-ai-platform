"""Operator-registered, hard-capped budgets for offline repository tests.
Model arguments never select resource limits or runtime mounts.
"""
DEFAULT = {
    'memory_mb': 512, 'address_space_mb': 1024, 'cpu_seconds': 25,
    'wall_seconds': 30, 'file_mb': 16, 'open_files': 128, 'processes': 64,
}
MAXIMUM = {
    'memory_mb': 4096, 'address_space_mb': 16384, 'cpu_seconds': 300,
    'wall_seconds': 420, 'file_mb': 256, 'open_files': 512, 'processes': 128,
}

def limits(profile):
    if not isinstance(profile, dict):
        raise ValueError('A server repository profile is required')
    value = profile.get('test_resources')
    if value is None:
        return dict(DEFAULT)
    if (not isinstance(value, dict) or set(value) != set(DEFAULT)
        or any(type(value[k]) is not int or not (k == 'file_mb' and value[k] == 0) and not DEFAULT[k] <= value[k] <= MAXIMUM[k] for k in DEFAULT)
        or value['memory_mb'] not in {512, 1024, 1536, 2048, 3072, 4096}
        or value['wall_seconds'] < value['cpu_seconds']):
        raise ValueError('Repository test resources exceed the fixed operator policy')
    return dict(value)


def sandbox_argv(snapshot, command, resource):
    from pathlib import Path
    value = limits({'test_resources': resource})
    if (not Path(snapshot).is_absolute() or not isinstance(command, list) or not 1 <= len(command) <= 30
        or any(not isinstance(v, str) or len(v) > 500 or '\x00' in v for v in command)):
        raise ValueError('Invalid bounded server sandbox arguments')
    argv = ['/usr/bin/prlimit', '--cpu=' + str(value['cpu_seconds']),
            '--as=' + str(value['address_space_mb'] * 1024 * 1024),
            '--nofile=' + str(value['open_files']), '--core=0',
            '--fsize=' + ('unlimited' if value['file_mb'] == 0 else str(value['file_mb'] * 1024 * 1024)),
            '--', '/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
            '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'HOME', '/tmp', '--tmpfs', '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/dev/shm']
    for path in ('/usr', '/bin', '/lib', '/lib64'):
        if Path(path).exists():
            argv += ['--ro-bind', path, path]
    # All writable test files are now tmpfs charged to the job memory cgroup.
    # Sparse JIT memfd reservations can be large without permitting host-disk growth.
    copy = "import os,shutil,sys;os.makedirs('/etc',exist_ok=True);open('/etc/passwd','w').write('gateway:x:'+str(os.getuid())+':'+str(os.getgid())+':Gateway:/tmp:/bin/false'+chr(10));open('/etc/group','w').write('gateway:x:'+str(os.getgid())+':'+chr(10));open('/etc/hosts','w').write('127.0.0.1 localhost'+chr(10)+'::1 localhost'+chr(10));open('/etc/nsswitch.conf','w').write('passwd: files'+chr(10)+'group: files'+chr(10)+'hosts: files'+chr(10));shutil.copytree('/source','/workspace',dirs_exist_ok=True);os.execvpe(sys.argv[1],sys.argv[1:],os.environ)"
    return argv + ['--ro-bind', str(snapshot), '/source', '--tmpfs', '/workspace',
                   '--chdir', '/workspace', '--', '/usr/bin/python3', '-I', '-S', '-c', copy, *command]
