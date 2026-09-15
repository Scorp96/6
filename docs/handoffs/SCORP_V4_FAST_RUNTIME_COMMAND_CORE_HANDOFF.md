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

普通 GPT 的交接方式是：它只生成上述结构化 JSON 请求，读取 JSON 响应中的 `status`、`state_version`、`master_epoch`、`generation`、`receipt_id` 和 `error`，然后根据 `runtime.status` 再构造下一次 CAS 请求。它不能把自然语言中的“继续”“重试”当作浏览器盲重发许可。`MAY_HAVE_SUBMITTED`、`BLOCKED_AMBIGUOUS`、登录失效、验证码和 Windows 交互会话不可用都必须暂停并报告。

## 请求限制恢复边界

当前候选在 `chrome_use_actor_driver_v3.py` 提供显式的
`recover_rate_limit_dialog()`。它只在新鲜 Chrome Use 无障碍快照同时包含已知
请求限制文案时，解析唯一的确认按钮（`确定`、`好的`、`明白`、`明白了`、
`Got it`、`OK` 或 `Okay`）并点击一次；随后只读轮询页面，默认每 5 秒一次、
最长 300 秒。页面恢复到已认证状态才返回 `RECOVERED`。按钮缺失/重复、登录、
验证码、未知页面或超时均保持 `BLOCKED`，不会填充、点击发送、强制盲刷新或
重放之前不明确的浏览器提交。Master A 运行入口和双 Worker canary 的 auth
probe 都经过这一边界；该恢复动作本身不代表消息已发送。

## 当前证据边界

- `TEST_VERIFIED`: V4 核心 173 个测试通过，GUI 桥接 487 个测试通过（含请求限制确认/等待/超时/单次刷新、安装依赖闭包和 bridge worker 单进程门禁回归），stdio connector → Named Pipe client → Named Pipe server → SQLite 的 Windows 进程回环、客户端/服务端回环和一次性 launcher 测试通过，暂停后 resume 的派发/assignment 恢复测试通过，compileall 和 `git diff --check` 通过；privileged broker 的分组测试也已通过。
- `LIVE_VERIFIED`（局部 transport）：当前 Windows 上真实跑通了 11 项 stdio/Named Pipe 进程回环测试，证据见 `SCORP_V4_RUNTIME_CONNECTOR_WINDOWS_LOOP_12FC17B.json`；这不等于网页 GPT 已注册该 connector。
- `LIVE_VERIFIED`: 当前候选 c205 单 Worker canary 已在真实 Chrome Use 中记录 conversation URL，但只读核对得到旧诊断回复且没有 c205 token；证据见 `SCORP_V4_LIVE_CANARY_FAILURE_C20561B.json`，按 fail-closed 规则不能盲重发。
- 请求限制 live preflight：当前真实页面没有限制弹窗，因此只记录了不点击分支，证据见 `SCORP_V4_RATE_LIMIT_RECOVERY_LIVE_PREFLIGHT_E1E31D1.json`；确认按钮、等待窗口和单次刷新已由离线测试覆盖，但真实弹窗分支仍未获得 LIVE_VERIFIED。
- `ACCEPTED`: 未声明。生产安装、生产切换、24 小时 soak 和真实双 Worker 浏览器闭环均不由本记录自动批准。

机器可读记录见同目录的 `SCORP_V4_FAST_RUNTIME_COMMAND_CORE_VALIDATION.json`。
