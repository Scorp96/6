# SCORP R2 Worker-2 browser incident — resource quarantine and ownership-first recovery

**Date:** October 9, 2026. **Status:** ISOLATED CANDIDATE / READ-ONLY HOST FORENSICS / NO PROCESS KILL / NO CHATGPT SEND.

## 1. The actual state — new Windows measurements

The first isolated, empty-profile Worker-2 browser start [Windows #2486](https://github.com/Scorp96/scorp-control-plane/issues/2486) **TIMED_OUT**, so a new ChatGPT tab was never confirmed. One named Chrome Use namespace was created and later explicitly stopped once [#2497](https://github.com/Scorp96/scorp-control-plane/issues/2497). Original R1 remained blocked, original Worker-1 receipt remained 1+1+1, no browser messages sent.

Subsequent process forensics show an important **unresolved host resource leak risk**:

- [Windows #2501](https://github.com/Scorp96/scorp-control-plane/issues/2501) / [#2502](https://github.com/Scorp96/scorp-control-plane/issues/2502): 21 Chrome browser-root processes created after the attempt, 21 distinct Chrome `--user-data-dir` arguments; no overlap with the old browser root directory but **no exact Worker-2 namespace nonce anywhere in new root command lines**. These roots are possible agent-style profiles, NOT proven assets of the one failed Worker-2 invocation.
- [Windows #2506](https://github.com/Scorp96/scorp-control-plane/issues/2506): 206 Chrome.exe processes, 7,542 MB aggregate working set, 8,069 MB available physical RAM. Original Master `BLOCKED_AMBIGUOUS=1`, old Worker-1 review SQLite 1+1+1, observer seq 42, all three SQLite `quick_check=ok`.
- [New Windows #2507](https://github.com/Scorp96/scorp-control-plane/issues/2507): **two hours after launch**, Chrome.exe processes still **206**; 22 browser roots, **21 post-launch roots**, 21 user-data dirs, 0 new roots since 18:25, 7,521 MB working set and 8,265 MB free. The system is NOT continuously spawning new browser roots, but orphan-like root processes persist.
- [New Windows #2508](https://github.com/Scorp96/scorp-control-plane/issues/2508): of 21 post-launch Chrome roots, **20 original parent PIDs cannot be resolved to a currently running parent** and one presently points to `chrome.exe`; all belong to interactive Windows Session 1; 0 command lines contain the unique Worker-2 launch nonce. Process start date, Chrome executable name, current parent PID and a custom user-data-dir are **insufficient to prove ownership**.
- No `taskkill /IM chrome.exe`, `Stop-Process chrome`, uncontrolled browser-profile deletion, scheduler restart, or old Master replay was performed. Do not do any of these without separately verified ownership and explicit operator approval.

**Do not conflate an aggregate 7.5 GB Chrome working set with 7.5 GB extra physical RAM that would be freed upon cleanup.** Working-set aggregation can count shared pages; and the exact 21 roots have not been linked to the failed action.

## 2. Engineering containment: no unverified orphan-root cleanup

New isolated source:

`scorp-agent/master_a_dynamic_v4/isolated_browser_process_ownership_v4.py`

New isolated regression:

`scorp-agent/master_a_dynamic_v4/tests/test_isolated_browser_process_ownership_v4.py`

The pure `assess_residual_chrome_resources(...)` gate checks a sanitized snapshot of Chrome process/root counts, distinct profile directories, parent process loss, memory, and exact nonce presence. It returns `HUMAN_ATTRIBUTION_REQUIRED` when even one unattributed newly created browser root remains; it **never** grants a new browser launch or process termination. Missing/negative/inconsistent data fails closed.

`review_host_process_witness(...)` accepts a **future** host-owned process witness: a unique action nonce, explicit isolated Worker namespace, local process ID **and actual process creation timestamp**, canonical user-data-dir SHA256, Windows session ID, and an HMAC signature under a protected host key. It rejects PID reuse, changed directory/session, missing key, malformed SHA, corrupted MAC, witness issued after the process, and a witness issued more than three minutes before process creation. A valid result is `OWNERSHIP_EVIDENCE_FOR_HUMAN_REVIEW` only; **`cleanup_authorized=false` always**.

**Security caveat:** the live October 9 launch had **no pre-created host-witness signer**. There is NO supported way to issue a convincing signed witness retroactively, and this code does NOT contain a trusted signer. Synthetic HMAC unit-test fixtures prove parsing/verification behavior only, not that a real Chrome process is owned.

No source code in this module can spawn browsers, kill processes, navigate tabs, access Chrome cookies, resume the original Master, or submit prompts.

## 3. How a future independently authorized browser launch must be contained

A safe new supervised Worker browser experiment needs all these prerequisites, **before** another launch:

1. Original Chrome process count and process IDs + creation times recorded **locally and privately**; verify the dedicated profile directory is fresh, exclusive, and non-overlapping; verify sufficient memory. Private data must not be pasted into GitHub Issue comments.
2. A trusted, long-lived Windows host worker persists the unique launch nonce/attempt and a locally protected signing-key reference BEFORE any external browser edge. A signed ownership witness must link exact process PID+birth time and profile SHA to that launch. No raw key in GitHub, ChatGPT or logs.
3. Use the existing one-shot `isolated_browser_launch_fence_v4.py` from [Draft PR #7](https://github.com/Scorp96/6/pull/7) to persist `EXTERNAL_LAUNCH_EDGE_RESERVED` **before** the external command; on timeout or restart, the original attempt remains permanently no-retry until a supervised reconciliation.
4. A true new browser process group must be isolated from the user Chrome and original R1. Prefer host-managed job-object/process ownership rather than guessing by parent after that parent exited. Closing a job group must never be implemented without proving it contains **only** the experiment's children and explicit operator cleanup authorization.
5. Prove the newly created tab is a fresh ChatGPT workspace under the correct account and independent physical session; old V3 aliases, internal browser tabs, CLI daemon session records and unsigned page state are NOT sufficient identity evidence.
6. A real GPT Worker task submission requires separate user authorization, a durable exactly-once send gate, a trustworthy browser completion event or artifact-based handoff, and explicit no-replay safeguards around the old Master. **This incident is NOT authorizing any prompt submission.**

## 4. Actual next GPT Worker2 task after the host is ready

The independent task remains [Worker-2 GitHub Issue #3](https://github.com/Scorp96/6/issues/3), fixed assignment commit `116556a2d934c9dfe75572b82232f6410d1dc900` and file SHA256 `68ed1c98b5687e7e185c2320db446e76fe2d2720d867b60f220a026dd0b2fa4b`.

A **separately authenticated ChatGPT session**, not the one that created Worker-1 report, must independently write its v2 work artifact. The Windows host then verifies the new immutable Git bytes + comment and appends the three receipts to the **existing** original Worker-1 review SQLite under the atomic append guard from [Draft PR #5](https://github.com/Scorp96/6/pull/5). First result remains 1+1+1 until a true second work product arrives. Two Git artifacts do **not** retroactively attest independent physical GPT Worker identity; the original Work-1 physical host proof is still absent.

## 5. Evidence / release matrix

| Claim | Current evidence | State |
| --- | --- | --- |
| Existing R1 protected | Windows #2506 | PASS (point in time) |
| Original Worker-1 1+1+1 preserved | Windows #2506 | PASS |
| Chrome profile fanout has stopped | Windows #2507 | New root count since 18:25 = 0 |
| 21 browser root ownership verified | Windows #2508 | **NO / 20 parents gone** |
| Safe automatic browser cleanup | No witnessed owner records | **BLOCKED** |
| Future host-signed witness verifier | New isolated source + tests | Candidate, not live signer |
| Browser send/automatic GPT resume | Real Worker2/terminal emitter missing | **BLOCKED** |
| New source CI | Must match exact candidate SHA | Check latest GitHub run |
| Separate Worker2 task completed | GitHub Issue #3 still PENDING | **NO** |
| Production branch/GUI modified | No such action | **NO** |

## 6. Mandatory interruption / handoff

```text
SCORP_R2_CHROME_ORPHAN_RESOURCE_HANDOFF
Last exact candidate source SHA:
GitHub Actions full Windows CI:
Real Windows offline V4 regression Issue:
Windows #2507: Chrome process 206, roots 22, new roots 21, 21 dirs, no new roots since 18:25
Windows #2508: of new roots, 20 parents gone, one chrome.exe parent, 0 exact nonce
Attributable owned process IDs: NONE HOST-VERIFIED
User browser mass kill or profile deletion: NEVER DONE
Worker2 failed namespace stop proof: #2497 #2498
Original Worker1 review SQLite 1+1+1 / quick_check:
Original Master BLOCKED_AMBIGUOUS:
Last observer seq:
New independent authenticated Worker2: NO
Real Worker2 Git file/comment: NO
Browser message sent / production modification: NONE
Next safe step: retain browser resource quarantine; request separate operator-authorized genuine ChatGPT Worker session or supervised process ownership attribution, not a blind --launch retry
```
