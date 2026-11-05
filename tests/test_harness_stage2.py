from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.state import WebAppState
from backend.domain import AssistantMessage, ToolMessage
from backend.domain.acceptance import AcceptanceContract
from backend.mcp.client import start_external_tools
from backend.runtime import AgentRunner
from backend.tools import ToolRegistry
from benchmarks.harness import evaluate, write_report
from benchmarks.harness_grading import grade
from tests.test_mcp_capabilities import peer
from tests.test_view_state_sync import seed
from tests.testing_sandbox import DirectTestSandboxLauncher


def test_public_acceptance_cannot_replace_independent_correctness_grading(tmp_path):
    task = {
        "id": "weak-rule",
        "family": "grading",
        "prompt": "Write correct output",
        "files": [],
        "checks": [{"id": "exists", "kind": "file_exists", "path": "result.json"}],
        "rubric": [{"id": "correct", "kind": "json_equals", "path": "result.json", "expected": {"ok": True}}],
        "demo_outputs": [{"path": "result.json", "content": '{"ok":false}'}],
    }
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"schema_version": 2, "tasks": [task]}))
    report = evaluate(suite)
    repair = next(row for row in report["results"] if row["arm"] == "repair")
    assert repair["acceptance"]["status"] == "passed"
    assert repair["grading"]["status"] == "failed"
    assert repair["passed"] is False
    assert report["summary"]["repair"]["false_acceptance_count"] == 1
    assert len({row["prompt_sha256"] for row in report["results"]}) == 1
    destination = tmp_path / "report.json"
    write_report(report, destination)
    assert json.loads(destination.read_text())["paired"]["repair"]["neither_passed"] == 1
    assert "verify" in destination.with_suffix(".md").read_text()


@pytest.mark.parametrize("repairs", [None, 0, 2])
def test_all_experiment_arms_stop_before_the_same_model_decision_limit(tmp_path, repairs):
    class Loop:
        name = "loop"
        calls = 0

        def decide(self, runtime):
            self.calls += 1
            return AssistantMessage(
                tool_messages=[
                    ToolMessage(name="read_file", call_id=f"read_{self.calls}", arguments={"path": "note.txt"})
                ]
            )

    (tmp_path / "note.txt").write_text("test")
    policy = (
        None
        if repairs is None
        else AcceptanceContract.model_validate(
            {"checks": [{"id": "file", "kind": "file_exists", "path": "note.txt"}], "max_repairs": repairs}
        )
    )
    planner = Loop()
    with AgentRunner(planner, ToolRegistry(tmp_path), workspace_root=str(tmp_path), max_model_turns=2) as runner:
        runtime = runner.new_runtime(task="test", acceptance=policy)
        run = runner.run(runtime)
        assert planner.calls == run.model_turns == 2
        assert run.stop_reason == "budget_exhausted"
        assert len(run.actions) == 2


def test_python_grading_is_blocked_without_a_sandbox(tmp_path):
    (tmp_path / "solution.py").write_text("raise RuntimeError('must not execute on host')")
    task = {
        "rubric": [
            {
                "id": "behavior",
                "kind": "python_cases",
                "path": "solution.py",
                "function": "f",
                "cases": [{"args": [], "expected": 1}],
            }
        ],
        "files": [],
        "demo_outputs": [{"path": "solution.py"}],
    }
    assert grade(tmp_path, task)["status"] == "blocked"


@pytest.mark.parametrize("source", ["raise SystemExit(0)", "def f(values):\n    values.sort()\n    return values\n"])
def test_independent_python_grader_rejects_early_exit_and_input_mutation(tmp_path, source):
    (tmp_path / "solution.py").write_text(source)
    task = {
        "rubric": [
            {
                "id": "behavior",
                "kind": "python_cases",
                "path": "solution.py",
                "function": "f",
                "cases": [{"args": [[2, 1]], "expected": [1, 2]}],
            }
        ],
        "files": [],
        "demo_outputs": [{"path": "solution.py"}],
    }
    assert grade(tmp_path, task, sandbox_launcher=DirectTestSandboxLauncher())["status"] == "failed"


def test_acceptance_draft_persists_and_can_be_cleared(tmp_path):
    root = tmp_path / "data"
    state = WebAppState(root)
    session, _ = seed(state.session_store)
    path = f"/api/view-state/{session.session_id}/{session.session_id}"
    policy = {"checks": [{"id": "file", "kind": "file_exists", "path": "report.json"}], "max_repairs": 1}
    with TestClient(create_app(state)) as client:
        saved = client.patch(path, json={"acceptance": policy})
        assert saved.status_code == 200
        assert client.patch(path, json={"draft": "new text"}).json()["acceptance"] == saved.json()["acceptance"]
        assert client.patch(path, json={"acceptance": {"checks": []}}).status_code == 422
    with TestClient(create_app(WebAppState(root))) as client:
        assert client.get(path).json()["acceptance"]["max_repairs"] == 1
        assert client.patch(path, json={"acceptance": None}).json()["acceptance"] is None


def test_loopback_mcp_bypasses_an_unusable_environment_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with peer(transport="http") as endpoint:
        resources = start_external_tools((endpoint,))
        try:
            assert "proxy-test" in resources.manager.call("fixture", "echo", {"value": "proxy-test"})
        finally:
            resources.close()
