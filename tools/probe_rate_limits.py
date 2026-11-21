#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, subprocess, threading, time, os, datetime
env=dict(os.environ); env["RUST_LOG"]="error"
def run():
    p=subprocess.Popen(["codex","app-server"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,env=env)
    out=[]
    threading.Thread(target=lambda:[out.append(l.rstrip()) for l in p.stdout],daemon=True).start()
    p.stdin.write(json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"quota-probe","version":"0.1"}}})+"\n"); p.stdin.flush()
    p.stdin.write(json.dumps({"jsonrpc":"2.0","method":"initialized","params":{}})+"\n"); p.stdin.flush()
    time.sleep(1.5)
    p.stdin.write(json.dumps({"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read","params":{}})+"\n"); p.stdin.flush()
    t0=time.time(); res=None
    while time.time()-t0<25:
        time.sleep(0.5)
        for l in out:
            try: o=json.loads(l)
            except: continue
            if o.get("id")==2: res=o; break
        if res: break
    p.terminate()
    return res
r=run()
if not r: print("NO RESPONSE"); raise SystemExit
print("top-level keys:", list(r.keys()))
res=r.get("result") or r.get("error")
def fts(e): return datetime.datetime.fromtimestamp(e).strftime("%Y-%m-%d %H:%M:%S %Z") if e else None
print(json.dumps({k:v for k,v in r.items() if k!="result"}, ensure_ascii=False)[:400])
d=r.get("result",{})
print("accountId:", d.get("accountId"), "| ordinaryUsageAllowed:", d.get("ordinaryUsageAllowed"))
for bucket, snap in list((d.get("rateLimitsByLimitId") or {}).items()) + [("(default)", d.get("rateLimits"))]:
    if not snap: continue
    print(f"\n== bucket {bucket}: limitId={snap.get('limitId')} plan={snap.get('planType')} reached={snap.get('rateLimitReachedType')} spendControl={snap.get('spendControlReached')}")
    for w in ("primary","secondary"):
        ww=snap.get(w) or {}
        print(f"   {w}: used={ww.get('usedPercent')}% window={ww.get('windowDurationMins')}min resetsAt={ww.get('resetsAt')} ({fts(ww.get('resetsAt'))})")
rc=d.get("rateLimitResetCredits") or {}
print("\nreset credits available:", rc.get("availableCount"))
for c in (rc.get("credits") or []): print("  ", c.get("title"), c.get("status"), "expires", fts(c.get("expiresAt")))
print("upsell reset_at:", fts((d.get("rateLimitUpsell") or {}).get("reset_at")))
