"""Single read-only Chrome Use tab-list inspection, no tab adoption or DOM read.

GPT-authored; Codex runs fixed command only. No private URLs, tab titles,
cookies, environment secrets, profile paths or raw CLI output are returned.
"""
from __future__ import annotations
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time

BIN = pathlib.Path(r"C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe")
URL = re.compile(r"(?:https?://)?(?:www\.)?chatgpt\.com/c/[A-Za-z0-9-]+", re.I)
BOUND_HASH16 = "53386382A30E6B47"

def classify(stderr: str) -> str:
    x = stderr.lower()
    if any(s in x for s in ("timeout", "timed out")):
        return "CLI_INTERNAL_TIMEOUT"
    if any(s in x for s in ("not connected", "disconnected", "extension not found", "not installed", "native messaging")):
        return "BROWSER_RELAY_NOT_CONNECTED"
    if any(s in x for s in ("session not found", "no active session")):
        return "NO_CHROME_USE_SESSION"
    if any(s in x for s in ("access denied", "permission denied", "os error 5")):
        return "PERMISSION_DENIED"
    if any(s in x for s in ("unknown argument", "unexpected argument", "unrecognized option", "invalid argument")):
        return "CLI_ARGUMENT_UNSUPPORTED"
    return "UNKNOWN_CLI_ERROR"

def main() -> int:
    out = {
        "probe": "scorp-chrome-use-tab-list-all/1",
        "executable_present": BIN.is_file(),
        "command": "tab list --all",
        "tab_metadata_observed": False,
        "observed_chatgpt_conversation_count": None,
        "unique_conversation_hashes16": [],
        "matches_existing_dpapi_binding_hash16": None,
        "raw_tab_metadata_withheld": True,
        "browser_actions": 0,
        "files_modified_by_probe": 0,
        "status": "UNKNOWN",
    }
    if not BIN.is_file():
        out["status"] = "CHROME_USE_EXECUTABLE_MISSING"
        print(json.dumps(out, sort_keys=True))
        return 2
    start = time.monotonic()
    try:
        # No --session, adopt, select, snapshot, get text, open or tab mutations.
        process = subprocess.run(
            [str(BIN), "tab", "list", "--all"],
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=8,
            env=os.environ.copy(),
            cwd=str(pathlib.Path(__file__).resolve().parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out["cli_exit_code"] = process.returncode
        out["stdout_bytes_approx"] = len(process.stdout.encode("utf-8"))
        out["stderr_bytes_approx"] = len(process.stderr.encode("utf-8"))
        if process.returncode != 0:
            out["status"] = classify(process.stderr)
        else:
            normalized = set()
            for match in URL.finditer(process.stdout):
                candidate = match.group(0)
                candidate = re.sub(r"^https?://", "", candidate, flags=re.I)
                candidate = re.sub(r"^www\.", "", candidate, flags=re.I)
                normalized.add("https://chatgpt.com" + candidate[candidate.lower().index("/c/"):])
            hashed = sorted(
                hashlib.sha256(u.encode("utf-8")).hexdigest()[:16].upper()
                for u in normalized
            )
            out["tab_metadata_observed"] = True
            out["observed_chatgpt_conversation_count"] = len(normalized)
            out["unique_conversation_hashes16"] = hashed
            out["matches_existing_dpapi_binding_hash16"] = (BOUND_HASH16 in hashed)
            out["status"] = "TAB_LIST_COMPLETE"
    except subprocess.TimeoutExpired:
        out["status"] = "CLI_TIMEOUT_8S_NO_REPLAY"
        out["cli_exit_code"] = None
    except (OSError, UnicodeError) as e:
        out["status"] = "CLI_PROCESS_" + type(e).__name__.upper()
    out["elapsed_seconds"] = round(time.monotonic() - start, 3)
    print(json.dumps(out, sort_keys=True))
    return 0 if out["status"] == "TAB_LIST_COMPLETE" else 2

if __name__ == "__main__":
    raise SystemExit(main())
