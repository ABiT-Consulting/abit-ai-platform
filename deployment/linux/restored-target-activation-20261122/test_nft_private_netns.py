"""Executable only in a freshly unshared network namespace, never the caller's."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('relay_bootstrap', HERE / 'relay_bootstrap.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

def main():
    assert os.getresuid() == (0, 0, 0)
    original = int(sys.argv[1])
    current = os.stat('/proc/self/ns/net').st_ino
    if current == original:
        raise RuntimeError('Refuse firewall action in the caller network namespace')
    subprocess.run(['/usr/bin/ip', 'link', 'set', 'lo', 'up'], check=True, timeout=5)
    nonce = 'a' * 64
    listener = socket.socket()
    listener.bind(('127.0.0.1', b.PORT))
    listener.listen()
    listener.settimeout(0.2)
    stop = threading.Event()
    peers, threads = [], []
    def echo(peer):
        try:
            while not stop.is_set():
                data = peer.recv(128)
                if not data:
                    return
                peer.sendall(data)
        except OSError:
            pass
        finally:
            peer.close()
    def serve():
        while not stop.is_set():
            try:
                peer, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peers.append(peer)
            thread = threading.Thread(target=echo, args=(peer,), daemon=True)
            threads.append(thread)
            thread.start()
    acceptor = threading.Thread(target=serve, daemon=True)
    acceptor.start()
    child = None
    path = None
    observations = []
    def command(document):
        result = subprocess.run([b.NFT, '-j', '-f', '-'], input=json.dumps(document).encode(),
                                capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError('Private nft fixture command failed: ' + result.stderr.decode()[:1200])
    try:
        command({'nftables': [{'add': {'table': {'family': 'ip', 'name': 'unrelated_fixture'}}},
                             {'add': {'chain': {'family': 'ip', 'table': 'unrelated_fixture',
                                               'name': 'untouched'}}}]})
        baseline, _ = b.split_ruleset(b.read_ruleset())
        baseline_hash = b.digest(baseline)
        # This disposable child is deliberately deprivileged only after entering
        # the private namespace. It has no credentials, outside sockets or files.
        client = """import os,socket,sys
os.setgroups([]);os.setgid(65534);os.setuid(65534)
s=socket.create_connection(('127.0.0.1',8810),timeout=2)
s.sendall(b'before');assert s.recv(6)==b'before';print('connected',flush=True)
assert sys.stdin.readline().strip()=='continue'
s.sendall(b'preserved');assert s.recv(9)==b'preserved';print('established_preserved',flush=True)
try:
 n=socket.create_connection(('127.0.0.1',8810),timeout=2)
except ConnectionRefusedError:
 print('new_nonroot_denied',flush=True)
else:
 n.close();raise AssertionError('nonroot SYN was accepted')
s.close()
"""
        child = subprocess.Popen(['/usr/bin/python3', '-I', '-S', '-B', '-c', client],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        require_line = child.stdout.readline().strip()
        assert require_line == 'connected', require_line
        command(b.create_document(nonce))
        other, owned = b.split_ruleset(b.read_ruleset())
        assert b.digest(other) == baseline_hash
        try:
            proof = b.verify_owned(owned, nonce)
        except RuntimeError:
            print(json.dumps({'private_fixture_owned_readback': owned, 'expected': b.expected_readback(nonce)}), flush=True)
            raise
        observations.append('preexisting_rules_and_exact_owned_readback_preserved')
        child.stdin.write('continue\n')
        child.stdin.flush()
        output, error = child.communicate(timeout=5)
        assert child.returncode == 0, error
        assert output.splitlines() == ['established_preserved', 'new_nonroot_denied'], output
        observations += output.splitlines()
        with socket.create_connection(('127.0.0.1', b.PORT), timeout=2) as root_probe:
            root_probe.sendall(b'fixed-root-probe')
            assert root_probe.recv(16) == b'fixed-root-probe'
        observations.append('bounded_root_probe_allowed')
        deadline = time.monotonic() + 2
        while b.usable_ingress(b.tcp_rows(Path('/proc/net/tcp').read_text())):
            assert time.monotonic() < deadline, 'fixture connections did not close naturally'
            time.sleep(0.01)
        command({'nftables': [{'delete': {'table': {'family': 'ip', 'handle': proof['table_handle']}}}]})
        other, owned = b.split_ruleset(b.read_ruleset())
        assert not owned and b.digest(other) == baseline_hash
        observations.append('only_exact_owned_table_removed')
        print(json.dumps({'result': 'PASS', 'assertion_groups': observations,
                          'caller_netns_inode': original, 'test_netns_inode': current,
                          'different_namespace': current != original, 'host_firewall_modified': False,
                          'module_sha256': hashlib.sha256((HERE/'relay_bootstrap.py').read_bytes()).hexdigest()}))
    finally:
        stop.set()
        listener.close()
        for peer in peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            peer.close()
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        acceptor.join(timeout=1)
        for thread in threads:
            thread.join(timeout=1)
        # Namespace lifetime is this bounded process; no caller rule is reachable.
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
