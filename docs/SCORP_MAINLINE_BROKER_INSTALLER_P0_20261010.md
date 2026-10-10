# Mainline P0 Broker installer refusal (no production deployment)

Source baseline: `main` at `79610ae3f3864e133f750f0c9dfde40b17a91def` on 2026-10-10.

## What changed

This is a *separate* narrow branch from production `main`, not a child of the candidate PR #26's 716 historical extra commits.

The old `install-broker.ps1` created staging, wrote a shared SYSTEM Broker secret, installed dependencies, stopped/removed a prior service, replaced its files and started SYSTEM Broker automatically. The default installer path was not review-safe.

This candidate:
- Blocks without `-InstallFreshIsolated` and exact acknowledgement `FRESH_ISOLATED_BROKER_NO_PRIOR_STATE`.
- Requires reviewed 64-hex-digit SHA-256 values for four Broker source files before any write.
- Rejects SYSTEM/built-in Administrator, previously existing service, install directory, Python runtime, state or signing key.
- Rechecks whether the service or install directory appeared during staging; refuses to replace it.
- Rollback only deletes the service if `sc create` was confirmed to succeed in this run. It does not overwrite the pre-existing R1 Broker.
- Windows CI tests only **default-denied** and missing-acknowledgement execution paths, verifying neither can create local installation directories; it does NOT exercise real privileged installation.

## Explicit remaining risks

The fresh install path, while guarded, has not been run on a real host and still creates a SYSTEM service using a symmetric HMAC key readable by its allowed interactive executor SID. It is **not** independent human approval for privileged work. This installer patch alone does not shut down mutation authorization inside a pre-existing Broker process; that requires PR #27's separate run-time positive read-only operation allowlist.

The two main-based PRs must be reviewed and jointly regression-tested against one explicit production commit *before* any controlled rollout. Neither approval nor rollout is performed by this Draft. Real Windows ACL and effective-token observations, second GPT Worker and 24-hour soak are still absent.

**Decision: installer source guard review only; production install, task replacement, and cutover not authorized.**
