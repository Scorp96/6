# P0 mainline no-write release manifest integrity preflight

Reviewed baseline: `main@79610ae3f3864e133f750f0c9dfde40b17a91def` (2026-10-10).

This independent main-derived Draft ports *only* two Python standard-library release inspection tools and their 16 negative/positive tests from earlier isolated P0 research. It does not overwrite `scorp-agent/release-manifest-v4.json`, install Windows services, change task principal, launch Chrome, open credentials or approve release.

**Source authority:**
- Examine exactly seven expected pinned executable/runner/bootstrap artifacts.
- Read committed Git blob data, not Windows CRLF-translated worktree content.
- Require SHA-1 Git object and full SHA-256 digest equality; `computed-at-bootstrap` placeholder is UNPINNED.
- Reject missing/duplicate JSON keys, symlink/out-of-repository paths, future unknown file sets and stale expected commit.
- A matching set returns only `READY_FOR_MANUAL_RELEASE_REVIEW`, with `release_authorized=false`; CI asserts current main manifest still BLOCKED.
- Emit a deterministic seven-file review-only JSON artifact from the **exact CI HEAD** for independent operator comparison. The artifact is not signed and not approved for deployment.

**Real-host STOP:** Connected local Codex Bridge read-only observations saw SYSTEM Broker Running Auto, but three of its installed source hashes differed from main and review PR #29. Its on-disk Broker gate markers were absent. Windows Scheduled Task effective RunLevel could not be verified. Real Worker2 sessions, task-scoped human privilege approval, current process module identity, ambiguous Master reconciliation and 24h soak remain unverified. No CI result can override this STOP.

For a production release: independently review code and exact hashes, prepare separate signature/approval, verify Windows effective token & ACLs and rollback for R1, stage a non-destructive canary, then test two genuine authenticated browser Workers and 24 hours of real host persistence. Do not publish a green manifest solely to make CI pass.
