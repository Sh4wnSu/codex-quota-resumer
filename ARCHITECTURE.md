# codex-quota-resumer 架构与实现原理

> 一句话：**盯着 Codex 套餐额度，等它真的恢复后，把被额度打断的会话用两个字「继续」接着跑。**
> 主程序 `bin/codex-quota-resumer`（Python 3 单文件，仅标准库；安装后软链到 `~/.local/bin/`），
> 以 systemd user service 常驻。

---

## 1. 组件拓扑

```
        ┌─────────────────────────────────────────────────────────┐
        │ systemd --user                                          │
        │   codex-quota-resumer.service                           │
        │   Restart=always · KillMode=process                     │
        └───────────────────────────┬─────────────────────────────┘
                                    │ 主循环 run_loop()
                                    ▼
        ┌──────────────────┬──────────────────┬──────────────────┐
        │   ① 额度感知      │   ② 暂停发现      │   ③ 恢复执行      │
        └────────┬─────────┴────────┬─────────┴────────┬─────────┘
                 │                  │                  │
       WebSocket(host unix sock)  SQLite(只读)     spawn 子进程
                 │                  │                  │
                 ▼                  ▼                  ▼
   ┌─────────────────────────┐ ┌────────────────┐ ┌──────────────────────┐
   │ codex app-server daemon │ │ thread_history │ │ 本脚本 --remote-turn │
   │ (--managed-daemon)      │ │ state_5        │ │ （自有内部模式）      │
   └───────────┬─────────────┘ └────────────────┘ └──────────┬───────────┘
               │                                              │
               ▼                                              ▼
   account/rateLimits/read  ◀────── 同一个 daemon ──────▶  thread/resume
   （后端 usage 接口）                                    turn/start
```

三个外部依赖的职责：

| 依赖 | 用途 | 为什么是它 |
|---|---|---|
| 常驻 app-server daemon | 读额度、发恢复指令 | 由 daemon 持有 writer 锁的线程通过同一个 daemon 续跑（见 §5.1） |
| `thread_history_1.sqlite` | 找"最新 turn 因额度失败"的线程 | 从 turn 级索引选出候选，避免扫描完整 rollout |
| `state_5.sqlite` | 取 cwd / source / archived | 判断是否子 agent、是否归档、在哪个目录跑 |

---

## 2. 主循环（状态机）

```
start
  │
  ├─ reconcile_running()     回收已结束/僵尸的盯梢子进程，刷新 state
  │
  ├─ snapshot()              读额度快照
  │    ├─ WS → daemon → account/rateLimits/read
  │    ├─ 失败 → 起 codex app-server(stdio) → 同一 RPC
  │    └─ 再失败 → 从 rollout 读取历史快照用于排障
  │
  ├─ ordinaryUsageAllowed ?
  │    ├─ 未知 → 退避重试
  │    ├─ false → 参考 resets_at 安排下一次查询，最多等 900s
  │    │          进入重置窗口后按 poll_seconds_near_reset 查询
  │    └─ true ↓
  │
  ├─ collect_targets()       找可恢复线程（见 §3.2）
  │
  ├─ budget = max_parallel_resumes - 运行中数量
  └─ spawn_resume()          起盯梢子进程后不阻塞，继续下一轮
       └─ sleep poll_seconds(120) → 回到 start
```

每个恢复任务是一个 `start_new_session=True` 的子进程。父循环记录进程身份，
每轮由 `reconcile_running()` 回收，因此多个会话可以并行恢复。

---

## 3. 三个核心环节的原理

### 3.1 额度感知：为什么不用百分比推断

以下是合成的 `account/rateLimits/read` 响应。

```json
{
  "ordinaryUsageAllowed": false,
  "rateLimitsByLimitId": {"codex": {
      "primary":   {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 1700000000},
      "secondary": {"usedPercent": 25, "windowDurationMins": 10080, "resetsAt": 1700600000},
      "rateLimitReachedType": "rate_limit_reached"}},
  "rateLimitResetCredits": {"availableCount": 0}
}
```

- **`primary` = 5 小时窗口（300min），`secondary` = 周窗口（10080min）。**
- 恢复判定以 **`ordinaryUsageAllowed`** 为准。官方协议 schema 明确写了：
  *"clients must not infer recovery from percentages or reset times"* —— 百分比和
  `resetsAt` 只是展示值，后端可能因 credit、workspace 限额等原因不按它走。
- `rateLimitResetCredits` 的计数仅用于日志。

### 3.2 暂停发现：索引 + 二次确认

```sql
-- ① 索引层：每个线程的最新 turn
SELECT thread_id, turn_id, status, error_json, COALESCE(completed_at, started_at) AS ts
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY thread_id
                                   ORDER BY COALESCE(completed_at, started_at) DESC) rn
      FROM thread_turns) WHERE rn = 1;
```

判定条件：`status='failed'` **且** `error_json` 命中额度类关键字
（`usage_limit_exceeded` / `usage_limit_reached` / `rate_limit_exceeded` / `quota_exceeded` / `usage limit`）。

再叠三层过滤：

| 过滤 | 默认 | 原因 |
|---|---|---|
| `archived = 0` | 硬编码 | 归档线程不该被复活 |
| `source` 含 `subagent` | 排除 | 子 agent 由父线程调度，单独续跑会和父线程重复劳动 |
| `updated_at` 超过 `max_age_days` | 7 天 | 避免把几周前的僵尸会话翻出来 |

**② 二次确认**由 `rollout_still_paused()` 读取 rollout 尾部 256 KiB，必要时扩大到 2 MiB。
最新的 `task_started`、`task_complete` 或 `turn_aborted` 中，只有带额度错误且
`turn_id` 与索引一致的 `task_complete` 可以恢复。rollout 缺失、正在写入或无法确认时跳过。
daemon 执行器在 `thread/resume` 返回后复查，exec 执行器在启动子进程前复查。

### 3.3 恢复执行：daemon JSON-RPC

子进程以 `--remote-turn` 模式启动，走一条极简 WebSocket 会话：

```
resumer 子进程                    app-server daemon
     │                                  │
     │──initialize─────────────────────▶│  clientInfo: codex-quota-resumer/2.0.0
     │◀────────────────────── userAgent ─│
     │──initialized（通知）──────────────▶│
     │──thread/resume {threadId, cwd}──▶│  rejoin 已在内存中的线程
     │  复查 rollout 中的失败 turn      │
     │──turn/start {threadId, input,───▶│  input = [{type:"text", text:"继续"}]
     │              cwd}                 │
     │◀── turn/started ──────────────────│
     │◀── item/*（事件摘要写 runs/*.log） │
     │◀── turn/completed（匹配 turn ID） ─│
     ▼                                   │
   退出（父进程下一轮回收）
```

客户端通过 Unix socket 上的 WebSocket 访问 daemon。`WsUnix` 处理 HTTP Upgrade、帧编解码与 ping/pong。可以用 `--probe` 检查握手和 JSON-RPC 初始化。

**双执行器（executor）**：

| 模式 | 机制 | 何时用 |
|---|---|---|
| `daemon`（默认） | `thread/resume` + `turn/start` | daemon 可达时（`auto` 会先探测，结果缓存 120s） |
| `exec`（回退） | `codex exec resume <id> "<prompt>"` | daemon 挂掉时；**仅对未被 daemon 挂载的线程有效** |

---

## 4. 状态机与幂等（`state.json`）

```json
{"threads": {
  "example-thread": {
    "last_failed_turn_id": "example-turn",
    "status": "finished",
    "pid": 1234, "pid_start_ticks": 12345, "pid_boot_id": "example-boot", "log": "runs/example.log",
    "attempts": 1,
    "resumed_at": 1700000060, "finished_at": 1700000120,
    "executor": "daemon", "cwd": "/path/to/project"
  }}}
```

```
(无记录) ──spawn──▶ running ──进程结束/僵尸──▶ finished
                      │                          │
                      │                          ├─ 命中 benign 冲突 → attempts-1（不计失败）
                      │                          ├─ 冷却中(900s) → 跳过
                      │                          ├─ attempts ≥ 3 → parked（需人工介入）
                      │                          └─ 冷却结束 → 再次 spawn
                      └─ 超过 6h 上限 → 终止 → finished
```

幂等靠两个键：**`last_failed_turn_id`**（同一个 turn 不重复恢复）+ **rollout 尾部确认**
（用户手动恢复过就自动让路）。注意 turn_id 变了说明是**新的一次打断**，
`attempts` 会重置并重新参与下一轮。

**良性跳过**：命中 `already has an active or pending turn` / `already has an active writer`
说明线程正被别的 turn 占用，不算失败，把尝试次数退回去，避免误 park。

---

## 5. 可靠性设计

### 5.1 writer 锁：为什么必须走 daemon

若 daemon 持有 `~/.codex/thread-writer-locks/<thread>.lock`，独立的 `codex exec resume` 进程会收到 writer 冲突。

```
thread-store conflict: thread <id> already has an active writer (code -32600)
```

**教训**：判断"某条命令能不能用"要看目标对象的**所有权**，不能只看命令本身是否可用。

### 5.2 僵尸进程：`os.kill(pid, 0)` 会说谎

子进程退出但父进程不回收 → 变僵尸（`/proc/<pid>/stat` 的 state = `Z`），
而 `os.kill(pid, 0)` **仍然返回成功**。旧实现因此在额度恢复后仍认为"2 个在跑"，
`budget = 2-2 = 0`，**再也不恢复任何会话**（自我强化的死锁）。

`monitor_state()` 读 `/proc/<pid>/stat` 的状态和启动时间，将 `Z/X` 视为已结束，
再用 `os.waitpid(pid, WNOHANG)` 回收能确认的子进程。超时终止还会校验系统启动标识和进程组。
旧记录仍可用于判断进程活跃状态，超时后仅释放槽位。

### 5.3 其他

| 机制 | 值 | 作用 |
|---|---|---|
| 单实例 `flock` | `resumer.lock` | 真实调度进程互斥，演练可以并行查看 |
| `KillMode=process` | systemd | restart 服务不连带杀掉盯梢子进程（turn 归 daemon，仍会跑完） |
| `resume_timeout_seconds` | 6h | 盯梢进程兜底超时 |
| `retry_cooldown_seconds` | 900s | 结束后的冷却，用**日志 mtime** 作为真实结束时间 |
| `max_attempts_per_turn` | 3 | 防无限重试烧额度 |
| 额度读失败退避 | 30s×n，上限 600s | daemon 抖动时不打爆 |
| 额度授权 | WS 或 stdio 的 RPC | rollout 历史快照用于展示，RPC 明确允许后才调度 |
| WebSocket 关闭 | shutdown + 回收读线程 | 主动结束阻塞中的读操作 |

---

## 6. 配置地图

| 键 | 当前值 | 说明 |
|---|---|---|
| `resume_prompt_template` | `继续` | 恢复时发给会话的提示词 |
| `executor` | `auto` | `auto` / `daemon` / `exec` |
| `transport` | `ws` | 额度读取：`ws`（直连 daemon）/ `stdio`（每次新起进程） |
| `poll_seconds` | 120 | 额度可用时的巡检间隔 |
| `near_reset_window_seconds` | 300 | 距离重置多久开始加快查询 |
| `poll_seconds_near_reset` | 15 | 临近重置时的查询间隔 |
| `max_parallel_resumes` | 2 | 同时恢复几个会话 |
| `max_resumes_per_cycle` | 3 | 每轮最多启动几个 |
| `max_age_days` | 7 | 只处理最近 N 天活跃的线程 |
| `include_subagent_threads` | false | 是否也恢复子 agent 线程 |
| `max_attempts_per_turn` | 3 | 同一 turn 尝试上限 |
| `retry_cooldown_seconds` | 900 | 恢复结束后的冷却 |
| `resume_timeout_seconds` | 21600 | 单次恢复最长盯梢时间 |

改完 `systemctl --user restart codex-quota-resumer`。

---

## 7. 可观测性

| 手段 | 看什么 |
|---|---|
| `codex-quota-resumer --status` | 额度快照 + 待恢复会话 + 监控进程健康度 + 并行占用 |
| `codex-quota-resumer --probe` | daemon 链路是否可用 + daemon 当前加载的线程 |
| `journalctl --user -u codex-quota-resumer` | 主循环决策日志（额度/待恢复/★恢复/跳过原因） |
| `~/.codex/quota-resumer/runs/*.log` | 单次恢复的命令、turn 状态和事件摘要 |
| `~/.codex/quota-resumer/resumer.log` | 与 journald 同源的文件日志 |

---

## 8. 已知边界

1. **不绕过额度**：额度没恢复就只等，绝不硬跑、绝不自动消耗 reset credit。
2. **不恢复子 agent 线程**（默认）：它们由父线程调度，单独续跑会重复劳动。
3. 服务需要访问 Codex 的 Unix socket、索引、rollout 和对应进程的 `/proc`。
4. **只处理"最新 turn 因额度失败"**：`Selected model is at capacity`（serverOverloaded）
   等非额度错误不在范围内。
5. **超 7 天未活跃的线程不回补**（`max_age_days`）。
