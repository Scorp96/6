# R2 Worker 2 — Existing evidence-safe append, NOT another GPT session

**Date:** 2026-10-09 UTC+8. **Branch:** `experiment/r2-atomic-existing-review-fence-20261009`. **Disposition:** isolated candidate; browser send and production cutover forbidden.

## Exact verified live state

- Baseline, still unmodified: [Draft PR #4](https://github.com/Scorp96/6/pull/4) at `116556a2d934c9dfe75572b82232f6410d1dc900`, [CI #37902838428](https://github.com/Scorp96/6/actions/runs/37902838428) 1,532/1,532 PASS.
- Existing real Worker-1-scope technical report (from the current GPT conversation, **NOT separately browser-session attested**): immutable Git commit `f01bd0bd349c92e1f2fe6b519b271f6ac8dcac80`, SHA256 `ce4d4bb8b39d29fa7a7e9fcfc49ffcb7c5e0aa194a97fe06d5396ea3b931f57a`, assignment manifest SHA256 `8f5d1278d6a128b7feed9ffb11c67338323e97cd76a305653962d9878d6e7bdf`.
- Existing **single** Review Issue result: `Scorp96/scorp-control-plane#2457` comment ID `6076934186`. Already posted under a durable `POST_CONFIRMED` latch; **NEVER repost**.
- Existing authoritative *isolated Worker review* SQLite path: `C:\ScorpAgent\experiments\r2-work-artifact-review-current-gpt-20261009\work-artifact-review.sqlite3`. It is a user data artifact and must NEVER be replaced by a newly empty SQLite file to claim a second result.
- [Real Windows preflight #2462](https://github.com/Scorp96/scorp-control-plane/issues/2462) read this exact live database using URI `mode=ro`: `integrity=ok`, exact first Git comment/blob/text proof, counts **1 / 1 / 1**, Worker-2 assignment rows **0**, `safe_append_candidate=true`. This is a point-in-time read, not a permanent send grant.
- [Windows regression #2463](https://github.com/Scorp96/scorp-control-plane/issues/2463) on isolated new candidate `e54548b67410a23df398b8074eb6fcc5964ab251`: **707/707 V4 core PASS**; no production, browser, or original review DB writes. Cloud CI must be verified against the final exact branch HEAD independently.

## New guard — why it matters

`scorp-agent/master_a_dynamic_v4/isolated_gh_atomic_verified_intake_v4.py` already validates real GitHub comment + exact pinned immutable Git bytes + substantive text + assignment scope before a transaction, then atomically inserts 3 receipts (comment, blob, text).

It previously prevented same `comment_id` and `assignment_digest` duplicates, but did not require *other pre-existing project receipts* to form a complete 1+1+1 triple before appending a new Worker result.

The new isolation guard, **inside the same `BEGIN IMMEDIATE` SQLite transaction**, checks all existing project receipts for:
- one existing slot at most before append; refuses capacity exceeding 2;
- no unscoped orphan blob-only or text-only receipts lacking any base comment row anywhere in the dedicated isolated review database;
- exact existing blob SHA256 matching claimed base SHA256;
- blob status `GIT_BLOB_VERIFIED_FOR_HUMAN_REVIEW`;
- substantive deliverable SHA256 is valid 64-character lowercase hex, with `SUBSTANTIVE_TEXT_PRESENT_UNREVIEWED`;
- existing `worker_slot` belongs to the two known slots and differs from the new incoming slot.

Failures return `UNSCOPED_LEGACY_REVIEW_ORPHAN_PRESENT`, `PREEXISTING_REVIEW_EVIDENCE_INCOMPLETE`, `PROJECT_WORKER_SLOT_ALREADY_FILLED` or `PROJECT_REVIEW_CAPACITY_EXCEEDED`. All failures **ROLL BACK** the attempted new intake; the original evidence remains untouched. The same-transaction guard closes the preflight-to-write race for the history rows. New tests inject missing blob, missing text, altered blob hash, altered text hash, reused slot, and orphan blob-only/text-only rows (all fail closed); prior two-valid-results test continues to pass.

**This is NOT a retrofit/migration of old orphaned rows.** If history is corrupt, stop and perform read-only forensic audit. Do not delete, repair, reset or rebuild the original review DB automatically.

## How the genuinely independent second Worker should continue

1. **A DIFFERENT ChatGPT conversation** (not the one that authored Worker 1) must read [Worker 2 Issue #3](https://github.com/Scorp96/6/issues/3), then `scorp-agent/r2-work-assignments/real-worker-session-two.json` from exact pinned Git HEAD and `scorp-agent/R2_ATOMIC_REAL_WORK_RECEIPT_HANDOFF_20261009.md`.
2. Independently audit the old two-stage comment/blob receiver and new atomic receiver, with real source refs, failure cases and verdict. Write substantive v2 artifact in **its own unique path** `scorp-agent/r2-work-artifacts/real-worker-session-two.json` and submit it through authorized GitHub action; do not copy Worker-1 text or pretend a distinct host session is proven.
3. Capture the **exact immutable 40-hex Git commit** and full file SHA256, plus assignment manifest hash and expected state version. The resulting GitHub comment is metadata only, and **must be posted once after the artifact is committed** to the original private Review Issue #2457. A single comment's `POST_CONFIRMED` must be checked before any retry. Do not repost Worker 1 comment 6076934186.
4. Independently fetch that *new* comment and the pinned *new* file from Windows native `gh`, verify author/Issue and unedited timestamps, Git blob identity, file SHA256, slot=2 and matching task/generation. Host must use `stage_atomic_pinned_substantive_artifact_for_review` against the **existing** database above, not a new blank path. Any failure must not be auto-retried if the delivery boundary is ambiguous.
5. Confirm the persistent original first record is unchanged; table counts should become exactly **2 / 2 / 2**, `PRAGMA quick_check=ok`. Original single-result barrier should progress from `AWAITING_SECOND_WORKER` to `BOTH_ARTIFACTS_FOR_REVIEW`. Strict v2 pinned barrier may progress to `BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW`.
6. Both review statuses are **only for human review**, not `TURN_FINAL_CONFIRMED`, not independent GPT-session authentication and **not permission to wake/send**. For independent Worker identity, a trusted host must provide externally verified distinct physical browser session and binding generation evidence. GitHub usernames and report self-claims are not sufficient.

## Binding and model policy

- GPT1–N designates distinct *sessions*, not GPT model generations. The old project's `AGENTS.md` explicitly names GPT-5.6 Sol; the current tool is GPT-6 and cannot claim the old identity. Do not bypass old production admission policy in an experiment.
- Two independently authored work products do **not** retroactively prove Worker 1's browser physical identity. If no historical host attestation exists, a new separately authorized Worker 1 session can be independently re-validated in a future isolated project, without rewriting the current immutable report or losing the current 1+1+1 review record.
- No original Master `BLOCKED_AMBIGUOUS` replay, no GUI rotation, no Chrome Use send, no TaskScheduler rewrite, no merge/cutover. Keep baseline PR #4 Draft; this fence belongs in a separate Draft branch.

## Minimal next-GPT interruption report

```text
SCORP_R2_WORKER2_APPEND_HANDOFF
Legacy baseline PR4 SHA:
New child branch SHA, CI link, conclusion:
Windows #2462 original DB one-result read-only pass:
Windows #2463 code regression:
Actual independent Worker2 session exists? VERIFIED/UNVERIFIED:
Worker2 exact assignment version and immutable SHA:
Worker2 work file commit + blob SHA1 + file SHA256:
New comment ID / original private Issue #2457:
Comment POST_CONFIRMED?:
Original Worker1 comment still exactly once:
Original review DB prewrite triple rows:
Postwrite triple rows + quick_check:
First and strict barrier statuses:
Browser-send / wake authorization: MUST BE FALSE
Original R1 and observer modified? MUST BE NO
Unfinished blockade and next strictly safe action:
```
