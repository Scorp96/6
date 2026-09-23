"""Run one PowerShell script without creating a console window.

The V4 watchdog is intentionally kept as PowerShell because it is also a
useful operator-facing diagnostic.  Scheduled Task actions, however, must not
start ``powershell.exe`` directly on hosts where Windows Terminal is the
default console application: even ``-WindowStyle Hidden`` can briefly create a
visible terminal tab.  This small ``pythonw.exe`` entry point is the
non-console boundary.  It starts PowerShell with both the Win32
``CREATE_NO_WINDOW`` flag and hidden startup information, and it discards the
watchdog's normal-object output because the scheduled task only needs the
exit status.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--script":
        return 2

    script = Path(argv[2])
    if not script.is_file():
        return 2

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *argv[3:],
    ]

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startupinfo,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(_run(sys.argv))
