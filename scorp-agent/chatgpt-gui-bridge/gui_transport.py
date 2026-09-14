import asyncio
import json
import os
import re
import time

from bridge_core import extract_mutation, extract_role_response, find_chat_editor, response_is_terminal

CHATGPT_URL = "https://chatgpt.com/"
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_CLIPBOARD_PREFIX = "Clipboard content:\n"
_CLIPBOARD_EMPTY = "Clipboard is empty or contains non-text data."


class ChatGptThrottleError(RuntimeError):
    pass


def chatgpt_throttle_visible(snapshot: str) -> bool:
    text = str(snapshot or "")
    chinese = (
        "请求过于频繁" in text
        and "重试" in text
        and any(marker in text for marker in ("稍等", "稍后", "分钟"))
    )
    lower = text.lower()
    english = (
        "too many requests" in lower
        and any(marker in lower for marker in ("retry", "try again", "wait"))
    )
    return chinese or english


def normalize_snapshot_text(raw: str) -> str:
    raw = raw or ""
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return "\n".join(str(x) for x in value)
        if isinstance(value, str):
            return value
    except json.JSONDecodeError:
        pass
    return raw


def result_text(result) -> str:
    parts = []
    for item in getattr(result, "content", []) or []:
        if getattr(item, "type", None) == "text":
            parts.append(getattr(item, "text", ""))
    return normalize_snapshot_text("\n".join(parts))


def clipboard_content_from_result(text: str) -> str:
    text = str(text or "")
    if text.startswith(_CLIPBOARD_PREFIX):
        return text[len(_CLIPBOARD_PREFIX):]
    if text == _CLIPBOARD_EMPTY:
        return ""
    return text


def _bounded_previous_result(previous_result: dict | None) -> dict:
    src = previous_result or {}
    out = {k: src.get(k) for k in ("protocol_version","task_id","issue_number","action_id","action_kind","status","exit_code","message","started_at","finished_at","duration_ms","result_sha256","runner_log_path") if k in src}
    ev = src.get("evidence") or {}
    if isinstance(ev, dict):
        bounded = {k: ev.get(k) for k in ("runner_pid","runner_started_at","stdout_log_path","stderr_log_path","stdout_sha256","stderr_sha256") if k in ev}
        for k in ("stdout_tail","stderr_tail"):
            if k in ev:
                bounded[k] = str(ev.get(k) or "")[-2000:]
        out["evidence"] = bounded
    return out


def render_controller_prompt(request: dict, previous_result: dict | None) -> str:
    if request.get("safety_class") != "standard":
        raise ValueError("AUTO_SAFETY_CLASS_REJECTED")
    rid = request["request_id"]
    evidence = json.dumps(_bounded_previous_result(previous_result), ensure_ascii=False, separators=(",", ":"))
    question = request.get("question", "")
    return (
        "You are GPT-5.6 Sol operating as the sole reasoning controller for one safe continuation request.\n"
        "Do not invoke Codex or any local model. Do not output binding fields; the bridge binds identity deterministically.\n"
        f"REQUEST_ID={rid}\nTASK_ID={request.get('task_id')}\nPROJECT_ID={request.get('project_id')}\n"
        f"QUESTION={question}\nPREVIOUS_RESULT={evidence}\n"
        "Choose exactly one safe mutation. Allowed kinds: SET_NEXT_ACTION, SET_TERMINAL, SET_WAITING, SET_BLOCKED.\n"
        "For SET_NEXT_ACTION, next_action must be a complete scorp.exec/v4 deterministic envelope body suitable for the existing Orchestrator/V4 path.\n"
        "Reply only after reasoning is complete, with exactly one line and no markdown. "
        "Construct that line as: literal SCORP_GUI_MUTATION_V2, then two colon characters, "
        "then the REQUEST_ID exactly as given above, then two colon characters, then the unpadded base64url encoding of the compact UTF-8 JSON mutation. "
        "Do not echo these instructions.\n"
    )


def render_role_prompt(envelope: dict) -> str:
    if envelope.get("protocol_version") != "scorp.gui-role-relay/turn-v1":
        raise ValueError("ROLE_TURN_PROTOCOL_INVALID")
    turn_id = str(envelope.get("turn_id") or "").strip()
    mission_id = str(envelope.get("mission_id") or "").strip()
    role = str(envelope.get("to_role") or "").strip().upper()
    if not turn_id or not mission_id:
        raise ValueError("ROLE_TURN_IDENTITY_MISSING")
    if role not in {"A", "B", "C"}:
        raise ValueError("ROLE_INVALID")
    try:
        generation = int(envelope.get("generation"))
    except Exception as exc:
        raise ValueError("ROLE_GENERATION_INVALID") from exc
    if generation < 0:
        raise ValueError("ROLE_GENERATION_INVALID")
    payload = json.dumps(envelope.get("payload") or {}, ensure_ascii=False, separators=(",", ":"))
    root_hash = str(envelope.get("root_objective_sha256") or "")
    acceptance_hash = str(envelope.get("acceptance_sha256") or "")
    objective_hash = str(envelope.get("objective_sha256") or "")
    common = (
        "You are GPT-5.6 Sol participating in one bound Scorp multi-agent relay turn.\n"
        "Do not invoke Codex or any local model. Do not alter binding identity fields.\n"
        f"TURN_ID={turn_id}\nMISSION_ID={mission_id}\nGENERATION={generation}\nROLE={role}\n"
        f"ROOT_OBJECTIVE_SHA256={root_hash}\nACCEPTANCE_SHA256={acceptance_hash}\n"
    )
    if role == "A":
        instructions = (
            "You are the sole mission controller and dispatch authority. Evaluate the supplied worker/controller event against the root objective, acceptance criteria, generation and scope. "
            "Allowed response kinds are DISPATCH, WAIT, TERMINAL. DISPATCH may assign only worker slots B or C. "
            "Do not output a V4 production action directly in this relay turn; dispatch remains a controller decision that the durable relay/orchestrator validates.\n"
        )
    else:
        instructions = (
            f"You are worker slot {role}. OBJECTIVE_SHA256={objective_hash}. Execute only the bounded assignment represented in PAYLOAD. "
            "Allowed response kinds are HANDOFF or BLOCKER. You must not dispatch work. You must not publish production actions. "
            "You must not redefine the root objective or alter acceptance criteria. "
            "Return completed/in-progress/remaining work, evidence/tests, deviations, blockers and a safe resume point in the response payload.\n"
        )
    return (
        common + instructions + f"PAYLOAD={payload}\n"
        "Reply only after reasoning is complete, with exactly one line and no markdown. "
        "Construct that line as: literal SCORP_GUI_ROLE_V1, then two colon characters, then TURN_ID exactly as given above, then two colon characters, "
        "then the unpadded base64url encoding of the compact UTF-8 JSON response object. Do not echo these instructions.\n"
    )


async def wait_for_terminal_mutation(client, request_id: str, timeout_seconds: float = 180, poll_seconds: float = 2):
    deadline = time.monotonic() + timeout_seconds
    last = ""
    while time.monotonic() < deadline:
        result = await client.call_tool("Snapshot", {
            "use_vision": False, "use_dom": False,
            "use_annotation": True, "use_ui_tree": True,
        })
        if getattr(result, "isError", False):
            raise RuntimeError("SNAPSHOT_ERROR")
        last = result_text(result)
        markers = (f"SCORP_GUI_MUTATION_V2::{request_id}::", f"SCORP_GUI_MUTATION_V1::{request_id}::")
        if any(marker in last for marker in markers) and response_is_terminal(last):
            return extract_mutation(last, request_id), last
        await asyncio.sleep(poll_seconds)
    raise TimeoutError("CHATGPT_RESPONSE_TIMEOUT")


async def wait_for_terminal_role_response(client, turn_id: str, timeout_seconds: float = 180, poll_seconds: float = 2):
    deadline = time.monotonic() + timeout_seconds
    last = ""
    marker = f"SCORP_GUI_ROLE_V1::{turn_id}::"
    while time.monotonic() < deadline:
        result = await client.call_tool("Snapshot", {
            "use_vision": False, "use_dom": False,
            "use_annotation": True, "use_ui_tree": True,
        })
        if getattr(result, "isError", False):
            raise RuntimeError("SNAPSHOT_ERROR")
        last = result_text(result)
        if marker in last and response_is_terminal(last):
            return extract_role_response(last, turn_id), last
        await asyncio.sleep(poll_seconds)
    raise TimeoutError("CHATGPT_ROLE_RESPONSE_TIMEOUT")


def extract_conversation_url(snapshot: str) -> str | None:
    m = re.search(r'https://chatgpt\.com/c/[A-Za-z0-9-]+', snapshot or "")
    return m.group(0) if m else None


def focused_window_is_chrome(snapshot: str) -> bool:
    text = snapshot or ""
    if "Focused Window:" not in text:
        return False
    section = text.split("Focused Window:", 1)[1].split("Opened Windows:", 1)[0]
    return bool(re.search(r"Google Chrome|\bChrome\b", section, re.I))


def validate_conversation_url(conversation_url: str | None) -> str:
    if conversation_url is None:
        return CHATGPT_URL
    if not re.fullmatch(r"https://chatgpt\.com/c/[A-Za-z0-9-]+", conversation_url):
        raise ValueError("CONVERSATION_URL_INVALID")
    return conversation_url


def _canonical_focused_chatgpt_url(value: str) -> str | None:
    value = str(value or "").strip()
    if value in {"https://chatgpt.com", CHATGPT_URL}:
        return CHATGPT_URL
    urls = sorted({u.rstrip("/") for u in re.findall(r"https://chatgpt\.com/c/[A-Za-z0-9-]+/?", value)})
    if len(urls) > 1:
        raise ValueError("ACTOR_GUI_FOCUSED_URL_AMBIGUOUS")
    if len(urls) == 1:
        url = urls[0]
        if url.rsplit("/", 1)[-1] == "WEB":
            return None
        return url
    return None


async def _copy_focused_chatgpt_url(client) -> str | None:
    await client.call_tool("Shortcut", {"shortcut": "ctrl+l"})
    try:
        await client.call_tool("Shortcut", {"shortcut": "ctrl+c"})
        address = await client.call_tool("Clipboard", {"mode": "get"})
        if getattr(address, "isError", False):
            raise RuntimeError("CHROME_ADDRESS_READ_FAILED")
        return _canonical_focused_chatgpt_url(clipboard_content_from_result(result_text(address)))
    finally:
        await client.call_tool("Shortcut", {"shortcut": "esc"})


async def focused_chatgpt_url(client) -> str | None:
    previous = await client.call_tool("Clipboard", {"mode": "get"})
    if getattr(previous, "isError", False):
        raise RuntimeError("CLIPBOARD_READ_FAILED")
    previous_text = clipboard_content_from_result(result_text(previous))
    try:
        return await _copy_focused_chatgpt_url(client)
    finally:
        await client.call_tool("Clipboard", {"mode": "set", "text": previous_text})


async def acquire_chatgpt_window(
    client,
    wait_seconds: int = 5,
    conversation_url: str | None = None,
    *,
    address_timeout_seconds: float = 3.0,
    address_poll_seconds: float = 0.25,
    address_max_attempts: int = 16,
    foreground_timeout_seconds: float = 3.0,
    foreground_poll_seconds: float = 0.25,
    foreground_max_attempts: int = 16,
    foreground_window_is_chrome_fn=None,
):
    target_url = validate_conversation_url(conversation_url)
    address_timeout_seconds = float(address_timeout_seconds)
    address_poll_seconds = float(address_poll_seconds)
    address_max_attempts = int(address_max_attempts)
    foreground_timeout_seconds = float(foreground_timeout_seconds)
    foreground_poll_seconds = float(foreground_poll_seconds)
    foreground_max_attempts = int(foreground_max_attempts)
    if address_timeout_seconds <= 0:
        raise ValueError("CHROME_ADDRESS_TIMEOUT_INVALID")
    if address_poll_seconds < 0:
        raise ValueError("CHROME_ADDRESS_POLL_INVALID")
    if address_max_attempts <= 0:
        raise ValueError("CHROME_ADDRESS_ATTEMPTS_INVALID")
    if foreground_timeout_seconds <= 0:
        raise ValueError("CHROME_FOREGROUND_TIMEOUT_INVALID")
    if foreground_poll_seconds < 0:
        raise ValueError("CHROME_FOREGROUND_POLL_INVALID")
    if foreground_max_attempts <= 0:
        raise ValueError("CHROME_FOREGROUND_ATTEMPTS_INVALID")
    launched = await client.call_tool("App", {
        "mode": "launch_executable",
        "executable": CHROME_EXE,
        "args": ["--new-window", target_url],
    })
    if getattr(launched, "isError", False):
        raise RuntimeError("CHROME_LAUNCH_FAILED")
    await client.call_tool("Wait", {"duration": wait_seconds})
    foreground_deadline = time.monotonic() + foreground_timeout_seconds
    foreground_attempts = 0
    text = ""
    while foreground_attempts < foreground_max_attempts:
        foreground_attempts += 1
        snap = await client.call_tool("Snapshot", {
            "use_vision": False, "use_dom": False,
            "use_annotation": True, "use_ui_tree": True,
        })
        text = result_text(snap)
        foreground_is_chrome = (
            bool(foreground_window_is_chrome_fn())
            if foreground_window_is_chrome_fn is not None
            else focused_window_is_chrome(text)
        )
        if foreground_is_chrome:
            break
        if foreground_attempts >= foreground_max_attempts or time.monotonic() >= foreground_deadline:
            raise RuntimeError("CHROME_FOREGROUND_NOT_ACQUIRED")
        if foreground_poll_seconds:
            if foreground_poll_seconds.is_integer():
                await client.call_tool("Wait", {"duration": int(foreground_poll_seconds)})
            else:
                await asyncio.sleep(foreground_poll_seconds)
        else:
            await asyncio.sleep(0)
    previous = await client.call_tool("Clipboard", {"mode": "get"})
    if getattr(previous, "isError", False):
        raise RuntimeError("CLIPBOARD_READ_FAILED")
    previous_text = clipboard_content_from_result(result_text(previous))
    deadline = time.monotonic() + address_timeout_seconds
    attempts = 0
    try:
        while attempts < address_max_attempts:
            attempts += 1
            observed = await _copy_focused_chatgpt_url(client)
            if observed == target_url:
                return text
            if attempts >= address_max_attempts or time.monotonic() >= deadline:
                break
            if address_poll_seconds:
                if address_poll_seconds.is_integer():
                    await client.call_tool("Wait", {"duration": int(address_poll_seconds)})
                else:
                    await asyncio.sleep(address_poll_seconds)
            else:
                await asyncio.sleep(0)
        raise RuntimeError("CHROME_TARGET_URL_MISMATCH")
    finally:
        await client.call_tool("Clipboard", {"mode": "set", "text": previous_text})


async def close_completed_chat_window(client, snapshot: str, request_id: str) -> bool:
    mutation_markers = (f"SCORP_GUI_MUTATION_V2::{request_id}::", f"SCORP_GUI_MUTATION_V1::{request_id}::")
    role_marker = f"SCORP_GUI_ROLE_V1::{request_id}::"
    if not focused_window_is_chrome(snapshot) or not (any(marker in (snapshot or "") for marker in mutation_markers) or role_marker in (snapshot or "")):
        return False
    await client.call_tool("Shortcut", {"shortcut": "alt+f4"})
    return True


async def open_new_chat_and_submit(
    client, prompt: str, page_wait_seconds: int = 5, conversation_url: str | None = None,
    *, foreground_window_is_chrome_fn=None,
    submit_timeout_seconds: float = 5.0,
    submit_poll_seconds: float = 0.25,
    submit_max_attempts: int = 24,
):
    await acquire_chatgpt_window(
        client, page_wait_seconds, conversation_url,
        foreground_window_is_chrome_fn=foreground_window_is_chrome_fn,
    )
    submit_timeout_seconds = float(submit_timeout_seconds)
    submit_poll_seconds = float(submit_poll_seconds)
    submit_max_attempts = int(submit_max_attempts)
    if submit_timeout_seconds <= 0:
        raise ValueError("CHAT_SUBMIT_TIMEOUT_INVALID")
    if submit_poll_seconds < 0:
        raise ValueError("CHAT_SUBMIT_POLL_INVALID")
    if submit_max_attempts <= 0:
        raise ValueError("CHAT_SUBMIT_ATTEMPTS_INVALID")
    clip = await client.call_tool("Clipboard", {"mode": "get"})
    old_clipboard = clipboard_content_from_result(result_text(clip))
    try:
        snap = await client.call_tool("Snapshot", {
            "use_vision": False, "use_dom": False,
            "use_annotation": True, "use_ui_tree": True,
        })
        text = result_text(snap)
        try:
            editor = find_chat_editor(text)
        except ValueError as exc:
            if str(exc) == "CHAT_EDITOR_NOT_FOUND" and chatgpt_throttle_visible(text):
                raise ChatGptThrottleError("CHATGPT_REQUEST_THROTTLED") from exc
            raise
        await client.call_tool("Type", {
            "text": " ", "loc": list(editor), "clear": True, "press_enter": False,
        })
        await client.call_tool("Shortcut", {"shortcut": "backspace"})
        for start in range(0, len(prompt), 1000):
            chunk = prompt[start:start + 1000]
            await client.call_tool("Clipboard", {"mode": "set", "text": chunk})
            await client.call_tool("Shortcut", {"shortcut": "ctrl+v"})
        submit_deadline = time.monotonic() + submit_timeout_seconds
        submit_attempts = 0
        while submit_attempts < submit_max_attempts:
            submit_attempts += 1
            submit_snap = await client.call_tool("Snapshot", {
                "use_vision": False, "use_dom": False,
                "use_annotation": True, "use_ui_tree": True,
            })
            submit_text = result_text(submit_snap)
            if chatgpt_throttle_visible(submit_text):
                raise ChatGptThrottleError("CHATGPT_REQUEST_THROTTLED")
            m = re.search(r'\((\d+),(\d+)\).*?(?:Send prompt|Send message|发送提示|发送消息)', submit_text, re.I)
            if m is not None:
                await client.call_tool("Click", {"loc": [int(m.group(1)), int(m.group(2))]})
                return text
            try:
                submit_editor = find_chat_editor(submit_text)
            except ValueError as exc:
                if str(exc) != "CHAT_EDITOR_NOT_FOUND":
                    raise
                if submit_attempts >= submit_max_attempts or time.monotonic() >= submit_deadline:
                    raise
                if submit_poll_seconds:
                    if submit_poll_seconds.is_integer():
                        await client.call_tool("Wait", {"duration": int(submit_poll_seconds)})
                    else:
                        await asyncio.sleep(submit_poll_seconds)
                else:
                    await asyncio.sleep(0)
                continue
            await client.call_tool("Type", {
                "text": " ",
                "loc": list(submit_editor),
                "clear": False,
                "caret_position": "end",
                "press_enter": True,
            })
            return text
        raise ValueError("CHAT_EDITOR_NOT_FOUND")
    finally:
        await client.call_tool("Clipboard", {"mode": "set", "text": old_clipboard})


async def _run_chat_turn(prompt: str, turn_id: str, timeout_seconds: int, conversation_url: str | None, role_mode: bool):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = dict(os.environ)
    env["ANONYMIZED_TELEMETRY"] = "false"
    env["WINDOWS_MCP_DISABLE_FLASH"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    tools = "App,Shortcut,Clipboard,Click,Type,Wait,Snapshot"
    server = StdioServerParameters(
        command="uvx",
        args=["--from", "windows-mcp==0.8.5", "windows-mcp", "serve", "--tools", tools],
        env=env,
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            await open_new_chat_and_submit(client, prompt, conversation_url=conversation_url)
            if role_mode:
                response, snapshot = await wait_for_terminal_role_response(client, turn_id, timeout_seconds)
            else:
                response, snapshot = await wait_for_terminal_mutation(client, turn_id, timeout_seconds)
            canonical_url = extract_conversation_url(snapshot)
            await close_completed_chat_window(client, snapshot, turn_id)
            return response, snapshot, canonical_url


async def run_live_turn(prompt: str, request_id: str, timeout_seconds: int = 900, conversation_url: str | None = None):
    return await _run_chat_turn(prompt, request_id, timeout_seconds, conversation_url, False)


async def run_role_turn(prompt: str, turn_id: str, timeout_seconds: int = 900, conversation_url: str | None = None):
    return await _run_chat_turn(prompt, turn_id, timeout_seconds, conversation_url, True)


def render_actor_prompt_v3(envelope: dict) -> str:
    if envelope.get("protocol_version") != "scorp.master-worker/turn-v3":
        raise ValueError("ACTOR_TURN_PROTOCOL_INVALID")
    turn_id = str(envelope.get("turn_id") or "").strip()
    project_id = str(envelope.get("project_id") or "").strip()
    session_id = str(envelope.get("session_id") or "").strip()
    actor_kind = str(envelope.get("actor_kind") or "").strip().upper()
    actor_id = str(envelope.get("actor_id") or "").strip()
    if not turn_id or not project_id or not session_id or not actor_kind or not actor_id:
        raise ValueError("ACTOR_TURN_IDENTITY_MISSING")
    try:
        state_version = int(envelope.get("state_version"))
    except Exception as exc:
        raise ValueError("PROJECT_STATE_VERSION_INVALID") from exc
    if state_version < 0:
        raise ValueError("PROJECT_STATE_VERSION_INVALID")
    if actor_kind == "MASTER":
        if actor_id != "A":
            raise ValueError("MASTER_IDENTITY_INVALID")
    elif actor_kind == "WORKER":
        if actor_id == "A" or not actor_id.startswith("worker-"):
            raise ValueError("WORKER_ID_INVALID")
        if not str(envelope.get("objective_sha256") or "").strip():
            raise ValueError("WORKER_OBJECTIVE_HASH_MISSING")
    else:
        raise ValueError("ACTOR_KIND_INVALID")

    root_hash = str(envelope.get("root_objective_sha256") or "").strip()
    acceptance_hash = str(envelope.get("acceptance_sha256") or "").strip()
    if not root_hash or not acceptance_hash:
        raise ValueError("ACTOR_CONTRACT_HASH_MISSING")
    payload = json.dumps(envelope.get("payload") or {}, ensure_ascii=False, separators=(",", ":"))
    common = (
        "You are GPT-5.6 Sol participating in one bound Scorp Master/Worker V3 turn.\n"
        "Do not invoke Codex or any local model. Do not alter binding identity fields, root objective, or acceptance criteria.\n"
        f"TURN_ID={turn_id}\nPROJECT_ID={project_id}\nPROJECT_STATE_VERSION={state_version}\n"
        f"SESSION_ID={session_id}\nROOT_OBJECTIVE_SHA256={root_hash}\nACCEPTANCE_SHA256={acceptance_hash}\n"
    )
    if actor_kind == "MASTER":
        if actor_id != "A":
            raise ValueError("MASTER_IDENTITY_INVALID")
        instructions = (
            "MASTER_IDENTITY=A\n"
            "A is a persistent identity, not a specific chat session. This SESSION_ID is only the current execution window.\n"
            "You are the sole project controller, engineer, integrator, and dispatch authority. "
            "Reconcile the supplied PROJECT_STATE/event, preserve the root contract, and continue the highest-value safe work.\n"
            "Allowed response kinds are DISPATCH, CONTINUE, DRAIN, WAIT, TERMINAL. "
            "Use DISPATCH only for truly independent work suitable for dynamic ephemeral workers; do not assume fixed B/C slots. "
            "The durable relay assigns worker identities. DRAIN means save a safe handoff/state update so deterministic D can start the next A window. "
            "TERMINAL is only for COMPLETE or HARD_BLOCKED.\n"
        )
    else:
        objective_hash = str(envelope.get("objective_sha256") or "").strip()
        instructions = (
            f"WORKER_ID={actor_id}\nOBJECTIVE_SHA256={objective_hash}\n"
            "WORKER_ASSIGNMENT_BOUNDARY=HARD\n"
            "You are one dynamic ephemeral worker. This conversation may be a reused worker transport slot. "
            "Treat this TURN_ID as a hard assignment boundary. Ignore assignment-specific goals, evidence, conclusions, and instructions from prior turns in this conversation; "
            "only current binding fields, current PAYLOAD, root objective hash, and acceptance hash are authoritative. Execute only the bounded assignment in PAYLOAD. "
            "Allowed response kinds are HANDOFF or BLOCKER. You must not dispatch work. "
            "You must not publish production actions. You must not redefine the root objective or acceptance criteria. "
            "Return completed/in-progress/remaining work, evidence/tests, deviations, blockers, and a safe resume point.\n"
        )
    return (
        common
        + instructions
        + f"PAYLOAD={payload}\n"
        + "Reply only after reasoning is complete, with exactly one line and no markdown. "
        + "Construct that line as: literal SCORP_GUI_ACTOR_V3, then two colon characters, then TURN_ID exactly as given above, "
        + "then two colon characters, then the unpadded base64url encoding of the compact UTF-8 JSON response object. "
        + "Do not echo these instructions.\n"
    )