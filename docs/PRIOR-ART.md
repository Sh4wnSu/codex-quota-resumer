# 同类实现调研

目的：确认"额度重置后自动续跑 Codex 会话"这个需求，GitHub 上有没有现成实现、
它们的投递通道是什么、和本项目有什么差异。

## 结论速览

- **有人做，且不止一个**，但绝大多数是 Windows / macOS 的**桌面 App 辅助工具**，
  玩法是"要求你把那条对话打开"或"驱动 App 的 UI/Web API"。
- **Linux + headless + 常驻服务**这条路线，公开实现里没看到。
- 技术分水岭在**投递通道**（怎么把"继续"送进那条已存在的会话），见下表。
- Codex 跨额度重置自动续跑的讨论见 [特性请求 #48392](https://github.com/openai/codex/issues/48392)。

## 找到的项目

| 项目 | ★ | 平台 | 额度来源 | 续跑投递方式 |
|---|---|---|---|---|
| [songyb111-gachon/codex-auto-resume-windows](https://github.com/songyb111-gachon/codex-auto-resume-windows) | 93 | Windows 桌面 App | "Codex 自己报告的 reset 时间" | **`codex queue`**，按精确 thread UUID |
| [progressrdx/codex-auto-resume](https://github.com/progressrdx/codex-auto-resume) | 21 | macOS/Windows App | 结构化 `UsageLimitExceeded` + 真实 reset 窗口 | 走 **App 的 Web API** 发一个固定续跑 turn |
| [feifeigong/codex-auto-resume](https://github.com/feifeigong/codex-auto-resume) | 17 | Win/macOS | `app-server` | 需要打开原对话（已归档，迁至 [ai-first-tools](https://github.com/feifeigong/ai-first-tools)） |
| [201224tlx-sketch/codex-auto-resume-watchdog](https://github.com/201224tlx-sketch/codex-auto-resume-watchdog) | 5 | Windows 为主 | **app-server stdio JSON-RPC** | **`codex exec resume`** + active-writer 冲突指数退避 |
| [Tikzen/codex-rate-limit-guard](https://github.com/Tikzen/codex-rate-limit-guard) | 4 | Windows PowerShell | — | CLI 层面重试 |

纯额度监控（不续跑，仅列出便于区分）：`codex-quota`(★81)、`codex-quota-bar`(★113)、
`Codex-Quota-Viewer`(★75)、`CodexQuotaMonitor`、`CodexQuotaTray`、`codex-ratelimit-vscode`(★12)。

## 关键差异：投递通道

这正是本项目踩过的坑——会话被常驻 daemon 持有 writer 锁
（`~/.codex/thread-writer-locks/<thread>.lock`）。三种应对：

| 通道 | 代表 | 代价 |
|---|---|---|
| `codex queue` | songyb（★93） | 作者明确写了限制：**Codex 必须当前正开着那个对话**，否则投不进去 |
| `codex exec resume` | watchdog（★5） | 撞 `already has an active writer (code -32600)`，只能指数退避等锁释放 |
| **daemon `thread/resume` + `turn/start`** | **本项目** | 让 daemon 从磁盘加载线程，**不需要 App 开着** —— 公开实现里没看到有人这么做 |

songyb 项目的原话：

> Codex has to currently have that conversation open for a recovery to be delivered.
> If the app restarted since, open the conversation once and recovery continues on its own.

## 官方与插件生态

- `openai/codex` 的 `/goal` 支持 `usage_limited` 状态和 `deferGoalContinuation` 机制。跨额度重置自动续跑见上面的特性请求。
- 插件生态只做**额度读取与归一化**，例如 openclaw 的
  `extensions/codex/src/app-server/rate-limits.ts`（解析 `ordinaryUsageAllowed`、
  两个窗口、Reserve 路由）与 `agentclientprotocol/codex-acp` 的类型定义，
  **没有续跑逻辑**。

## 值得借鉴的设计点

1. **严格证据判定**（progressrdx）：只认结构化 `UsageLimitExceeded`，
   明确拒绝用 "quota"/"continue"/"limit" 这类模糊文本当证据。
   本项目目前用 `error_json` 关键字匹配，比它宽松，理论上可能误判。
2. **任务指纹校验**（progressrdx）：model / permissions / cwd / goal 变了就停。
   本项目没有这层。
3. **fail-closed**（songyb）：不明错误不重试、投递不确定就停、绝不重复发送。
   本项目有尝试次数上限与冷却，但没有"投递不确定性"这个概念。

## 本项目的差异点

- **自动发现所有暂停线程**（同类项目基本是"显式选中一个会话"再盯）
- **Linux + systemd 常驻、headless**，不依赖桌面 App
- 排除子 agent 线程、僵尸进程检测、`--status` 可观测性
- 只读额度，**绝不消耗 reset credit**

## 检索方式（便于复查）

```bash
gh search repos "codex auto resume" --limit 10
gh search repos "codex rate limit" --limit 10
gh search code "ordinaryUsageAllowed" --limit 10      # 用同一套 RPC 的项目
gh search code "autoResume" --repo openai/codex        # 官方是否实现（空）
```
