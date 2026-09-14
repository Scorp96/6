from __future__ import annotations

import pathlib
from typing import Any

from ..models import sha256_json


def build_task_graph(
    source_path: str | pathlib.Path,
    report_path: str | pathlib.Path,
) -> list[dict[str, Any]]:
    source = str(pathlib.Path(source_path).resolve())
    report = str(pathlib.Path(report_path).resolve())
    return [
        {
            "task_id": "T1",
            "objective_sha256": sha256_json({"task": "STRICT_CSV_READ", "source": source}),
            "resource_scope": [source],
            "access_mode": "read",
            "dependencies": [],
        },
        {
            "task_id": "T2",
            "objective_sha256": sha256_json({"task": "DECIMAL_AGGREGATE", "source": source}),
            "resource_scope": [source],
            "access_mode": "read",
            "dependencies": [],
        },
        {
            "task_id": "T3",
            "objective_sha256": sha256_json({"task": "STABLE_JSON_REPORT", "source": source, "report": report}),
            "resource_scope": [report],
            "access_mode": "write",
            "dependencies": ["T1", "T2"],
        },
    ]
