# GPT startup and execution handoff

This is the first file an ordinary GPT should read when opening repository
`Scorp96/6`. It explains the complete action sequence and the boundary between
offline code validation, a real browser canary, and production operation.

**Model identity:** use GPT-5.6 Sol as the reasoning controller for Master A and
for any dynamically assigned Worker conversation. The repository name `6` does
not mean GPT-6. If the active host cannot verify GPT-5.6 Sol, record
`BLOCKED`/`NOT_RUN` rather than silently substituting another model.

## What the system does

The intended flow is:

```text
user root objective
        ↓
one logical Master A
        ↓
SQLite contract + task dependency graph
        ↓
up to two dynamic Worker assignments
        ↓
bounded Worker results and independent verification
        ↓
browser submission intent persisted before I/O
        ↓
existing ChatGPT web session through Windows MCP or Chrome Use
        ↓
response capture, reconciliation, or explicit BLOCKED state
        ↓
deterministic acceptance gate
```

The local bridge is not a model API. It operates an already logged-in ChatGPT
browser session using the user's existing subscription. It must stop when the
Windows interactive session is unavailable, ChatGPT is logged out, a CAPTCHA is
shown, or the browser result is ambiguous.

**Web-GPT boundary:** opening this repository in ordinary ChatGPT does not
grant that chat local Windows, SQLite, Python, Chrome Use, or Windows MCP
permissions. If the chat has no local connector, it is in planning/review mode
and must report `WEB_GPT_DIRECT_LOCAL_CONTROL_UNAVAILABLE`. Use
`docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.md` and its JSON companion for the
human/operator handoff, then run the read-only preflight before claiming any
local capability.

如果接手者需要一份可直接上传到普通网页版 GPT 的单文件上下文，运行
`scorp-agent/chatgpt-gui-bridge/tools/v4_web_gpt_packet.py`，输出
`docs/handoffs/SCORP_V4_WEB_GPT_PACKET.json`。该工具只汇总当前候选身份和
本地预检；它不会给网页 GPT 增加 Windows 权限，也不会发送浏览器消息。

## First action: offline validation

Open PowerShell at the repository root and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\run-candidate-validation.ps1
```

The script runs the V4 tests, the full GUI bridge regression suite, and Python
compilation. It does not open a browser, send a ChatGPT message, contact
GitHub, or modify production state.

Equivalent manual commands are:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python -B -m unittest discover -s scorp-agent/master_a_dynamic_v4/tests -p 'test_*.py'
Push-Location scorp-agent/chatgpt-gui-bridge
python -B -m unittest discover -s tests -p 'test_*.py'
Pop-Location
python -B -m compileall -q scorp-agent/master_a_dynamic_v4 scorp-agent/chatgpt-gui-bridge
```

## First runtime probe (still browser-free)

After offline validation, use the repository-relative V4 probe before wiring a
real browser engine:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scorp-agent\chatgpt-gui-bridge\run-v4-candidate.ps1
```

The probe opens the local SQLite candidate and prints a JSON status record. It
reports `browser_io: NOT_ATTEMPTED`, uses a fixed first-release capacity of two
Workers, and never imports or starts the legacy JSON relay. It is a readiness
check, not a browser canary and not a production daemon. Set `SCORP_PYTHON` to
an existing Python executable when the `python` command is not on PATH.

### No-send composer diagnostic

Before authorizing any live canary send, use the dedicated fill-only
diagnostic. It opens an already-authenticated ChatGPT session, fills a fixed
harmless marker, allows the candidate's controlled-input key-event repair, and
then records whether a unique Send control is visible. It never creates a V4
browser intent, never clicks, and never sends. Use a fresh driver-state path
and an evidence path outside production state:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python -B .\scorp-agent\chatgpt-gui-bridge\tools\v4_browser_fill_diagnostic.py `
  --run-fill-only `
  --driver-state-path C:\ScorpAgent\v4-fill-diagnostic\driver.json `
  --evidence-path C:\ScorpAgent\v4-fill-diagnostic\evidence.json `
  --candidate-commit 5827941b60f51150d47a4bb0e26879edb66ede38 `
  --candidate-manifest C:\ScorpAgent\worktrees\v4-fast-runtime-command-core\docs\handoffs\SCORP_V4_FAST_RUNTIME_CANDIDATE_MANIFEST_5827941.json `
  --manifest-sha256 beb038ed5be504d3609b49adfa2475f1b9b805180801e9af8dd09420722d4a7c
```

`READY_TO_SEND_NO_CLICK` proves only that the composer repair exposed a Send
control in that session. `BLOCKED_SEND_CONTROL_MISSING` or an authentication
challenge must remain blocked. Neither result proves `/c/<id>` creation or a
Worker response; those require a separately reviewed, explicitly authorized
canary.

## Candidate V4 sequence

Use `scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py` as the explicit
candidate seam. A caller must provide a real, read-only authentication probe to
`ChatGptGuiEngine`; if that probe is absent or does not return
`AUTHENTICATED`, the intent is blocked before browser I/O.

The machine sequence is:

1. Create or verify the immutable root contract with `ensure_contract()`.
2. Add a dependency graph with `enqueue_graph()`.
3. Call `claim_workers()`; it returns at most two fenced assignments.
4. Execute only the paths and actions in each assignment.
5. For the V4 path, return a version-bound structured `WORK_RESULT` through
   `record_structured_worker_result()` (the payload must carry project/task/
   assignment identity, objective hash, base state version, candidate commit,
   evidence and acceptance coverage). `record_worker_result()` remains a
   legacy compatibility seam for imported V3 results.
6. Independently call `verify_worker_result()` before treating the task as
   accepted. A structured result moves the task to `ACCEPTED`; a legacy result
   may be retained for migration but cannot satisfy the new completion gate.
7. Call `prepare_browser_intent()` so the prompt intent is durable.
8. Call `submit_intent()`; a complete response is captured in SQLite, while an
   uncertain browser result remains recoverable and cannot be blindly retried.
9. After restart call `recover()` before creating a new browser submission.

The repository includes a repeatable real-code closure fixture in
`master_a_dynamic_v4/tests/test_real_code_acceptance.py`. It runs the CSV
reader, Decimal aggregator, and stable JSON report as T1/T2/T3, proves that
T1 and T2 use the two available Worker slots, waits for both before T3, and
requires the independent acceptance validator to return `PASS`. This proves
the local engineering loop; it does not prove a live browser submission.

The V4 package does not allow a Worker to rewrite the root contract, increase
capacity, bypass a lease, or declare final completion.

### Fast local runtime command core

The isolated `feature/v4-fast-runtime-command-core` branch adds a small,
versioned local command boundary. A browser or GPT connector sends one JSON
request per line to `master_a_dynamic_v4.runtime_cli`; the command service reads
SQLite and returns one JSON response per line. The request body cannot run a
shell, Python expression, Git command, filesystem command, or browser action.

Run it from the repository root with the bundled Windows runtime. The database
must already contain the project contract and the `--daemon-epoch` must be the
currently held local daemon epoch:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
@'
{"protocol_version":"scorp.runtime.command/1","request_id":"status-1","command":"runtime.status","project_id":"demo","actor":"gpt-master","payload":{}}
'@ | & C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -B -m master_a_dynamic_v4.runtime_cli `
  --database C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --project-id demo `
  --allowed-root C:\ScorpAgent\v4-runtime `
  --daemon-epoch 1
```

Reads are `runtime.status`, `runtime.snapshot`, `project.status`,
`master.status`, `worker.status`, and bounded `evidence.query`. Responses
include `actor`, `daemon_epoch`, `state_version`, `master_epoch`, and the
operator `generation`. Mutations are `project.pause`, `project.resume`,
`project.cancel`, `project.supersede`, and `project.emergency_stop`; each mutation must include both
`expected_state_version` and `expected_daemon_epoch` and may bind
`expected_master_epoch` and `expected_generation`. New projects use the
explicit operator state `RUNNING`; old imported `ACTIVE` rows remain readable
as a compatibility value. Reusing a `request_id` returns the durable receipt;
reusing it with a different command or payload is rejected. A paused,
cancelled, superseded, stale, or ambiguous browser intent cannot be blindly
replayed. `project.supersede` remains durably `SUPERSEDED` until a fresh
CAS-bound `project.resume` admits the replacement objective. Daemon supervision state also exposes bounded restart backoff and a
restart-budget circuit; a failed SQLite intent fence returns
`SQLITE_WRITE_FAILED` without attempting browser I/O.

This command core is a local feature-branch implementation and a testable
handoff surface. Its focused tests and offline regression do not prove live
ChatGPT browser acceptance or production cutover.

The current candidate also has a fresh Windows process-loop receipt for the
restricted stdio connector and authenticated Named Pipe transport at
`docs/handoffs/SCORP_V4_RUNTIME_CONNECTOR_WINDOWS_LOOP_5827941.json`. This proves
only the local transport loop; it does not register the connector with ordinary
web ChatGPT or grant it local permissions automatically.

The candidate also contains an optional Windows Named Pipe adapter at
`scorp-agent/master_a_dynamic_v4/runtime_pipe.py`. It requires a local
authenticated `multiprocessing.connection` client, uses a fixed project-scoped
`\\.\pipe\scorp-runtime-<project>` endpoint, and accepts only the same bounded
`scorp.runtime.command/1` envelope. The matching bounded client is
`runtime_pipe_client.py`: it validates the request and response, performs one
exchange, and never retries a timeout. The transport is not registered as a
production listener and does not give an ordinary web GPT direct Windows
access. The candidate also contains `runtime_connector_cli.py`, a fail-closed
stdio facade that binds one configured actor and forwards only this protocol to
the Named Pipe client. It is a connector building block, not proof that a
ChatGPT host has registered or authorized it. A future approved local
connector may use this facade after its own authentication, process
supervision, and evidence gates are verified.

For a bounded local test, `runtime_pipe_cli.py --once` starts exactly one
authenticated connection and exits after returning one response. The auth key
must be supplied through an environment variable; it is never placed in the
JSON request. Example:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
$env:SCORP_RUNTIME_PIPE_AUTHKEY = 'use-a-local-secret-at-least-16-bytes'
& C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -B -m master_a_dynamic_v4.runtime_pipe_cli `
  --database C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --project-id demo `
  --allowed-root C:\ScorpAgent\v4-runtime `
  --daemon-epoch 1 `
  --once
```

This command is a candidate transport check. It does not install a Windows
task, open Chrome, send a ChatGPT message, or grant arbitrary local execution.
The `--daemon-epoch` argument is optional for this pipe launcher. When omitted,
the launcher reads the current SQLite lease epoch; a supplied value remains an
explicit stale-epoch fence check.

The current Phase 0 field audit is recorded in
`docs/handoffs/SCORP_V4_PHASE0_RUNTIME_AUDIT.md`. At the latest inspection,
the candidate is `TEST_VERIFIED`; the read-only browser preflight cleared, but
the current single-worker canary is `LIVE_VERIFIED=BLOCKED`: the c205 run
recorded a conversation URL, but read-only reconciliation found an older
diagnostic response and no c205 token. No blind retry is permitted; see
`docs/handoffs/SCORP_V4_LIVE_CANARY_FAILURE_C20561B.json`.

The V4 daemon entrypoint may omit `--daemon-epoch`; it then acquires the
current SQLite lease epoch. A fixed epoch is accepted only as an explicit
fence check. The installer therefore should not bake an old epoch into a
long-lived Scheduled Task. Daemon supervision records restart/recovery counts,
bounded backoff, and a restart-budget circuit in SQLite.

### One-plan Master A runtime

The repository now includes an operator-gated runtime for the actual browser
bridge:
`scorp-agent/chatgpt-gui-bridge/tools/v4_master_controller_runtime.py`.
Prepare a plan using
`docs/handoffs/SCORP_V4_MASTER_PLAN_TEMPLATE.json`, replace every placeholder,
and review the paths and acceptance criteria. Then run it from the repository
root with a fresh database and driver-state path:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python .\scorp-agent\chatgpt-gui-bridge\tools\v4_master_controller_runtime.py `
  --plan-json .\docs\handoffs\SCORP_V4_MASTER_PLAN_TEMPLATE.json `
  --database-path C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --driver-state-path C:\ScorpAgent\v4-runtime\driver.json `
  --allowed-root C:\ScorpAgent\workspaces\project `
  --candidate-commit 5827941b60f51150d47a4bb0e26879edb66ede38 `
  --candidate-manifest C:\ScorpAgent\worktrees\v4-fast-runtime-command-core\docs\handoffs\SCORP_V4_FAST_RUNTIME_CANDIDATE_MANIFEST_5827941.json `
  --manifest-sha256 beb038ed5be504d3609b49adfa2475f1b9b805180801e9af8dd09420722d4a7c `
  --send
```

The script refuses to open Chrome unless `--send` and an exact candidate
commit/manifest binding are present. It loads one
structured root plan, starts one logical Master A, creates at most two dynamic
Worker browser intents, accepts only `WORK_RESULT/1`, and routes any local
execution request through the path and Git-worktree gates. A missing login,
CAPTCHA, unavailable interactive session, ambiguous browser result, or invalid
plan is reported as a blocker; no login or verification challenge is bypassed.
This entry point is a finite run with a cycle cap. `MasterWatchdog` remains the
durable monitor signal for a separate local supervisor and is not silently
represented as a continuously running service.

### Chrome Use session lifecycle

V4 records a lifecycle row for every driver session and migrates older
driver-state files by adding `sessions` without deleting existing bindings.
`MASTER` and `WORKER` sessions are persistent resources and cannot be stopped
by ordinary cleanup. Diagnostic sessions must be registered as `DIAGNOSTIC` and
retired explicitly; the driver stops them only after their active turn
references are gone. A timeout during `session stop` is recorded as
`CLEANUP_BLOCKED` and is never silently retried. Chrome labels and tab colors
are identifiers only; they do not carry status semantics. The lifecycle record,
not color or natural-language claims, is the authority for ACTIVE, RETIRED, and
cleanup state.

To inspect or retire one named session, use
`scorp-agent/chatgpt-gui-bridge/tools/v4_session_lifecycle.py`. `list` is
read-only. `retire` requires an exact candidate manifest binding and either a
single `--session` or a single `--turn-id`; `--stop` affects only that named
session. The command has no global cleanup option and never invokes
`session prune` or `close --all`:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python -B .\scorp-agent\chatgpt-gui-bridge\tools\v4_session_lifecycle.py list `
  --driver-state-path C:\ScorpAgent\v4-runtime\driver.json
```

Use the `retire` form only after reviewing the lifecycle report and the exact
candidate manifest. A persistent Master or Worker stop requires the explicit
`--allow-persistent` flag and is still recorded as a separate cleanup action.
For a retirement receipt, add `--evidence-path C:\ScorpAgent\v4-runtime\session-lifecycle.json`; the command writes an atomic JSON record with `artifact_sha256`.

### Monitor and privileged recovery boundaries

The bridge watchdog checks both the scheduler-owned process tree and the
bridge's functional `health.json` heartbeat. A stale, missing, malformed, or
`ERROR` heartbeat is reported as `WATCHDOG_STALE_HEALTH` and the scheduled task
is restarted; an orphaned process tree remains `WATCHDOG_ORPHAN_BLOCKED`.

The privileged broker keeps a durable request ledger. If it dies after marking
an operation `INFLIGHT`, recovery must call `RequestLedger.reconcile_inflight()`
with an external evidence reference and either `DONE_CONFIRMED` plus the
verified result or `BLOCKED_AMBIGUOUS`. There is no automatic retry of an
uncertain privileged side effect. `BLOCKED_AMBIGUOUS` is an auditable stop
state, not a successful operation.

The V4 SQLite core also owns the Master A liveness lease in
`master_sessions`. `MasterWatchdog.start()` creates one logical session,
`heartbeat()` renews it, and `run_once()` returns `MASTER_ACTIVE`,
`RESUME_REQUIRED`, or `TERMINAL`. An expired session is fenced by a new
`master_epoch`; the watchdog records one idempotent `MASTER_RESUME_REQUIRED`
event for the physical browser adapter to consume. The watchdog does not open a
browser or bypass authentication, so `RESUME_REQUIRED` is a durable handoff
signal rather than proof that a new ChatGPT window has already been created.

For a local monitor process, import `master_a_dynamic_v4.MasterSupervisor` and
call `run_loop()` with an interval and either a stop event or an iteration
bound. It renews an active Master lease. If
the lease expired, it first acquires a new `master_epoch`, then invokes the
injected physical-session `rebind_callback`. Without that callback it returns
`RESUME_REQUIRED` without advancing the epoch; if the callback fails it ends
the unbound epoch when supported and returns `BLOCKED`. The supervisor
does not open Chrome, create a ChatGPT conversation, or bypass login itself.
Windows Task Scheduler or another local monitor may call this seam, while the
browser adapter remains responsible for authentication, session creation, and
read-only reconciliation of ambiguous submits.

`run_loop()` returns `SupervisorLoopResult` with every decision and a stop
reason. It stops on `TERMINAL`, `BLOCKED`, or an unresolved
`RESUME_REQUIRED`, so a Windows monitor can report the blocker instead of
creating an uncontrolled retry loop.

For a directly runnable local monitor, use
`scorp-agent/chatgpt-gui-bridge/tools/v4_master_supervisor_runtime.py`. It
attaches to an existing SQLite Master lease, records one JSON line per decision,
and performs no browser submission:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python .\scorp-agent\chatgpt-gui-bridge\tools\v4_master_supervisor_runtime.py `
  --database-path C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --allowed-root C:\ScorpAgent\workspaces\project `
  --decision-log C:\ScorpAgent\v4-runtime\supervisor.jsonl `
  --max-iterations 1
```

Use `--forever` only when a host process is intentionally supervising the
monitor. Add `--rebind --driver-state-path ... --executable ...` to enable the
read-only physical browser snapshot/rebind callback. This callback verifies an
existing authenticated conversation and updates binding evidence; it never
creates a chat, fills a composer, or clicks Send. A missing database, invalid
lease, failed authentication probe, or ambiguous browser snapshot exits as a
reported blocker.

The existing browser bridge provides the safe callback implementation in
`scorp-agent/chatgpt-gui-bridge/v4_physical_rebind.py`. It proves the existing
Master conversation with an authenticated, read-only snapshot and records the
binding evidence. It does not send a prompt during rebind. A caller can inject
`ReadOnlyBrowserRebinder(...)` as `rebind_callback` when constructing the
supervisor.

### Structured Worker result contract

The machine result validator is in
`scorp-agent/master_a_dynamic_v4/work_result.py`. It rejects results whose
`project_id`, `assignment_id`, `task_id`, `objective_sha256`, or
`base_state_version` does not match the live assignment. `COMPLETE` also
requires non-empty evidence and acceptance coverage. If Master A advances the
project state after a Worker is assigned, the old result is fenced rather than
merged into the newer task graph.

### Master A controller workflow

For an ordinary GPT handoff, use `master_a_dynamic_v4.MasterAController` as the
control surface instead of manually sequencing the lower-level gateway calls.
The controller accepts a structured plan produced by GPT, but never accepts
natural-language claims as authority. Its normal sequence is:

```text
start(root_contract, acceptance_contract)
        -> apply_plan({project_id, master_identity: "A", tasks: [...]})
        -> repeat run_cycles(prompt_factory, optional response_decoder)
        -> watchdog_once() / heartbeat()
        -> completion(candidate_commit, artifact_hashes)
```

`run_cycles()` repeatedly invokes `step()` with a hard cycle cap. `step()` first
reloads active claims, then fills free capacity up to two Worker
slots. It persists each assignment-bound browser intent, refuses to resubmit an
intent in `MAY_HAVE_SUBMITTED`, `CONFIRMED_SUBMITTED`, or
`BLOCKED_AMBIGUOUS`, and admits a response only when the decoder returns a
version-bound `WORK_RESULT/1` that the scheduler verifies. When no custom decoder
is supplied, `decode_work_result_response()` accepts only a standalone JSON
object or a fenced JSON object; explanatory prose is rejected. A custom decoder
is an injected boundary for the existing browser transport; it is not a model
API and does not grant local shell permissions to a web GPT.

The local Execution Plane is exposed as `LocalExecutionAdapter`. It is a
fail-closed seam for bounded Worker work: only an explicit Python module
allowlist can run, `shell=False` is enforced, assignment/epoch/lease identity
and path scope are checked before process start, output is bounded, and the
receipt records exit code, timestamps, output hashes, and artifact hashes. It
does not replace the legacy executor or authorize production writes. The
current real-code check runs the allowlisted CSV CLI in an isolated directory;
a live browser Worker still needs its own authorized canary and structured
`WORK_RESULT/1` response.

When a Worker result contains an optional `execution_request`,
`MasterAController` routes it through the injected `LocalExecutionAdapter`
before admitting the result. The request is restricted to module, argument,
working-directory, resource-scope, access-mode, and timeout fields; the
adapter rechecks the assignment's own `resource_scope`, not only the global
allowed root. A successful receipt is added to the candidate result and its
content hash is recomputed before SQLite verification. If no adapter is
configured, or the local process fails, the result is rejected and the task
does not advance.

Local execution uses the same durable intent/outbox boundary as browser I/O.
The process is never started before `MAY_HAVE_SUBMITTED` is committed. A
restart that finds an unresolved local execution intent reports
`LOCAL_EXECUTION_RECONCILIATION_REQUIRED` and does not rerun the process;
there is no fabricated exactly-once claim for an OS subprocess.

Write-capable requests additionally require `GitWorktreeManager`. It verifies
that the repository is clean, resolves the requested base commit, creates a
detached worktree inside the assignment scope, and checks the new worktree
HEAD before the Worker process starts. Existing targets, dirty repositories,
base drift, and scope escapes are rejected; the manager never deletes a target
or force-resets a user's repository.

The implementation is in
`scorp-agent/master_a_dynamic_v4/master_controller.py`, with an actual
`V4BridgeGateway` integration test in
`scorp-agent/chatgpt-gui-bridge/tests/test_v4_bridge_gateway.py`. This proves
the local two-Worker structured loop with an injected engine; the existing live
canary remains the only real browser evidence and uses harmless fixed markers.

## Master A control surface

The logical Master A must use the gateway facade instead of opening SQLite and
editing tables itself. After `ensure_contract()` succeeds, the handoff order is:

1. Call `start_master_session(session_id)` and retain its `master_epoch`.
2. Send periodic `heartbeat_master_session()` calls and let a separate monitor
   call `watchdog_once()`; `RESUME_REQUIRED` means the browser adapter must
   rebind a new physical session before work resumes.
3. Read `describe()` and submit a validated proposal with
   `commit_master_proposal()` using the
   observed `state_version` and `master_epoch`.
4. For a task graph, call `enqueue_graph()` and then `claim_workers(limit=2)`.
5. For each live claim, call `prepare_worker_intent()` or
   `submit_worker_intent()`. This persists an assignment-bound prompt on a
   distinct `worker/worker-slot-*` channel; an injected browser engine can use
   the missing conversation URL to create a new Worker chat. The response must
   still be validated as `WORK_RESULT/1` and admitted with
   `record_structured_worker_result()`.
6. After a coordinator or browser restart, call `load_worker_claims()` before
   dispatching. It rehydrates the assignment, lease token and persisted base
   state version without creating a new lease or replaying a browser action.
7. Re-read state after each verified Worker result and submit the next proposal
   or a `REPLAN` proposal. A stale version or epoch returns a conflict/fenced
   result and must trigger a fresh read, never a blind retry.

The facade makes the GPT-to-transaction boundary explicit; it does not make
model reasoning or browser conversation creation automatic.

Before reporting completion, call `evaluate_completion()` with the candidate
commit and artifact hashes. The returned status must be `PASS` and its blockers
must be empty. `BLOCKED`, `FAIL`, or `NOT_RUN` must remain non-complete and be
reported with the returned blocker list. This check is read-only; a GPT message
claiming `COMPLETE` cannot change the authoritative project state.

## Existing browser bridge

The legacy bridge source is under `scorp-agent/chatgpt-gui-bridge/`. Its current
Windows runtime, when separately installed on the user's machine, has commonly
been located at:

```text
C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe
```

That runtime path is machine-local and is deliberately not stored in this
repository. The bridge may use either the existing Windows MCP transport or
Chrome Use according to its explicit configuration. The repository source does
not grant login, CAPTCHA, or arbitrary computer permissions.

`scorp-agent/chatgpt-gui-bridge/run-bridge.ps1` is a legacy V3 compatibility
launcher. It still points at the old JSON relay and `Scorp96/scorp-control-plane`;
do not use it as proof that the V4 SQLite authority is running. A separate
migration is required before that launcher can be replaced.

To inject an existing Windows MCP or Chrome Use actor driver into V4, use
`v4_browser_engine.build_v4_browser_engine(driver, auth_probe=..., response_parser=...)`.
The authentication probe is read-only. The response parser is required for a
`RESPONSE_CAPTURED` result; without it, the adapter returns `SUBMITTED` with a
conversation URL and preserves the snapshot for reconciliation. A page snapshot
alone is never accepted as structured completion evidence.

## Browser canary procedure

Only run a real browser canary after the user explicitly authorizes sending the
test prompt. Use a harmless, non-sensitive analysis task. The canary should
open two separate ChatGPT conversations, assign Worker 1 and Worker 2 distinct
bounded subtasks, collect both responses, and record:

- conversation URLs and binding epochs;
- assignment IDs and lease tokens (hashes only in reports);
- submitted/response-captured states;
- evidence hashes and acceptance result;
- any login, CAPTCHA, timeout, or ambiguous-submit blocker.

Do not use real customer data, credentials, private files, or production
commands in the canary. A successful simulated test is not evidence of a real
browser canary.

The current candidate's single-worker live canary is fail-closed after
ambiguous Chrome Use conversation binding; its receipt is
`docs/handoffs/SCORP_V4_LIVE_CANARY_FAILURE_C20561B.json`. The newest read-only target-state receipt is `docs/handoffs/SCORP_V4_LIVE_READONLY_PREFLIGHT_5827941.json`. Read-only
reconciliation found an older diagnostic response and no c205 token, so it was
not blindly retried. A separate read-only preflight is required before any new
canary.

When a read-only auth probe sees the known `请求过于频繁` / `Too many requests`
dialog, the current candidate may perform one bounded recovery: click exactly
one recognized acknowledgement control (`确定`, `明白了`, `Got it`, `OK`, or
`Okay`), then poll read-only snapshots for at most 300 seconds (normally every
5 seconds); if the window expires while the dialog remains, it may refresh the
page once and perform one final read-only check. It does not force a prompt
resend or bypass login/CAPTCHA. Missing
or ambiguous acknowledgement controls, authentication challenges, and timeout
remain `BLOCKED`.

For a new live check, use
`scorp-agent/chatgpt-gui-bridge/tools/v4_live_two_worker_canary.py`. It refuses
to send by default. A deliberate run must provide `--send-canary`, an already
authenticated Chrome Use session, a new empty SQLite path, and an evidence
path. The two prompts are fixed marker-only messages; the script never reads
repository files as Worker input.

## Production boundary

Do not replace the Windows scheduled task, copy state into production, or change
the live browser bridge merely because the offline tests pass. Production
cutover requires a separate candidate manifest, matching install receipt,
fresh browser evidence, and explicit authorization. Historical evidence is
under `docs/handoffs/historical-2026-09-14/`; it describes an earlier local
candidate and must not be presented as evidence for this GitHub copy.
The root `docs/handoffs/SCORP_V4_VERIFICATION.json` is kept only because an
upstream test contract reads that path; use
`docs/handoffs/SCORP_V4_FAST_RUNTIME_COMMAND_CORE_VALIDATION.json` for the current repository-copy
validation record.

## What to report after every run

Report the repository commit, exact commands, test counts, elapsed time, real
browser status, production status, blockers, and unverified items. Keep
`PASS`, `FAIL`, `BLOCKED`, and `NOT_RUN` separate.



## Current candidate identity (2026-09-15)

Use these paths when an ordinary GPT or operator takes over this repository:

- Current code candidate: `5827941` (`fix: gate master heartbeat on physical browser health`) on `feature/v4-fast-runtime-command-core`; the five-minute wait and one-refresh boundary, fail-closed rate-limit transport errors, durable heartbeat/progress separation, and lost-worker detection are recorded in the current candidate evidence.
- Latest rate-limit failure-boundary evidence: `docs/handoffs/SCORP_V4_RATE_LIMIT_RECOVERY_FAILURE_BOUNDARY_5827941.json`.
- Isolated worktree: `C:\ScorpAgent\worktrees\v4-fast-runtime-command-core`.
- Current validation record: `docs/handoffs/SCORP_V4_FAST_RUNTIME_COMMAND_CORE_VALIDATION.json`.
- Current live blocker receipt: `docs/handoffs/SCORP_V4_LIVE_READONLY_PREFLIGHT_5827941.json`; prior ambiguous canary receipt: `docs/handoffs/SCORP_V4_LIVE_CANARY_FAILURE_C20561B.json`.
- Current browser driver state used by that receipt: `C:\ScorpAgent\v4-c20561b-live-single\driver.json`.

The current result is `TEST_VERIFIED`; current-candidate `LIVE_VERIFIED` is blocked by ambiguous Chrome Use browser state, and it is not `ACCEPTED`.
The current candidate has a fail-closed browser send blocker and no new
conversation. It does not prove a real repository task over ChatGPT, live
crash/restart recovery, scheduled-task registration, production cutover, or
long-duration unattended stability. Do not claim those gates pass.

An ordinary web GPT cannot infer or invoke local Windows, SQLite, Python, or
Chrome Use control merely by reading GitHub. It must either work through an
operator-owned local connector or provide a structured plan for the local runner.
The first action is always the offline validation command above; browser sends
require an explicit `--send` gate and a fresh isolated state path.
