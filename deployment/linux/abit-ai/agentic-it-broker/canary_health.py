#!/usr/bin/python3
"""Only the owned physical-IT canary service: actual PID/start/invocation health."""

import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import time

STATE_ROOT=Path('/var/lib/abit-ai-it-canary-health-20261114')
state=Path(os.environ['ABIT_CANARY_STATE_PATH'])
if state.parent!=STATE_ROOT or state.name not in {'service.json','restore-probe.json'} or STATE_ROOT.resolve()!=STATE_ROOT:
    raise SystemExit(65)
invocation=os.environ.get('INVOCATION_ID','')
if len(invocation)!=32 or any(char not in '0123456789abcdef' for char in invocation):raise SystemExit(65)
previous={}
if state.exists():
    descriptor=os.open(state,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_size>16384:
            raise SystemExit(65)
        previous=json.loads(os.read(descriptor,16385))
    finally:os.close(descriptor)
process=Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()
body={'schema_version':1,'pid':os.getpid(),'process_start_ticks':int(process[19]),'invocation_id':invocation,
      'ready':True,'starts':int(previous.get('starts',0))+1}
descriptor,name=tempfile.mkstemp(prefix='health-',dir=STATE_ROOT)
with os.fdopen(descriptor,'w') as stream:
    json.dump(body,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
os.chmod(name,0o644)  # nonsensitive owned health; fixed Root worker has no DAC override
os.replace(name,state)
running=True
def stop(*args):
    global running
    running=False
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
while running:time.sleep(0.1)
