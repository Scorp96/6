# SCORP V4 verification record

## Current boundary

Candidate `682005bb1edab6149517b2fa7398fd63dbda945f` passed the frozen-code
regression, guarded named-lab installation, real browser/login gate, and the revised
short AC12 performance/recovery soak. This is a validated experimental candidate,
not a production cutover. PDF delivery and its rendering checks were cancelled by
the user before generation.

## Fresh results

| Check | Result | Observation |
|---|---|---|
| V4 transaction-core tests | PASS | 42 tests in 4.077 seconds, zero failures |
| Full bridge regression | PASS | 397 tests in 11.654 seconds, zero failures |
| New Python source compilation | PASS | 35 files compiled in memory |
| Git whitespace check | PASS | no errors |
| V4 critical PowerShell hardening | PASS | `V4_CRITICAL_HARDENING_PASS` |
| V4 production PowerShell hardening | PASS | `V4_PRODUCTION_HARDENING_PASS` |
| V4 self-heal PowerShell hardening | PASS | `V4_SELFHEAL_HARDENING_PASS` |
| Legacy/reference scan | PASS | 15 named targets scanned and classified |
| Named lab installation | PASS | candidate and 21-file manifest matched; SQLite settings matched |
| Protected production roots | PASS | before/after/fresh SHA-256 all `ac6d6b8c…f7b5b` |
| Real browser/login gate | PASS | Plus login, one marker, one ACK, one submit, zero duplicate |
| AC12 short soak | PASS | 10.201 s, 48 cycles, 144 tasks, 14.116 tasks/s, 2 recoveries, 0 errors |

The bridge regression was run from its required `scorp-agent/chatgpt-gui-bridge`
working directory. PowerShell checks use their own disposable fixtures; production
installers, bootstrap scripts, live watchdog tests, and scheduled-task mutation paths
were not executed.

## Interpretation

These results verify code-level behavior and protect the existing 397-test baseline.
Crash-injection tests and the short soak use an injected browser engine around the
real local adapter state machine; the separate live gate proves one real ChatGPT
submission and response, not crash recovery against the live service. The passing
short soak is useful performance/recovery evidence but does not establish 24-hour
continuity or authorize merge, push, deployment, deletion, or production cutover.
