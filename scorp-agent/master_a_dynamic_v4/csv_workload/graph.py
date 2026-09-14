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
            "task_context": {
                "workload": "csv_summary",
                "task": "T1",
                "instruction": "Read the assigned UTF-8 CSV strictly. Reject malformed rows and do not emit a partial-success result.",
                "expected_artifacts": [source],
            },
            "dependencies": [],
        },
        {
            "task_id": "T2",
            "objective_sha256": sha256_json({"task": "DECIMAL_AGGREGATE", "source": source}),
            "resource_scope": [source],
            "access_mode": "read",
            "task_context": {
                "workload": "csv_summary",
                "task": "T2",
                "instruction": "Aggregate amount by category with Decimal arithmetic and return canonical decimal strings.",
                "expected_artifacts": [source],
            },
            "dependencies": [],
        },
        {
            "task_id": "T3",
            "objective_sha256": sha256_json({"task": "STABLE_JSON_REPORT", "source": source, "report": report}),
            "resource_scope": [source, report],
            "access_mode": "write",
            "task_context": {
                "workload": "csv_summary",
                "task": "T3",
                "instruction": "Integrate T1 and T2 into a stable JSON report sorted by category, then run the required end-to-end checks.",
                "expected_artifacts": [report],
            },
            "dependencies": ["T1", "T2"],
        },
    ]
