"""Independent post-run graders. Submitted Python executes only via Sandbox tools."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from backend.domain import AssistantMessage
from backend.domain.acceptance import AcceptanceContract, AcceptanceReport, CheckResult
from backend.domain.file_paths import ScopedPaths
from backend.runtime import AgentRunner
from backend.runtime.core.contracts import InterruptDecision
from backend.runtime.execution.acceptance import verify_acceptance
from backend.tools import ToolRegistry
from backend.tools.command import WorkspaceCommand
from backend.tools.default_tools.command import command_tool, stdin_tool

from .evidence import safe_value


class _NoModel:
    name = "independent-grader"

    def decide(self, runtime) -> AssistantMessage:
        raise RuntimeError("The independent grader must never call a model.")


def shell_command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _python_check(workspace: Path, rule: dict, launcher, sandbox_config: dict) -> tuple[CheckResult, list[dict]]:
    def finish(result: CheckResult, payload: dict | None = None) -> tuple[CheckResult, list[dict]]:
        cases = []
        codes = payload.get("codes") if payload else None
        valid = (
            isinstance(codes, list)
            and len(codes) == len(rule["cases"])
            and all(type(c) is int and 0 <= c <= 4 for c in codes)
        )
        kinds = (None, "wrong_result", "input_mutated", "exception", "import_error")
        for index, case in enumerate(rule["cases"]):
            code = codes[index] if valid else None
            details = payload.get("details") if valid else None
            detail = details.get(str(index), {}) if isinstance(details, dict) else {}
            detail = detail if isinstance(detail, dict) else {}
            cases.append(
                {
                    "check_id": rule["id"],
                    "case_id": case.get("id", f"case_{index + 1}"),
                    "status": "passed"
                    if code == 0
                    else "failed"
                    if valid or result.status == "failed"
                    else result.status,
                    "failure_kind": kinds[code]
                    if valid
                    else "grader_incomplete"
                    if result.status == "failed"
                    else "timeout"
                    if "deadline" in result.detail.lower()
                    else "environment_blocked",
                    "expected_preview": json.dumps(case["expected"], ensure_ascii=False)[:256],
                    "actual_preview": detail.get("actual"),
                    "exception_type": detail.get("exception_type"),
                }
            )
        return result, safe_value(cases)

    if launcher is None:
        return finish(
            CheckResult(id=rule["id"], status="blocked", detail="Python grading requires a healthy Sandbox Broker.")
        )
    module = ScopedPaths(workspace).resolve(rule["path"])
    if not module.is_file():
        result, cases = finish(CheckResult(id=rule["id"], status="failed", detail="Submitted module is missing."))
        return result, [{**case, "failure_kind": "missing_artifact"} for case in cases]
    if module.stat().st_size > 1_048_576:
        return finish(
            CheckResult(id=rule["id"], status="blocked", detail="Submitted module exceeds the grading limit.")
        )
    nonce = uuid4().hex
    marker = "PRAXIS_GRADE_" + nonce
    # The script/rubric are created only after the agent is closed and outside
    # its approved workspace. Neither shell commands nor imports run on the host.
    with TemporaryDirectory(prefix="praxis-hidden-grader-") as directory:
        root = Path(directory)
        script = root / "grade.py"
        script.write_bytes(Path(__file__).with_name("grading_worker.py").read_bytes())
        cases_file = root / "cases.json"
        cases_file.write_text(json.dumps(rule["cases"], allow_nan=False), encoding="utf-8")
        command = shell_command(
            [sys.executable, "-I", "-B", str(script), str(module), rule["function"], str(cases_file), marker]
        )
        commands = WorkspaceCommand(root)
        tools = ToolRegistry([command_tool(commands), stdin_tool(commands)])
        policy = AcceptanceContract.model_validate(
            {"checks": [{"id": rule["id"], "kind": "command", "cmd": command, "timeout_seconds": 15}], "max_repairs": 0}
        )
        runner = AgentRunner(
            _NoModel(),
            tools,
            workspace_root=str(workspace),
            project_cwd=str(root),
            sandbox_launcher=launcher,
            sandbox_config=sandbox_config,
        )
        try:
            runtime = runner.new_runtime(
                task="Independent behavior grading",
                acceptance=policy,
                interrupt=lambda _: InterruptDecision("continue"),
            )
            runtime.state.permission_mode = "read_only"
            deadline = monotonic() + 15
            runtime.services.cancel_requested = lambda: monotonic() >= deadline
            result = verify_acceptance(runtime).checks[0]
            if monotonic() >= deadline:
                result = result.model_copy(
                    update={
                        "status": "blocked",
                        "detail": "Independent grading admission or execution deadline exceeded.",
                    }
                )
            payload = None
            if result.status in {"passed", "failed"}:
                try:
                    # The protocol may arrive before exit; the final poll can
                    # be empty. Recover it from earlier bounded tool chunks.
                    chunks = []
                    for action in runtime.run.actions[-32:]:
                        try:
                            content = json.loads(action.content or "")
                        except (ValueError, TypeError):
                            continue
                        if isinstance(content, dict) and isinstance(content.get("output"), str):
                            chunks.append(content["output"][-2000:])
                    output = "".join(chunks)[-8192:] + "\n" + result.detail
                    payload, _ = json.JSONDecoder().raw_decode(output.rsplit(marker, 1)[1])
                    codes = payload["codes"]
                    if len(codes) != len(rule["cases"]) or any(type(c) is not int or c not in range(5) for c in codes):
                        raise ValueError("Invalid case status")
                    status = "passed" if result.status == "passed" and all(c == 0 for c in codes) else "failed"
                    result = result.model_copy(update={"status": status})
                except (ValueError, IndexError, KeyError, TypeError):
                    payload = None
                    result = result.model_copy(update={"status": "failed"})
            detail = "Independent Python behavior checks " + result.status + "."
            if payload is None:
                detail += " " + str(safe_value(result.detail))[:500]
            return finish(result.model_copy(update={"detail": detail}), payload)
        finally:
            runner.close()


def grade(workspace: Path, task: dict, *, sandbox_launcher=None, sandbox_config: dict | None = None) -> dict:
    rubric = task["rubric"]
    digest = hashlib.sha256(json.dumps(rubric, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    results = []
    case_results = []
    judge = AgentRunner(_NoModel(), ToolRegistry(), workspace_root=str(workspace))
    try:
        for rule in rubric:
            if rule["kind"] == "python_cases":
                result, cases = _python_check(workspace, rule, sandbox_launcher, sandbox_config or {})
                results.append(result)
                case_results.extend(cases)
            elif rule["kind"] == "file_equals":
                path = ScopedPaths(workspace).resolve(rule["path"])
                expected = rule["expected"].encode("utf-8")
                matched = path.is_file() and path.stat().st_size == len(expected)
                if matched:
                    with path.open("rb") as stream:
                        matched = stream.read(len(expected) + 1) == expected
                results.append(
                    CheckResult(
                        id=rule["id"],
                        status="passed" if matched else "failed",
                        detail="Exact file comparison " + ("passed." if matched else "failed."),
                    )
                )
            else:
                runtime = judge.new_runtime(
                    task="Independent file grading",
                    acceptance=AcceptanceContract.model_validate({"checks": [rule], "max_repairs": 0}),
                )
                results.extend(verify_acceptance(runtime).checks)
        outputs = {item["path"] for item in task["demo_outputs"]}
        for seed in task.get("files", []):
            if seed["path"] in outputs:
                continue
            path = ScopedPaths(workspace).resolve(seed["path"])
            expected = seed["content"].encode("utf-8")
            unchanged = path.is_file() and path.stat().st_size == len(expected)
            if unchanged:
                with path.open("rb") as stream:
                    unchanged = stream.read(len(expected) + 1) == expected
            results.append(
                CheckResult(
                    id="input_" + str(len(results)),
                    status="passed" if unchanged else "failed",
                    detail="Input file preserved." if unchanged else "Input or public tests were changed.",
                )
            )
    finally:
        judge.close()
    status = next(
        (
            value
            for value in ("blocked", "failed", "cancelled", "budget_exhausted")
            if any(item.status == value for item in results)
        ),
        "passed",
    )
    report = AcceptanceReport(attempt=1, status=status, checks=tuple(results))
    python_ids = {item["check_id"] for item in case_results}
    for item in results:
        if item.id not in python_ids:
            case_results.append(
                {
                    "check_id": item.id,
                    "case_id": "criterion",
                    "status": item.status,
                    "failure_kind": None
                    if item.status == "passed"
                    else "input_mutated"
                    if item.id.startswith("input_")
                    else "criterion_mismatch"
                    if item.status == "failed"
                    else "environment_blocked",
                }
            )
    return {**report.model_dump(mode="json"), "rubric_sha256": digest, "case_results": case_results}
