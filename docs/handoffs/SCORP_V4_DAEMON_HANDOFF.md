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

- `TEST_VERIFIED`: V4 suite 103/103 and GUI bridge suite 465/465 on the bundled
  Windows Python runtime; the two Worker canary dispatch regression is included.
  PowerShell parser and Python compilation checks pass when run against the
  candidate checkout.
- `LIVE_VERIFIED`: no current-candidate ChatGPT Send or two-Worker browser
  result has been claimed here.
- `ACCEPTED`: not reached. Production registration, current-candidate Chrome
  reconcile, restart recovery and Windows unattended evidence are still
  required.

## Fresh live canary result

On 2026-09-14, a fresh two-Worker canary was attempted against candidate
`167473a` using a new SQLite database and driver state. Worker-1 reached the
durable `MAY_HAVE_SUBMITTED` fence, then Chrome Use returned an EOF/daemon-busy
error during `fill`; no URL or response was captured and Worker-2 was not
started. The run was stopped without retrying. The read-only receipt is
`docs/handoffs/SCORP_V4_LIVE_CANARY_FAILURE_167473a.json`; its result is
`BLOCKED`, with `retry_count: 0`. This is LIVE_VERIFIED failure evidence, not
an acceptance pass.

After the transport command serialization fix, a second fresh canary was run
against candidate `0760c26` with a new SQLite database and driver state. The
EOF/daemon-busy symptom did not recur, but the browser remained at the logged-in
ChatGPT root URL after the submit path and never produced `/c/<id>`. The first
Worker intent was therefore recorded as `BLOCKED_AMBIGUOUS` with reason
`CONVERSATION_URL_MISSING`; Worker-2 was not started by that older serial
canary implementation. The read-only receipt is
`docs/handoffs/SCORP_V4_LIVE_CANARY_FAILURE_0760c26.json`; its SHA-256 is
`93755FFAED06821463BBEBF5548C0622E658C79411228D1A601794B1C2B75AD9`.

The canary code now prepares both durable intents first and dispatches their
browser submissions concurrently. This fixes the orchestration serialization
defect, but it has not been promoted to a live PASS: a new canary is required
only after the current ambiguous browser session is reconciled or retired by a
fresh operator-approved run.
