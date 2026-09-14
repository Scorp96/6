# ChatGPT GUI Continuation Bridge V1 Spec

**Objective:** Add a local deterministic bridge that consumes Orchestrator continuation requests, opens a fresh ChatGPT web conversation through Windows-MCP, obtains a GPT-5.6 Sol decision, validates it, and posts exactly one bound continuation decision to the private control repository without user interaction.

## Architecture

- Intelligence remains GPT-5.6 Sol in the authenticated ChatGPT UI.
- Local bridge contains no reasoning model and does not invent next actions.
- Windows-MCP is the GUI execution hand.
- Chrome is the primary ChatGPT host for V1 because it has a verified logged-in main window.
- `Scorp96/scorp-control-plane` remains the runtime control repo.
- Existing Orchestrator continuation protocol remains unchanged.
- Existing V4 remains the only deterministic action executor.

## Input / Output

Input is a published `scorp.orchestrator/continuation-request-v1` plus the matching current task and bounded previous V4 result evidence.
Output is exactly one `SCORP_CONT_DECISION` GitHub comment whose JSON conforms to `scorp.orchestrator/continuation-decision-v1` and is identity-bound to the request.

## Safety / Truthfulness

- Only `safety_class=standard` requests may be auto-processed.
- The bridge must never invoke Codex or any local reasoning model.
- It must fail closed if the browser, ChatGPT editor, response terminal state, request identity, task generation, or parsed mutation is ambiguous.
- A visible response marker is insufficient; the bridge waits until ChatGPT streaming is terminal before parsing.
- Binding fields are generated deterministically by the bridge, not trusted from model output.
- Model output supplies only the requested mutation and an optional concise rationale.
