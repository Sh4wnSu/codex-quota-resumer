#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, subprocess, threading, time, sys, os
sock = sys.argv[1] if len(sys.argv)>1 else None
cmd = ["codex","app-server","proxy"] + (["--sock", sock] if sock else [])
print("CMD:", " ".join(cmd))
env=dict(os.environ); env["RUST_LOG"]="error"
p=subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
out=[]
threading.Thread(target=lambda: [out.append(l.rstrip()) for l in p.stdout], daemon=True).start()
def send(o):
    try:
        p.stdin.write(json.dumps(o)+"\n"); p.stdin.flush()
    except Exception as e: print("send err", e)
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"probe","version":"1"}}})
send({"jsonrpc":"2.0","method":"initialized","params":{}})
t0=time.time(); ok=False
while time.time()-t0<20:
    time.sleep(0.5)
    if any('"id":1' in l or '"id": 1' in l for l in out): ok=True; break
    if p.poll() is not None: break
print("initialize ok:", ok, "| proc rc:", p.poll())
for l in out[:5]: print("  OUT:", l[:300])
if ok:
    send({"jsonrpc":"2.0","id":2,"method":"thread/loaded/list","params":{}})
    t0=time.time()
    while time.time()-t0<20:
        time.sleep(0.5)
        if any('"id":2' in l or '"id": 2' in l for l in out): break
    for l in out:
        if '"id":2' in l or '"id": 2' in l:
            print("  loaded threads:", l[:600])
try: p.terminate()
except Exception: pass
time.sleep(0.3)
err=p.stderr.read()[:800]
if err.strip(): print("STDERR:", err)
