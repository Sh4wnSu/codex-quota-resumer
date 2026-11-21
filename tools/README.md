# tools/ — 侦查与验证脚本

这些不是主程序的一部分，是排障/验证/复现用的独立脚本。都能直接
`python3 tools/<name>.py` 跑；大多只读 `~/.codex/`，不写任何东西。
诊断输出可能含账号和会话标识、本机路径或对话片段。分享前请用合成值替换。

| 脚本 | 用途 |
|---|---|
| `scan_sessions.py` | 扫全部 rollout，汇总每个会话最后一次 `rate_limits` 快照（5h/周用量与重置时间） |
| `report_quota_stops.py` | 找出"最后一条 turn 是额度失败"的会话，列出 cwd / turn / 中断时间 / 重置时间 |
| `trace_after_limit.py` | 给定一个 rollout，打印额度中断之后发生了什么（是否有人工/自动重试） |
| `check_auto_retry_gaps.py` | 全量统计"额度中断 → 下一个 turn 开始"的时间差，用来验证 codex 是否会自动重试 |
| `probe_rate_limits.py` | 最小化地调 `account/rateLimits/read`，打印普通字段与 reset credit |
| `ws_probe.py` | 用自写 WebSocket 客户端直连 daemon 控制 socket，验证 WS 链路可用 |
| `probe_daemon_proxy.py` | 验证官方 `codex app-server proxy` 是否可用（本机历史上是坏的，留作复测） |

常用：

```bash
python3 tools/probe_rate_limits.py     # 现在额度多少、下次重置几点
python3 tools/report_quota_stops.py    # 哪些会话是被额度打断的
python3 tools/check_auto_retry_gaps.py # codex 自己会不会到点重试（结论：不会）
```

注意：`trace_after_limit.py` 需要传 rollout 路径：

```bash
python3 tools/trace_after_limit.py /path/to/rollout.jsonl
```
