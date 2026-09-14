# Repository operating rules

Read `GPT_START_HERE.md` first.

- Treat SQLite V4 state and the root contract as authoritative in candidate
  mode. Legacy JSON is import/read-only compatibility input.
- Default queue: `Scorp96/666`. The historical
  `Scorp96/scorp-control-plane` queue requires explicit migration mode.
- Default Worker concurrency is two. Do not increase it automatically.
- Do not bypass login, CAPTCHA, browser ambiguity, lease fencing, or the
  acceptance gate.
- Do not run production scheduled-task changes from this repository.
- Do not commit cookies, browser profiles, tokens, passwords, API keys,
  `*.sqlite3`, runtime folders, logs, or local state.
- Before claiming completion, run the offline validation script and report
  separately whether real-browser and production-canary checks were run.
