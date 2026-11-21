# Codex 额度与会话恢复的技术记录

以下 JSON、日志和时间均为合成示例。

## 数据来源

路径相对于 `CODEX_HOME`，默认是 `~/.codex`。

| 路径 | 用途 |
|---|---|
| `sessions/YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl` | 会话记录，每行一个 JSON 对象 |
| `state_5.sqlite` 的 `threads` 表 | 工作目录、线程来源、归档状态和 rollout 路径 |
| `thread_history_1.sqlite` 的 `thread_turns` 表 | turn 状态、错误、开始和完成时间 |
| `thread_history_1.sqlite` 的 `thread_items` 表 | 取回提示词模板引用的上一条用户消息 |
| `app-server-control/app-server-control.sock` | 常驻 app-server daemon 的 Unix socket |

## 如何识别额度中断

SQLite 用于筛选每个线程的最新 turn。候选要求状态为 `failed` 且错误符合额度中断特征。rollout 用于复核最新 turn 事件及其 ID，避免在 SQLite 投影滞后时恢复已被用户继续或中止的会话。

下面是一次额度中断对应的合成 rollout 记录。

```json
{
  "type": "event_msg",
  "payload": {
    "type": "task_complete",
    "turn_id": "example-turn",
    "error": {
      "message": "Usage limit reached",
      "codex_error_info": "usage_limit_exceeded"
    }
  }
}
```

rollout 还会记录 `task_started`、`turn_aborted` 和 `token_count` 事件。`token_count.rate_limits` 提供最近的用量和重置时间，供排障参考。历史分析中，fork 或 compaction 复制的记录可能影响按事件计算的恢复间隔。

## 如何确认额度恢复

客户端初始化 JSON-RPC 后，调用 `account/rateLimits/read`。

```jsonl
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"example-client","version":"1.0.0"}}}
{"jsonrpc":"2.0","method":"initialized","params":{}}
{"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read","params":{}}
```

`ordinaryUsageAllowed` 为布尔值 `true` 时才允许调度。它为 `false` 时，可以参考 `resetsAt` 安排下一次查询。字段缺失或为 `null` 时继续退避查询。额度窗口和用量百分比用于展示。

```json
{
  "ordinaryUsageAllowed": false,
  "rateLimitsByLimitId": {
    "codex": {
      "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 1700000000},
      "secondary": {"usedPercent": 25, "windowDurationMins": 10080, "resetsAt": 1700600000},
      "rateLimitReachedType": "rate_limit_reached"
    }
  }
}
```

可用 `codex app-server generate-json-schema --out <dir>` 导出本机 CLI 对应的协议。仓库中的 [协议快照](schema/codex_app_server_protocol.v2.schemas.json) 可用于核对 RPC 字段。

## 恢复链路和故障诊断

### 线程已有 writer

独立的 `codex exec resume` 在目标线程已有 writer 时会收到类似错误。

```text
thread-store conflict: thread example-thread already has an active writer (code -32600)
```

如果 writer 由常驻 daemon 持有，使用同一个 daemon 的 `thread/resume` 和 `turn/start`。可用 `--probe` 检查连接。执行器按 `turn/start` 返回的 turn ID 匹配完成通知。

### 监控进程占用并行槽位

`os.kill(pid, 0)` 对尚未回收的僵尸进程也可能返回成功。`/proc/<pid>/stat` 的 `Z` 或 `X` 状态可用于识别已结束的进程。主进程会回收自己的子进程，并在超时终止之前检查 PID 的启动时间、系统启动标识和进程组。可用 `--status` 查看监控状态。

### 服务看不到 daemon

检查服务是否能访问同一个 `CODEX_HOME` 和 Unix socket。隔离的挂载或 PID 命名空间可能影响 socket 和 `/proc` 的访问。诊断命令见 [tools/README.md](../tools/README.md)。
