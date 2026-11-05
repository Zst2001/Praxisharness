from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.providers import ModelConfig
from benchmarks.budget_cases import build_suite
from benchmarks.efficiency import action_efficiency
from benchmarks.harness import evaluate, load_suite
from tests.testing_sandbox import DirectTestSandboxLauncher

SUITE = Path("benchmarks/harness_budget_finish_v1.json")


def test_fixed_eight_candidates_and_equal_feedback_policy(tmp_path):
    assert json.loads(SUITE.read_text(encoding="utf-8")) == build_suite()
    suite, _ = load_suite(SUITE)
    assert len(suite["tasks"]) == 8
    assert len({t["family"] for t in suite["tasks"]}) == 4
    checkpoints = []
    report = evaluate(
        SUITE,
        sandbox_launcher=DirectTestSandboxLauncher(),
        evidence_dir=tmp_path / "evidence",
        on_trial=lambda row: checkpoints.append(json.loads(json.dumps(row))),
        max_tool_calls=12,
    )
    assert report["schema_version"] == 7 and report["complete"]
    assert report["trial_arms"] == ["shared", "reserved"]
    assert len(report["results"]) == 16 and all(r["passed"] for r in report["results"])
    assert checkpoints == report["results"]
    for task in suite["tasks"]:
        rows = [r for r in report["results"] if r["task_id"] == task["id"]]
        assert rows[0]["initial_submission"] == rows[1]["initial_submission"]
        assert rows[0]["prompt_sha256"] == rows[1]["prompt_sha256"]
        for row in rows:
            assert row["initial_public_feedback_injected"] and row["review_opportunities"] == 1
            assert row["acceptance"]["repairs"] == 0
            assert row["execution_budget"]["reserved_tool_calls"] == (2 if row["arm"] == "reserved" else 0)
            assert row["tool_calls"] <= 12
        assert report["initial_screening"][task["id"]]["independent_after_all_trials"]["status"] == "failed"


def test_action_diagnostics_count_arguments_without_reusing_commands():
    def tool(name, args, **updates):
        return {"tool": name, "arguments": args, "phase": "agent", "status": "succeeded", "exit_code": None, **updates}

    command = tool("run_command", {"cmd": "python checks.py"}, exit_code=0)
    trace = [
        tool("read_file", {"path": "x"}),
        tool("read_file", {"path": "x"}),
        command,
        command,
        tool("file_operation", {}),
        command,
        tool("file_operation", {}, status="failed", result_preview="expected_content must contain every selected line"),
        tool("run_command", {"cmd": "echo begin\necho end"}, status="failed"),
    ]
    result = action_efficiency(trace, [{"kind": "command", "cmd": "python checks.py"}])
    assert result["repeated_read_arguments"] == 1
    assert result["repeated_command_arguments"] == 2
    assert result["public_self_tests"] == 3
    assert result["public_retests_after_success_without_recorded_mutation"] == 1
    assert result["editing_conflicts"] == result["multiline_command_requests"] == 1
    assert result["automatic_command_reuse"] is False


def test_loopback_budget_handoff_has_equal_feedback_and_no_extra_model_call(tmp_path):
    selected = ("repair-stable-unique-sorted", "release-version-package-version")
    tasks = {t["id"]: t for t in load_suite(SUITE)[0]["tasks"]}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            wire = json.dumps(payload)
            assert "Initial public screening result" in wire
            task = tasks[selected[0] if "stable_unique" in wire else selected[1]]
            written = any(
                m.get("role") == "tool" and m.get("tool_call_id", "").startswith("write-") for m in payload["messages"]
            )
            calls = (
                [
                    {
                        "id": "read-again",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": task["demo_outputs"][0]["path"]}),
                        },
                    }
                ]
                if written
                else [
                    {
                        "id": f"write-{i}",
                        "type": "function",
                        "function": {
                            "name": "file_operation",
                            "arguments": json.dumps({"operation": "write", **output}),
                        },
                    }
                    for i, output in enumerate(task["demo_outputs"])
                ]
            )
            message = {"role": "assistant", "content": None, "tool_calls": calls}
            body = json.dumps(
                {
                    "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }
            ).encode()
            if payload.get("stream"):
                for i, call in enumerate(calls):
                    call["index"] = i
                body = (
                    "data: "
                    + json.dumps({"choices": [{"index": 0, "delta": message, "finish_reason": None}]})
                    + "\n\ndata: "
                    + json.dumps(
                        {
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = evaluate(
            SUITE,
            config=ModelConfig(
                api_key="local-only", model="loopback", base_url=f"http://127.0.0.1:{server.server_port}/v1"
            ),
            task_ids=selected,
            max_tool_calls=4,
            max_transport_retries=0,
            sandbox_launcher=DirectTestSandboxLauncher(),
            evidence_dir=tmp_path / "evidence",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert report["complete"] and len(report["results"]) == 4
    assert len(requests) == sum(row["model_calls"] for row in report["results"])
    for row in report["results"]:
        if row["arm"] == "reserved":
            assert row["passed"] and row["execution_budget"]["handoff_reason"] == "agent_tool_limit"
            assert row["execution_budget"]["agent_used"] == 2
        else:
            assert row["outcome"] == "budget_exhausted" and row["tool_calls"] == 4
        assert row["grading"]["status"] == "passed"
