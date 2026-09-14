from __future__ import annotations

import shutil
import subprocess


def classify_candidate_b(evidence: dict) -> str:
    cgu = evidence.get("chatgpt_use") or {}
    cu = evidence.get("chrome_use") or {}
    if all(
        cgu.get(key) is True
        for key in ("present", "request_receipts", "conversation_record", "windows")
    ):
        return "CHATGPT_USE"
    if cu.get("present") is True and cu.get("windows") is True:
        return "CHROME_USE_DIRECT"
    return "BLOCKED"


def _version(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"present": False, "path": None, "version": None}
    cp = subprocess.run(
        [path, "--version"],
        text=True,
        capture_output=True,
        timeout=20,
    )
    return {
        "present": cp.returncode == 0,
        "path": path,
        "version": (cp.stdout or cp.stderr or "").strip(),
    }


def probe_versions() -> dict:
    return {
        "chatgpt_use": _version("chatgpt-use"),
        "chrome_use": _version("chrome-use"),
    }
