"""Eight authored regression candidates, fixed before any budget-policy trial."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


def build_suite() -> dict:
    original = json.loads(Path(__file__).with_name("harness_suite_v2.json").read_text(encoding="utf-8"))
    source = {task["id"]: task for task in original["tasks"]}
    variants = {
        "normalize-config": [
            ("string-bool", '{"enabled":"true","retries":3}'),
            ("wrong-retries", '{"enabled":true,"retries":0}'),
        ],
        "aggregate-orders": [("includes-cancelled", '{"alice":15,"bob":16}'), ("drops-order", '{"alice":12,"bob":7}')],
        "repair-stable-unique": [
            ("sorted", "def stable_unique(values):\n    return sorted(set(values))\n"),
            (
                "mutates-input",
                "def stable_unique(values):\n    values[:] = list(dict.fromkeys(values))\n    return values\n",
            ),
        ],
        "release-version": [
            ("package-version", '{"name":"praxis-demo","version":"1.1.0"}'),
            ("package-name", '{"name":"wrong","version":"1.2.0"}'),
        ],
    }
    tasks = []
    for task_id, candidates in variants.items():
        for candidate_id, content in candidates:
            task = copy.deepcopy(source[task_id])
            task.update(id=task_id + "-" + candidate_id, candidate_id=candidate_id)
            if task_id == "aggregate-orders":
                task["files"].append({"path": "totals.json", "content": content})
                task["checks"] = [
                    {"id": "totals", "kind": "json_equals", "path": "totals.json", "expected": {"alice": 15, "bob": 7}}
                ]
                task["rubric"].append(
                    {
                        "id": "input",
                        "kind": "file_equals",
                        "path": "orders.csv",
                        "expected": task["files"][0]["content"],
                    }
                )
            else:
                task["files"][0]["content"] = content
            if task_id == "release-version":
                task["files"][1]["content"] = "1.2.0\n"
                task["checks"][0] = {
                    "id": "package",
                    "kind": "json_equals",
                    "path": "package.json",
                    "expected": {"name": "praxis-demo", "version": "1.2.0"},
                }
            if task_id == "repair-stable-unique":
                task["files"][1]["content"] = (
                    "from solution import stable_unique\nvalues = [3, 1, 3, 2]\nassert stable_unique(values) == [3, 1, 2]\nassert values == [3, 1, 3, 2], 'input mutated'\nprint('public checks passed')\n"
                )
                task["rubric"].append(
                    {
                        "id": "public_preserved",
                        "kind": "file_equals",
                        "path": "tests_public.py",
                        "expected": task["files"][1]["content"],
                    }
                )
            tasks.append(task)
    return {
        "schema_version": 2,
        "evaluation_protocol": "budget_finish_v1",
        "acceptance_revision": "budget-cases-v1",
        "purpose": "Authored development regression, not blind evaluation. All eight fixed public-failing candidates are included. Both arms receive common initial public feedback; shared reserves zero calls, reserved reserves two within the same total. No post-acceptance model repair.",
        "independent_rubric_sha256": hashlib.sha256(
            json.dumps(
                [{"id": t["id"], "rubric": t["rubric"]} for t in tasks], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest(),
        "tasks": tasks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("harness_budget_finish_v1.json"))
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(build_suite(), stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
