# SCORP V4 Fast Local Runtime Command Core

这是 `Scorp96/6` 的隔离实施分支 `feature/v4-fast-runtime-command-core`，基于 `bd67ae78c874d2a5f897101097db0578f01a5c00`。实现目标是给本地 GPT/浏览器桥接一个受限的 JSON 命令边界；它不会把普通 GPT 的自然语言变成任意 PowerShell、Python、Git 或浏览器操作。

## 运行

在仓库根目录使用现有 Windows Python：

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
@'
{"protocol_version":"scorp.runtime.command/1","request_id":"status-1","command":"runtime.status","project_id":"demo","payload":{}}
'@ | & C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -B -m master_a_dynamic_v4.runtime_cli `
  --database C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --project-id demo `
  --allowed-root C:\ScorpAgent\v4-runtime `
  # omit --daemon-epoch for a daemon process; it acquires the current SQLite epoch
  --daemon-epoch 1
```

Runtime daemon 启动时可以省略固定的 `--daemon-epoch`，由 SQLite lease 返回当前 epoch；如果显式提供，则只作为期望值校验。这样 Scheduled Task 重启后不会把旧 epoch 固定在任务参数中。读取命令是 `runtime.status`、`runtime.snapshot`、`project.status`、`master.status`、`worker.status` 和有界的 `evidence.query`。控制命令是 `project.pause`、`project.resume`、`project.cancel`、`project.supersede` 和 `project.emergency_stop`；控制命令必须同时带 `expected_state_version`、`expected_daemon_epoch`，并使用新的 `request_id`。`project.supersede` 持久化 `SUPERSEDED`、递增 objective/operator generation 并 fence 旧代际；必须随后通过新的、带 CAS 的 `project.resume` 才进入 `RUNNING`。`project.emergency_stop` 持久化 `EMERGENCY_STOPPED`、递增 operator generation，并在同一事务中 fence 活动 assignment、lease、task 和未验证 candidate result；它不盲目关闭浏览器或重放外部动作。请求可带有界的 `actor`；响应会返回 `master_epoch` 和 operator `generation`，调用方可以用 `expected_master_epoch`、`expected_generation` 拒绝旧 Master 或旧控制代际。
每个控制命令还写入语义化的 append-only event kind：`PROJECT_PAUSED`、`PROJECT_RESUMED`、`PROJECT_CANCELLED`、`OBJECTIVE_SUPERSEDED` 或 `EMERGENCY_STOPPED`；receipt 与 event 在同一事务内提交。

当前候选还提供 `scorp-agent/master_a_dynamic_v4/runtime_pipe.py`、
`runtime_pipe_client.py`、`runtime_connector_cli.py` 和 `runtime_pipe_cli.py`。它们使用固定的
`\\.\pipe\scorp-runtime-<project>` Windows Named Pipe、认证 `authkey` 和 64 KiB
消息上限，只转发同一版本化 Runtime JSON 协议。客户端只做一次请求/响应交换，
超时不自动重试。它是库级候选 transport，尚未注册
生产 listener，也没有让普通网页 GPT 自动获得 Windows 权限；`runtime_pipe_cli.py`
只用于候选的一次性或有界连接测试，正式 connector 仍需
单独完成身份、进程监督和证据门禁。

`runtime_pipe_cli.py` 现在也允许省略 `--daemon-epoch`；启动时从当前 SQLite
daemon lease 获取 epoch。显式提供 epoch 仍会执行 fence 检查，旧 epoch 不会被
接受。这样宿主重启时不会把上一次进程的 epoch 固定在长生命周期命令中。

运行时观测现已在 SQLite schema v5 中持久化 `last_heartbeat_at`，并把它与
`last_progress_at`、`last_content_change_at`、浏览器成功/失败时间分开。单纯的
`ACTIVE_GENERATING` 心跳不会伪造进度；只有明确的内容变化或 `progress_made`
信号才推进 `last_progress_at`。旧 schema 会在打开数据库时补齐心跳列，并以
`last_observed_at` 作为迁移初值。

普通 GPT 的交接方式是：它只生成上述结构化 JSON 请求，读取 JSON 响应中的 `status`、`state_version`、`master_epoch`、`generation`、`receipt_id` 和 `error`，然后根据 `runtime.status` 再构造下一次 CAS 请求。它不能把自然语言中的“继续”“重试”当作浏览器盲重发许可。`MAY_HAVE_SUBMITTED`、`BLOCKED_AMBIGUOUS`、登录失效、验证码和 Windows 交互会话不可用都必须暂停并报告。

## 请求限制恢复边界

当前候选在 `chrome_use_actor_driver_v3.py` 提供显式的
`recover_rate_limit_dialog()`。它只在新鲜 Chrome Use 无障碍快照同时包含已知
请求限制文案时，解析唯一的确认按钮（`确定`、`好的`、`明白`、`明白了`、
`Got it`、`OK` 或 `Okay`）并点击一次；随后只读轮询页面，默认每 5 秒一次、
最长 300 秒。页面恢复到已认证状态才返回 `RECOVERED`。按钮缺失/重复、登录、
验证码、未知页面或超时均保持 `BLOCKED`，不会填充、点击发送、强制盲刷新或
重放之前不明确的浏览器提交。Master A 运行入口和双 Worker canary 的 auth
probe 都经过这一边界；Worker 自己的新页面在填入前发现限流时也经过同一边界。
如果限流是在 prompt 已填入但尚未发送时出现，驱动会恢复后解析新的 textbox
ref、重新填入同一 prompt，再解析 Send；不会把限流弹窗误判为编辑器故障并执行
key-event 发送修复。该恢复动作本身不代表消息已发送。

如果确认按钮调用本身失败，或确认后只读快照读取失败，驱动会返回
`BLOCKED/RATE_LIMIT_ACK_FAILED` 或
`BLOCKED/RATE_LIMIT_RECOVERY_READ_FAILED`；这两种情况都不会刷新、重试或把
异常当成可以继续发送的信号。对应证据见
`SCORP_V4_RATE_LIMIT_RECOVERY_FAILURE_BOUNDARY_5E60A3D.json`。

## 当前证据边界

- `TEST_VERIFIED`: V4 核心 191 个测试通过，GUI 桥接 498 个测试通过（含请求限制确认/等待/超时/单次刷新/新页面恢复/填入后恢复/确认与读取失败 fail-closed、安装依赖闭包和 bridge worker 单进程门禁回归），stdio connector → Named Pipe client → Named Pipe server → SQLite 的 Windows 进程回环、客户端/服务端回环和一次性 launcher 测试通过，暂停后 resume 的派发/assignment 恢复测试通过，compileall 和 `git diff --check` 通过；privileged broker 的分组测试也已通过。
- Master 物理重绑定的认证探针现在接入同一条请求限制恢复路径：先只读确认限流，再只点击一次已知“确定”控件，最多等待 300 秒，必要时最多刷新一次；恢复失败仍保持 `BLOCKED`，不会进入发送。Master 心跳现在先做只读物理健康检查；限流或认证阻塞不会创建新 epoch，浏览器失联才进入受控重绑定。Master epoch 变化会同步 fence 旧 Worker lease 和迟到结果。
- 恢复探针本身的传输异常也会收敛为 `AUTH_PROBE_FAILED` 结果，保留阻塞证据并停止后续动作，不把异常冒泡成可继续状态。
- Master 过期恢复现在为新的物理 session 生成一次性 `::resume-<nonce>` 标识，不再复用已标记为 `STALE` 的旧 session id；这不等于已经通过真实浏览器重绑定。
- `LIVE_VERIFIED`（局部 transport）：当前 Windows 上真实跑通了 11 项 stdio/Named Pipe 进程回环测试，证据见 `SCORP_V4_RUNTIME_CONNECTOR_WINDOWS_LOOP_424FFE3.json`；这不等于网页 GPT 已注册该 connector。
- `LIVE_VERIFIED`: 当前候选的真实 Chrome Use 仍为 `BLOCKED`；最近只读预检发现 active tab 与 inspected ChatGPT target 状态不一致，证据见 `SCORP_V4_LIVE_READONLY_PREFLIGHT_424FFE3.json`。此前 c205 ambiguous canary 证据保留在 `SCORP_V4_LIVE_CANARY_FAILURE_C20561B.json`，按 fail-closed 规则不能盲重发。
- 请求限制 live preflight：历史真实页面没有限制弹窗，因此只记录了不点击分支，证据见 `SCORP_V4_RATE_LIMIT_RECOVERY_LIVE_PREFLIGHT_E1E31D1.json`；确认按钮、等待窗口和单次刷新已由离线测试覆盖，但真实弹窗分支仍未获得 LIVE_VERIFIED。
- `ACCEPTED`: 未声明。生产安装、生产切换、24 小时 soak 和真实双 Worker 浏览器闭环均不由本记录自动批准。

进度/心跳和旧 SQLite 迁移的专门证据见 `SCORP_V4_PROGRESS_SEMANTICS_424FFE3.json`。

机器可读记录见同目录的 `SCORP_V4_FAST_RUNTIME_COMMAND_CORE_VALIDATION.json`。

本候选还在 Daemon 动作前增加了第二次租约核验：如果续租/栅栏检查失败，决策不会写入事件表，动作处理器不会被调用，健康状态为 BLOCKED。
