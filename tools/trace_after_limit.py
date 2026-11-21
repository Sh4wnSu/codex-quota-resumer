#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追踪一个 rollout 里"额度中断之后发生了什么"。

用途：验证 codex 自己在额度恢复后会不会自动重试（结论：不会）。
用法：
    python3 tools/trace_after_limit.py <rollout.jsonl> [--max 400]
"""
import argparse
import json
import os
import sys

INTERESTING = {"task_started", "task_complete", "turn_aborted",
               "thread_goal_updated", "thread_settings_applied"}


def main() -> int:
    ap = argparse.ArgumentParser(description="追踪额度中断之后的事件")
    ap.add_argument("rollout", help="rollout jsonl 路径")
    ap.add_argument("--max", type=int, default=400, help="中断后最多看多少行（默认 400）")
    args = ap.parse_args()

    path = os.path.expanduser(args.rollout)
    if not os.path.isfile(path):
        print("找不到文件: %s" % path, file=sys.stderr)
        return 2

    hit = False
    lineno = 0
    printed = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            lineno += 1
            if not hit:
                if "usage_limit_exceeded" in line:
                    hit = True
                    print("[line %d] --- USAGE LIMIT STOP ---" % lineno)
                    try:
                        obj = json.loads(line)
                        print(obj.get("timestamp"),
                              json.dumps((obj.get("payload") or {}).get("error", {}), ensure_ascii=False)[:300])
                    except Exception:
                        pass
                continue

            printed += 1
            if printed > args.max:
                break
            try:
                obj = json.loads(line)
            except Exception:
                continue
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            etype, ptype = obj.get("type"), payload.get("type")
            if (etype == "event_msg" and ptype in INTERESTING) \
                    or "goal" in str(ptype).lower() or "goal" in line[:200].lower():
                extra = ""
                if ptype == "thread_goal_updated":
                    extra = json.dumps(payload, ensure_ascii=False)[:200]
                elif ptype == "task_complete":
                    extra = json.dumps(payload.get("error"), ensure_ascii=False)[:120] if payload.get("error") else "ok"
                print("[+%d] %s %s/%s %s" % (printed, obj.get("timestamp"), etype, ptype, extra))

    if not hit:
        print("这个 rollout 里没有 usage_limit_exceeded", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
