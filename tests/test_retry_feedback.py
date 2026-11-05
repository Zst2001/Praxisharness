from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.providers import ModelConfig
from benchmarks.harness import evaluate, load_suite
from benchmarks.spending import SpendingBudget, SpendingClient
from tests.testing_sandbox import DirectTestSandboxLauncher

SUITE = Path("benchmarks/harness_retry_feedback_v1.json")


def test_source_fingerprint_does_not_traverse_excluded_artifacts(monkeypatch, tmp_path):
    import os

    from benchmarks.provenance import source_manifest

    folder = tmp_path / "benchmarks"
    folder.mkdir()
    (folder / "source.py").write_text("pass", encoding="utf-8")
    excluded = folder / "output"
    excluded.mkdir()
    (excluded / "private.json").write_text("opaque artifact", encoding="utf-8")
    original = os.scandir

    def guarded(path):
        assert not Path(path).resolve().is_relative_to(excluded)
        return original(path)

    monkeypatch.setattr("benchmarks.provenance.os.scandir", guarded)
    assert set(source_manifest(tmp_path)) == {"benchmarks/source.py"}


def test_review_protocol_has_equal_opportunities_and_immutable_checkpoints(tmp_path):
    checkpoints = []
    report = evaluate(
        SUITE,
        sandbox_launcher=DirectTestSandboxLauncher(),
        evidence_dir=tmp_path / "evidence",
        on_trial=lambda row: checkpoints.append(json.loads(json.dumps(row))),
    )
    assert report["complete"] and report["planned_trials"] == 4
    assert report["trial_arms"] == ["retry", "feedback"]
    assert report["budgets"]["max_repairs"] == 0
    assert checkpoints == report["results"]
    for task in report["selected_task_ids"]:
        rows = [r for r in report["results"] if r["task_id"] == task]
        assert all(
            r["passed"] and r["review_opportunities"] == 1 and r["synthetic_submission_decisions"] == 0 for r in rows
        )
        assert rows[0]["initial_submission"] == rows[1]["initial_submission"]
        assert {r["initial_public_feedback_injected"] for r in rows} == {True, False}
        assert all(r["acceptance"]["repairs"] == 0 and len(r["acceptance"]["reports"]) == 1 for r in rows)
        assert report["initial_screening"][task]["public_result"]["status"] == "failed"
        assert report["initial_screening"][task]["independent_after_all_trials"]["status"] == "failed"


def test_public_passing_candidate_rejected_before_client_creation(tmp_path):
    suite = load_suite(SUITE)[0]
    task = suite["tasks"][0]
    task["files"][0]["content"] = task["demo_outputs"][0]["content"]
    path = tmp_path / "passing.json"
    path.write_text(json.dumps(suite), encoding="utf-8")

    def forbidden(*_args):
        raise AssertionError("Client must not be created before screening")

    config = ModelConfig(api_key="local-only", model="local", base_url="http://127.0.0.1:1/v1")
    with pytest.raises(ValueError, match="must fail public screening"):
        evaluate(path, config=config, client_factory=forbidden, sandbox_launcher=DirectTestSandboxLauncher())


def test_experiment_budget_denial_stops_batch_before_http(tmp_path):
    config = ModelConfig(api_key="local-only", model="local", base_url="http://127.0.0.1:1/v1", max_tokens=2048)
    budget = SpendingBudget(
        tmp_path / "budget.db",
        limit_cny="10",
        model=config.model,
        endpoint=config.base_url + "/chat/completions",
        input_cny_per_million="2",
        output_cny_per_million="8",
    )
    budget.configure_experiment("no-send", "1")
    report = evaluate(
        SUITE,
        config=config,
        experiment_id="no-send",
        max_transport_retries=0,
        sandbox_launcher=DirectTestSandboxLauncher(),
        evidence_dir=tmp_path / "evidence",
        client_factory=lambda c, t: SpendingClient(c, budget, trial=t),
    )
    assert not report["complete"] and report["halt_reason"] == "evaluation_budget_exhausted"
    assert len(report["results"]) == 1 and report["results"][0]["outcome"] == "budget_exhausted"
    assert budget.summary()["http_attempts"] == 0


def test_loopback_review_has_no_screening_tool_history_and_only_feedback_sees_report(monkeypatch, tmp_path):
    import benchmarks.harness as module

    sources = {t["id"]: t["demo_outputs"][0]["content"] for t in load_suite(SUITE)[0]["tasks"]}
    requests, checkpoints = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            wire = json.dumps(payload)
            assert "large-exact-integers" not in wire and "newly-ready-order" not in wire
            written = any(
                m.get("role") == "tool" and m.get("tool_call_id") == "review_write" for m in payload["messages"]
            )
            task = "allocate-cents" if "allocate_cents" in wire else "topo-order"
            message = {"role": "assistant", "content": "Review complete"}
            if not written:
                assert not any(m.get("role") == "tool" for m in payload["messages"])
                message.update(
                    content=None,
                    tool_calls=[
                        {
                            "id": "review_write",
                            "type": "function",
                            "function": {
                                "name": "file_operation",
                                "arguments": json.dumps(
                                    {"operation": "write", "path": "solution.py", "content": sources[task]}
                                ),
                            },
                        }
                    ],
                )
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
                                {"index": 0, "delta": {}, "finish_reason": "stop" if written else "tool_calls"}
                            ],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                        }
                    )
                    + "\n\ndata: [DONE]\n\n"
                ).encode()
            else:
                body = json.dumps(
                    {
                        "choices": [
                            {"index": 0, "message": message, "finish_reason": "stop" if written else "tool_calls"}
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    original = module.regrade

    def after_all(*args, **kwargs):
        assert len(checkpoints) == 4
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "regrade", after_all)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = ModelConfig(
            api_key="local-only",
            model="local-review",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            max_tokens=2048,
        )
        report = evaluate(
            SUITE,
            config=config,
            max_transport_retries=0,
            sandbox_launcher=DirectTestSandboxLauncher(),
            evidence_dir=tmp_path / "evidence",
            on_trial=checkpoints.append,
        )
        assert len(requests) == 8
        first = [p for p in requests if not any(m.get("role") == "tool" for m in p["messages"])]
        assert len(first) == 4
        assert sum("[Initial public screening result:" in json.dumps(p) for p in first) == 2
        assert all(
            r["passed"] and r["model_calls"] == 2 and r["usage"]["total_tokens"] == 30 for r in report["results"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
