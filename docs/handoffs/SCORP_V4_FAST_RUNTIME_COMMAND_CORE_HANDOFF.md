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

当前候选还提供 `scorp-agent/master_a_dynamic_v4/runtime_pipe.py`。它使用固定的
`\\.\pipe\scorp-runtime-<project>` Windows Named Pipe、认证 `authkey` 和 64 KiB
消息上限，只转发同一版本化 Runtime JSON 协议。它是库级候选 transport，尚未注册
生产 listener，也没有让普通网页 GPT 自动获得 Windows 权限；正式 connector 仍需
单独完成身份、进程监督和证据门禁。

普通 GPT 的交接方式是：它只生成上述结构化 JSON 请求，读取 JSON 响应中的 `status`、`state_version`、`master_epoch`、`generation`、`receipt_id` 和 `error`，然后根据 `runtime.status` 再构造下一次 CAS 请求。它不能把自然语言中的“继续”“重试”当作浏览器盲重发许可。`MAY_HAVE_SUBMITTED`、`BLOCKED_AMBIGUOUS`、登录失效、验证码和 Windows 交互会话不可用都必须暂停并报告。

## 当前证据边界

- `TEST_VERIFIED`: V4 核心 159 个测试通过，GUI 桥接 475 个测试通过，Named Pipe Windows 回环测试通过，compileall 和 `git diff --check` 通过；privileged broker 的分组测试也已通过。
- `LIVE_VERIFIED`: 当前只读 Chrome 预检仍显示 ChatGPT “请求过于频繁”；本分支未重新点击真实 ChatGPT 提交，不能盲重发。
- `ACCEPTED`: 未声明。生产安装、生产切换、24 小时 soak 和真实双 Worker 浏览器闭环均不由本记录自动批准。

机器可读记录见同目录的 `SCORP_V4_FAST_RUNTIME_COMMAND_CORE_VALIDATION.json`。
