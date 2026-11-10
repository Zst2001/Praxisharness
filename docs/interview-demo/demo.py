"""Prepare an isolated interview workspace and check artifacts without calling a model."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def prepare(destination: Path | None) -> Path:
    if destination is None:
        destination = Path(tempfile.mkdtemp(prefix="Praxis-interview-demo-", dir=PACKAGE.parents[2]))
    else:
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(PACKAGE / "workspace", destination, dirs_exist_ok=True)
    return destination


def verify(workspace: Path) -> list[str]:
    expected = json.loads((PACKAGE / "acceptance-main.json").read_text(encoding="utf-8"))["checks"][0]["expected"]
    failures = []
    actual = json.loads((workspace / "daily-summary.json").read_text(encoding="utf-8-sig"))
    # Python's ordinary equality treats True as 1. JSON acceptance must preserve types.
    if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
        failures.append("daily-summary.json does not match the expected values and types")
    report_path = workspace / "report.md"
    report = report_path.read_text(encoding="utf-8-sig") if report_path.is_file() else ""
    for required in ("订单日报", "2026-11-08", "310.00 元"):
        if required not in report:
            failures.append(f"report.md is missing required content ({ascii(required)})")
    for relative in ("data/orders.json", "TASK.md"):
        if (workspace / relative).read_bytes() != (PACKAGE / "workspace" / relative).read_bytes():
            failures.append(f"Original input changed: {relative}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare", help="Create a fresh project directory")
    preparation.add_argument("--destination", type=Path)
    verification = commands.add_parser("verify", help="Check the main task's real output files")
    verification.add_argument("workspace", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare(args.destination))
            return 0
        failures = verify(args.workspace.resolve())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("PASS: summary values/types, required report content, original input integrity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
