# SCORP P0 source-only release pin review

Date: 2026-10-10. The output is a **review plan**, not a signed release and not an install instruction.

## Purpose

Current R2 candidate cannot pass the pinned `release-manifest-v4.json` check: four of seven Git source blob SHA-1 values are stale, and all seven release `sha256` fields are the `computed-at-bootstrap` placeholder. The prior no-write integrity validator correctly blocks it. A green offline test suite does not imply a deployable release.

The new stdlib-only `scripts/scorp_p0_release_pin_plan.py` produces an exact-commit, reproducible list of the same seven artifact paths, their Git blob SHA-1 values, and their SHA-256 file digests. It always retains the original release `BLOCKED` assessment and emits `release_authorized=false`.

## Reproduction

On a pinned isolated checkout **after independent source review**:

```powershell
python scripts/scorp_p0_release_pin_plan.py --repo-root . --expected-head <REVIEWED_FULL_40_CHAR_COMMIT>
```

The script reads only immutable `HEAD:path` Git object content, not the mutable working-tree text or CRLF translation. It rejects stale `--expected-head`, absent Git source, and malformed manifests. It computes a deterministic review fingerprint for the proposed manifest. A signature, external verification, or operator approval is **not** claimed.

## Hard boundary

- No rewrite of `scorp-agent/release-manifest-v4.json` or active release pin.
- No automatic matching of old manifest to unreviewed HEAD.
- No updating `C:\ScorpAgent`, service, scheduled task, local HMAC secret, GitHub control-plane issue or authenticated browser.
- The V4 release manifest is primarily for the seven executor/runner/bootstrap files. It is NOT a comprehensive immutable release manifest for two GPT Workers or the SYSTEM Broker.
- The actual Windows effective token, user/group ACLs, Broker key isolation and real browser session identity require independent live evidence.
- The original R1 blocked ambiguous browser intent is never retried.

## Acceptance

Six isolated Git-source positive and negative tests must show: exact HEAD produces only a review proposal; dirty checkout and uncommitted manifest edits cannot change the plan; wrong/old expected HEAD blocks; a missing Git repository blocks. A Windows CI job must check exactly seven fixed-format digests, previous release remains BLOCKED, and all inherited P0/GUI/R2 regressions pass.

Only **after** a human reviews exact source code, accepts hashes and approves a separate migration with rollback may a release manifest be updated, tagged and tested. Code compilation and CI are not substitutes for 24-hour real-host soak.
