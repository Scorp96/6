"""One-shot, read-only Windows MCP Snapshot via JSON-RPC stdio.

GPT creates this isolated probe; Codex may run and record its *sanitized*
output. Does not use the Python mcp package. Does not click, type, navigate,
read Chrome profile/cookies, or submit ChatGPT messages.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time


CMD = [
    "uvx", "--offline", "--from", "windows-mcp==0.8.5",
    "windows-mcp", "serve", "--tools", "Snapshot",
]
TARGET = re.compile(r"https://chatgpt\.com/c/[A-Za-z0-9-]+")
TIMEOUT = 24.0
MAX_LINE_CHARS = 8_000_000


def main():
    outcome = {
        "probe": "scorp-offline-windows-mcp-snapshot/1",
        "snapshot_success": False,
        "status": "UNKNOWN",
        "unique_chatgpt_conversation_count": None,
        "conversation_sha256_prefixes": [],
        "raw_snapshot_withheld": True,
        "browser_send_actions": 0,
        "files_modified_by_probe": 0,
    }
    proc = None
    try:
        env = os.environ.copy()
        env["UV_OFFLINE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.Popen(
            CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=env,
        )
        output_queue: queue.Queue = queue.Queue()
        stderr_chunks = []
        def stdout_reader():
            try:
                for line in proc.stdout:
                    if len(line) > MAX_LINE_CHARS:
                        output_queue.put(("TOO_LARGE", None))
                        break
                    output_queue.put(("LINE", line))
            finally:
                output_queue.put(("EOF", None))
        def stderr_reader():
            for line in proc.stderr:
                if len(stderr_chunks) < 20:
                    stderr_chunks.append(line[:300])
        threading.Thread(target=stdout_reader, daemon=True).start()
        threading.Thread(target=stderr_reader, daemon=True).start()

        def send(method, params=None, rid=None):
            msg = {"jsonrpc": "2.0", "method": method}
            if rid is not None:
                msg["id"] = rid
            if params is not None:
                msg["params"] = params
            proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
            proc.stdin.flush()

        send("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "scorp-readonly-probe", "version": "1.0"},
        }, 1)
        deadline = time.monotonic() + TIMEOUT
        initialized = False
        called = False
        while time.monotonic() < deadline:
            try:
                typ, line = output_queue.get(timeout=min(0.7, max(0.01, deadline-time.monotonic())))
            except queue.Empty:
                if proc.poll() is not None:
                    outcome["status"] = "MCP_PROCESS_EXITED"
                    break
                continue
            if typ == "TOO_LARGE":
                outcome["status"] = "MCP_RESPONSE_TOO_LARGE"
                break
            if typ == "EOF":
                outcome["status"] = "MCP_STDOUT_ENDED"
                break
            try:
                packet = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(packet, dict):
                continue
            if packet.get("id") == 1:
                if packet.get("error"):
                    outcome["status"] = "INITIALIZE_REJECTED"
                    break
                initialized = True
                send("notifications/initialized")
                send("tools/call", {
                    "name": "Snapshot",
                    "arguments": {"use_annotation": False, "use_ui_tree": True},
                }, 2)
                called = True
            elif packet.get("id") == 2:
                if packet.get("error"):
                    outcome["status"] = "SNAPSHOT_RPC_ERROR"
                    break
                result = packet.get("result") or {}
                if result.get("isError") is True:
                    outcome["status"] = "SNAPSHOT_TOOL_ERROR"
                    break
                # Keep all content in memory. Never print or persist raw data.
                body = json.dumps(result, ensure_ascii=False)
                unique = sorted(set(TARGET.findall(body)))
                outcome["snapshot_success"] = True
                outcome["status"] = "SNAPSHOT_OK"
                outcome["unique_chatgpt_conversation_count"] = len(unique)
                outcome["conversation_sha256_prefixes"] = [
                    hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
                    for url in unique
                ]
                break
        else:
            outcome["status"] = "SNAPSHOT_TIMEOUT"
        if not initialized and outcome["status"] in ("MCP_PROCESS_EXITED", "MCP_STDOUT_ENDED"):
            error_text = "".join(stderr_chunks).lower()
            if any(x in error_text for x in ("offline", "not found", "not cached", "no solution")):
                outcome["status"] = "OFFLINE_DEPENDENCY_NOT_AVAILABLE"
        outcome["mcp_initialize_seen"] = initialized
        outcome["snapshot_tool_called"] = called
    except FileNotFoundError:
        outcome["status"] = "UVX_NOT_FOUND"
    except (BrokenPipeError, OSError, UnicodeError) as exc:
        outcome["status"] = "MCP_TRANSPORT_" + type(exc).__name__.upper()
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            outcome["mcp_exit_code"] = proc.returncode
    print(json.dumps(outcome, sort_keys=True, ensure_ascii=False))
    return 0 if outcome["snapshot_success"] else 2


if __name__ == "__main__":
    sys.exit(main())
