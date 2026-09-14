# SCORP GPT computer-control bridge

This repository contains the source and operating instructions for the SCORP
Master A + dynamic Worker system. Start with [GPT_START_HERE.md](GPT_START_HERE.md)
before running anything.

The reasoning controller for this design is **GPT-5.6 Sol**. `Scorp96/6` is the
Git repository name; it is not a model identifier, and no GPT-6 runtime is
required or implied. The local components are deterministic state, scheduling,
execution, and evidence layers. They do not silently substitute another model.

The repository includes two generations of the system:

- `scorp-agent/chatgpt-gui-bridge/` is the existing ChatGPT browser bridge. It
  can use Windows MCP or Chrome Use through a logged-in browser session.
- `scorp-agent/master_a_dynamic_v4/` is the SQLite transaction core with one
  logical Master A and two dynamic Worker slots.
- `scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py` and `gui_engine.py`
  connect the V4 core to the existing asynchronous GUI transport.

The source is safe to test offline. It does not contain browser profiles,
cookies, authentication data, SQLite state, runtime virtual environments,
GitHub credentials, or model API keys. Production switching is intentionally
not performed by repository scripts.

The preserved file `22` is the original content of this repository. It is not a
runtime instruction and must not be treated as an authority for the bridge.

The root `docs/handoffs/SCORP_V4_VERIFICATION.json` is retained because the
upstream regression contract reads that path. It is a compatibility contract
fixture from the original candidate. The fresh Git6 validation record is
`docs/handoffs/SCORP_V4_GIT6_VALIDATION.json`.
