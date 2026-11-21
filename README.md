# codex-quota-resumer

> Codex 套餐额度（5 小时窗口 / 周窗口）耗尽导致会话中断后，**等额度真正恢复，自动在原会话里续跑**。

## 它做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 通过 app-server 读权威额度快照（`ordinaryUsageAllowed`） | 不按百分比或 resets_at 硬猜额度是否恢复 |
| 自动发现"最新 turn 因额度失败"的会话 | 不恢复子 agent 线程（由父线程调度） |
| 到重置点用 daemon `thread/resume` + `turn/start` 续跑 | 不绕过额度硬跑 |
| 幂等、冷却、尝试次数上限、僵尸进程检测 | **永不自动消耗 reset credit**（不可逆，需人工） |

## 快速开始

```bash
cd ~/codex-quota-resumer
./install.sh
```

`install.sh` 做三件事：

1. `~/.local/bin/codex-quota-resumer` → `bin/codex-quota-resumer`（软链，保留 PATH 入口）
2. `~/.config/systemd/user/codex-quota-resumer.service` → `systemd/codex-quota-resumer.service`（软链）
3. 重载 systemd，启用服务，再启动或重启服务。启用失败时安装会报错退出。

之后 **本仓库就是唯一源码**；改完代码/配置执行：

```bash
systemctl --user restart codex-quota-resumer
```

若 systemd unit 也有变动，重启前执行 `systemctl --user daemon-reload`。

## 日常使用

```bash
codex-quota-resumer --status    # 额度快照 + 待恢复会话 + 监控进程健康度
codex-quota-resumer --probe     # 探测 daemon 链路是否可用
codex-quota-resumer --once      # 只跑一轮（调试）
codex-quota-resumer --dry-run --force-allowed --once   # 演练：只打印不执行

journalctl --user -u codex-quota-resumer -f            # 主循环决策日志
tail -f ~/.codex/quota-resumer/resumer.log             # 同源文件日志
ls -lt ~/.codex/quota-resumer/runs/                    # 每次恢复的日志
```

`--force-allowed` 仅用于 `--dry-run`，可在额度 RPC 不可用时模拟恢复。演练用只读方式检查状态，可与常驻服务同时运行。

## 工作原理（一图流）

```
        codex-quota-resumer.service  (Restart=always · KillMode=process)
                       │ 主循环
        ┌──────────────┼──────────────┬─────────────────┐
        │ ① 额度感知    │ ② 暂停发现    │ ③ 恢复执行       │
        └──────┬───────┴──────┬───────┴────────┬────────┘
      WebSocket(host sock)  SQLite(只读)   spawn 子进程
               ▼              ▼                ▼
   codex app-server daemon  thread_history  --remote-turn 模式
               └────────────── 同一个 daemon ────┘
```

细节见 [ARCHITECTURE.md](ARCHITECTURE.md)，踩坑与复盘见 [docs/FINDINGS.md](docs/FINDINGS.md)。

## 配置

运行期配置在 `~/.codex/quota-resumer/config.json`（首次启动真实调度时自动生成），样例见
[examples/config.json](examples/config.json)。常改的几个：

| 键 | 默认 | 说明 |
|---|---|---|
| `resume_prompt_template` | `继续` | 恢复时发给会话的文案 |
| `executor` | `auto` | `auto` / `daemon` / `exec` |
| `transport` | `ws` | 额度读取走 daemon(`ws`) 还是新起进程(`stdio`) |
| `poll_seconds_near_reset` | 15 | 临近额度重置时的查询间隔，单位为秒 |
| `max_parallel_resumes` | 2 | 同时恢复几个会话 |
| `max_age_days` | 7 | 只处理最近 N 天活跃的线程 |
| `include_subagent_threads` | false | 是否也恢复子 agent 线程 |
| `max_attempts_per_turn` | 3 | 同一 turn 尝试上限，超限 `parked` |
| `retry_cooldown_seconds` | 900 | 一次恢复结束后的冷却 |

已有配置解析或校验失败时，程序会报错并停止调度。旧配置中的 `auth_retry_limit` 会被忽略，额度查询失败由主循环退避重试。

如果 Codex 依赖版本管理器提供的 Node.js，可通过 `systemctl --user edit codex-quota-resumer` 在本机设置服务的 `PATH`。

## 项目结构

```
codex-quota-resumer/
├── bin/codex-quota-resumer        # 主程序（Python 3 单文件，仅标准库）
├── systemd/                       # systemd user unit
├── install.sh / uninstall.sh      # 安装/卸载（软链 + systemd）
├── examples/config.json           # 配置样例
├── tests/                         # 临时数据库和模拟 app-server 回归测试
├── ARCHITECTURE.md                # 架构与实现原理
├── docs/
│   ├── FINDINGS.md                # 协议、诊断方法与脱敏示例
│   ├── PRIOR-ART.md               # 同类实现调研
│   └── schema/                    # app-server v2 协议 schema（写代码时对照用）
└── tools/                         # 侦查/验证脚本（见 tools/README.md）
```

运行期数据固定在 `~/.codex/quota-resumer/`（config / state / 日志 / 每次恢复的事件流），
不随仓库移动。

## 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 额度恢复了却不续跑 | 并行槽位被"运行中"占满 | `--status` 看监控进程状态；僵尸会标红 |
| 一直 `额度不可用 … 等待` | 额度确实没恢复（它不会绕过） | 等 `resets_at`，或你手动解封（15 分钟内会被察觉） |
| `额度状态未知` | RPC 未返回明确的套餐可用状态 | 检查 daemon 或 stdio。rollout 历史快照用于排障 |
| `already has an active writer` | 用 `codex exec` 抢被 daemon 占用的线程 | 改用 daemon 链路（默认 `executor=auto` 已处理） |
| `daemon 不可用` | app-server daemon 没跑 | `systemctl --user status codex-app-server-daemon` |
| 某会话被 `parked` | 同一 turn 尝试 3 次仍失败 | 改配置或删 `state.json` 对应条目 |
| 服务重启后子进程失联 | 缺 `KillMode=process` | unit 里已包含 |

## 安全与隐私

- **仓库内不含任何凭据**：不读取也不保存 API key / token。额度读取复用本机 codex 的登录态，
  本程序不接触 `~/.codex/auth.json`、不复制、不上报。
- **仓库内不含用户名路径**：文档统一用 `~/`，systemd unit 用 `%h` 说明符（由 systemd 展开）。
- **运行期数据**在 `~/.codex/quota-resumer/`（权限 0700/0600），内含：
  - `config.json` — 仅配置项，无密钥
  - `state.json` — 线程和 turn ID、监控进程身份、最近一次额度快照（可能含本机 account_id）
  - `resumer.log`、`runs/*.log` — 决策日志、turn 状态和事件摘要，**可能包含被恢复会话的对话内容**；
    如需分享日志，请先自行脱敏。
- **无遥测、无第三方上报**。除本机 codex 与 OpenAI 后端之间的既有流量外，本程序不发起其它网络请求。

## 已知边界

1. **不绕过额度**，也**不消耗 reset credit**。
2. 默认**不恢复子 agent 线程**。
3. 服务需要访问 Codex 的 Unix socket、SQLite 索引、rollout 文件和对应进程的 `/proc`。
4. 只处理"最新 turn 因**额度**失败"；`Selected model is at capacity` 等不在范围内。
5. 超过 `max_age_days` 未活跃的线程不回补。

## 文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 组件拓扑、主循环状态机、三个核心环节、幂等状态机、可靠性设计
- [docs/FINDINGS.md](docs/FINDINGS.md) — rollout JSONL 格式、额度 RPC 和故障诊断
- [docs/PRIOR-ART.md](docs/PRIOR-ART.md) — GitHub 同类实现调研与路线对比

运行回归测试可用 `python3 -m unittest discover -s tests -v`。
