# SCORP R2 Local Zero-Token Poll — Operator Runbook (2026-10-09)

**Scope:** isolated experiment branch only. **No** production cutover, browser send,
ChatGPT message submission, original Master replay, scheduler modification, or
claim that a GPT turn finished.

## Current code

- `chatgpt-gui-bridge/chrome_use_loopback_observer_v4.py`: bounded native
  stream-status port discovery and three fixed, no-proxy/no-redirect local GETs.
- `chatgpt-gui-bridge/isolated_loopback_poll_journal_v4.py`: SQLite
  `BEGIN IMMEDIATE` journal, minimum 15/25 minute interval, strict duplicate
  suppression across threads, Windows processes and restarts.
- `chatgpt-gui-bridge/isolated_loopback_poll_once_v4.py`: standalone
  one-shot command, using only an existing `r2-loopback-poll-*` scratch directory.
- `chatgpt-gui-bridge/isolated_chrome_error_redacted_reader_v4.py`:
  restricted passive Chrome CLI inventory with sanitized error envelopes.
- `chatgpt-gui-bridge/isolated_chrome_tab_transition_evidence_v4.py`:
  no-send comparison of existing tab IDs; missing inventory is NOT verified
  physical tab closure.

None of these contain a ChatGPT browser send, wake callback, scheduling hook,
trusted terminal event issuer, or worker dispatcher.

## One-time setup: isolated scratch only

Do NOT do this in `C:\ScorpAgent\state-v3\active`,
`C:\ScorpAgent\chatgpt-gui-bridge`, or the frozen 15-minute observer
worktree. Use a fresh directory under `C:\ScorpAgent\experiments`.

```powershell
$root = 'C:\ScorpAgent\experiments'
$name = 'r2-loopback-poll-operator-canary-001'
$dir = Join-Path $root $name
if (Test-Path -LiteralPath $dir) { throw 'SCRATCH_EXISTS_DO_NOT_RESET' }
New-Item -ItemType Directory -Path $dir -ErrorAction Stop | Out-Null
```

This setup is **not authorized by this document**; it is an operator procedure
to execute only in an explicitly permitted isolated worktree.

## Single local observation: 15 or 25 minute policy

With the candidate branch present inside the isolated worktree, never
configure an existing production Task Scheduler job to run this command.

```powershell
$repo = 'C:\ScorpAgent\experiments\r2-host-terminal-security-20261009'
$env:PYTHONPATH = Join-Path $repo 'scorp-agent\chatgpt-gui-bridge'
$python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe'
$namespace = '<EXPLICITLY_VERIFIED_ISOLATED_R2_NAMESPACE>'
& $python -B -m isolated_loopback_poll_once_v4 `
    --experiment-folder r2-loopback-poll-operator-canary-001 `
    --namespace $namespace --cadence 15
```

For 25-minute cadence, change only `--cadence 25`. The source CLI derives
the experiment root and installed browser binary from its own checkout;
it does not accept arbitrary destination paths or browser URLs.

- **Exit code 0:** one nonterminal local observation was durably recorded.
  It does not mean ChatGPT is done.
- **Exit code 3:** blocked, stale, early, duplicate, missing source or unsafe
  path. This is an expected safe result for a too-frequent poll.
- Never override `CLOCK_REWIND_OR_DUPLICATE`, `POLL_CADENCE_NOT_ELAPSED`,
  or a host terminal evidence blocker by deleting or modifying SQLite.
- Changing 25 to 15 minutes before 25 minutes have passed does **not** let
  a caller bypass the prior stricter interval.
- No cron/Task Scheduler task is installed by this implementation. A later
  isolated schedule needs its own explicit authorization and 24-hour soak.

## What SQLite may store

Only a digest of the designated logical session name, UNIX UTC timestamp,
cadence, integer session count, integer tab count and a fixed class:
`FIRST_LOCAL_STATE_UNATTESTED`, `COUNTS_STABLE_UNATTESTED`, or
`COUNTS_CHANGED_UNATTESTED`.

It must not store raw session names, model prompts, user messages, page
content, account identifiers, actual browser URLs, credentials or native
CLI stderr.

## Trust gates (none may be inferred from the others)

| Observed input | Proves | Does not prove |
|---|---|---|
| Local GET status | local daemon answered | ChatGPT generation ended |
| Local GET tabs/sessions | last-observed tab/session counts | live owner or account |
| Chrome Use tab inspect / snapshot | some page metadata / UI controls | exact GPT turn final |
| Stable text, missing Stop button | heuristic state only | `TURN_FINAL_CONFIRMED` |
| HMAC-signed synthetic fixtures | test integrity gate logic | real issuer provenance |
| SQLite sample committed | at-most-once local journal sample | any right to wake/send |

A genuine host-issued `TURN_FINAL_CONFIRMED` must be linked to one exact
conversation URL, session identity, binding generation, original intent,
one definite outbound submission edge and response digest. The current
Chrome Use local HTTP GET surfaces do NOT provide this issuer.

The existing `master_a_dynamic_v4/turn_completion_evidence.py` and
`host_terminal_receipt.py` already fail closed without independently
attested host receipts. Do not relax this gate just to achieve unattended
operation.

## Real Windows proof set

- #2407: 1377/1377 offline, original poll journal test suite.
- #2409: 1392/1392 offline, standalone CLI test suite.
- #2411: 1414/1414 offline, tempfile native CLI runner and redacted
  read-only wrapper.
- #2412: 15-minute mode real browser + SQLite first observation success,
  immediate second invocation blocked, zero model calls.
- #2414: independently repeated same real behavior for 25-minute mode.
- #2416: four distinct Windows Python processes, one SQLite sample winner,
  three blocked, one final row, zero unauthorized wakes.
- #2415: local API metadata did not expose a directly attested terminal
  completion field.
- #2408: previous read-only browser sample issue outer TIMED_OUT; #2410
  confirmed scratch directory existed but journal database was absent.
  Do NOT imply the timed-out issue succeeded or automatically replay it.

Control-plane evidence:
`https://github.com/Scorp96/scorp-control-plane/issues/<number>`.

## Preserved production invariants

- Original R1 Master `BLOCKED_AMBIGUOUS` outstanding submit must not replay.
- Original GUI reports `MASTER_CONVERSATION_ROTATION_REQUIRED` / `ERROR`.
- Frozen 15-minute observer worktree HEAD remains
  `8da5085f906b4aeeece6fb0ae1f488bc597507b1`.
- No merge, production cutover, existing scheduler alteration or R1 state
  writes. The isolated branch remains a Draft PR.
- Inherited `ChromeUseCliV3` may include native error streams in exceptions.
  The **new** R2 read-only wrapper sanitizes its own path; it is not a
  general repair of the old running GUI control channel.

## Acceptance backlog

1. Independent local host-terminal issuer provenance, not just an HMAC
   verifier or DOM heuristic.
2. Dedicated, human-approved isolated master + two worker bindings.
3. Exact-once user submission edge and crash/no-send recovery.
4. Isolated scheduler setup, monitoring and **24-hour real canary**.
5. Explicit separate R1 cutover approval, only after every safety gate passes.

**Never describe this version as autonomous GPT wakeup or a complete
24-hour running agent.** It is a tested zero-model local observation layer.
