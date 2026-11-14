#!/usr/bin/python3
"""Root-installed fixed broker/worker entrypoint; verified code, no shell API."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
sys.dont_write_bytecode=True


def require(value,message):
    if not value:raise ValueError(message)


def checked_file(path,maximum=1_000_000):
    require(path.is_absolute() and path.resolve()==path,'Canonical installed broker code required')
    cursor=path.parent
    while cursor!=cursor.parent:
        info=cursor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'Installed broker ancestor is writable')
        cursor=cursor.parent
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o022
                and info.st_size<=maximum,'Installed broker file owner/mode differs')
        content=os.read(fd,maximum+1)
        require(len(content)==info.st_size,'Installed broker source changed while reading')
        return content
    finally:os.close(fd)


async def main():
    require(os.geteuid()==0,'Fixed physical broker requires its reviewed root service')
    require(len(sys.argv) in {3,4} and sys.argv[1] in {'broker','worker'},'Fixed broker or original-worker mode required')
    root=Path(__file__).resolve().parent
    manifest=json.loads(checked_file(root/'code-manifest.json',64000))
    require(isinstance(manifest,dict) and len(manifest)<=30,'Bounded installed source manifest required')
    for relative,sha in manifest.items():
        require(isinstance(relative,str) and not relative.startswith('/') and '..' not in Path(relative).parts,
                'Installed source manifest escape denied')
        require(hashlib.sha256(checked_file(root/relative)).hexdigest()==sha,'Installed broker code differs from reviewed source')
    require('entry.py' in manifest,'Installed entrypoint must be pinned')
    # The installed lib contains empty package markers and only the selected
    # stdlib-only modules. It imports no application/environment/live database.
    sys.path.insert(0,str(root/'lib'))
    from open_webui.utils.agentic_it_broker_resources import installed_resources
    profile,resources,launcher=installed_resources(Path(sys.argv[2]))
    if sys.argv[1]=='worker':
        require(len(sys.argv)==4,'Exact original request file required')
        await launcher.run_original(sys.argv[3]);return
    require(len(sys.argv)==3,'Broker mode has no original-worker selector')
    from open_webui.utils.agentic_it_broker import BrokerServer
    server=await BrokerServer(profile['socket_path'],profile['root_transport_key'],client_uid=profile['core_uid'],
        socket_gid=profile['core_gid'],prepare=resources.prepare,launch_original=launcher.launch,observe_original=launcher.observe,
        verify_peer=resources.core_peer,verify_original=resources.verify_original,
        verify_dependency=resources.verify_dependency).start()
    stopped=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stopped.set)
    try:await stopped.wait()
    finally:await server.close()


if __name__=='__main__':
    try:asyncio.run(main())
    except Exception:
        # No plan, credentials, private paths or raw exception enters journald.
        print('Fixed IT broker/worker stopped: original receipt reconciliation required',file=sys.stderr)
        raise SystemExit(65)
