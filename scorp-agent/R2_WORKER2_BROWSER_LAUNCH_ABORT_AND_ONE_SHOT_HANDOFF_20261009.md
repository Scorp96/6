# SCORP R2 — Failed isolated Chrome worker bootstrap / resource incident / safe next GPT

**2026-10-09, Windows local UTC+8. READ THIS BEFORE ANY MORE BROWSER OPERATIONS.**

**Final disposition: REAL_BROWSER_WORKER_2_NOT_ESTABLISHED / FAILED_LAUNCH_STOPPED / NO MASTER SEND / NO PRODUCTION CUTOVER.**

## A. Prior immutable safe state

- Parent branch [Draft PR #6](https://github.com/Scorp96/6/pull/6) @ `56c0e71f18987e7e1a42260e40446e5da758d3f3`, GitHub CI #37913926850 1,562/1,562 PASS.
- Real Worker-1-scope report previously atomically stored once: private Review Issue `Scorp96/scorp-control-plane#2457`, GitHub comment `6076934186`; original review SQLite retains comment/blob/work-text rows **1+1+1**.
- True second GPT Worker report is NOT present. Work assignment: [Worker 2 Issue #3](https://github.com/Scorp96/6/issues/3), pinned base Git SHA `116556a2d934c9dfe75572b82232f6410d1dc900`.
- Master has **one** `BLOCKED_AMBIGUOUS` old intent; V4 browser bindings count=0; GUI Bridge continues `MASTER_CONVERSATION_ROTATION_REQUIRED`. Old Master must never be retried.
- Chrome Use native CLI **1.5.123**; existing extension installed, but the currently effective extension-vs-direct-CDP mode and true host GPT completion event are NOT attested. Existing native CLI session has ONE internal Chrome tab and ZERO chatgpt.com tabs. Local loopback stream connected/HTTP GET works. **Transport is NOT a Worker identity**.

## B. Actual attempted new Worker 2 browser (NOT model execution)

One distinct action was attempted on [Windows #2486](https://github.com/Scorp96/scorp-control-plane/issues/2486):

- On an initially 1-Session native CLI, issue a single `chrome-use --session scorp-r2-worker2-empty-launch-20261009-A --launch --json open https://chatgpt.com/`.
- Explicit `--launch` chooses a NEW EMPTY Chrome profile (no reused existing login/cookies) to prevent original Master tab interference.
- The agent reported **TIMED_OUT / ExitCode 124**, so navigation and ChatGPT page creation were NOT confirmed; no model interaction/credentials or browser Send were attempted. Do NOT replay/open the same namespace.
- [#2487](https://github.com/Scorp96/scorp-control-plane/issues/2487) found exactly ONE new named Session record and two Session records total, but its tab list was not verified. Original Worker-1 SQLite remained 1+1+1.
- [#2488](https://github.com/Scorp96/scorp-control-plane/issues/2488) saw the new namespace in CLI's JSON list with name + PID metadata, but no trusted physical browser evidence.
- [#2490](https://github.com/Scorp96/scorp-control-plane/issues/2490) confirmed a running daemon process for the new namespace under interactive Windows Session 1; not a Session 0-only failure.
- [#2491](https://github.com/Scorp96/scorp-control-plane/issues/2491): read-only `tab list` without `--launch` failed, error indicators included LAUNCH (mode mismatch).
- [#2492](https://github.com/Scorp96/scorp-control-plane/issues/2492): same named Session with `--launch --json tab list` still returned exit code 1; earlier decoder emitted GBK UnicodeDecodeError.
- [#2493](https://github.com/Scorp96/scorp-control-plane/issues/2493): byte-based UTF-8-safe stdout/stderr capture confirmed genuine CLI nonzero (not only GBK decoding).
- [#2494](https://github.com/Scorp96/scorp-control-plane/issues/2494) classified safe, limited error information (browser process crashed and launch/profile/mode conflict indicators). No credentials/IDs/URLs/raw stderr exposed.

**Do not treat any partial Session record, daemon PID, or a launched browser process as independent GPT work, authenticated ChatGPT login, or a trusted TURN_FINAL_CONFIRMED event.**

## C. Possible unintended Chrome process proliferation

This is a host resource risk. Do not repeat CLI `--launch`, `open`, `tab list` in the failed profile in an unbounded loop.

- Chrome.exe process count observed 71 by #2490's earlier comparison, then 179 at [#2495](https://github.com/Scorp96/scorp-control-plane/issues/2495), 206 at [#2496](https://github.com/Scorp96/scorp-control-plane/issues/2496). Temporal correlation does not independently prove every Chrome process was ours.
- [#2501](https://github.com/Scorp96/scorp-control-plane/issues/2501) identifies **21 Chrome main process roots** created after the first attempted Worker-2 launch; their 176 descendants were classified as **POSSIBLE_AGENT_CUSTOM_PROFILE**.
- [#2502](https://github.com/Scorp96/scorp-control-plane/issues/2502) verifies the **21 new roots use 21 distinct user-data directories**, none of which matches the original pre-launch Chrome root profile. No root command line carried the exact attempted Worker-2 namespace nonce, so **strong process ownership attestation remains false**.
- After failure, [#2497](https://github.com/Scorp96/scorp-control-plane/issues/2497) performed exactly one **targeted** `chrome-use --json session stop scorp-r2-worker2-empty-launch-20261009-A` (SUCCEEDED); did NOT kill unrelated Chrome processes.
- [#2498](https://github.com/Scorp96/scorp-control-plane/issues/2498): native CLI Session count returned to **1**, failed namespace count **0**, Chrome.exe count then 206, **no new Chrome processes since stop**, original R1/Worker1 DB integrity `ok`, original Worker1 1+1+1, R1 ambiguous intent 1.
- **Remaining limitation**: lingering Chrome processes must NOT be killed with `taskkill /IM chrome.exe` or bulk `Stop-Process chrome`. Before targeted cleanup, demand demonstrable ownership of actual isolated user-data-dir(s), a process group trace to the abandoned one-shot launch, and no overlap with user/production browsers. If this cannot be attested, defer process termination and request supervised owner review. Do not automatically delete profile directories, browser cookies or session files.

## D. Engineered next-generation no-retry barrier (ISOLATED ONLY)

Files on the new child branch `experiment/r2-worker2-fresh-launch-reconciliation-20261009`:

- `scorp-agent/master_a_dynamic_v4/isolated_browser_launch_fence_v4.py`
- `scorp-agent/master_a_dynamic_v4/tests/test_isolated_browser_launch_fence_v4.py`

API (not wired to production or any browser sender):
1. `reserve_isolated_browser_launch(...)` inserts one SQLite immutable namespace+attempt reservation with strict `https://chatgpt.com` allowlist in a dedicated `experiments/r2-browser-launch-*/launch_attempts.sqlite3`; duplicates always BLOCK.
2. `reserve_external_launch_edge_once(...)` atomically advances to `EXTERNAL_LAUNCH_EDGE_RESERVED` **BEFORE** an external browser action. If the process crashes, the edge remains consumed; never auto-retry an UNKNOWN action.
3. `record_host_review_only(...)` persists `COMMAND_RETURNED_UNVERIFIED`, `LAUNCH_TIMED_OUT_UNVERIFIED`, or `ISOLATED_SESSION_STOPPED_FOR_REVIEW`. A host report here is a review hint, not a signed session/terminal event; it **cannot** authorize browser Send.
4. `read_attempt_state(...)` opens SQLite read-only and reports a fixed no-send status.
5. No secrets, browser URLs other than the exact allowlisted origin, cookies, OS subprocess, model prompts, Chrome Use driver or Task Scheduler actions. Live use requires operator-reviewed integration first; this patch intentionally does NOT retroactively write the old 2026-10-09 browser attempt into a new ledger.

## E. Safe way to advance actual independent Worker-2 GPT deliverable

The biggest block is not the immutable GitHub review system; that already accepted one authentic *content* report. We do NOT have a host-authenticated second ChatGPT session. Because the present Chrome Use empty launch is unstable and may proliferate browser profiles, **do not use that path again without explicitly validating the installed version and isolation profile process behavior**.

1. In an **independent, user-initiated ChatGPT conversation**, authorize the GitHub connector and open [Worker-2 Issue #3](https://github.com/Scorp96/6/issues/3). Read the fixed assignment `real-worker-session-two.json` from Git commit `116556a...` and independently inspect the listed source paths. Do not impersonate Worker-2 from the Worker-1 conversation.
2. Generate a real, distinct technical report, submit a new immutable v2 JSON artifact and only one new review-comment metadata envelope after the committed blob is verified.
3. The Windows host must compare exact Git blob SHA1, content SHA256, assignment digest, scoped Issue/author, task/state version, and append the new three receipts atomically to the **same** original review SQLite, respecting [Draft PR #5](https://github.com/Scorp96/6/pull/5) orphan/duplicate gates. Do not clear the 1+1+1 first receipt.
4. Barrier 2+2+2 and `BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW` are still **review only**, not host-authenticated two-GPT proof or Send permission. The first Worker-1 physical browser identity was never host-signed and cannot be retroactively upgraded.
5. For future supervised host-side browser Worker automation, require a genuinely independent verified tab/context, account authorization, trusted host-owned completion event, key custody, one-time action ledger and restart recovery. No bypass of CAPTCHAs or old Master blocked intents.

## F. Final checkpoint

```text
SCORP_R2_FRESH_BROWSER_BOOTSTRAP_ABORTED_SAFELY
Original Worker-1 review database: 1+1+1, quick_check=ok
Original Master: 1 BLOCKED_AMBIGUOUS, no replay
Original Chrome Use CLI sessions after rollback: 1
Fresh Worker2 namespace: stopped, removed from active list
Actual Worker2 ChatGPT messages sent: 0
Real Worker2 technical artifact accepted: NO
Chrome.exe root profile fanout: 21 new agent-custom roots; 21 unique dirs; NO exact ownership attestation
Bulk Chrome process cleanup: NOT DONE (unsafe without ownership)
One-shot future browser launch ledger: isolated-code candidate only
GitHub PRs: #1, #4, #5, #6 DRAFT; new child PR also must stay DRAFT
No production merge, no local original R1/GUI/Observer edits, no ChatGPT browser send.
```
