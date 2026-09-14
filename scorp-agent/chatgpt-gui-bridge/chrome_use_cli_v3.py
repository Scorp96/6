from __future__ import annotations

import asyncio
import json
import tempfile
import threading


_DAEMON_LOCKS: dict[str, threading.Lock] = {}
_DAEMON_LOCKS_GUARD = threading.Lock()


def _daemon_lock(executable: str) -> threading.Lock:
    key = str(executable).strip().casefold()
    with _DAEMON_LOCKS_GUARD:
        return _DAEMON_LOCKS.setdefault(key, threading.Lock())


async def _terminate_process(proc):
    if getattr(proc, "returncode", None) is None:
        proc.kill()
    try:
        await asyncio.shield(asyncio.wait_for(proc.wait(), timeout=2.0))
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass


async def _default_runner(argv, timeout_seconds):
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=float(timeout_seconds))
        except asyncio.TimeoutError as exc:
            await _terminate_process(proc)
            raise TimeoutError("CHROME_USE_TIMEOUT") from exc
        except asyncio.CancelledError:
            await _terminate_process(proc)
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read()
        stderr = stderr_file.read()
    return (
        int(proc.returncode),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class ChromeUseCliV3:
    def __init__(self, *, executable="chrome-use.exe", runner=None):
        self.executable = str(executable or "").strip()
        if not self.executable:
            raise ValueError("CHROME_USE_EXECUTABLE_EMPTY")
        self.runner = runner or _default_runner
        self._daemon_mutex = _daemon_lock(self.executable)

    async def run_json(self, session, *args, timeout_seconds=30):
        session = str(session or "").strip()
        if not session:
            raise ValueError("CHROME_USE_SESSION_EMPTY")
        timeout_seconds = float(timeout_seconds)
        if timeout_seconds <= 0:
            raise ValueError("CHROME_USE_TIMEOUT_INVALID")
        argv = [
            self.executable,
            "--session",
            session,
            "--json",
            *[str(value) for value in args],
        ]
        # The Chrome Use relay is a single command daemon even when logical
        # sessions differ. Serialize subprocess requests across driver
        # instances so two Worker threads cannot make the relay return an EOF
        # or ``daemon may be busy`` response. This is transport serialization,
        # not a duplicate-submit retry; the durable browser intent remains the
        # side-effect fence.
        acquired = await asyncio.to_thread(
            self._daemon_mutex.acquire,
            True,
            timeout_seconds,
        )
        if not acquired:
            raise RuntimeError("CHROME_USE_DAEMON_BUSY_LOCAL_LOCK")
        try:
            returncode, stdout, stderr = await self.runner(argv, timeout_seconds)
        finally:
            self._daemon_mutex.release()
        if int(returncode) != 0:
            detail = (stderr or stdout or "").strip()
            raise RuntimeError(f"CHROME_USE_EXIT_{int(returncode)}: {detail}")
        text = str(stdout or "").strip()
        try:
            return json.loads(text)
        except Exception as exc:
            raise ValueError("CHROME_USE_INVALID_JSON") from exc

    async def prepare_interactive(self, session, *, timeout_seconds=30):
        """Surface this session before reading controls that depend on visibility.

        Chrome Use drives extension-connected tabs in the background by default.
        ChatGPT's controlled composer can expose the textbox while withholding
        the post-fill Send control until the tab is visible. Keep this as an
        explicit capability on the Chrome Use adapter so injected test/fake
        clients do not need to emulate browser visibility semantics.
        """

        return await self.run_json(
            session,
            "bringToFront",
            timeout_seconds=timeout_seconds,
        )

    async def open_new_tab(self, session, url, *, timeout_seconds=30):
        """Create an owned tab before starting a new ChatGPT conversation."""

        return await self.run_json(
            session,
            "tab",
            "new",
            str(url),
            timeout_seconds=timeout_seconds,
        )
