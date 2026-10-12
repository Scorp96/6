# GPT-6 Executor / Codex Activator-Recorder — isolated acceptance lane

Status: EXPERIMENTAL / NOT_PRODUCTION.

1. GPT-6 is the sole reasoning, work-execution, and acceptance authority in each bound GPT turn.
2. Codex is the local activator and append-only recorder, NOT the substitute GPT master and NOT the task executor.
3. SQLite binds immutable project_id, conversation_url, master_epoch, turn_id, request_id and prompt_sha256. Do not repeat a browser submit after MAY_HAVE_SUBMITTED.
4. Any ambiguous browser result, lost authentication, unexpected conversation, session collision, stale epoch, or unavailable physical host -> BLOCKED; preserve evidence.
5. No login bypass, scraping, credential use, automatic webpage send, or production modification is permitted during this isolated acceptance run.
6. Offline tests can establish deterministic state transitions only, NOT real GPT wake/Chrome/28-minute/25-minute behavior.
