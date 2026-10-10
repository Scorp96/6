# Mainline P0 Bootstrap + task installer authorization review

Date: 2026-10-10. Based on exact `main` commit `79610ae3f3864e133f750f0c9dfde40b17a91def`. **Draft-only; no Windows host installation.**

The current `main` Bootstrap creates state/staging directories and can fetch/execute candidate self-tests **before** checking `RunLevel`. Current `main` also permits an existing `Highest` Windows task to continue and carries a legacy installer that unregisters the existing task, replaces it and immediately launches the new task. These operations endanger R1 and defeat the P0 least-privilege target.

This narrow source transplant copies the already isolated Windows-CI-tested Bootstrap and task installer blobs from review PR #26 **without importing any candidate ancestry**:
- A task must exist, be Interactive, RunLevel `Limited`, not built-in Administrator/SYSTEM, with UAC enabled, **before any bootstrap staging write, candidate self-test, task backup or task stop**.
- If the authority precheck fails, no new failure-evidence or staging write is performed. Later staged rollback retains its original behavior.
- Task installer refuses by default. The only fresh-only path requires exact operator acknowledgement and pinned Relay SHA-256, refuses to overwrite an already-registered task (also after UAC), and registers a **Disabled**, Interactive, `Limited` task without automatic start.
- Parser/static order checks and default-denied invocations are executed only on ephemeral Windows CI.

**Caution:** This code refuses the legacy `Highest` path; applying it to the existing production R1 without a separate, operator-reviewed migration could leave SCORP unable to upgrade. A task set to `Limited` is not a proof of restricted file ACLs/effective token. The previous real Windows read-only Codex Bridge observation could not read the task principal (metadata unavailable), so no host entitlement is certified.

The separate combined Broker source review is [PR #29](https://github.com/Scorp96/6/pull/29), not merged/deployed; local SYSTEM Broker code hashes do not correspond to PR #29. Two independently authenticated Worker sessions, original ambiguous Master recovery, and a 24-hour actual host test remain NOT VERIFIED.

**Result required: Windows isolated CI green and 1-commit/7-file main-only review. Production cutover NOT AUTHORIZED.**
