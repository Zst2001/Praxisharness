"""Free, bounded real-Broker parallel command admission diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Timer
from time import perf_counter
from uuid import uuid4

from backend.domain import AssistantMessage, ToolMessage
from backend.runtime import AgentRunner
from backend.runtime.core.contracts import InterruptDecision
from backend.sandbox import SandboxLauncher, WindowsBrokerClient
from backend.tools import ToolRegistry
from backend.tools.command import WorkspaceCommand
from backend.tools.default_tools.command import command_tool, stdin_tool

from .evidence import safe_value
from .harness_grading import shell_command
from .provenance import source_provenance


def probe(count: int, *, timeout_seconds: float = 30) -> dict:
    if count not in (2, 4):
        raise ValueError("Probe supports two or four parallel commands")
    broker = WindowsBrokerClient.from_system()
    if not broker.status().healthy:
        return {"status": "blocked", "reason": "broker_unhealthy", "external_model_requests": 0}
    trace, lock, expired = [], Lock(), Event()
    original = broker.resource_request
    started = perf_counter()

    def observe(operation, **values):
        result = original(operation, **values)
        if operation in {"resource_acquire", "resource_cancel"}:
            row = {
                "operation": operation,
                "ticket": values["ticket"],
                "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                "granted": result.get("granted"),
                "queued": result.get("queued"),
                "usage": result.get("usage"),
                "error_present": bool(result.get("error_report")),
                "reason": result.get("wait_reason"),
            }
            with lock:
                if len(trace) < 512:
                    trace.append(row)
        return result

    broker.resource_request = observe
    launcher = SandboxLauncher(broker=broker)

    class Planner:
        name = "parallel-admission-probe"
        submitted = False

        def decide(self, runtime):
            if self.submitted:
                return AssistantMessage(content="Probe complete")
            self.submitted = True
            return AssistantMessage(
                tool_messages=[
                    ToolMessage(
                        name="run_command",
                        call_id=f"probe_{index}",
                        arguments={
                            "cmd": shell_command([sys.executable, "-c", f"print('probe-{index}')"]),
                            "yield_time_ms": 30000,
                        },
                    )
                    for index in range(count)
                ]
            )

    with TemporaryDirectory(prefix="praxis-admission-probe-") as directory:
        commands = WorkspaceCommand(Path(directory))
        timer = Timer(timeout_seconds, expired.set)
        timer.daemon = True
        with AgentRunner(
            Planner(),
            ToolRegistry([command_tool(commands), stdin_tool(commands)]),
            workspace_root=directory,
            sandbox_launcher=launcher,
            sandbox_config={"network_mode": "no_network", "proxy_port": 17831},
            max_tool_calls=16,
            max_model_turns=3,
            max_tool_parellel=count,
        ) as runner:
            runtime = runner.new_runtime(
                task="Run parallel sandbox diagnostics", interrupt=lambda _: InterruptDecision("continue")
            )
            runtime.state.permission_mode = "workspace_write"
            runtime.services.cancel_requested = expired.is_set
            timer.start()
            try:
                runner.run(runtime)
            finally:
                timer.cancel()
        results = []
        for action in runtime.run.actions:
            try:
                result = json.loads(action.content or "{}")
            except ValueError:
                result = {}
            results.append(
                {
                    "call_id": action.call_id,
                    "status": action.status,
                    "failure_code": action.failure_code,
                    "exit_code": result.get("exit_code"),
                    "detail": safe_value(action.content or "")[:500],
                }
            )
    response = original("resource_status")
    status = {
        key: response.get(key)
        for key in (
            "usage",
            "limits",
            "queued",
            "error_report",
            "diagnostics_version",
            "wait_reason",
            "permit_present",
            "active_jobs",
            "stopping_jobs",
            "recent_events",
        )
    }
    passed = (
        not expired.is_set()
        and len(results) == count
        and all(r["status"] == "succeeded" and r["exit_code"] == 0 for r in results)
        and status["queued"] == 0
        and status["usage"]["processes"] == 0
        and runtime.run.tool_calls == count
    )
    return {
        "status": "passed" if passed else "failed",
        "commands": count,
        "timed_out": expired.is_set(),
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "results": results,
        "reported_tool_calls": runtime.run.tool_calls,
        "resource_trace": trace,
        "resources_after": status,
        "external_model_requests": 0,
    }


def queue_scenarios() -> dict:
    broker = WindowsBrokerClient.from_system()
    initial = broker.resource_request("resource_status")
    if initial.get("diagnostics_version") != 1:
        return {"status": "blocked", "reason": "broker_restart_required", "external_model_requests": 0}
    launcher = SandboxLauncher(broker=broker)
    head, cancelled, waiter = ("probe-" + uuid4().hex for _ in range(3))
    stop = Event()
    timer = Timer(0.1, stop.set)
    timer.daemon = True
    started = perf_counter()
    try:
        assert broker.resource_request("resource_acquire", ticket=head)["granted"]
        timer.start()
        try:
            launcher.wait_resources(cancelled, cancelled=stop.is_set, timeout_seconds=2)
        except InterruptedError:
            cancelled_ok = True
        else:
            cancelled_ok = False
        launcher.wait_resources(waiter, timeout_seconds=35)
        current = broker.resource_request("resource_status")
        expired = any(e["ticket"] == head and e["event"] == "expired" for e in current.get("recent_events", []))
    finally:
        timer.cancel()
        for ticket in (head, cancelled, waiter):
            broker.resource_request("resource_cancel", ticket=ticket)
    after = broker.resource_request("resource_status")
    passed = cancelled_ok and expired and not after["queued"] and not after.get("permit_present")
    return {
        "status": "passed" if passed else "failed",
        "cancelled_waiter_removed": cancelled_ok,
        "abandoned_grant_expired": expired,
        "queued_after": after["queued"],
        "permit_present_after": after.get("permit_present"),
        "diagnostics_version": after.get("diagnostics_version"),
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "external_model_requests": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path)
    parser.add_argument("--queue-scenarios", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; keep the original diagnostics")
    if args.temp_root is not None:
        args.temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(args.temp_root.resolve())
    report = {"provenance": source_provenance(), "results": [probe(n) for n in (2, 4)], "external_model_requests": 0}
    if args.queue_scenarios:
        report["queue_scenarios"] = queue_scenarios()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "statuses": [r["status"] for r in report["results"]],
                "output": str(args.output),
                "external_model_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
