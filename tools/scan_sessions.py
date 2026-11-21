#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, glob, datetime

root = os.path.expanduser("~/.codex/sessions")
files = sorted(glob.glob(root + "/**/*.jsonl", recursive=True), key=os.path.getmtime, reverse=True)

def ts(e):
    if not e: return "-"
    try: return datetime.datetime.fromtimestamp(e).strftime("%m-%d %H:%M")
    except: return str(e)

print(f"{'file':<48} {'mtime':<12} {'last_kind':<22} {'pri%':>5} {'5h_reset':<12} {'sec%':>5} {'wk_reset':<12} {'err'}")
for f in files:
    last_rl=None; last_type=None; err=None; plan=None
    try:
        with open(f, errors="replace") as fh:
            for line in fh:
                if '"rate_limits"' not in line and '"error"' not in line and '"task_complete"' not in line:
                    continue
                try: o=json.loads(line)
                except Exception: continue
                p=o.get("payload") or {}
                if isinstance(p,dict) and p.get("rate_limits"):
                    last_rl=p["rate_limits"]; last_type="token_count"; plan=last_rl.get("plan_type")
                t=o.get("type")
                if t=="event_msg":
                    last_type=p.get("type") or last_type
                    if p.get("type") in ("error","stream_error"):
                        err=json.dumps(p, ensure_ascii=False)[:80]
    except Exception as e:
        print("READERR", f, e); continue
    rl=last_rl or {}; pri=rl.get("primary") or {}; sec=rl.get("secondary") or {}
    mt=datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%m-%d %H:%M")
    nm=os.path.basename(f).replace("rollout-","")[:46]
    print(f"{nm:<48} {mt:<12} {str(last_type)[:20]:<22} {str(pri.get('used_percent')):>5} {ts(pri.get('resets_at')):<12} {str(sec.get('used_percent')):>5} {ts(sec.get('resets_at')):<12} {plan or ''} {err or ''}")
