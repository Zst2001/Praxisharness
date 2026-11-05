from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.domain import ToolMessage
from backend.domain.acceptance import AcceptanceContract
from backend.runtime import AgentRunner
from backend.runtime.execution.acceptance import verify_acceptance
from backend.tools import ToolError, ToolRegistry
from backend.tools.command import WorkspaceCommand
from backend.tools.default_tools.command import command_tool, stdin_tool
from benchmarks.evidence import MAX_FILE_BYTES, TrialEvidence, regrade, tool_trace
from benchmarks.harness import evaluate, load_suite
from benchmarks.harness_grading import _NoModel, grade
from benchmarks.telemetry import TrialTelemetry
from tests.testing_sandbox import DirectTestSandboxLauncher

V1 = Path("benchmarks/harness_challenge_v1.json")
V2 = Path("benchmarks/harness_challenge_v2.json")


def prepare(workspace, task, source):
    workspace.mkdir(exist_ok=True)
    for item in task["files"]:
        (workspace / item["path"]).write_bytes(item["content"].encode("utf-8"))
    (workspace / "solution.py").write_text(source, encoding="utf-8")


def public_status(workspace, task):
    commands = WorkspaceCommand(workspace)
    with AgentRunner(
        _NoModel(),
        ToolRegistry([command_tool(commands), stdin_tool(commands)]),
        workspace_root=str(workspace),
        sandbox_launcher=DirectTestSandboxLauncher(),
    ) as runner:
        import sys

        contract = json.loads(json.dumps(task["checks"]).replace("{python}", sys.executable.replace("\\", "/")))
        runtime = runner.new_runtime(
            task="public calibration", acceptance=AcceptanceContract.model_validate({"checks": contract})
        )
        runtime.state.permission_mode = "workspace_write"
        return verify_acceptance(runtime).status


def test_public_v2_rejects_a_known_v1_false_acceptance_and_reference_still_passes(tmp_path):
    v1 = load_suite(V1)[0]["tasks"][0]
    v2 = load_suite(V2)[0]["tasks"][0]
    assert v1["rubric"] == v2["rubric"]
    # Recursion is skipped when a dictionary replaces a scalar: nested nulls
    # survive, although the original shallow public cases all pass.
    faulty = """from copy import deepcopy
def merge_layers(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_layers(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
"""
    workspace = tmp_path / "workspace"
    prepare(workspace, v1, faulty)
    assert public_status(workspace, v1) == "passed"
    original = grade(workspace, v1, sandbox_launcher=DirectTestSandboxLauncher())
    assert original["status"] == "failed"
    assert original["case_results"][1]["failure_kind"] == "wrong_result"
    prepare(workspace, v2, faulty)
    assert public_status(workspace, v2) == "failed"
    prepare(workspace, v2, v2["demo_outputs"][0]["content"])
    assert public_status(workspace, v2) == "passed"
    assert grade(workspace, v2, sandbox_launcher=DirectTestSandboxLauncher())["status"] == "passed"


def test_grader_reports_each_failure_kind_and_suppresses_submission_stdout(tmp_path):
    (tmp_path / "solution.py").write_text(
        """print('private candidate output')
def f(values, mode):
    if mode == 'mutate':
        values.append(9)
        return 0
    if mode == 'raise':
        raise ValueError('private exception message')
    return False
""",
        encoding="utf-8",
    )
    task = {
        "files": [],
        "demo_outputs": [{"path": "solution.py"}],
        "rubric": [
            {
                "id": "behavior",
                "kind": "python_cases",
                "path": "solution.py",
                "function": "f",
                "cases": [
                    {"id": "wrong", "args": [[], "wrong"], "expected": 0},
                    {"id": "mutation", "args": [[], "mutate"], "expected": 0},
                    {"id": "exception", "args": [[], "raise"], "expected": 0},
                ],
            }
        ],
    }
    result = grade(tmp_path, task, sandbox_launcher=DirectTestSandboxLauncher())
    assert [r["failure_kind"] for r in result["case_results"]] == ["wrong_result", "input_mutated", "exception"]
    assert result["case_results"][2]["exception_type"] == "ValueError"
    assert "private" not in json.dumps(result)


def test_grader_retains_protocol_from_a_poll_before_process_exit(tmp_path):
    (tmp_path / "solution.py").write_text(
        "import atexit, time\natexit.register(time.sleep, 1.5)\ndef f(): return 1\n", encoding="utf-8"
    )
    task = {
        "files": [],
        "demo_outputs": [{"path": "solution.py"}],
        "rubric": [
            {
                "id": "behavior",
                "kind": "python_cases",
                "path": "solution.py",
                "function": "f",
                "cases": [{"args": [], "expected": 1}],
            }
        ],
    }
    result = grade(tmp_path, task, sandbox_launcher=DirectTestSandboxLauncher())
    assert result["status"] == "passed"
    assert result["case_results"][0]["failure_kind"] is None


def test_evidence_survives_workspace_cleanup_and_regrades_without_a_model(tmp_path):
    report = evaluate(
        V2, task_ids=("merge-layers",), sandbox_launcher=DirectTestSandboxLauncher(), evidence_dir=tmp_path / "evidence"
    )
    for row in report["results"]:
        manifest = Path(row["evidence"]["manifest"])
        assert row["evidence"]["replayable"]
        assert (manifest.parent / "candidate/solution.py").exists()
        assert (manifest.parent / "diff.patch").exists()
        replay = regrade(manifest, V2, sandbox_launcher=DirectTestSandboxLauncher())
        assert replay["external_model_requests"] == 0
        assert replay["grading"]["status"] == row["grading"]["status"]
        assert [(x["case_id"], x["failure_kind"]) for x in replay["grading"]["case_results"]] == [
            (x["case_id"], x["failure_kind"]) for x in row["grading"]["case_results"]
        ]
    repaired = next(r for r in report["results"] if r["arm"] == "repair")
    assert repaired["tool_summary"]["acceptance_calls"] > 0


def package(tmp_path, secret=""):
    task = load_suite(V2)[0]["tasks"][0]
    workspace = tmp_path / "workspace"
    prepare(workspace, task, task["demo_outputs"][0]["content"] + (f"\n# {secret}" if secret else ""))
    evidence = TrialEvidence(
        tmp_path / "evidence",
        workspace,
        {"experiment_id": "unit", "task_id": "merge-layers", "arm": "repair", "attempt": 1},
        suite_sha256=load_suite(V2)[1],
        provenance={},
        secret=secret,
    )
    evidence.capture(workspace, task, [])
    evidence.finish({"grading": {"status": "passed"}})
    return evidence.path / "manifest.json", workspace


def test_regrade_refuses_tampered_candidate_and_changed_suite(tmp_path):
    manifest, _ = package(tmp_path)
    with pytest.raises(ValueError, match="fingerprint"):
        regrade(manifest, V1)
    (manifest.parent / "candidate/solution.py").write_text("raise RuntimeError('must never run')", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        regrade(manifest, V2)


def test_redacted_candidate_is_not_claimed_to_be_exactly_replayable(tmp_path):
    secret = "opaque-provider-credential"
    manifest, _ = package(tmp_path, secret)
    assert all(secret.encode() not in p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file())
    with pytest.raises(ValueError, match="redacted"):
        regrade(manifest, V2)


def test_regrade_refuses_path_traversal_and_auxiliary_tampering(tmp_path):
    manifest, _ = package(tmp_path)
    original = manifest.read_bytes()
    value = json.loads(original)
    value["files"][0]["path"] = "../outside.py"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ToolError):
        regrade(manifest, V2)
    manifest.write_bytes(original)
    (manifest.parent / "tools.json").write_text("[]", encoding="utf-8")
    # Change the bytes even when the original trace was the empty list.
    with (manifest.parent / "tools.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="integrity"):
        regrade(manifest, V2)


def test_oversized_candidate_and_manifest_are_refused(tmp_path):
    manifest, workspace = package(tmp_path)
    task = load_suite(V2)[0]["tasks"][0]
    (workspace / "solution.py").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    evidence = TrialEvidence(
        tmp_path / "oversized",
        workspace,
        {"experiment_id": "unit", "task_id": "merge-layers", "arm": "repair", "attempt": 1},
        suite_sha256=load_suite(V2)[1],
        provenance={},
    )
    evidence.capture(workspace, task, [])
    assert not evidence.manifest["replayable"]
    with pytest.raises(ValueError, match="incomplete"):
        regrade(evidence.path / "manifest.json", V2)
    manifest.write_bytes(b" " * (MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="too large"):
        regrade(manifest, V2)


def test_hard_link_candidate_is_omitted_without_reading_it(tmp_path):
    task = load_suite(V2)[0]["tasks"][0]
    workspace = tmp_path / "workspace"
    prepare(workspace, task, "def merge_layers(a,b): return {}")
    os.link(workspace / "solution.py", tmp_path / "other-link.py")
    evidence = TrialEvidence(
        tmp_path / "evidence",
        workspace,
        {"experiment_id": "unit", "task_id": "merge-layers", "arm": "baseline", "attempt": 1},
        suite_sha256=load_suite(V2)[1],
        provenance={},
    )
    evidence.capture(workspace, task, [])
    assert not evidence.manifest["replayable"]
    assert not (evidence.path / "candidate/solution.py").exists()


def test_trace_distinguishes_acceptance_and_redacts_tool_results():
    from types import SimpleNamespace

    telemetry = TrialTelemetry("opaque-key")
    telemetry.observe(
        SimpleNamespace(
            kind="assistant_message",
            data={"source": "acceptance", "message": {"tool_messages": [{"call_id": "check"}]}},
        )
    )
    action = ToolMessage(
        name="run_command",
        call_id="check",
        arguments={"cmd": "python public.py"},
        status="succeeded",
        content=json.dumps({"exit_code": 1, "output": "opaque-key"}),
    )
    trace = tool_trace([action], telemetry)
    assert trace[0]["phase"] == "acceptance" and trace[0]["exit_code"] == 1
    assert "opaque-key" not in json.dumps(trace)
