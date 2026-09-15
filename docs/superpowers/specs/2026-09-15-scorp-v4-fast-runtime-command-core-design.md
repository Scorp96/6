# SCORP V4 Fast Local Runtime Command Core Design

**Date:** 2026-09-15  
**Base:** `bd67ae78c874d2a5f897101097db0578f01a5c00`  
**Repository:** `Scorp96/6`  
**Status:** implementation design for the isolated `feature/v4-fast-runtime-command-core` worktree

## Purpose

The existing SCORP runtime already owns SQLite state, daemon leasing, Master
supervision, dynamic Worker scheduling, bounded local execution, and the
browser adapter. The missing boundary is a small local command surface that a
future local connector can call without turning GitHub or ChatGPT text into a
remote shell. This design adds that boundary while preserving the existing
authority chain and browser fail-closed rules.

The first transport is a direct Python API plus a one-request-per-line JSON
CLI over stdin/stdout. This candidate also includes an optional authenticated
Windows Named Pipe adapter that calls the same service without changing command
semantics. It is a library-level candidate only; no production listener or
ChatGPT connector is registered by this branch.

## Current architecture delta

The read-only audit found these facts on the base commit:

| Area | Current state | Delta required by this design |
| --- | --- | --- |
| SQLite authority | Project, task, lease, intent, event, evidence, and release tables exist. | Add operator control generations, bounded runtime observations, and command receipts. |
| Command boundary | No versioned local command envelope or bounded CLI exists. | Add `runtime_protocol.py`, `runtime_commands.py`, `operator_control.py`, and `runtime_cli.py`. |
| Mutation audit | Existing state transitions and activation decisions are durable, but there is no request-level receipt/idempotency table. | Persist every mutation request before returning success and replay the original response by `request_id`. |
| Generation fencing | Master/daemon/lease fencing exists; operator pause/cancel/supersede generation is absent. | Add operator and objective generations to control state and bind new intents/assignments to them. |
| Browser side effects | `PREPARED` and `MAY_HAVE_SUBMITTED` reconciliation exists. | Refuse new browser side effects when the intent generation is paused, cancelled, or superseded. |
| Local execution | `LocalExecutionAdapter` is allowlisted and assignment-bound. | Check the durable generation gate immediately before execution. |
| Arbiter | Terminal, auth, ambiguity, stale result, master resume, assignment, then stalled recovery. | Fence operator states first and move confirmed stall recovery ahead of assignment. |
| Liveness | The daemon currently updates `last_progress_at` for `IDLE`. | Persist bounded observation timestamps; `IDLE` updates observation only, never progress. |
| Evidence | Acceptance receipts and activation decisions are queryable only through internal APIs. | Add bounded read-only `evidence.query` with explicit filters and a hard limit. |

The current base tests pass before implementation: 111 V4 tests and the
focused daemon/supervisor runtime tests. This is baseline evidence only.

## Authority boundary

The authority chain remains:

```text
Windows process/task
    -> LocalDaemon daemon lease and daemon_epoch
    -> ActivationArbiter decision
    -> MasterSupervisor / Logical Master A
    -> dynamic Worker lease and assignment
    -> BrowserAdapter or LocalExecutionAdapter
```

The command surface is an operator/control-plane boundary inside the daemon.
It may request a durable project state transition, read bounded state, or
record a receipt. It cannot execute a shell command, click a browser, navigate
Chrome, or revive/replace/kill Master A. A command never bypasses a lease,
epoch, generation, authentication, CAPTCHA, browser reconciliation, or the
independent completion gate.

## Protocol

The request envelope is `scorp.runtime.command/1`:

```json
{
  "protocol_version": "scorp.runtime.command/1",
  "request_id": "req-uuid-or-stable-id",
  "command": "project.pause",
  "project_id": "project-id",
  "expected_state_version": 17,
  "expected_daemon_epoch": 4,
  "payload": {}
}
```

`request_id`, `command`, `project_id`, and an object `payload` are required.
An optional bounded `actor` identifies the logical caller. Mutation commands
also require both expected values and may bind Master/generation expectations.
Unknown top-level fields,
unknown commands, non-object payloads, malformed versions, and unsafe command
strings are rejected before any SQLite write.

The response envelope is `scorp.runtime.response/1`:

```json
{
  "protocol_version": "scorp.runtime.response/1",
  "request_id": "req-uuid-or-stable-id",
  "status": "OK",
  "command": "project.pause",
  "project_id": "project-id",
  "actor": "gpt-master",
  "daemon_epoch": 4,
  "state_version": 18,
  "master_epoch": 2,
  "generation": 3,
  "result": {},
  "receipt_id": "receipt-...",
  "error": null
}
```

`status` is one of `OK`, `BLOCKED`, `REJECTED`, or `ERROR`. A mutation
response has a durable `receipt_id`; a repeated identical `request_id` returns
the stored response without another mutation. A repeated `request_id` with a
different command or payload is rejected as an idempotency conflict.

## Command registry

| Command | Read/write | Behavior |
| --- | --- | --- |
| `runtime.status` | read | Return one bounded snapshot of daemon lease, project, operator state, master, Worker counts, ambiguity, progress, browser/auth blocker, and last decision. |
| `runtime.snapshot` | read | Return the bounded StateStore runtime snapshot. |
| `project.status` | read | Return the bounded project/control snapshot without log scanning. |
| `project.pause` | mutation | Increment operator generation, set `PAUSED`, prevent new assignment and external side effects, and leave ambiguous intents for reconciliation. |
| `project.resume` | mutation | Increment operator generation, set `ACTIVE`, and leave existing ambiguous effects unresolved until the normal reconciliation path proves them. |
| `project.cancel` | mutation | Increment operator and objective generations, set `CANCELLED`, and fence old assignments/intents. Existing ambiguous effects remain reconcilable only. |
| `project.supersede` | mutation | Increment both generations, replace the objective hash/contract generation, set `ACTIVE`, and mark old results stale/fenced. |
| `master.status` | read | Return logical Master identity, epoch, lease, physical binding-known state, pending results/reconciliation, last decision, and last progress. |
| `worker.status` | read | Return at most the two nonterminal logical Worker assignments and lease state. |
| `evidence.query` | read | Query only bounded machine evidence by project, assignment, intent, receipt, and UTC time range, with `limit <= 100`. |

## Durable schema and transaction rules

Add three tables without rewriting existing tables:

1. `operator_controls(project_id PRIMARY KEY, operator_state,
   operator_generation, objective_generation, objective_sha256, updated_at)`.
2. `runtime_observations(project_id PRIMARY KEY, progress_state,
   browser_semantic_state, auth_host_blocker, last_observed_at,
   last_progress_at, last_state_change_at, last_content_change_at,
   last_browser_success_at, last_browser_error_at)`.
3. `runtime_command_receipts(request_id PRIMARY KEY, receipt_id UNIQUE,
   project_id, command, actor, input_state_version, output_state_version,
   daemon_epoch, master_epoch, operator_generation, objective_generation,
   payload_sha256, status, reason, response_json, created_at)`.

`StateStore` creates the control row with `ACTIVE`, generation `0`, and the
contract objective hash when a project contract is created. Every mutation
checks the expected state version and daemon epoch, validates the current
control row, performs the state transition, inserts the receipt, and updates
the project state in one SQLite transaction. The browser or local execution
adapter is never called inside that transaction.

For a new browser or local-execution intent, the current operator and objective
generations are persisted into the intent payload. Immediately before the
external operation, the adapter checks that the payload generation still
matches the active control row. A paused/cancelled/superseded or mismatched
generation returns a durable blocked/rejected result and does not call the
external adapter. `MAY_HAVE_SUBMITTED` remains reconcilable and is never
blindly replayed.

## Arbiter and liveness semantics

The arbiter’s fixed order becomes:

1. terminal project;
2. cancel/supersede/pause/emergency operator fence;
3. explicit auth or host blocker;
4. ambiguous external effect reconciliation;
5. stale result fencing;
6. Master resume/replace;
7. pending Worker result wake-up;
8. lost Worker logical resume;
9. `STALLED_CONFIRMED` recovery;
10. ready task assignment;
11. idle heartbeat/no-op.

`IDLE` updates `last_observed_at` and `last_state_change_at` when applicable,
but never `last_progress_at`. `ACTIVE_GENERATING` may update progress; an
active browser process without content or response change is
`ACTIVE_NO_VISIBLE_PROGRESS`, not proof of progress. Auth and host blockers are
classified explicitly (`AUTH_REQUIRED`, `LOGIN_EXPIRED`,
`CAPTCHA_OR_CHALLENGE`, `HOST_INTERACTIVE_SESSION_UNAVAILABLE`, or
`BROWSER_SEMANTIC_ERROR`) and never converted into normal stall recovery.

## CLI and transport

`python -m master_a_dynamic_v4.runtime_cli` reads JSON request lines and writes
one JSON response line per request. It writes diagnostics only to stderr. It
does not import a shell, evaluate Python, run subprocesses, open URLs, click
Chrome, or expose arbitrary paths. The candidate `runtime_pipe.py` adapter
accepts only an authenticated Windows Named Pipe connection, enforces a fixed
project-scoped endpoint and a 64 KiB message limit, and passes the same parsed
envelope to `RuntimeCommandService.execute()`.

## Failure semantics and explicit non-goals

- SQLite write failure is `ERROR`/`BLOCKED` and prevents all side effects.
- CAS or epoch mismatch is `REJECTED` with a fencing reason.
- Unknown browser outcome remains `MAY_HAVE_SUBMITTED` or
  `BLOCKED_AMBIGUOUS`; no command surface retries it.
- Missing auth, CAPTCHA, or Windows interactive session is `BLOCKED`.
- Restart budget/circuit breaker and clock-gap detection receive explicit
  observation fields and extension points in this phase, but no production
  scheduled-task change is made and no claim of Windows service recovery is
  allowed.
- Named Pipe production registration, a packaged ChatGPT connector, HTTP,
  WebSocket, cloud services, paid model APIs, third Worker, arbitrary shell,
  arbitrary Git, arbitrary browser actions, and production cutover are outside
  this implementation.

## Verification boundary

Focused tests prove protocol, receipts, idempotency, CAS/fencing, generation
fencing, side-effect ordering, bounded queries, arbiter priority, liveness
timestamps, and a real authenticated Windows Named Pipe round trip. Existing
V4 and GUI bridge regressions must still pass. None of these tests is live
ChatGPT or production acceptance; real browser canary and production cutover
remain separately `LIVE_VERIFIED`/`ACCEPTED` gates.
