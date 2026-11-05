from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.domain import AssistantMessage, ToolMessage
from backend.providers import ModelConfig
from benchmarks.evidence import TrialEvidence, regrade
from benchmarks.harness import DemoPlanner, evaluate, load_suite
from benchmarks.rounds import RoundEvidence
from benchmarks.spending import SpendingBudget, SpendingClient
from tests.testing_sandbox import DirectTestSandboxLauncher

REPAIR = Path("benchmarks/harness_repair_v1.json")
CHALLENGE = Path("benchmarks/harness_challenge_v3.json")


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    return evaluate(
        REPAIR, sandbox_launcher=DirectTestSandboxLauncher(), evidence_dir=tmp_path_factory.mktemp("rounds")
    )


def test_frozen_candidates_fail_both_graders_and_references_pass(reference):
    assert reference["evaluation_protocol"] == "submitted_candidate_repair_v1"
    assert reference["summary"]["baseline"]["passed"] == reference["summary"]["verify"]["passed"] == 0
    assert reference["summary"]["repair"]["independently_repaired_successes"] == 2
    assert reference["summary"]["repair"]["independently_failed_repair_trials"] == 2
    challenge = load_suite(CHALLENGE)[0]
    repair = load_suite(REPAIR)[0]
    assert challenge["independent_rubric_sha256"] == repair["independent_rubric_sha256"]
    assert all(a["rubric"] == b["rubric"] for a, b in zip(challenge["tasks"], repair["tasks"], strict=True))
    for row in reference["results"]:
        assert row["synthetic_submission_decisions"] == 1
        if row["arm"] == "baseline":
            assert row["grading"]["status"] == "failed"
            continue
        rounds = row["repair_analysis"]["rounds"]
        assert rounds[0]["public_status"] == rounds[0]["independent_status"] == "failed"
        if row["arm"] == "repair":
            assert [r["public_status"] for r in rounds] == ["failed", "passed"]
            assert rounds[1]["independent_status"] == "passed"
            assert row["repair_analysis"]["transitions"][0]["resolved_cases"]
            assert not row["repair_analysis"]["transitions"][0]["new_failed_cases"]


def test_round_snapshots_feedback_diffs_and_offline_regrade(reference):
    row = next(r for r in reference["results"] if r["arm"] == "repair")
    manifest = Path(row["evidence"]["manifest"])
    first, second = manifest.parent / "rounds/01", manifest.parent / "rounds/02"
    assert (first / "candidate/solution.py").read_bytes() != (second / "candidate/solution.py").read_bytes()
    feedback = json.loads((first / "feedback.json").read_bytes())
    assert feedback["source"] == "public_acceptance" and feedback["last_report"]["status"] == "failed"
    assert "rubric" not in json.dumps(feedback) and "case_results" not in json.dumps(feedback)
    assert json.loads((second / "manifest.json").read_bytes())["diff_base"] == "previous_round"
    assert "-" in (second / "diff.patch").read_text(encoding="utf-8")
    for index, status in ((1, "failed"), (2, "passed")):
        replay = regrade(manifest, REPAIR, round_index=index, sandbox_launcher=DirectTestSandboxLauncher())
        assert replay["grading"]["status"] == status and replay["external_model_requests"] == 0
    with pytest.raises(ValueError, match="round"):
        regrade(manifest, REPAIR, round_index=7)


def test_frozen_independent_case_edits_are_detected_before_execution(tmp_path):
    suite = json.loads(REPAIR.read_bytes())
    suite["tasks"][0]["rubric"][0]["cases"][0]["expected"] = [0]
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(suite), encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen"):
        load_suite(path)


def test_trial_deadline_cancels_resource_wait_and_stops_further_trials(tmp_path):
    from time import monotonic, sleep

    class WaitingLauncher(DirectTestSandboxLauncher):
        cancelled = False

        def wait_resources(self, ticket, *, cancelled=None, notify=None):
            while not cancelled():
                sleep(0.005)
            self.cancelled = True
            raise InterruptedError("Resource wait cancelled")

    task = {
        "id": "queued",
        "family": "file",
        "prompt": "Write ok into result.txt",
        "files": [{"path": "result.txt", "content": "bad"}],
        "demo_outputs": [{"path": "result.txt", "content": "ok"}],
        "checks": [{"id": "public", "kind": "command", "cmd": '"{python}" -c "pass"'}],
        "rubric": [{"id": "independent", "kind": "file_equals", "path": "result.txt", "expected": "ok"}],
    }
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"schema_version": 2, "tasks": [task]}), encoding="utf-8")
    launcher = WaitingLauncher()
    started = monotonic()
    report = evaluate(suite, sandbox_launcher=launcher, evidence_dir=tmp_path / "evidence", trial_timeout_seconds=0.1)
    assert monotonic() - started < 5 and launcher.cancelled
    assert not report["complete"] and report["planned_trials"] == 3
    assert report["halt_reason"] == "trial_timeout" and len(report["results"]) == 1
    assert report["results"][0]["outcome"] == "trial_timeout"
    assert report["results"][0]["trial_timed_out"]
    assert report["paired"]["verify"]["pairs"] == 0


def test_independent_grading_deadline_also_covers_resource_admission(monkeypatch, tmp_path):
    from benchmarks.harness_grading import grade

    now = [0]

    class WaitingLauncher(DirectTestSandboxLauncher):
        def wait_resources(self, ticket, *, cancelled=None, notify=None):
            now[0] = 20
            assert cancelled()
            raise InterruptedError("Resource wait cancelled")

    monkeypatch.setattr("benchmarks.harness_grading.monotonic", lambda: now[0])
    (tmp_path / "solution.py").write_text("raise RuntimeError('must not run on host')", encoding="utf-8")
    task = {
        "files": [],
        "demo_outputs": [{"path": "solution.py"}],
        "rubric": [
            {
                "id": "hidden",
                "kind": "python_cases",
                "path": "solution.py",
                "function": "f",
                "cases": [{"args": [], "expected": 1}],
            }
        ],
    }
    result = grade(tmp_path, task, sandbox_launcher=WaitingLauncher())
    assert result["status"] == "blocked"
    assert result["case_results"][0]["failure_kind"] == "timeout"


def test_round_manifest_tampering_is_detected(reference, tmp_path):
    import shutil

    row = next(r for r in reference["results"] if r["arm"] == "repair")
    source = Path(row["evidence"]["manifest"]).parent
    copied = tmp_path / "copied"
    shutil.copytree(source, copied)
    with (copied / "rounds/01/manifest.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="integrity"):
        regrade(copied / "manifest.json", REPAIR, round_index=1)


def test_round_diff_captures_deleted_extra_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "extra.txt").write_text("temporary", encoding="utf-8")
    task = {"files": [], "demo_outputs": [{"path": "solution.py", "content": ""}]}
    trial = {"experiment_id": "unit", "task_id": "task", "arm": "repair", "attempt": 1}
    first = TrialEvidence(tmp_path / "evidence", workspace, trial, suite_sha256="test", provenance={}, round_index=1)
    first.capture(workspace, task, [])
    (workspace / "extra.txt").unlink()
    second = TrialEvidence(tmp_path / "evidence", workspace, trial, suite_sha256="test", provenance={}, round_index=2)
    second.capture(workspace, task, [], previous=first)
    assert "-temporary" in (second.path / "diff.patch").read_text(encoding="utf-8")


def test_independent_transition_detects_a_regression_then_resolution(monkeypatch, tmp_path):
    class RegressingPlanner(DemoPlanner):
        written = 0

        def decide(self, runtime):
            repair = runtime.run.acceptance.repairs if runtime.run.acceptance else 0
            if repair > self.written:
                self.written = repair
                content = (
                    "def allocate_cents(total, weights): return []\n"
                    if repair == 1
                    else self.task["demo_outputs"][0]["content"]
                )
                return AssistantMessage(
                    tool_messages=[
                        ToolMessage(
                            name="file_operation",
                            call_id=runtime.next_tool_call_id(),
                            arguments={"operation": "write", "path": "solution.py", "content": content},
                        )
                    ]
                )
            return AssistantMessage(content="Complete")

    monkeypatch.setattr("benchmarks.harness.DemoPlanner", RegressingPlanner)
    report = evaluate(
        REPAIR,
        task_ids=("allocate-cents",),
        sandbox_launcher=DirectTestSandboxLauncher(),
        evidence_dir=tmp_path / "evidence",
    )
    row = next(r for r in report["results"] if r["arm"] == "repair")
    assert row["passed"] and row["acceptance"]["repairs"] == 2
    assert row["repair_analysis"]["transitions"][0]["new_failed_cases"]
    assert row["repair_analysis"]["transitions"][1]["resolved_cases"]
    assert report["summary"]["repair"]["introduced_case_failures"] > 0


def test_unchecked_candidate_after_budget_stop_still_reports_new_failures(monkeypatch, tmp_path):
    class BadFix(DemoPlanner):
        def decide(self, runtime):
            if runtime.run.acceptance and runtime.run.acceptance.repairs:
                return AssistantMessage(
                    tool_messages=[
                        ToolMessage(
                            name="file_operation",
                            call_id=runtime.next_tool_call_id(),
                            arguments={
                                "operation": "write",
                                "path": "solution.py",
                                "content": "def allocate_cents(total, weights): return []\n",
                            },
                        )
                    ]
                )
            return AssistantMessage(content="Submit")

    monkeypatch.setattr("benchmarks.harness.DemoPlanner", BadFix)
    report = evaluate(
        REPAIR,
        task_ids=("allocate-cents",),
        max_tool_calls=2,
        sandbox_launcher=DirectTestSandboxLauncher(),
        evidence_dir=tmp_path / "evidence",
    )
    row = next(r for r in report["results"] if r["arm"] == "repair")
    assert row["outcome"] == "budget_exhausted"
    assert len(row["repair_analysis"]["rounds"]) == 1
    assert row["repair_analysis"]["transitions"][-1]["to_stage"] == "final_candidate"
    assert row["repair_analysis"]["transitions"][-1]["new_failed_cases"]


def test_real_loopback_model_runs_only_after_public_failure_and_never_sees_independent_cases(monkeypatch, tmp_path):
    sources = {t["id"]: t["demo_outputs"][0]["content"] for t in load_suite(REPAIR)[0]["tasks"]}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            wire = json.dumps(payload)
            assert "last_report" in wire
            assert "large-exact-integers" not in wire and "newly-ready-order" not in wire
            written = any(
                m.get("role") == "tool" and m.get("tool_call_id") == "repair_write" for m in payload["messages"]
            )
            task_id = "allocate-cents" if "allocate_cents" in wire else "topo-order"
            message = {"role": "assistant", "content": "Completed"}
            if not written:
                message.update(
                    content=None,
                    tool_calls=[
                        {
                            "id": "repair_write",
                            "type": "function",
                            "function": {
                                "name": "file_operation",
                                "arguments": json.dumps(
                                    {"operation": "write", "path": "solution.py", "content": sources[task_id]}
                                ),
                            },
                        }
                    ],
                )
            body = json.dumps(
                {
                    "id": "local-round-model",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": "tool_calls" if not written else "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }
            ).encode()
            if payload.get("stream"):
                delta = dict(message)
                for index, tool in enumerate(delta.get("tool_calls", [])):
                    tool["index"] = index
                body = (
                    "data: "
                    + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                    + "\n\ndata: "
                    + json.dumps(
                        {
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "tool_calls" if not written else "stop"}
                            ],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                        }
                    )
                    + "\n\ndata: [DONE]\n\n"
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    original = RoundEvidence.grade_after_exit

    def after_exit(self, *args, **kwargs):
        assert self.runtime.run.status in {"completed", "failed"}
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RoundEvidence, "grade_after_exit", after_exit)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = ModelConfig(
            api_key="local-only-key",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model="local-rounds",
            max_tokens=2048,
            context_size=32768,
        )
        budget = SpendingBudget(
            tmp_path / "budget.db",
            limit_cny="10",
            model=config.model,
            endpoint=config.base_url + "/chat/completions",
            input_cny_per_million="2",
            output_cny_per_million="8",
        )
        report = evaluate(
            REPAIR,
            config=config,
            sandbox_launcher=DirectTestSandboxLauncher(),
            evidence_dir=tmp_path / "evidence",
            client_factory=lambda c, trial: SpendingClient(c, budget, trial=trial),
            max_transport_retries=0,
        )
        assert len(requests) == 4
        for row in report["results"]:
            assert row["model_calls"] == (2 if row["arm"] == "repair" else 0)
            assert row["spending"]["http_attempts"] == row["model_calls"]
            if row["arm"] == "repair":
                assert row["passed"] and row["usage"]["total_tokens"] == 30
                assert row["repair_analysis"]["extra_after_first_submission"]["provider_attempts"] == 2
                assert row["repair_analysis"]["extra_after_first_submission"]["total_tokens"] == 30
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
