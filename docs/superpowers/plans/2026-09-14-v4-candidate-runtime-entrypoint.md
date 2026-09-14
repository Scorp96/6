# V4 candidate runtime entrypoint

## Goal
Provide an explicit Git6 V4 runtime entrypoint that can initialize/describe the SQLite candidate without invoking the legacy V3 JSON bridge or browser I/O.

## Steps
- [x] Add a failing unit test for JSON status output and browser-free behavior.
- [x] Implement `v4_runtime.py` with a fail-closed unavailable browser engine and `--describe` command.
- [x] Add `run-v4-candidate.ps1` as the documented Windows entrypoint with explicit repository-relative paths.
- [x] Update GPT_START_HERE.md with the exact command and legacy boundary.
- [x] Run focused tests, full V4/bridge tests, compile, secret scan, diff check.
- [x] Commit and push to Git6; report the exact commit and remaining unverified browser items.

## Boundary
This does not claim a live browser canary, automatic Master re-planning, or production cutover. The legacy `run-bridge.ps1` remains compatibility-only until a separate migration is implemented.

