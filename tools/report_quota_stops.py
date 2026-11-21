#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, glob, datetime
root=os.path.expanduser("~/.codex/sessions")
files=glob.glob(root+"/**/*.jsonl", recursive=True)
def fts(e):
    if not e: return "-"
    return datetime.datetime.fromtimestamp(e).strftime("%Y-%m-%d %H:%M:%S")
rows=[]
for f in files:
    meta=None; limited=None; last_rl=None; users=[]; ncalls=0
    for line in open(f, errors="replace"):
        if '"task_complete"' not in line and '"session_meta"' not in line and '"rate_limits"' not in line and '"role":"user"' not in line and '"function_call"' not in line:
            continue
        try: o=json.loads(line)
        except Exception: continue
        t=o.get("type"); p=o.get("payload") if isinstance(o.get("payload"),dict) else {}
        if t=="session_meta" and meta is None: meta=p
        rl=p.get("rate_limits")
        if rl and (rl.get("primary") or rl.get("secondary")): last_rl=rl
        if t=="event_msg" and p.get("type")=="task_complete":
            err=p.get("error") or {}
            limited = {"at":o.get("timestamp"),"err":err} if err.get("codex_error_info")=="usage_limit_exceeded" else None
        if t=="response_item" and p.get("type")=="message" and p.get("role")=="user":
            txt="".join(c.get("text","") for c in (p.get("content") or []) if isinstance(c,dict))
            if txt.strip(): users.append(txt.strip().replace("\n"," "))
        if t=="response_item" and p.get("type")=="function_call": ncalls+=1
    if limited:
        rows.append(dict(file=f, meta=meta or {}, rl=last_rl or {}, limited=limited, users=users, ncalls=ncalls))
rows.sort(key=lambda r: r["limited"]["at"], reverse=True)
print("配额耗尽而中断的会话数:", len(rows), "\n")
for r in rows:
    m=r["meta"]; pri=(r["rl"].get("primary") or {}); sec=(r["rl"].get("secondary") or {})
    print("="*100)
    print("stop_at :", r["limited"]["at"], "| sid:", m.get("session_id"), "| cli:", m.get("cli_version"), "| source:", m.get("source"), "| originator:", m.get("originator"))
    print("cwd     :", m.get("cwd"))
    print("file    :", os.path.relpath(r["file"], root))
    print("rl(last): 5h %.1f%% reset %s | week %.1f%% reset %s | plan %s" % (pri.get("used_percent") or -1, fts(pri.get("resets_at")), sec.get("used_percent") or -1, fts(sec.get("resets_at")), r["rl"].get("plan_type")))
    print("turns/calls:", len(r["users"]), "/", r["ncalls"])
    for u in r["users"][-2:]: print("   user:", u[:160])
    print("   err :", (r["limited"]["err"].get("message") or "")[:180])
