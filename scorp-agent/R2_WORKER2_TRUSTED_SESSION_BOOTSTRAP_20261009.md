# SCORP R2 — Genuine Worker-2 Session Bootstrap (No-Send)

**2026-10-09; isolated branch; NOT production.** This is the remaining technical admission boundary after a genuine GPT-authored Worker-1-scope artifact was atomically ingested.

## 1. What is already true, not inferred

- Draft [PR #4](https://github.com/Scorp96/6/pull/4) retains a **real** GPT-authored Worker-1-scope analysis in [private Review Issue #2457](https://github.com/Scorp96/scorp-control-plane/issues/2457), original comment ID `6076934186`; Windows #2459 atomically ingested comment/blob/text. Worker 1 GPT *browser* session identity remains unverified.
- [Draft PR #5](https://github.com/Scorp96/6/pull/5) at code SHA `3a5f768bc0ea28e3bfd0169ec8b5c49d37b08d28` passed 710 Windows V4 tests + 801 GUI + 29 broker = **1,540/1,540 PASS**, but no browser/GPT sends. Its append receiver rejects old missing/malformed/orphaned receipts.
- Windows #2465 original real review SQLite: 1 comment + 1 blob + 1 text, zero Worker-2 rows, SQLite `quick_check=ok`; original R1 still had 1 `BLOCKED_AMBIGUOUS`, old observer at seq 35.
- [Windows #2467](https://github.com/Scorp96/scorp-control-plane/issues/2467) saw zero V4 `browser_bindings`, GUI Bridge `ERROR` and `MASTER_CONVERSATION_ROTATION_REQUIRED`. V3 `sessions` count was **UNKNOWN**, not observed zero.
- [Windows #2469](https://github.com/Scorp96/scorp-control-plane/issues/2469) saw raw V3 keys `protocol_version`, `turns`, `conversations`; **no persisted `sessions` registry**.
- [Windows #2470](https://github.com/Scorp96/scorp-control-plane/issues/2470) counted **6 historical conversations, 219 historical turns, four historical Session references** (the role and submit-edge claims are not physically attested).
- [Windows #2471](https://github.com/Scorp96/scorp-control-plane/issues/2471) proved **219/219 turn roles unclassified**, **219/219 browser_io_started unknown**, **219/219 submit_edge_crossed unknown**. Unknown does **not** mean `False`.
- [Windows #2472](https://github.com/Scorp96/scorp-control-plane/issues/2472): an 11s bounded `chrome-use.exe --help` diagnostic TIMED_OUT. Do not repeat as unbounded direct process call; it supplied no trustworthy physical browser session evidence.

## 2. Source-level explanation of why legacy sessions are NOT adoptable

`chrome_use_actor_driver_v3.py::_load()` handles old JSON without `sessions` by putting `sessions={}` **in memory**. That does not confirm a physical Chrome tab, an authenticated ChatGPT account, an independent GPT Worker, or safe retry status.

`ChromeUseActorDriverV3._existing_physical_binding()` looks at persisted ACTIVE role-bound `sessions` entries. With none persisted, it cannot obtain a unique Worker binding; no call to `observe_current_binding('worker-2')` should assume this state has a physically verified tab. `restore_known_binding(...)` may **navigate** and mutate driver state, so do not call that to manufacture physical evidence.

`session_admission.py` requires trusted observation from a local host and a VERFIED physical state. A historical `turn.session` string or a self-reported ChatGPT message cannot replace that trusted host observation.

All original 219 historical Turn submit-edge values are UNKNOWN, and the 2026-09-24 Master `TimeoutError` remains `BLOCKED_AMBIGUOUS`. **Do not replay, rewrite the old URL, clear history or auto-rotate**.

## 3. New safety preflight (isolation only)

Code: `scorp-agent/master_a_dynamic_v4/isolated_worker_session_readiness_v4.py`.

Run only from the new pinned isolated checkout in Python; it reads the existing V3 driver state JSON and emits aggregate fields **without** conversation IDs, URLs, session labels, prompts or cookies:

```powershell
$env:PYTHONPATH='C:\ScorpAgent\experiments\r2-worker2-session-readiness-20261009\scorp-agent'
& 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe' -B -m master_a_dynamic_v4.isolated_worker_session_readiness_v4 --state-file 'C:\ScorpAgent\state-v3\active\chrome-use-driver-v3.json'
```

Outputs include `HISTORICAL_METADATA_ONLY`, `LEGACY_SESSION_REGISTRY_MISSING`, unknown irreversible send-edge counts, and permanent `two_distinct_gpt_workers_authenticated=false`, `original_master_replay_authorized=false`, `browser_send_authorized=false`. Even if a future V3 saved file gains two `sessions` rows, they are `PERSISTED_SESSIONS_NOT_HOST_ATTESTED`, **not** proof of two real sessions.

This scanner itself can never sign physical identity, send a GPT prompt, rotate the original Master, or wake any Worker.

### Live native Chrome Use inventory — an additional READ-ONLY option

The installed Chrome Use binary's **real** session count must be obtained from `--json session list` with a finite timeout and validated JSON collection, not guessed from the old V3 `conversations` map. Do NOT use unbounded `--help`.

- [Windows #2475](https://github.com/Scorp96/scorp-control-plane/issues/2475) returned one Chrome Use session object and no browser sends.
- [Independent schema check #2476](https://github.com/Scorp96/scorp-control-plane/issues/2476) confirmed that the payload is an actual root `sessions` **list**, count=1, rather than a PowerShell `@($null)` count bug. Even that one CLI session is not a host-attested GPT Worker.
- The new `isolated_worker_session_readiness_v4.py` can optionally include a **bounded** native CLI count with `--include-live-cli-sessions`. It only logs counts and fixed status, never session labels/URLs/identifiers and never opens a tab.

```powershell
$env:PYTHONPATH='C:\ScorpAgent\experiments\r2-worker2-session-readiness-20261009\scorp-agent'
& 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe' -B -m master_a_dynamic_v4.isolated_worker_session_readiness_v4 --include-live-cli-sessions
```

This command is allowed **only after pinning an isolated source SHA and verifying the new regression**. It invokes native `chrome-use.exe --json session list` with a 15-second bounded timeout. `OBSERVED_SESSION_OBJECTS_ONLY` never turns into `HOST_VERIFIED`, even if the count later reaches 2. When the CLI times out, exits nonzero or has no collection, it returns `UNAVAILABLE`, **not** a fictitious count.

## 4. Actual next step to obtain the second real GPT result (without falsely claiming host attestation)

1. A different, genuinely separate ChatGPT conversation must receive [the Worker-2 task #3](https://github.com/Scorp96/6/issues/3) under the user's account authorization. Its instructions read `scorp-agent/r2-work-assignments/real-worker-session-two.json`; it must independently produce meaningful code-specific source review.
2. The independent conversation commits `scorp-agent/r2-work-artifacts/real-worker-session-two.json` with v2 format and a new immutable Git SHA. This is **work completion evidence** but NOT automatically **host identity evidence**.
3. It posts one scoped metadata `SCORP_R2_WORK_ARTIFACT::` comment to existing private Issue #2457 using a durable exactly-once comment posting policy. The Worker-1 original comment ID `6076934186` must never be posted again.
4. Windows independently reads **new** comment and **new pinned** Git blob, validates both SHA-256 and Git SHA-1, assignments, Worker slot, GitHub author/issue, timestamps, then calls `stage_atomic_pinned_substantive_artifact_for_review` against the **same original** review SQLite. Fail closed on legacy orphan rows; do not create a blank review DB.
5. Check existing Worker-1 receipt still unchanged; counts become 2/2/2, `quick_check=ok`, strict barrier `BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW`. **No Master wake or browser send follows from this status.**
6. Independently audit host-proven true session differentiation. If no trusted, pre-existing Worker-1 host record exists, **redo Worker-1 only as a new, separately authorized session** in a new isolated project; never retroactively assert its old report was host-attested or overwrite original immutable proof.

## 5. Physical host session protocol needed before autonomous GPT continuation

A trusted host-side **producer** (not a model response or GitHub issue) must create fresh physical-session observations bound to a non-secret unique challenge, project/assignment, logical Worker slot, host-controlled tab/context, conversation generation, and a real application-layer completion event.

Required checks:
- distinct host-observed browser session identities for Slot 1 and Slot 2, not merely two strings/URLs in V3 legacy JSON;
- neither may overlap the old ambiguous Master or inherit an UNKNOWN submit edge;
- no authentication bypass, CAPTCHA bypass, wrong account, wrong role, expired lease or stale generation;
- host-signing key never appears in ChatGPT prompt, GitHub, a report, or debug output;
- replay ledger and state authority must persist through restart and reject stale binding generations;
- user-approved browser submit is a separate capability from work artifact inspection, signed observation and host liveness;
- the host's physical completion event must be genuine; page stability, missing Stop button, connected WebSocket or unsigned `TURN_FINAL_CONFIRMED` text is not sufficient.

**Current Chrome Use driver does not implement this trusted emitter.** This is an explicit STOP condition for autonomous browser resubmission. It does not block manually initiated separate ChatGPT conversations from creating review-only GitHub artifacts.

## 6. Operator handoff checklist

```text
PR4 base SHA:
PR5 fenced atomic receiver SHA:
New isolated historical-preflight SHA / Windows CI / host regression:
Current real Worker-1 comment ID: 6076934186
Original review SQLite status / triple counts / orphan counts:
Original Master BLOCKED_AMBIGUOUS:
V3 historical conversations / turns / session refs:
V3 irreversible send-edge UNKNOWN count:
Actual distinct physical host Worker sessions proved:
Real Worker-2 file SHA / comment ID / atomic intake:
Strict 2+2+2 human-review barrier:
Browser sends this run: NONE
Production edits this run: NONE
Next safe action: Run Worker 2 in a genuinely separate user-authorized ChatGPT conversation, then real immutable review intake.
```
