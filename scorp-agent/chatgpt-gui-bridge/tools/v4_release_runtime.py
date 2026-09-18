"""Verify an immutable installed release before starting the persistent V4 daemon."""

from __future__ import annotations

import json
import pathlib
import sys

BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
RELEASE_ROOT = AGENT_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from master_a_dynamic_v4.install_manifest import verify_runtime_release  # noqa: E402
from master_a_dynamic_v4.production_bootstrap import verify_production_authority  # noqa: E402
from tools.v4_daemon_runtime import build_parser, run_runtime  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    manifest_path = RELEASE_ROOT / "candidate-manifest.json"
    receipt_path = RELEASE_ROOT / "release-receipt.json"
    if not manifest_path.is_file() or not receipt_path.is_file():
        raise RuntimeError("RUNTIME_RELEASE_IDENTITY_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    verify_runtime_release(RELEASE_ROOT, receipt, manifest)
    args = build_parser().parse_args(argv)
    database_path = pathlib.Path(args.database_path).resolve(strict=True)
    verify_production_authority(
        database_path.parent,
        release_root=RELEASE_ROOT,
        manifest=manifest,
        expected_project_id=str(args.project_id),
    )
    return run_runtime(args)


if __name__ == "__main__":
    raise SystemExit(main())
