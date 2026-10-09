"""One-shot offline-safe LOCAL poll entrypoint for later isolated scheduling.

Do NOT connect this executable to the original SCORP Master, 15m observer or
Task Scheduler automatically. It reads Chrome Use loopback status once and
records a nonterminal, minimum-cadence SQLite sample. It cannot wake GPT.

CLI input is restricted to a pre-existing isolated scratch directory directly
under the experiments root derived from its own checked-out source tree.
Neither browser URLs nor credentials or session/tab IDs reach stdout.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import json
import re

from chrome_use_loopback_observer_v4 import observe_local_chrome_status_no_send
from isolated_loopback_poll_journal_v4 import commit_one_nonterminal_local_sample
from isolated_chrome_namespace_admission_v4 import _CANARY


_FOLDER = re.compile(r"^r2-loopback-poll-[a-z0-9-]{5,64}$")
_PROTOCOL = "scorp.local-zero-token-poll-once/1"


@dataclass(frozen=True)
class OneShotPollResult:
    protocol: str = _PROTOCOL
    status: str = "BLOCKED"
    reason: str = "UNKNOWN"
    observed_sessions: int | None = None
    observed_tabs: int | None = None
    journal_category: str = "NONE"
    local_model_calls: int = 0
    browser_send_authorized: bool = False
    host_terminal_event_verified: bool = False
    worker_wake_authorized: bool = False
    production_writes: str = "NONE"


def run_one_shot_local_poll(
    *,
    experiments_root: Path,
    experiment_folder: str,
    executable: Path,
    namespace: str,
    cadence_minutes: int,
    observer=observe_local_chrome_status_no_send,
    journal=commit_one_nonterminal_local_sample,
) -> OneShotPollResult:
    def outcome(status, reason, sessions=None, tabs=None, category="NONE"):
        return OneShotPollResult(
            status=status, reason=reason,
            observed_sessions=sessions, observed_tabs=tabs,
            journal_category=category,
        )

    if (
        not isinstance(experiments_root, Path)
        or not isinstance(experiment_folder, str)
        or _FOLDER.fullmatch(experiment_folder) is None
        or not isinstance(namespace, str)
        or _CANARY.fullmatch(namespace) is None
        or type(cadence_minutes) is not int or cadence_minutes not in (15, 25)
        or not isinstance(executable, Path)
    ):
        return outcome("BLOCKED", "ISOLATED_POLL_CLI_ARGUMENT_INVALID")

    try:
        root = experiments_root.resolve(strict=True)
        scratch = (root / experiment_folder).resolve(strict=True)
        binary = executable.resolve(strict=True)
        if (
            root.name != "experiments"
            or scratch.parent != root
            or not scratch.is_dir()
            or not binary.is_file()
            or binary.name.casefold() not in ("chrome-use.exe", "chrome-use")
        ):
            return outcome("BLOCKED", "ISOLATED_POLL_CLI_PATH_INVALID")
    except (OSError, RuntimeError, ValueError):
        return outcome("BLOCKED", "ISOLATED_POLL_CLI_PATH_UNAVAILABLE")

    try:
        observed = observer(
            executable=str(binary), pinned_isolated_namespace=namespace,
        )
    except Exception:
        return outcome("BLOCKED", "LOOPBACK_OBSERVATION_UNAVAILABLE")

    try:
        recorded = journal(
            observed, isolated_namespace=namespace,
            database=scratch / "local-poll-journal.sqlite3",
            allowed_isolated_root=root,
            cadence_minutes=cadence_minutes,
        )
    except Exception:
        return outcome("BLOCKED", "ISOLATED_POLL_JOURNAL_UNAVAILABLE")

    if not recorded.sample_written:
        return outcome(
            "BLOCKED", recorded.reason,
            sessions=observed.session_count if type(observed.session_count) is int else None,
            tabs=observed.tab_count if type(observed.tab_count) is int else None,
        )
    return outcome(
        "LOCAL_OBSERVATION_ONLY",
        "NONTERMINAL_BROWSER_COUNTS_PERSISTED_NO_WAKE",
        observed.session_count, observed.tab_count, recorded.category,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SCORP isolated, zero-model-call browser monitor (NO SEND)",
    )
    parser.add_argument("--experiment-folder", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--cadence", type=int, choices=(15, 25), default=15)
    args = parser.parse_args(argv)

    # Only same SCORP installation's isolated experiments tree. The
    # production chatgpt-gui-bridge and old frozen observer never receive
    # writes. No CLI-provided root or executable override is allowed.
    repository = Path(__file__).resolve().parents[2]
    experiments = repository.parent
    executable = (
        experiments.parent / "p0-transport-bakeoff"
        / "chrome-use" / "bin" / "chrome-use.exe"
    )
    result = run_one_shot_local_poll(
        experiments_root=experiments,
        experiment_folder=args.experiment_folder,
        executable=executable,
        namespace=args.namespace,
        cadence_minutes=args.cadence,
    )
    print(json.dumps(result.__dict__,sort_keys=True,separators=(",",":")))
    return 0 if result.status == "LOCAL_OBSERVATION_ONLY" else 3


if __name__ == "__main__":
    raise SystemExit(main())
