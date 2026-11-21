#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, glob, datetime
root=os.path.expanduser("~/.codex/sessions")
files=glob.glob(root+"/**/*.jsonl", recursive=True)
rows=[]
def p(s):
    try: return datetime.datetime.fromisoformat(s.replace("Z","+00:00"))
    except: return None
for f in files:
    ev=[]
    for line in open(f, errors="replace"):
        if '"task_started"' not in line and '"task_complete"' not in line and '"turn_aborted"' not in line: continue
        try: o=json.loads(line)
        except: continue
        p2=o.get("payload") if isinstance(o.get("payload"),dict) else {}
        if p2.get("type") in ("task_started","task_complete","turn_aborted"):
            err=(p2.get("error") or {}).get("codex_error_info") if p2.get("type")=="task_complete" else (p2.get("reason") if p2.get("type")=="turn_aborted" else None)
            ev.append((p(o.get("timestamp")), p2.get("type"), err))
    for i,(ts,t,err) in enumerate(ev):
        if t=="task_complete" and err=="usage_limit_exceeded" and i+1<len(ev):
            nts,nt,nerr=ev[i+1]
            if ts and nts:
                rows.append((nts-ts, ts.isoformat(), os.path.relpath(f,root), nt, nerr))
rows.sort()
print("gap between a usage-limit stop and the NEXT turn event, all sessions:")
for g,ts,f,nt,nerr in rows:
    print(f"{str(g):>18}  stop={ts}  next={nt}:{nerr}  {f[:60]}")
