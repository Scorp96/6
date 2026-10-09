# SCORP R2 — Atomic actual-GPT result receipt and next independent Worker handoff

Snapshot date: 2026-10-09 16:05 UTC+8. Isolated Draft PR #4; no production deployment. This document is a historical snapshot, not current Windows status.

## What is genuinely accomplished

The current GPT-6 conversation produced **an actual, substantive engineering report** covering session binding, original Master ambiguity and physical UI completion limits, not a metadata-only synthetic example:

- Task manifest: `scorp-agent/r2-work-assignments/real-worker-session-one.json`.
- Pinned report: `scorp-agent/r2-work-artifacts/real-worker-session-one.json`, exact Git commit `f01bd0bd349c92e1f2fe6b519b271f6ac8dcac80`.
- Source report SHA256: `ce4d4bb8b39d29fa7a7e9fcfc49ffcb7c5e0aa194a97fe06d5396ea3b931f57a`.
- Source assignment manifest SHA256: `8f5d1278d6a128b7feed9ffb11c67338323e97cd76a305653962d9878d6e7bdf`.
- Review issue in the private control plane: https://github.com/Scorp96/scorp-control-plane/issues/2457.
- Actual GitHub comment (one line, no private source code): https://github.com/Scorp96/scorp-control-plane/issues/2457#issuecomment-6076934186.
- Windows #2456: native `gh` GET at pinned SHA, Git blob identity verified, actual report **7,625 bytes and 5,664 characters**.
- Windows #2458: **one-shot** GitHub metadata comment posted after durable isolated `ATTEMPT_RESERVED` latch, then confirmed as `POST_CONFIRMED`. **Never republish this comment**.
- Windows #2459: native Windows `gh` independently fetched **the actual comment and pinned artifact**; exactly **1 comment row + 1 immutable Git blob proof + 1 substantive text receipt** were inserted in an SQLite transaction. Status: `ONE_REAL_GPT_REPORT_STAGED_WAITING_FOR_SECOND`; single-result barrier `AWAITING_SECOND_WORKER`, strict verified-blob barrier `AWAITING_SCOPED_BASE_RESULTS`. Integrity `ok`.
- Windows #2460: 4 separate Windows Python processes simultaneously fetched the same **real comment and immutable Git file** in a **fresh different isolated database**; exactly **one succeeded and three were blocked**, SQLite persisted exactly 1+1+1 rows, `quick_check=ok`, no browser send, no model calls.

**Critical honesty:** Report was authored in the existing current GPT-6 conversation. Its *technical content* is real, but the current ChatGPT session is **not proven to be a separately authenticated browser Worker**. The first result is suitable for human review, not passing the real Worker identity acceptance. It cannot be used to authorize original R1 Master wake or ChatGPT browser message send.

## Code delta (branch experiment/r2-substantive-worker-artifacts-20261009)

- `isolated_github_work_result_inbox_v4.py`: added `validate_only=True` to reuse strict provider comment validator **without writing any SQLite row**.
- `isolated_gh_atomic_verified_intake_v4.py`: validates exact GitHub comment, matching immutable file, substantive work text and full scope **BEFORE** `BEGIN IMMEDIATE`; then commits comment review, pinned Git blob proof and SHA256-only substantive text proof in a **single transaction**.
- `test_isolated_gh_atomic_verified_intake_v4.py`: 14 tests include aborting blob insertion after base insert, aborting text insertion after blob insert, duplicate replay, prior orphan preservation, tampered author/Issue/digest/generation and production directory rejection.
- Before atomic intake, the legacy two-stage latch could leave an unverified base row after a crash. That old row still correctly blocks barriers, and is NOT silently repaired, deleted or reset. The **new atomic endpoint prevents new partial base/blob/text receipts** in the tested error windows.

## Historical test evidence and failed first attempt

- Windows #2454 **FAILED** (1 negative test accidentally used correct Issue ID, not wrong Issue ID). Corrected the test fixture, did not loosen the validator.
- Windows #2455: exact source SHA `17d53ed593cd68dcc9a6adccc0b2ccfe36da628f`, **702/702 V4 core PASS**.
- GitHub Actions #37901603107: source-only and Windows completed SUCCESS on the same code SHA, 702 V4 + 801 GUI + 29 broker = **1,532/1,532 PASS**, `CANDIDATE_VALIDATION=PASS`.
- Follow-up actual document and this handoff changed branch HEAD after the test SHA. New GPT must re-check the final HEAD and its own CI before declaring a clean HEAD.

## Real Windows scratch directories (DO NOT reset or replay)

- `C:\ScorpAgent\experiments\r2-work-artifact-review-current-gpt-20261009\comment-post-once.sqlite3`: **POST_CONFIRMED** for GitHub comment 6076934186.
- `C:\ScorpAgent\experiments\r2-work-artifact-review-current-gpt-20261009\work-artifact-review.sqlite3`: 1+1+1 actual current-GPT result receipts; state waits for second.
- `C:\ScorpAgent\experiments\r2-work-artifact-review-real-concurrency-20261009\work-artifact-review.sqlite3`: separate four-process concurrency experiment 1+1+1, one winner.
- Original live read-only 15m observer remains at pinned old SHA `8da5085f906b4aeeece6fb0ae1f488bc597507b1`. Do NOT edit/restart its Task Scheduler entry.
- Original R1 Master `BLOCKED_AMBIGUOUS` after timeout and GUI `MASTER_CONVERSATION_ROTATION_REQUIRED` remain untouched.
- Original one-shot ChatGPT home canary remains `ATTEMPT_RESERVED`; never repeat tab creation or clear it.

## The next **genuinely separate** GPT Worker

Worker 2 must act in its own ChatGPT conversation/user-authorized browser session. The current GPT cannot simply write another section or call a tool twice to impersonate that session. Task:

- GitHub https://github.com/Scorp96/6/issues/3.
- Assignment `scorp-agent/r2-work-assignments/real-worker-session-two.json`, expected `assignment-genuine-worker-two`, `task-blob-atomicity-audit`, `worker-slot-2`, state_version=1.
- Independently inspect existing two-phase commit and new atomic transaction code, with its own sources, counterexamples and recommendations.
- Commit substantive v2 report to **unique** `scorp-agent/r2-work-artifacts/real-worker-session-two.json` without changing first Worker file or old Master.
- Obtain *exact immutable commit SHA* and Git blob SHA1 / file SHA256; publish one deliberate work-artifact metadata comment to private review issue #2457 only after file is truly committed. Do not pass tokens, private chat text or full deliverable in comment.
- For host-side receiving, use native `gh` and `stage_atomic_pinned_substantive_artifact_for_review` with exact Worker-2 scope and verified author/Issue/comment. Confirm SQLite review has 2 comment, 2 blob, 2 text rows, and strict barrier returns `BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW`.
- Even after the two real work artifacts are present, require a **separate trusted physical session provenance/host terminal policy** before claiming the GPT Worker sessions are both independently verified. Shared GitHub login does not prove two different GPT sessions.

Do not assume a future Worker-2 Github artifact exists today; Issue #3 currently has no verified output.

## Safety & next priority

This implementation specifically addresses crash-safety and exactly-once *result receipt*; it does not solve exactly-once *browser prompt submission*. No host terminal-event issuer has been shown. The next action is to have a genuinely independent GPT session perform Worker 2 assignment, while a read-only reviewer checks both receipts and source integrity. Keep PR #4 Draft and do not merge/restart GUI or transition R1.
