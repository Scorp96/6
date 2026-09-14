# Persistent ChatGPT Conversation Bridge Design

## Goal
Change the GUI bridge from `one continuation = one ChatGPT conversation` to `one task = one durable ChatGPT conversation with many continuation turns`, while preserving exactly-once publication, request/generation/hash binding, fail-closed behavior, and the existing V2 mutation protocol.

## Architecture
The bridge owns a durable task-to-conversation mapping in `conversation-registry.json`. On the first continuation for a task it opens a new ChatGPT window; after the response is captured it stores the canonical `https://chatgpt.com/c/<id>` URL. Later continuations for the same task reopen that URL in a dedicated Chrome window and append only the new bound continuation prompt.

Conversation continuity is an optimization, never an identity authority. Every turn still carries a unique `REQUEST_ID`, continuation generation, previous action/result hash, and produces a separately bound decision. GitHub/Orchestrator remain authoritative for exactly-once adoption and execution.
## Conversation Lifecycle
A task without a valid mapping uses `https://chatgpt.com/` exactly once. A task with a valid mapping launches a dedicated Chrome window at the saved conversation URL. The bridge never reuses a conversation owned by a different task.

A conversation mapping becomes reusable only after a completed owned turn yields a canonical conversation URL. Publication failure does not erase the mapping because `RESPONSE_CAPTURED` retries must publish the same decision without another GUI turn. Terminal tasks retain the mapping as audit evidence but no longer open it.

## Fail-Closed Rules
If the saved URL is malformed, belongs to an unsupported host/path, or the opened page cannot prove Chrome focus, the bridge does not submit. It records a retryable transport error. It must not silently fall back to a new chat for the same live continuation, because that can create duplicate reasoning turns.

If a conversation is genuinely unavailable on a later continuation, recovery may rotate to a new conversation only through an explicit bounded rotation path that records the old URL and reason. Automatic rotation is out of scope for this first release.

## Rate-Limit Policy
Only real Orchestrator continuations may invoke ChatGPT. Production acceptance must not create diagnostic GPT turns. A task may have at most one active GUI lease, and the daemon must not open another conversation while that lease is valid.
## Data and Interfaces
`conversation-registry.json` is keyed by `task_id`. Each entry stores `conversation_url`, `created_request_id`, `last_request_id`, `last_decision_id`, and `updated_at`. Writes are atomic and UTF-8.

`run_live_turn(prompt, request_id, conversation_url=None)` accepts an optional validated URL. `None` means first-turn new chat; a URL means continue that task's existing conversation. The return remains `(mutation, snapshot, canonical_conversation_url)`.

The worker resolves the task mapping before setting `GUI_INFLIGHT`, passes it to the GUI turn, and persists the returned canonical URL after response capture. Existing decision adoption and `RESPONSE_CAPTURED` publication retries never invoke the GUI.

## Testing and Acceptance
Unit tests cover URL validation, new-chat first turn, existing-conversation reuse, task isolation, persistence across worker restart, no GUI on captured/adopted decisions, and fail-closed invalid URL behavior. Existing 56 tests remain green.

Production acceptance uses the current live task only: after the stale lease is no longer active, one continuation establishes or adopts its conversation; the next Orchestrator continuation must reuse the exact same canonical URL. Acceptance requires two distinct request IDs in one conversation, no extra ChatGPT conversation created by the bridge, exactly one trusted decision per request, and normal V4 execution.