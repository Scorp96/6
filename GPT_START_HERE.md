# GPT startup and execution handoff

This is the first file an ordinary GPT should read when opening repository
`Scorp96/6`. It explains the complete action sequence and the boundary between
offline code validation, a real browser canary, and production operation.

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
5. Submit a `HANDOFF` or `BLOCKER` with `record_worker_result()`.
6. Independently call `verify_worker_result()` before treating the task as
   complete.
7. Call `prepare_browser_intent()` so the prompt intent is durable.
8. Call `submit_intent()`; a complete response is captured in SQLite, while an
   uncertain browser result remains recoverable and cannot be blindly retried.
9. After restart call `recover()` before creating a new browser submission.

The V4 package does not allow a Worker to rewrite the root contract, increase
capacity, bypass a lease, or declare final completion.

## Master A control surface

The logical Master A must use the gateway facade instead of opening SQLite and
editing tables itself. After `ensure_contract()` succeeds, the handoff order is:

1. Read `describe()` and call `acquire_master_epoch(expected_epoch=...)`.
2. Submit a validated proposal with `commit_master_proposal()` using the
   observed `state_version` and `master_epoch`.
3. For a task graph, call `enqueue_graph()` and then `claim_workers(limit=2)`.
4. Re-read state after each verified Worker result and submit the next proposal
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

## Production boundary

Do not replace the Windows scheduled task, copy state into production, or change
the live browser bridge merely because the offline tests pass. Production
cutover requires a separate candidate manifest, matching install receipt,
fresh browser evidence, and explicit authorization. Historical evidence is
under `docs/handoffs/historical-2026-09-14/`; it describes an earlier local
candidate and must not be presented as evidence for this GitHub copy.
The root `docs/handoffs/SCORP_V4_VERIFICATION.json` is kept only because an
upstream test contract reads that path; use
`docs/handoffs/SCORP_V4_GIT6_VALIDATION.json` for the current repository-copy
validation record.

## What to report after every run

Report the repository commit, exact commands, test counts, elapsed time, real
browser status, production status, blockers, and unverified items. Keep
`PASS`, `FAIL`, `BLOCKED`, and `NOT_RUN` separate.
