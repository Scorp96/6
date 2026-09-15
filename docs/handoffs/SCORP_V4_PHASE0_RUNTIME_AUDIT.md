# SCORP V4 Phase 0 Runtime Audit

本文件记录 `SCORP NEXT-GENERATION PERSISTENT INTELLIGENCE RUNTIME AUTHORITATIVE CODEX ENGINEERING HANDOFF` 要求的现场审计。审计依据是当前隔离工作树、当前 Windows 只读状态、当前 SQLite 只读快照和当前 Chrome 只读预检；交接文档本身不是实现证据。

审计时间：2026-09-15（Asia/Shanghai）

## CURRENT_HEAD

current documentation snapshot (code candidate `5827941b60f51150d47a4bb0e26879edb66ede38`; exact branch HEAD is recorded after this documentation update)

## REMOTE_MAIN_HEAD

`79610ae3f3864e133f750f0c9dfde40b17a91def`

## BRANCH

`feature/v4-fast-runtime-command-core`

## WORKTREE

`C:\ScorpAgent\worktrees\v4-fast-runtime-command-core`

## GIT_STATUS

工作树干净，无未跟踪文件。当前分支相对 `origin/main` 有本地未推送提交；没有 reset、clean、stash、force-checkout 或主分支切换。

## CURRENT_RUNTIME_MAP

- 规范源仓库：`Scorp96/6`。
- 当前实现处于独立 worktree；生产 checkout 未修改。
- Runtime Command Core：`scorp-agent/master_a_dynamic_v4/runtime_protocol.py`、`runtime_commands.py`、`operator_control.py`、`runtime_cli.py`、`runtime_pipe.py`、`runtime_pipe_client.py`、`runtime_connector_cli.py`、`runtime_pipe_cli.py`。
- 权威状态：代码支持本地 SQLite v4 迁移后的事务状态；JSON 仅作为兼容导入/导出来源。
- Phase 1 transport：直接 Python API 加 stdin/stdout JSON；没有未经认证的 HTTP 服务。
- daemon 入口：`scorp-agent/chatgpt-gui-bridge/tools/v4_daemon_runtime.py`。
- 浏览器适配器：现有 Chrome Use driver；当前不允许通过 Runtime Command Core 直接执行任意浏览器操作。
- 当前候选运行时 Python：`C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe`，文件存在。
- Chrome Use 可执行文件：`C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe`，文件存在。

## CURRENT_WINDOWS_SUPERVISION

当前任务状态（只读）：

- `ScorpChatGptGuiBridge = Running`
- `ScorpChatGptGuiBridgeWatchdog = Disabled`
- `ScorpComputerAgent = Running`
- `ScorpFullAutoOrchestrator = Ready`
- `ScorpV4SelfUpgrade5561f44 = Ready`

没有证据表明专用的 V4 Runtime daemon Scheduled Task 已完成生产注册；V4 daemon installer 仍是候选安装入口。现场还观察到两个同时存在的旧 `bridge_worker.py` 实例：PID 7876 使用 bundled Python，PID 13488 使用 uv Python，二者命令参数相同且后者的父进程是前者；`health.json` 当前为 `ERROR`，错误为 Chrome Use tab 已丢失。现场还观察到多个旧的 `chrome-use.exe` 进程；这证明存在历史 transport/session 生命周期残留和当前旧桥接的重复进程风险，但不提供安全依据去全局杀进程或切换生产任务。候选分支已加入进程锁，但尚未部署到生产任务。进程存在不等于 UI 自动化可用，也不等于候选版本正在运行。

## CURRENT_SQLITE_STATE_MODEL

代码模型包含 contracts、project state、master sessions、task DAG、assignments、leases、daemon leases、events、action intents、outbox、browser bindings、candidate results、evidence receipts、operator controls、runtime observations、runtime command receipts 和 daemon supervision。

当前已知的两个只读数据库快照仍是旧 schema，不能被当作新候选运行库：

- `C:\ScorpAgent\_publish_git6\state\v4-candidate.sqlite3`
- `C:\ScorpAgent\v4-core-lab\state.sqlite3`

两者现场读数均为 `user_version=0`、`journal_mode=delete`、`synchronous=2`、`foreign_keys=0`，且缺少新 Runtime 表。它们没有被本轮自动迁移或双写；直接拿它们运行新 Runtime 必须先显式迁移并重新验证。

## CURRENT_DAEMON_MODEL

已实现 daemon lease、daemon epoch、heartbeat、SQLite activation snapshot、唯一事件 ID、restart/recovery 计数、restart budget、bounded backoff 和 circuit state。daemon 默认可重新获取当前 SQLite epoch，避免 Scheduled Task 固定旧 epoch。

当前入口仍是 monitor-oriented seam：它能读取 durable snapshot、记录 ActivationArbiter decision、运行有限循环并输出 health；真实浏览器提交与歧义核对仍由受约束 adapter 负责，不能宣称它已经是完整无人值守 Fast Local Runtime。除 `TERMINAL` 与 `HEARTBEAT_IDLE` 外，缺少动作处理器或处理器明确返回阻塞状态时会写入 `BLOCKED`，不会伪报 `HEALTHY`；循环随后停止，不会继续重试。

## CURRENT_ACTIVATION_MODEL

Activation Arbiter 是无副作用、确定性的单决策函数。当前顺序已覆盖：terminal、emergency stop、operator fencing、auth/host blocker、ambiguous reconciliation、stale result fencing、Master resume、pending result wake、lost Worker resume、confirmed stall recovery、ready assignment、idle。

`IDLE` 不更新 `last_progress_at`。`project.supersede` 现在持久化 `SUPERSEDED`，新 objective 必须显式 `project.resume` 才能进入 `RUNNING`。`project.emergency_stop` 持久化 `EMERGENCY_STOPPED` 并 fence 当前工作。`project.resume` 后调度器同时接受 `ACTIVE` 与 `RUNNING` 生命周期状态，既有活动 assignment 可以重新装载，新的 READY 任务可以重新派发。

## CURRENT_MASTER_MODEL

Master A 是由 SQLite project、master epoch、checkpoint 和 evidence 绑定的逻辑身份，不等于某个固定 ChatGPT URL。旧 Master epoch 的 Runtime mutation 会被拒绝。当前代码具备 MasterSupervisor 接口；过期恢复现在会为新的物理 ChatGPT/browser session 生成一次性 session id，旧物理 session 保留为 STALE，真实浏览器重绑定和当前候选版本的 live 运行尚未通过。

## CURRENT_WORKER_MODEL

Scheduler 使用最多两个动态 Worker slot，assignment、lease、task dependency、resource scope、generation 和 fencing token 均持久化。Worker 不是固定 B/C 角色，旧 lease 结果不能直接改变权威状态。

离线测试证明动态任务图和槽位复用；此前真实实机证据显示一个浏览器 dispatch 阻塞时另一个 Worker 可能尚未进入提交阶段。当前候选没有新的双 GPT live 并行证据，因此真实浏览器并行能力仍是 `UNVERIFIED`，不能由 `max_workers=2` 推断。

## CURRENT_BROWSER_MODEL

Chrome Use driver 已实现 durable intent、MAY_HAVE_SUBMITTED、原 actor reconcile、session/turn binding、受约束 retire 和 ambiguity fail-closed。本轮只读预检已显示正常新聊天 composer；随后当前候选 c205 单 Worker canary 记录了 conversation URL，但只读核对得到旧诊断回复且没有 c205 token，证据见 `SCORP_V4_LIVE_CANARY_FAILURE_C20561B.json` 和当前只读预检 `SCORP_V4_LIVE_READONLY_PREFLIGHT_5827941.json`。

因此本轮未确认新 prompt 已送达、未确认当前候选 Worker GPT 对话结果、未重放历史 ambiguous intent。请求限制恢复的历史只读 preflight 显示当时页面没有限制弹窗，证据见 `SCORP_V4_RATE_LIMIT_RECOVERY_LIVE_PREFLIGHT_E1E31D1.json`；当前会话状态不一致的只读证据见 `SCORP_V4_LIVE_READONLY_PREFLIGHT_5827941.json`；只证明了不点击分支，未证明真实弹窗确认和等待恢复。大量现存 tab/session 仍在 Chrome 中，但没有证据允许全局清理；只能由精确的 lifecycle binding 管理。

## CURRENT_EXECUTION_MODEL

LocalExecutionAdapter 对 project、assignment、master epoch、lease、allowed root、resource scope、access mode、命令/模块 allowlist 和 candidate identity 做约束，返回 exit code、受限输出、artifact hash 和 execution receipt。Runtime CLI 不接受 shell、任意 Python、任意 subprocess、任意路径写入或任意浏览器动作。

## CURRENT_EVIDENCE_MODEL

- V4 core：181 项测试通过。
- GUI bridge：493 项测试通过。
- compileall：通过。
- `git diff --check`：通过。
- validation JSON：可解析。
- 当前候选 validation：代码候选 `5827941b60f51150d47a4bb0e26879edb66ede38`；请求限制 fail-closed 专项证据见 `SCORP_V4_RATE_LIMIT_RECOVERY_FAILURE_BOUNDARY_5827941.json`。
- 当前候选真实 Chrome：`LIVE_VERIFIED = BLOCKED`，原因是当前 canary 的 Chrome Use send ref 无法解析。
- Windows supervision snapshot：见 `SCORP_V4_WINDOWS_SUPERVISION_AUDIT_5827941.json`；生产旧 bridge worker 曾出现重复进程，候选进程锁仅在隔离代码中验证。
- 当前候选：`ACCEPTED = false`。
- 生产切换：未授权、未执行。

## IMPLEMENTED

- Versioned Runtime protocol 和 bounded command registry。
- SQLite transaction core、durable receipts、request idempotency。
- state/daemon/master/generation fencing。
- pause/resume/cancel/supersede/emergency-stop 控制语义。
- explicit operator states：`RUNNING`、`PAUSED`、`CANCELLED`、`SUPERSEDED`、`EMERGENCY_STOPPED`。
- daemon supervision、epoch reacquisition、backoff、restart budget。
- stall ordering 和 IDLE/progress 分离。
- SQLite write failure 在 browser intent fence 前 fail-closed。
- 现有 V4 regression 与 GUI bridge regression。
- 请求限制恢复：明确确认按钮只点击一次，最多等待 300 秒并只读复核；窗口结束仍限流时最多刷新一次；未知按钮、登录和验证码保持阻塞。
- 请求限制恢复 transport 异常：确认按钮点击失败或确认后的只读读取失败时明确返回 `BLOCKED`，不刷新、不重试、不冒泡为可继续动作。
- Master 重绑定认证探针已接入请求限制恢复函数；该接线有离线回归覆盖，但真实限流弹窗分支仍未获得 LIVE_VERIFIED。
- Master 心跳现在先执行只读物理健康检查；浏览器失联才请求受控重绑定，限流/认证阻塞直接 BLOCKED，不推进逻辑 epoch。
- Master 重绑定恢复探针的底层传输异常会返回 `AUTH_PROBE_FAILED`，不会继续到浏览器提交。
- daemon 缺少任何可执行 arbiter 动作处理器时 fail-closed 为 `BLOCKED`，不会伪报 `HEALTHY`。
- bridge worker 启动前使用 OS 进程锁，重复进程直接退出；真实 Windows 子进程竞争测试通过。
- Named Pipe 启动器允许省略固定 daemon epoch，并从当前 SQLite lease 重新获取；显式旧 epoch 仍被 fence；Windows 一次性进程回环已覆盖该路径。
- Master 过期恢复不再复用旧物理 session id；真实 SQLite 集成回归证明会生成新的 `::resume-<nonce>` session，并推进新的 master epoch。

## PARTIAL

- Runtime 已有 stdin/stdout transport，并有经过真实 Windows 进程回环测试的 connector facade、Named Pipe 客户端、服务端和一次性/有界 launcher；尚无正式 approved ChatGPT host connector 或生产 listener 注册。
- daemon 有监督和恢复 seam，但没有生产 Scheduled Task 证据。
- Master/Worker browser actor lifecycle 有持久化边界，但真实当前候选双 Worker 提交未验证。
- session lifecycle 有精确 retire API，但现有 Chrome 中历史 tab/session 数量仍较多。
- 迁移代码存在，但当前已存在的旧数据库尚未被本轮迁移为生产候选。

## MISSING

- 当前候选单 Worker canary 的 send/capture 和重新授权验证。
- 真实 ChatGPT Worker conversation 执行 CSV 代码工作负载。
- 生产 Scheduled Task 注册和切换授权。
- 当前候选跨真实浏览器边界的 crash/restart/rebind 证据。
- 长时间无人值守稳定性证据。
- 正式 GPT → local Runtime connector。

## INCORRECT

- “代码测试通过即可宣称真实浏览器完成”是错误结论。
- “两个 assignment 等于两个 GPT 已并行运行”没有依据；历史实机结果反而显示浏览器 dispatch 串行阻塞风险。
- 将旧数据库 `user_version=0` 快照当成新 Runtime 已安装状态是错误的。
- 在 rate limit、auth 或 ambiguous side effect 期间盲目重发是禁止行为。

## UNVERIFIED

- 当前候选的真实 Chrome submit/capture。
- 当前候选的真实双 Worker 并行和 Worker-2 结果回传。
- Windows 重启、睡眠恢复、daemon hard crash 后的真实恢复。
- 物理 Master conversation replacement 后 Logical Master 连续性。
- 真实 local execution 与 Git candidate 在浏览器 Worker 闭环中的绑定。
- Native Astra acceptance；本轮使用的是已声明的 Sol compatibility fallback。

## P0_FINDINGS

- P0 durable command/fencing invariants：通过当前离线测试验证。
- P0 no arbitrary shell：通过协议拒绝和 CLI 边界测试验证。
- P0 no blind retry：代码和离线故障注入覆盖；c205 live canary 在浏览器状态歧义处 fail-closed，未重试。
- P0 ambiguous intent fence：scheduler 现在拒绝在 `MAY_HAVE_SUBMITTED` 或 `BLOCKED_AMBIGUOUS` 存在时创建新的普通 assignment。
- P0 operator fence admission：`PAUSED`、`SUPERSEDED`、`CANCELLED` 和 `EMERGENCY_STOPPED` 状态拒绝新的 task graph admission。
- P0 missing authority：缺失 `operator_controls` 时，新的 graph/assignment admission fail-closed。
- P0 snapshot authority：缺失 `operator_controls` 的 activation snapshot 返回 `UNKNOWN`，不再伪造 `RUNNING`。
- P0 resume admission：修复了 resume 后 scheduler 误把 `RUNNING` 当成非活动状态，避免暂停恢复后永久不派发任务或丢失活动 assignment。
- P0 stale Master physical session：已修复同一 stale `session_id` 恢复命中 `MASTER_SESSION_ID_REUSED` 的断点；当前只证明真实 SQLite/离线恢复，尚未证明浏览器重绑定成功。
- P0 auth/rate-limit human boundary：已实现有界确认/等待/单次刷新恢复；当前 live canary 仍有浏览器状态歧义，不能把恢复动作当作发送成功。
- P0 current-candidate browser exactly-once：未达到 LIVE_VERIFIED。
- P0 dedicated production daemon authority：未证明已安装。
- P0 legacy bridge singleton：当前生产现场曾有两个同命令 bridge worker；候选已修复启动门禁，但生产尚未切换，因此现场 P0 仍未关闭。

## P1_FINDINGS

- stdio connector facade 和 Named Pipe 客户端/服务端仅作为未注册的候选 transport；正式 ChatGPT host connector 尚未接入。
- 真实 browser dispatch 的并行隔离需要单独修复或 live 复验。
- 旧 SQLite 快照与候选 schema 的安装/迁移边界需要显式操作流程。
- session/target lifecycle 需要继续收敛，但不能做无绑定的全局清理。

## P2_FINDINGS

- Goal Graph、World Model、Cognitive Memory、Learning 和 Self-Model 尚未进入本阶段。
- Provider abstraction 和 Codex schedulable provider 尚未达到 live actuator 证据门槛。

## CURRENT_EXECUTION_CEILING

当前可执行上限是：在隔离 worktree 中运行 SQLite Runtime Core、离线测试、受约束本地 CLI、故障注入、状态审计和只读浏览器预检。当前不能安全证明新候选已经向 ChatGPT 发送 prompt，也不能证明生产 daemon 已接管。

## CURRENT_CHAT_DEPENDENCIES

- Windows 已登录交互会话。
- ChatGPT 有效登录和有效订阅。
- 当前没有 rate-limit、CAPTCHA、security challenge 或必须人工处理的认证交互。
- Chrome Use executable、driver state 和目标 tab 绑定可核对。
- 发送前必须有当前候选 SHA、只读 preflight、明确授权和 fresh evidence。

## CURRENT_MANUAL_STEPS

1. 人工确认 ChatGPT rate-limit blocker 已清除；若未清除，保持 BLOCKED。
2. 重新运行只读 preflight，确认登录、目标 tab 和候选 driver binding。
3. 由操作者单独授权当前候选 canary；发送前不得调用历史 intent。
4. 记录新的 conversation URL、response marker、candidate SHA 和 evidence receipt。
5. 只有单 Worker canary 正面通过后，才评估双 Worker 和真实 CSV workload。
6. live evidence 完整前不注册或切换生产 Scheduled Task。

## CURRENT_CODEX_PROVIDER_STATUS

本轮 Codex 作为工程实现和验证者，不是永久 SCORP Master。实现与验证按已声明的 `gpt-5.6-sol/high` compatibility fallback 处理；这不是 native Astra acceptance，也没有把 Sol 接手模型等同于产品运行时 Master A。

## PHASE_1_DESIGN

Logical Master A 发出 versioned semantic Runtime command；Command Core 负责 protocol validation、authorization seam、fencing、idempotency、durable receipt 和 deterministic dispatch；StateStore/Arbiter/Adapters 返回 machine evidence。没有 arbitrary shell，没有未经认证网络服务。

## PHASE_1_PLAN

Phase 1 的代码、测试和隔离文档已完成并通过离线回归。Phase 1 的 acceptance 仍受当前候选真实浏览器 blocker、生产未切换和 live recovery 未验证约束；因此阶段结论是 `PARTIAL`，不是 `PASS` 或 `ACCEPTED`。
