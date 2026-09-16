from __future__ import annotations

import asyncio
import contextlib
import ctypes
import functools
import os
import re
import time

from gui_transport import (
    ChatGptThrottleError,
    acquire_chatgpt_window,
    chatgpt_throttle_visible,
    focused_chatgpt_url,
    focused_window_is_chrome,
    open_new_chat_and_submit,
    result_text,
    validate_conversation_url,
)




class WindowBoundSnapshot(str):
    def __new__(cls, value: str, window_handle: int):
        obj = str.__new__(cls, value)
        obj.window_handle = int(window_handle)
        return obj


def _foreground_window_handle() -> int:
    if os.name != "nt":
        raise RuntimeError("ACTOR_GUI_HWND_UNAVAILABLE")
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    hwnd = int(user32.GetForegroundWindow() or 0)
    if hwnd <= 0:
        raise RuntimeError("ACTOR_GUI_WINDOW_HANDLE_MISSING")
    return hwnd


def _foreground_window_is_chrome() -> bool:
    if os.name != "nt":
        return False
    hwnd = _foreground_window_handle()
    user32 = ctypes.windll.user32
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    buf = ctypes.create_unicode_buffer(256)
    length = int(user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, len(buf)))
    if length <= 0:
        return False
    return buf.value.startswith("Chrome_WidgetWin_")


def _close_window_handle(window_handle: int) -> bool:
    if os.name != "nt":
        return False
    try:
        hwnd = int(window_handle)
    except (TypeError, ValueError):
        return False
    if hwnd <= 0:
        return False
    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_bool
    target = ctypes.c_void_p(hwnd)
    if not user32.IsWindow(target):
        return True
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.PostMessageW.restype = ctypes.c_bool
    if not user32.PostMessageW(target, 0x0010, None, None):  # WM_CLOSE
        return False
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not user32.IsWindow(target):
            return True
        time.sleep(0.05)
    return not user32.IsWindow(target)


def _focus_window_handle(window_handle: int) -> bool:
    if os.name != "nt":
        return False
    try:
        hwnd = int(window_handle)
    except (TypeError, ValueError):
        return False
    if hwnd <= 0:
        return False
    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_bool
    if not user32.IsWindow(ctypes.c_void_p(hwnd)):
        return False
    user32.IsIconic.argtypes = [ctypes.c_void_p]
    user32.IsIconic.restype = ctypes.c_bool
    if user32.IsIconic(ctypes.c_void_p(hwnd)):
        user32.ShowWindow(ctypes.c_void_p(hwnd), 9)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    current = int(user32.GetForegroundWindow() or 0)
    if current == hwnd:
        return True
    kernel32 = ctypes.windll.kernel32
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    current_tid = int(kernel32.GetCurrentThreadId())
    foreground_tid = int(user32.GetWindowThreadProcessId(ctypes.c_void_p(current), None)) if current else 0
    target_tid = int(user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None))
    attached = []
    try:
        try:
            user32.AllowSetForegroundWindow(ctypes.c_uint(0xFFFFFFFF))
        except Exception:
            pass
        for tid in (foreground_tid, target_tid):
            if tid and tid != current_tid:
                try:
                    if user32.AttachThreadInput(ctypes.c_ulong(current_tid), ctypes.c_ulong(tid), True):
                        attached.append(tid)
                except Exception:
                    pass
        try:
            user32.SwitchToThisWindow(ctypes.c_void_p(hwnd), ctypes.c_int(1))
        except Exception:
            pass
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        user32.BringWindowToTop(ctypes.c_void_p(hwnd))
    finally:
        for tid in reversed(attached):
            try:
                user32.AttachThreadInput(ctypes.c_ulong(current_tid), ctypes.c_ulong(tid), False)
            except Exception:
                pass
    return int(user32.GetForegroundWindow() or 0) == hwnd


def _windows_mcp_environment():
    env = dict(os.environ)
    env["ANONYMIZED_TELEMETRY"] = "false"
    env["WINDOWS_MCP_DISABLE_FLASH"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("WINDOWS_MCP_MAX_TREE_ELEMENTS", "2000")
    return env


@contextlib.asynccontextmanager
async def _default_session_factory():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = _windows_mcp_environment()
    tools = "App,Shortcut,Clipboard,Click,Type,Wait,Snapshot"
    server = StdioServerParameters(
        command="uvx",
        args=["--from", "windows-mcp==0.8.5", "windows-mcp", "serve", "--tools", tools],
        env=env,
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


def _turn_visible(snapshot: str, turn_id: str) -> bool:
    return re.search(
        rf"(?:TURN_ID\s*=\s*{re.escape(turn_id)}\b|SCORP_GUI_ACTOR_V3::{re.escape(turn_id)}::)",
        str(snapshot or ""),
    ) is not None


def _bound_snapshot(snapshot: str, conversation_url: str) -> str:
    canonical = validate_conversation_url(conversation_url)
    if canonical == "https://chatgpt.com/":
        raise ValueError("ACTOR_GUI_CONVERSATION_URL_MISSING")
    return canonical + "\n" + str(snapshot or "")


class WindowsMcpActorDriverV3:
    """Concrete Windows-MCP driver for nonblocking actor conversations.

    Conversation identity is read from the focused Chrome address bar, never
    inferred from Snapshot's global opened-window URL list. UI manipulation is
    serial; remote ChatGPT generations can overlap after submit returns.
    """

    def __init__(
        self,
        *,
        session_factory=None,
        open_submit_fn=None,
        acquire_fn=None,
        foreground_window_handle_fn=None,
        foreground_window_is_chrome_fn=None,
        focus_window_handle_fn=None,
        close_window_handle_fn=None,
        throttle_backoff_seconds=90.0,
        throttle_max_attempts=3,
        window_focus_settle_seconds=0.25,
        url_timeout_seconds=20.0,
        url_poll_seconds=0.5,
        page_wait_seconds=3,
        poll_page_wait_seconds=2,
    ):
        self.session_factory = session_factory or _default_session_factory
        self.foreground_window_is_chrome_fn = foreground_window_is_chrome_fn or _foreground_window_is_chrome
        self.open_submit_fn = open_submit_fn or functools.partial(
            open_new_chat_and_submit,
            foreground_window_is_chrome_fn=self.foreground_window_is_chrome_fn,
        )
        self.acquire_fn = acquire_fn or functools.partial(
            acquire_chatgpt_window,
            foreground_window_is_chrome_fn=self.foreground_window_is_chrome_fn,
        )
        self.foreground_window_handle_fn = foreground_window_handle_fn or _foreground_window_handle
        self.focus_window_handle_fn = focus_window_handle_fn or _focus_window_handle
        self.close_window_handle_fn = close_window_handle_fn or _close_window_handle
        self.throttle_backoff_seconds = float(throttle_backoff_seconds)
        self.throttle_max_attempts = int(throttle_max_attempts)
        self.window_focus_settle_seconds = float(window_focus_settle_seconds)
        self.url_timeout_seconds = float(url_timeout_seconds)
        self.url_poll_seconds = float(url_poll_seconds)
        self.page_wait_seconds = int(page_wait_seconds)
        self.poll_page_wait_seconds = int(poll_page_wait_seconds)
        if self.url_timeout_seconds <= 0:
            raise ValueError("ACTOR_GUI_URL_TIMEOUT_INVALID")
        if self.url_poll_seconds < 0:
            raise ValueError("ACTOR_GUI_URL_POLL_INVALID")
        if self.window_focus_settle_seconds < 0:
            raise ValueError("ACTOR_GUI_WINDOW_FOCUS_SETTLE_INVALID")
        if self.throttle_backoff_seconds < 0:
            raise ValueError("ACTOR_GUI_THROTTLE_BACKOFF_INVALID")
        if self.throttle_max_attempts < 1:
            raise ValueError("ACTOR_GUI_THROTTLE_ATTEMPTS_INVALID")

    async def _snapshot(self, client) -> str:
        result = await client.call_tool("Snapshot", {
            "use_vision": False,
            "use_dom": False,
            "use_annotation": True,
            "use_ui_tree": True,
        })
        if getattr(result, "isError", False):
            raise RuntimeError("ACTOR_GUI_SNAPSHOT_ERROR")
        return result_text(result)

    async def _focused_conversation_url(self, client) -> str | None:
        url = await focused_chatgpt_url(client)
        return None if url == "https://chatgpt.com/" else url

    async def observe_current_binding(self, _channel):
        """Read the focused browser identity without opening or navigating a URL."""

        async with self.session_factory() as client:
            snapshot = await self._snapshot(client)
            physical_url = await self._focused_conversation_url(client)
            if not physical_url:
                raise ValueError("ACTOR_GUI_PHYSICAL_CONVERSATION_MISSING")
            return {
                "driver_url": physical_url,
                "physical_url": physical_url,
                "snapshot": _bound_snapshot(snapshot, physical_url),
            }


    async def submit_prompt(self, *, prompt, turn_id, actor_kind, conversation_url):
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        known_url = None
        if conversation_url is not None:
            known_url = validate_conversation_url(conversation_url)
            if known_url == "https://chatgpt.com/":
                raise ValueError("ACTOR_GUI_CONVERSATION_URL_MISSING")

        async with self.session_factory() as client:
            await self.open_submit_fn(
                client,
                prompt,
                page_wait_seconds=self.page_wait_seconds,
                conversation_url=known_url,
            )

            deadline = time.monotonic() + self.url_timeout_seconds
            while True:
                snapshot = await self._snapshot(client)
                if chatgpt_throttle_visible(snapshot):
                    raise RuntimeError("ACTOR_GUI_POST_SUBMIT_THROTTLED")
                if _turn_visible(snapshot, turn_id):
                    focused_url = await self._focused_conversation_url(client)
                    if focused_url is not None:
                        if known_url is not None and focused_url != known_url:
                            raise ValueError("ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH")
                        window_handle = int(self.foreground_window_handle_fn())
                        if window_handle <= 0:
                            raise RuntimeError("ACTOR_GUI_WINDOW_HANDLE_MISSING")
                        return WindowBoundSnapshot(
                            _bound_snapshot(snapshot, known_url or focused_url),
                            window_handle,
                        )

                if time.monotonic() >= deadline:
                    raise TimeoutError("ACTOR_GUI_URL_TIMEOUT")
                if self.url_poll_seconds:
                    if self.url_poll_seconds.is_integer():
                        await client.call_tool("Wait", {"duration": int(self.url_poll_seconds)})
                    else:
                        await asyncio.sleep(self.url_poll_seconds)
                else:
                    await asyncio.sleep(0)

    async def close_conversation_window(self, window_handle):
        if type(window_handle) is not int or window_handle <= 0:
            raise ValueError("ACTOR_GUI_WINDOW_HANDLE_INVALID")
        if not self.close_window_handle_fn(window_handle):
            raise RuntimeError("ACTOR_GUI_WINDOW_CLOSE_FAILED")
        return True

    async def snapshot_conversation(self, conversation_url, *, window_handle=None):
        conversation_url = validate_conversation_url(conversation_url)
        if conversation_url == "https://chatgpt.com/":
            raise ValueError("ACTOR_GUI_CONVERSATION_URL_MISSING")
        async with self.session_factory() as client:
            opened_poll_window = False
            if window_handle is None:
                await self.acquire_fn(
                    client,
                    wait_seconds=self.poll_page_wait_seconds,
                    conversation_url=conversation_url,
                )
                opened_poll_window = True
            else:
                if type(window_handle) is not int or window_handle <= 0:
                    raise ValueError("ACTOR_GUI_WINDOW_HANDLE_INVALID")
                if not self.focus_window_handle_fn(window_handle):
                    raise RuntimeError("ACTOR_GUI_WINDOW_FOCUS_FAILED")
                if self.window_focus_settle_seconds:
                    await asyncio.sleep(self.window_focus_settle_seconds)
            try:
                await client.call_tool("Shortcut", {"shortcut": "ctrl+end"})
                await client.call_tool("Wait", {"duration": 1})
                snapshot = await self._snapshot(client)
                focused_url = await self._focused_conversation_url(client)
                if focused_url != conversation_url:
                    raise ValueError("ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH")
                return snapshot
            finally:
                if opened_poll_window:
                    await client.call_tool("Shortcut", {"shortcut": "alt+f4"})

    async def discover_turn_conversations(self, turn_id):
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        async with self.session_factory() as client:
            snapshot = await self._snapshot(client)
            if not focused_window_is_chrome(snapshot) or not _turn_visible(snapshot, turn_id):
                return []
            url = await self._focused_conversation_url(client)
        if not url:
            return []
        return [{"conversation_url": url, "snapshot": snapshot}]
