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
- Current candidate: `5c4a127f03642778799c65abdf04aa1fbdddcb9e`
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

`master_a_dynamic_v4.MasterSupervisor` is the local monitor seam for this
handoff. It renews a live lease, acquires a fresh epoch after expiry, and calls
an injected physical-session rebind callback. It does not open Chrome or infer
that ChatGPT authentication succeeded; missing or failed rebind remains
`RESUME_REQUIRED` or `BLOCKED`.

The directly runnable monitor is
`scorp-agent/chatgpt-gui-bridge/tools/v4_master_supervisor_runtime.py`. It
attaches to an existing SQLite lease, appends machine-readable decisions to a
JSONL journal, and forbids browser submission. `--rebind` enables only the
read-only physical snapshot/rebind callback; it does not create a conversation
or click Send. The default run is one bounded polling pass; `--forever` is an
explicit choice for a host process that owns the lifecycle.

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

Before using that send-gated runner, use
`scorp-agent/chatgpt-gui-bridge/tools/v4_browser_fill_diagnostic.py` with
`--run-fill-only`. It fills one fixed marker in a fresh session, applies the
same key-event repair as the candidate driver, reports
`READY_TO_SEND_NO_CLICK` or a blocked diagnostic, and never creates a V4
intent or clicks Send. A ready result is a composer-control check only; it is
not evidence of `/c/<id>` creation or Worker response capture.

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

The isolated installation and short local recovery evidence are now stored in
`SCORP_V4_GIT6_CANDIDATE_MANIFEST_5c4a127.json`,
`SCORP_V4_GIT6_AC10_AC12_EVIDENCE_5c4a127.json`, and
`SCORP_V4_GIT6_AC12_SHORT_SOAK_5c4a127.json`. The installed monitor probe is
recorded in `SCORP_V4_GIT6_SUPERVISOR_RUNTIME_5c4a127.json`. AC10 covers a
39-file detached
candidate install with unchanged protected-root hashes. AC12 covers a 10.17 s
run with 47 cycles, two Worker slots, 139 slot reuses, two scheduler recoveries, and
zero duplicate submits or errors. The browser engine is injected and local;
this does not upgrade the real-browser or production status.

The web-GPT handoff and read-only local capability report are
`docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.md`,
`docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.json`, and
`SCORP_V4_GIT6_PREFLIGHT_5c4a127.json`. The preflight reports
`web_gpt_direct_local_control=UNAVAILABLE` until an actual local connector or
operator-owned host is present.

## R4 live-browser finding

A user-provided R4 report found a real-path failure after the composer was located: the previous serial controller waited on Worker-1, left its intent at `MAY_HAVE_SUBMITTED` with no conversation URL or response, and never dispatched Worker-2. The current candidate includes the serial-dispatch fix, shared driver-state locking, controlled-composer repair, accessibility-aware selector matching, and safe post-fill diagnostics containing the editor reference and prompt hash. The fill-only diagnostic is available, but the actual `fill → send → /c/<id>` path remains `FAIL/BLOCKED/NOT_RUN` until a fresh harmless canary produces raw browser evidence.
