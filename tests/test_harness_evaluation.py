from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.session_store import session_store
from backend.api.state import WebAppState
from backend.domain import AssistantMessage
from backend.domain.acceptance import AcceptanceContract
from backend.domain.runtime_state import NodeWriter
from backend.domain.runtime_state import RuntimeState as RuntimeNode
from backend.providers import ModelConfig
from backend.runtime import AgentRunner
from backend.tools import ToolRegistry
from benchmarks.harness import DEFAULT_SUITE, evaluate
from tests.testing_sandbox import DirectTestSandboxLauncher


def test_scripted_ablation_is_reproducible_and_explicitly_not_capability_score():
    report = evaluate(repeat=2, sandbox_launcher=DirectTestSandboxLauncher())
    assert report["mode"] == "scripted_demo" and report["capability_score"] is False
    assert report["summary"]["baseline"]["passed"] == 0
    assert report["summary"]["verify"]["passed"] == 0
    assert report["summary"]["repair"]["passed"] == 24
    assert len(report["results"]) == 72
    assert len(report["suite_sha256"]) == 64
    for row in report["results"]:
        assert row["error"] is None
        if row["arm"] == "repair":
            assert row["acceptance"]["repairs"] == 1
            assert row["tool_calls"] >= 1


class LocalModel(BaseHTTPRequestHandler):
    requests_seen = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests_seen.append(payload)
        feedback = any("last_report" in str(message.get("content", "")) for message in payload["messages"])
        has_tool_result = any(message["role"] == "tool" for message in payload["messages"])
        message = {"role": "assistant", "content": "Completed"}
        if feedback and not has_tool_result:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "local_fix",
                        "type": "function",
                        "function": {
                            "name": "file_operation",
                            "arguments": json.dumps(
                                {"operation": "write", "path": "config.json", "content": '{"enabled":true,"retries":3}'}
                            ),
                        },
                    }
                ],
            }
        body = json.dumps(
            {
                "id": "local-only",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
                    }
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
                + json.dumps({"id": "local-only", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                + "\n\ndata: "
                + json.dumps(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }
                )
                + "\n\ndata: [DONE]\n\n"
            ).encode()
        self.send_response(200)
        self.send_header("x-request-id", "local-harness-request")
        self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_paired_evaluation_uses_real_loopback_model_http(tmp_path):
    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    suite["tasks"] = suite["tasks"][:1]
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        config = ModelConfig(
            api_key="local-test",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model="local-test",
            context_size=128000,
        )
        from benchmarks.spending import SpendingBudget, SpendingClient

        budget = SpendingBudget(
            tmp_path / "budget.db",
            limit_cny="10",
            model=config.model,
            endpoint=config.base_url + "/chat/completions",
            input_cny_per_million="2",
            output_cny_per_million="8",
            max_output_tokens=8192,
        )
        report = evaluate(path, config=config, client_factory=lambda c, trial: SpendingClient(c, budget, trial=trial))
        assert report["summary"]["baseline"]["passed"] == 0, report
        assert report["summary"]["repair"]["passed"] == 1, report
        assert report["results"][-1]["usage"]
        for row in report["results"]:
            assert row["usage"]["total_tokens"] == row["model_calls"] * 15
            assert row["usage_accounting"]["complete"] is True
            assert row["spending"]["http_attempts"] == row["model_calls"]
            for request in row["spending"]["attempts"]:
                assert request["experiment_id"] == report["experiment_id"]
                assert request["arm"] == row["arm"] and request["task_id"] == row["task_id"]
                assert request["exchange_id"] and request["created_at"] and request["ended_at"]
                assert request["request_id"] == "local-harness-request"
                assert request["charged"] == 60
        assert "api_key" not in json.dumps(report)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


def test_provider_failure_is_diagnosable_and_missing_usage_is_not_zero(tmp_path):
    class Unavailable(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {"error": {"message": "opaque-test-key Authorization: Bearer private-test-value"}}
            ).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    suite["tasks"] = suite["tasks"][:1]
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Unavailable)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        report = evaluate(
            path,
            config=ModelConfig("opaque-test-key", f"http://127.0.0.1:{server.server_port}/v1", "local-error"),
            max_transport_retries=0,
        )
        for row in report["results"]:
            assert row["outcome"] == "model_error"
            assert row["error"]
            assert row["model_errors"][-1]["status_code"] == 503
            assert row["model_errors"][-1]["exchange_id"]
            assert row["usage"]["total_tokens"] is None
            assert row["usage_accounting"]["unknown_attempts"] == 1
        assert all(group["mean_total_tokens"] is None for group in report["summary"].values())
        assert "opaque-test-key" not in json.dumps(report) and "private-test-value" not in json.dumps(report)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


def test_turn_api_captures_contract_and_checks_real_local_runtime(tmp_path, local_sandbox_runtime):
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    state = WebAppState(tmp_path / "data")
    state.model_config = lambda provider_name=None: ModelConfig(
        api_key="local-test", base_url=f"http://127.0.0.1:{server.server_port}/v1", model="local-test"
    )
    try:
        with TestClient(create_app(state)) as client:
            sidebar = client.post("/api/sidebar-threads", json={}).json()
            body = {
                "session_id": sidebar["session_id"],
                "thread_id": sidebar["thread_id"],
                "message": {"role": "user", "content": [{"type": "text", "text": "check missing file"}]},
                "acceptance": {
                    "checks": [{"id": "file", "kind": "file_exists", "path": "missing.txt"}],
                    "max_repairs": 0,
                },
            }
            invalid = client.post("/api/turns", json={**body, "acceptance": {"checks": []}})
            assert invalid.status_code == 422
            assert client.post("/api/turns", json={**body, "running_mode": "plan"}).status_code == 422
            assert client.post("/api/turns", json=body, headers={"Origin": "https://example.com"}).status_code == 403
            response = client.post("/api/turns", json=body)
            assert response.status_code == 202, response.text
            turn_id = response.json()["id"]
            store = session_store(state)
            deadline = monotonic() + 15
            while monotonic() < deadline:
                turn = store.get_node(sidebar["session_id"], turn_id)
                if turn.status != "running":
                    break
                sleep(0.05)
            assert turn.status == "failed", turn.to_dict()
            runtime = store.load_runtime(sidebar["session_id"], thread_id=sidebar["thread_id"])
            assert runtime.current_run.acceptance.status == "failed"
            evidence = [item for item in turn.assistant_items if item.get("event") == "acceptance_checked"]
            assert evidence and evidence[0]["report"]["status"] == "failed"
            query = {"session_id": sidebar["session_id"], "thread_id": sidebar["thread_id"], "data_idx": 0}
            report_url = f"/api/turns/{turn_id}/acceptance"
            original = client.get(report_url, params=query).json()
            assert original["acceptance"]["status"] == "failed"
            exported = client.get(report_url + "/export", params=query)
            assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
            assert exported.json()["turn_id"] == turn_id
            assert client.get(report_url, params={**query, "data_idx": 5}).status_code == 404
            assert client.get(report_url, params={**query, "thread_id": "wrong"}).status_code == 404
            # Overwrite the latest runtime slot. Historical evidence must still
            # come from this Turn's own versioned trace.
            runtime.current_run = None
            store.save_runtime(runtime)
            assert client.get(report_url, params=query).json()["acceptance"] == original["acceptance"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


def test_resume_api_rejects_replacing_saved_contract(tmp_path):
    state = WebAppState(tmp_path / "data")
    state.turn_message_worker.close()
    with TestClient(create_app(state)) as client:
        sidebar = client.post("/api/sidebar-threads", json={}).json()
        store = session_store(state)
        writer = NodeWriter(store)
        turn = writer.create(
            RuntimeNode.create(
                session_id=sidebar["session_id"],
                thread_id=sidebar["thread_id"],
                parent=store.ensure_root_node(sidebar["session_id"]),
                user_content=[{"type": "text", "text": "test"}],
            )
        )
        writer.finalize(turn, "paused")
        policy = AcceptanceContract.model_validate(
            {"checks": [{"id": "file", "kind": "file_exists", "path": "original.txt"}]}
        )

        class Planner:
            name = "test"

            def decide(self, runtime):
                return AssistantMessage(content="done")

        with AgentRunner(Planner(), ToolRegistry()) as runner:
            runtime = runner.new_runtime(task="test", session_id=sidebar["session_id"], acceptance=policy)
            runtime.state.thread_id = sidebar["thread_id"]
            runtime.run.turn_id, runtime.run.thread_id = turn.id, sidebar["thread_id"]
            store.save_runtime(runtime.state)
        preview = client.get(f"/api/turns/{turn.id}/recovery", params={"session_id": sidebar["session_id"]})
        assert preview.status_code == 200
        response = client.post(
            f"/api/turns/{turn.id}/resume",
            json={
                "recovery_token": preview.json()["recovery_token"],
                "acceptance": {"checks": [{"id": "file", "kind": "file_exists", "path": "different.txt"}]},
            },
        )
        assert response.status_code == 409, response.text
        assert (
            store.load_runtime(sidebar["session_id"], thread_id=sidebar["thread_id"]).current_run.acceptance.contract
            == policy
        )
