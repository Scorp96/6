"""Stable, no-send native pinned Master presence preflight.

Reads an existing on-disk V3 Master identity, compares its exact durable
driver-owned Chrome Use session to the live session+tab inventory. Never
constructs a new Master binding from a currently online tab.

This is PRESENCE ONLY, not authentication, completion, or browser authority.
Prints neither conversation URL, session name, browser title nor transcript.
No webdriver navigation or cross-session adoption.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from chrome_use_cli_v3 import ChromeUseCliV3
from chrome_use_physical_presence_v4 import inspect_pinned_chrome_tabs_readonly


_PROTOCOL = "scorp.pinned-master-presence-readonly/1"


@dataclass(frozen=True)
class MasterPresencePreflight:
    status: str
    reason: str
    session_count: int | None = None
    matching_tab_count: int = 0
    browser_send_authorized: bool = False
    auth_verified: bool = False
    host_terminal_event_verified: bool = False
    production_writes: str = "NONE"
    model_calls: int = 0


def _read_json_bounded(path: Path) -> dict:
    try:
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            return {}
        with path.open("r", encoding="utf-8-sig") as handle:
            item = json.load(handle)
        return item if isinstance(item, dict) else {}
    except (OSError, ValueError, UnicodeError, TypeError):
        return {}


async def check_persisted_master_readonly(root: Path, *, cli: Any) -> MasterPresencePreflight:
    """No session discovery fallback: refuse missing/ambiguous durable identity."""
    def denied(reason: str, status: str = "BLOCKED",
               count: int | None = None, matching: int = 0):
        return MasterPresencePreflight(status, reason, count, matching)

    if not isinstance(root, Path):
        return denied("SCORP_ROOT_INVALID")
    project = _read_json_bounded(root / "state-v3/active/project-state.json")
    registry = _read_json_bounded(root / "state-v3/active/sessions-v3.json")
    driver = _read_json_bounded(root / "state-v3/active/chrome-use-driver-v3.json")
    project_id = project.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return denied("PROJECT_ID_UNAVAILABLE")
    entry = registry.get("master-conversation:" + project_id)
    if not isinstance(entry, dict):
        return denied("PERSISTED_MASTER_REGISTRY_UNAVAILABLE")
    url = entry.get("conversation_url")
    conversations = driver.get("conversations")
    if not isinstance(url, str) or not isinstance(conversations, dict):
        return denied("DURABLE_MASTER_IDENTITY_UNAVAILABLE")
    candidate = conversations.get(url)
    if not isinstance(candidate, dict) or not isinstance(candidate.get("session"), str):
        return denied("PINNED_DRIVER_SESSION_UNAVAILABLE")
    session = candidate["session"]
    if not session:
        return denied("PINNED_DRIVER_SESSION_UNAVAILABLE")

    observed = await inspect_pinned_chrome_tabs_readonly(
        cli, expected_session=session, expected_conversation_url=url,
    )
    return denied(
        observed.reason, observed.status,
        observed.session_count, observed.matching_tab_count,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only pinned Master browser presence")
    parser.add_argument("--scorp-root", type=Path, default=Path(r"C:\ScorpAgent"))
    parser.add_argument("--chrome-use", type=str, required=True)
    args = parser.parse_args(argv)
    # Only two readonly Chrome Use commands are available to this adapter.
    result = asyncio.run(check_persisted_master_readonly(
        args.scorp_root, cli=ChromeUseCliV3(executable=args.chrome_use),
    ))
    print(json.dumps({"protocol": _PROTOCOL, **asdict(result)},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
