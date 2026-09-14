# SCORP V4 F01/F02 Authority Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and verification-before-completion.

**Goal:** Close the two false-COMPLETE trust gaps found in the frozen V4 experimental candidate: caller-controlled result verification and caller-controlled acceptance artifact hashes.

**Architecture:** Keep SQLite as the only mutable V4 authority. Candidate-result verification must bind the verified digest to immutable data already stored with that result; Acceptance must derive its authoritative artifact hash set from the SQLite release candidate manifest rather than trust the caller-supplied set. Preserve fail-closed behavior and backward-compatible public method shapes where practical.

**Tech Stack:** Python 3.15, unittest, SQLite.

**Spec:** docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml

## Global Constraints

- Work only in C:\ScorpAgent\worktrees\v4-transaction-core.
- No merge, push, deploy, service/Scheduled Task mutation, production switch, production canary, live crash injection, or legacy deletion.
- TDD RED must be observed before any runtime implementation change.
- Preserve SQLite DELETE/FULL/foreign_keys/busy_timeout durability contract and two-slot scheduler behavior.
- AC12 remains short-soak evidence only and must never be relabeled as 24-hour stability.

---

### Task 1: Bind Scheduler verification to recorded result identity

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/tests/test_ac05_dynamic_workers.py`
- Modify: `scorp-agent/master_a_dynamic_v4/scheduler.py`

- [ ] Add a failing test proving an arbitrary 64-hex digest cannot move a recorded candidate to VERIFIED.
- [ ] Run the targeted test and confirm it fails because current verify_candidate accepts the unbound digest.
- [ ] Implement the smallest immutable-result binding check.
- [ ] Re-run targeted and full V4 suites.

### Task 2: Derive Acceptance artifact authority from release manifest

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/tests/test_ac04_false_completion.py`
- Modify: `scorp-agent/master_a_dynamic_v4/acceptance.py`

- [ ] Add a failing test proving caller-supplied artifact hashes cannot make evidence for an artifact absent from the frozen release manifest pass.
- [ ] Run the targeted test and confirm current validator incorrectly PASSes it.
- [ ] Derive authoritative artifact hashes from release_candidates.manifest_json and validate manifest identity fail-closed.
- [ ] Re-run targeted and full V4 suites.

### Task 3: Regression and evidence refresh

- [ ] Run all V4 tests, bridge baseline in its bound scope, compile and git diff --check.
- [ ] Rebuild candidate manifest/install in lab only after runtime changes are frozen.
- [ ] Re-check SQLite PRAGMAs and protected production roots read-only.
- [ ] Do not authorize production; report the new candidate SHA and remaining live-browser/crash/long-soak boundaries.
