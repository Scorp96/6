# SCORP V4 Local Daemon Handoff

This document describes the current V4 daemon entrypoint and its verification
boundary. It is an operational handoff, not a production acceptance receipt.

## Components

- `scorp-agent/master_a_dynamic_v4/activation_arbiter.py` contains the
  side-effect-free deterministic priority decision.
- `scorp-agent/master_a_dynamic_v4/daemon.py` runs a bounded local decision
  loop, renews the daemon lease, writes an atomic health file, and persists
  decisions in SQLite.
- `scorp-agent/chatgpt-gui-bridge/tools/v4_daemon_runtime.py` is the Windows
  command entrypoint.
- `scorp-agent/chatgpt-gui-bridge/install-v4-daemon.ps1` is a transactional,
  opt-in Task Scheduler installer. It is separate from the legacy V3 bridge
  installer and does not silently replace that runtime.

## One bounded read-only pass

Use the bundled Python runtime and an existing V4 SQLite database:

```powershell
$env:PYTHONPATH = 'C:\ScorpAgent\_publish_git6\scorp-agent;C:\ScorpAgent\_publish_git6\scorp-agent\chatgpt-gui-bridge'
& 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe' -B `
  'C:\ScorpAgent\_publish_git6\scorp-agent\chatgpt-gui-bridge\tools\v4_daemon_runtime.py' `
  --database-path 'C:\path\to\state.sqlite3' `
  --allowed-root 'C:\path\to\allowed\root' `
  --project-id 'project-id' `
  --daemon-epoch 1 `
  --health-path 'C:\path\to\daemon-health.json' `
  --max-iterations 1
```

The daemon reads SQLite and writes one `ACTIVATION_DECISION` event. It does not
send a browser message. If there is no valid Master binding, the expected
result is `RESUME_MASTER` with process exit code `2` and health status
`BLOCKED`. That is a fail-closed blocker, not a failed blind retry.

The first pass acquires the single `daemon_leases` row for the project. A live
lease owned by another process returns `DAEMON_LEASE_ACTIVE`; an expired lease
advances the epoch. Each bounded-loop pass renews the same owner and epoch.

To let the machine dog renew an already-active logical Master A, add
`--supervise-master --master-session-id master-a-runtime`. This attaches the
existing `MasterSupervisor` through a monitor-only gateway. It renews the
Master lease when the session is healthy and returns `BLOCKED` when a physical
rebind is required. This option still does not fill a composer, click Send, or
reconcile an ambiguous browser side effect.

## Optional Task Scheduler registration

The installer requires an existing database, allowed root, project id and
daemon epoch. It validates the Python runtime and entrypoint before stopping a
same-name task, saves the prior task XML, and restores it if registration or
post-registration validation fails.

```powershell
& 'C:\ScorpAgent\_publish_git6\scorp-agent\chatgpt-gui-bridge\install-v4-daemon.ps1' `
  -DatabasePath 'C:\path\to\state.sqlite3' `
  -AllowedRoot 'C:\path\to\allowed\root' `
  -ProjectId 'project-id' `
  -DaemonEpoch 1 `
  -Start
```

Do not use this installer as evidence that the browser channel is accepted.
It only installs the local monitor. Master rebind, Chrome Use session health,
browser reconcile and Send remain separate gates.

## Current evidence boundary

- `TEST_VERIFIED`: the current isolated command-core candidate `83c07cc2727db29ed5b28fb6260e5094e75883bf` has V4 suite
  202/202 and GUI bridge suite 508/508 on the bundled Windows Python runtime;
  task-context migration/prompt propagation, distinct CSV
  validate/aggregate/report operations, two Worker dispatch, independent
  reconcile, owned-tab isolation, rate-limit recovery unit cases, and the
  daemon missing-handler fail-closed case are included. The installed lab and
  short recovery soaks are separate simulated evidence.
- `LIVE_VERIFIED`: the current candidate has both a fresh isolated local-daemon
  restart receipt at
  `docs/handoffs/SCORP_V4_DAEMON_RESTART_LIVE_EVIDENCE_20260915.json` and a
  fresh real Chrome Use two-Worker canary at
  `docs/handoffs/SCORP_V4_LIVE_CANARY_SUCCESS_83C07CC.json`. The daemon process
  was forcibly terminated and recovered from SQLite with `daemon_epoch=2`;
  the browser canary captured two independent responses with two submit
  actions and zero duplicate submits. The rate-limit-popup branch remains
  unverified because the live page did not show that external state.
- `ACCEPTED`: not reached. A current-candidate real repository workload
  through live Workers, Master physical rebind, production registration and
  long-duration unattended evidence are still required.

## Fresh live canary result

On 2026-09-14, a fresh two-Worker canary was attempted against candidate
`167473a` using a new SQLite database and driver state. Worker-1 reached the
durable `MAY_HAVE_SUBMITTED` fence, then Chrome Use returned an EOF/daemon-busy
error during `fill`; no URL or response was captured and Worker-2 was not
started. The run was stopped without retrying. The read-only receipt is
`docs/handoffs/SCORP_V4_LIVE_CANARY_FAILURE_167473a.json`; its result is
`BLOCKED`, with `retry_count: 0`. This is LIVE_VERIFIED failure evidence, not
an acceptance pass.

After the transport command serialization fix, an older fresh canary was run
against candidate `0760c26` with a new SQLite database and driver state. The
EOF/daemon-busy symptom did not recur, but the browser remained at the logged-in
ChatGPT root URL after the submit path and never produced `/c/<id>`. The first
Worker intent was therefore recorded as `BLOCKED_AMBIGUOUS` with reason
`CONVERSATION_URL_MISSING`; Worker-2 was not started by that older serial
canary implementation. The read-only receipt is
`docs/handoffs/SCORP_V4_LIVE_CANARY_FAILURE_0760c26.json`; its SHA-256 is
`93755FFAED06821463BBEBF5548C0622E658C79411228D1A601794B1C2B75AD9`.

The canary code now prepares both durable intents first and dispatches their
browser submissions concurrently. The current candidate's successful receipt
above is the live evidence for that behavior; the c205 and earlier ambiguous
sessions remain historical fail-closed evidence.

## Historical live PASS receipt (prior candidate)

After the `press Enter` fallback and owned-tab isolation fixes, a fresh canary
was run against prior candidate `0fe4464`. It used a new SQLite database, new driver
state, and two new Chrome Use sessions. Both results reached
`RESPONSE_CAPTURED` with distinct conversation URLs, two submit actions, two
response messages, and zero duplicate submits. The receipt is
`docs/handoffs/SCORP_V4_LIVE_CANARY_PASS_760a7d7.json`; its SHA-256 is
`3D73E787C19CD007C4D7ADB08C2D8B8EF40069A3CDF11212CFF7BEF378B23DCD`.

This historical PASS is bound to prior candidate commit `0fe4464`. It is not
evidence for the current command-core candidate and does not prove the real
CSV Git workload, production cutover, restart recovery, or a 24-hour soak.
