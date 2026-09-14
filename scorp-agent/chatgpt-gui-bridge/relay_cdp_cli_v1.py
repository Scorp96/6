from __future__ import annotations

import json
from pathlib import Path

from chrome_use_cli_v3 import _default_runner


class RelayCdpCliV1:
    """ChromeUseCliV3-compatible adapter backed by chrome-use's extension relay CDP endpoint."""

    def __init__(
        self,
        *,
        node_executable="node.exe",
        helper_path="relay_cdp_bridge_v1.mjs",
        state_path="relay-cdp-state-v1.json",
        runner=None,
    ):
        self.node_executable = str(node_executable or "").strip()
        self.helper_path = str(helper_path or "").strip()
        self.state_path = str(Path(state_path))
        if not self.node_executable:
            raise ValueError("RELAY_CDP_NODE_EMPTY")
        if not self.helper_path:
            raise ValueError("RELAY_CDP_HELPER_EMPTY")
        if not self.state_path.strip():
            raise ValueError("RELAY_CDP_STATE_PATH_EMPTY")
        self.runner = runner or _default_runner

    async def run_json(self, session, *args, timeout_seconds=30):
        session = str(session or "").strip()
        if not session:
            raise ValueError("RELAY_CDP_SESSION_EMPTY")
        timeout_seconds = float(timeout_seconds)
        if timeout_seconds <= 0:
            raise ValueError("RELAY_CDP_TIMEOUT_INVALID")
        argv = [
            self.node_executable,
            self.helper_path,
            self.state_path,
            session,
            *[str(value) for value in args],
        ]
        returncode, stdout, stderr = await self.runner(argv, timeout_seconds)
        if int(returncode) != 0:
            detail = (stderr or stdout or "").strip()
            raise RuntimeError(f"RELAY_CDP_EXIT_{int(returncode)}: {detail}")
        text = str(stdout or "").strip()
        try:
            return json.loads(text)
        except Exception as exc:
            raise ValueError("RELAY_CDP_INVALID_JSON") from exc
