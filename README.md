# SCORP GPT computer-control bridge

This repository contains the source and operating instructions for the SCORP
Master A + dynamic Worker system. Start with [GPT_START_HERE.md](GPT_START_HERE.md)
before running anything.

If an ordinary web GPT is taking over, read
[docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.md](docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.md)
first. Git visibility is not local-computer access; the handoff includes a
read-only Windows preflight and the exact boundary between web planning and
local execution.

The reasoning controller for this design is **GPT-5.6 Sol**. `Scorp96/6` is the
Git repository name; it is not a model identifier, and no GPT-6 runtime is
required or implied. The local components are deterministic state, scheduling,
execution, and evidence layers. They do not silently substitute another model.

The repository includes two generations of the system:

- `scorp-agent/chatgpt-gui-bridge/` is the existing ChatGPT browser bridge. It
  can use Windows MCP or Chrome Use through a logged-in browser session.
- `scorp-agent/master_a_dynamic_v4/` is the SQLite transaction core with one
  logical Master A and two dynamic Worker slots.
- `scorp-agent/master_a_dynamic_v4/master_controller.py` is the model-agnostic
  Master A control surface. It turns one structured GPT plan into durable graph
  admission, bounded Worker dispatch, recovery, and an independent completion
  decision.
- `scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py` and `gui_engine.py`
  connect the V4 core to the existing asynchronous GUI transport.
- `scorp-agent/chatgpt-gui-bridge/tools/v4_master_supervisor_runtime.py`
  provides the bounded local Master monitor. It renews an existing SQLite
  lease, journals each decision, and forbids browser sends; `--rebind` is a
  separate read-only physical-session check.

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
