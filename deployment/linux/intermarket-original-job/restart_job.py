"""Root broker worker for the fixed InterMarket DEV service. No configurable target."""
import json
import os
from pathlib import Path
import re
import socket
import sys
import time

UNIT = 'intermarket-sbodesk.service'
MANAGER = 'org.freedesktop.systemd1.Manager'
PATH = '/org/freedesktop/systemd1'


def restart():
    if os.geteuid() != 0 or socket.gethostname() != 'abit-dev-linux' or len(sys.argv) != 1:
        raise RuntimeError('fixed host worker required')
    from gi.repository import Gio, GLib

    flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    bus = Gio.DBusConnection.new_for_address_sync('unix:path=/run/dbus/system_bus_socket', flags, None, None)
    def call(destination, path, interface, method, args=None):
        return bus.call_sync(destination, path, interface, method, args, None,
                             Gio.DBusCallFlags.NO_AUTO_START, 5000, None)
    try:
        owner, = call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                      'GetNameOwner', GLib.Variant('(s)', ('org.freedesktop.systemd1',))).unpack()
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        job = None
        terminal = []
        def removed(connection, sender, path, interface, signal, params):
            if sender == owner and params.get_type_string() == '(uoss)':
                number, object_path, unit, result = params.unpack()
                if object_path == job and object_path == f'{PATH}/job/{number}' and unit == UNIT:
                    terminal.append(result)
        bus.signal_subscribe(owner, MANAGER, 'JobRemoved', PATH, None,
                             Gio.DBusSignalFlags.NONE, removed)
        call(owner, PATH, MANAGER, 'Subscribe')
        # The private connection both subscribes and submits. GLib queues signals
        # until we dispatch its context, including a terminal before the reply.
        job, = call(owner, PATH, MANAGER, 'RestartUnit', GLib.Variant('(ss)', (UNIT, 'fail'))).unpack()
        match = re.fullmatch(PATH + r'/job/([1-9][0-9]{0,9})', job)
        if not match:
            raise RuntimeError('unknown original manager job')
        ident = dict(jobId=int(match[1]), manager=owner, bootId=boot, unit=UNIT)
        print(json.dumps(ident), flush=True)
        ctx = GLib.MainContext.default()
        deadline = time.monotonic() + 85
        while not terminal:
            if time.monotonic() >= deadline or bus.is_closed():
                raise RuntimeError('original manager terminal unavailable')
            if not ctx.iteration(False):
                time.sleep(0.015)
        print(json.dumps(dict(ident, result=terminal[0])), flush=True)
        if terminal != ['done']:
            return 1
        unit_path, = call(owner, PATH, MANAGER, 'GetUnit', GLib.Variant('(s)', (UNIT,))).unpack()
        raw, = call(owner, unit_path, 'org.freedesktop.DBus.Properties', 'Get',
                    GLib.Variant('(ss)', ('org.freedesktop.systemd1.Unit', 'InvocationID'))).unpack()
        generation = bytes(raw)
        if len(generation) != 16 or not any(generation):
            raise RuntimeError('post-job generation unavailable')
        print(json.dumps({'invocationId': generation.hex()}), flush=True)
        return 0
    finally:
        bus.close_sync(None)


if __name__ == '__main__':
    sys.exit(restart())
