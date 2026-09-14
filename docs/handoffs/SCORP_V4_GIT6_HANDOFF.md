# SCORP V4 GitHub repository handoff

This file adapts the local candidate handoff to repository `Scorp96/6`.

## Source of truth for this repository

- Repository: `Scorp96/6`
- Start file: `GPT_START_HERE.md`
- V4 package: `scorp-agent/master_a_dynamic_v4/`
- Browser bridge: `scorp-agent/chatgpt-gui-bridge/`
- Offline validation: `scripts/run-candidate-validation.ps1`
- Default queue: `Scorp96/666`
- Legacy queue: `Scorp96/scorp-control-plane`, migration-only
- Current candidate: `49b0647b0bce3d721f8245c9b9d1160ddad245f6`
- Reasoning controller: **GPT-5.6 Sol**

The original local candidate was developed in `Scorp96/666`. This repository is
a source-and-runbook copy for another GPT to inspect and run. It does not carry
the local Windows runtime, browser profile, cookies, SQLite state, or scheduled
task registration.

## Runtime boundary

The existing bridge can control an already logged-in ChatGPT web session through
Windows MCP or Chrome Use. The V4 gateway adds SQLite state, dynamic two-slot
Worker scheduling and browser ambiguity recovery. The gateway is candidate code;
the production scheduled task is not changed by this repository.

Master session liveness is now part of the same SQLite authority. The
`master_sessions` table and `MasterWatchdog` lease API fence expired sessions,
advance `master_epoch` for a replacement, and emit an idempotent
`MASTER_RESUME_REQUIRED` event. That event is a handoff to the browser adapter;
it does not claim that a new browser window was opened or that authentication was
recovered.

The bridge facade exposes this lifecycle through `start_master_session()`,
`heartbeat_master_session()`, `watchdog_once()`, and
`end_master_session()`. `describe()` remains read-only; a monitor must call
`watchdog_once()` explicitly when it wants to advance an expired lease to a
resume decision.

For dynamic browser work, the facade also exposes `prepare_worker_intent()` and
`submit_worker_intent()`. Each live claim gets a deterministic intent and a
distinct `worker/worker-slot-*` channel. The injected browser driver may create
one new conversation per channel; the returned response is still only a
candidate until `WORK_RESULT/1` validation and independent acceptance succeed.

Assignment rows persist `base_state_version`, and the facade exposes
`load_worker_claims()` for coordinator restart recovery. This prevents a
crashed Master from losing the in-flight assignment identity or inventing a
replacement lease. The Chrome Use driver also normalizes transient root/query
redirects and rejects non-canonical `WEB:` placeholders while polling for the
real `/c/<id>` conversation URL.

The current candidate also validates structured, version-bound Worker results and
the real CSV workload fixture: T1 and T2 run in parallel, T3 waits for both, and
the independent completion validator returns `PASS`. Broker recovery and the
bridge functional heartbeat are covered by the candidate validation script.

The higher-level `MasterAController` now wraps this seam for an ordinary GPT:
`start()` acquires the logical Master lease, `apply_plan()` validates and records
the task DAG, `step()` fills at most two dynamic Worker slots and admits only
verified `WORK_RESULT/1`, and `completion()` delegates to the independent gate.
It is model-agnostic by design: GPT supplies structured plans and response
decoding, while SQLite and the gateway remain the authority. The controller's
integration test is offline and does not imply a production browser deployment.

The reproducible runner is
`scorp-agent/chatgpt-gui-bridge/tools/v4_live_two_worker_canary.py`. It has a
fail-closed send gate: without `--send-canary` it performs no browser action.
Use a fresh SQLite path and an already authenticated Chrome Use session when
running it.

## Acceptance language

Offline tests demonstrate code behavior only. Real browser evidence must be
collected from a fresh, harmless canary with a logged-in session. A CAPTCHA,
expired login, unavailable Windows interactive session, or ambiguous submit is
`BLOCKED`, not `PASS`. Historical files under
`docs/handoffs/historical-2026-09-14/` are retained for provenance and do not
prove this GitHub copy has been installed or run on a particular machine.

The fresh machine-readable record is
`docs/handoffs/SCORP_V4_GIT6_VALIDATION.json`. It is bound to the candidate
commit above. It explicitly records browser canary, GitHub write, production
cutover, and long-duration stability as unverified where no current evidence
exists.



