"""Free real-Broker command integrity and budget-boundary probes."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from backend.domain import AssistantMessage, ToolMessage
from backend.domain.acceptance import AcceptanceContract
from backend.runtime import AgentRunner
from backend.runtime.core.contracts import InterruptDecision
from backend.runtime.execution.workflows.budgets import budget_snapshot
from backend.sandbox import SandboxLauncher, WindowsBrokerClient
from backend.tools import Tool, ToolRegistry
from backend.tools.command import WorkspaceCommand
from backend.tools.default_tools.command import command_tool, stdin_tool

from .evidence import safe_value
from .harness_grading import shell_command
from .provenance import source_provenance


class _Planner:
    name = "free-budget-probe"

    def __init__(self, command: str | None = None):
        self.command = command
        self.calls = 0

    def decide(self, runtime):
        self.calls += 1
        if self.command is not None and self.calls > 1:
            return AssistantMessage(content="Probe complete")
        return AssistantMessage(
            tool_messages=[
                ToolMessage(
                    name="run_command" if self.command else "inspect",
                    call_id=runtime.next_tool_call_id(),
                    arguments={"cmd": self.command, "yield_time_ms": 0 if "sleep" in self.command else 10000}
                    if self.command
                    else {},
                )
            ]
        )


def probe() -> dict:
    broker = WindowsBrokerClient.from_system()
    if not broker.status().healthy:
        raise RuntimeError("Real Broker unavailable; no host fallback")
    results = []
    with tempfile.TemporaryDirectory(prefix="praxis-command-integrity-") as directory:
        workspace = Path(directory)
        script = workspace / "验证 script.py"
        script.write_text(
            "from pathlib import Path\nprint('begin', flush=True)\nPath('last-line.txt').write_text('done')\nprint('end', flush=True)\n",
            encoding="utf-8",
        )
        failure = workspace / "late failure.py"
        failure.write_text("print('begin', flush=True)\nraise RuntimeError('late-failure')\n", encoding="utf-8")
        cases = [
            (
                "multiline-lf-rejected",
                shell_command([sys.executable, "-c", "print('begin')\nraise RuntimeError('late-failure')"]),
                None,
                None,
            ),
            (
                "multiline-crlf-rejected",
                shell_command([sys.executable, "-c", "print('begin')\r\nprint('end')"]),
                None,
                None,
            ),
            ("script-unicode-spaces-last-line", shell_command([sys.executable, str(script)]), 0, "end"),
            ("script-late-failure", shell_command([sys.executable, str(failure)]), 1, "late-failure"),
            ("single-line-quoting", shell_command([sys.executable, "-c", "print('quoted value')"]), 0, "quoted value"),
        ]
        for name, command, code, marker in cases:
            planner, commands = _Planner(command), WorkspaceCommand(workspace)
            with AgentRunner(
                planner,
                ToolRegistry([command_tool(commands), stdin_tool(commands)]),
                workspace_root=directory,
                sandbox_launcher=SandboxLauncher(broker=broker),
                sandbox_config={"network_mode": "no_network", "proxy_port": 17831},
            ) as runner:
                runtime = runner.new_runtime(task=name, interrupt=lambda _: InterruptDecision("continue"))
                runtime.state.permission_mode = "workspace_write"
                runner.run(runtime)
            action = runtime.run.actions[0]
            if code is None:
                passed = action.status == "failed" and "workspace script file" in action.content
            else:
                payload = json.loads(action.content)
                passed = payload.get("exit_code") == code and marker in payload.get("output", "")
                if name == "script-unicode-spaces-last-line":
                    passed = passed and (workspace / "last-line.txt").read_text() == "done"
            results.append(
                {
                    "name": name,
                    "status": "passed" if passed else "failed",
                    "tool_result": safe_value(action.content)[:1500],
                }
            )
        for busy in (False, True):
            commands = WorkspaceCommand(workspace)
            command = shell_command([sys.executable, "-c", "import time; time.sleep(30)"]) if busy else None
            planner = _Planner(command)
            policy = AcceptanceContract.model_validate(
                {
                    "checks": [
                        {"id": "verify", "kind": "command", "cmd": shell_command([sys.executable, str(script)])}
                    ],
                    "max_tool_calls": 3,
                    "reserved_tool_calls": 2,
                    "max_repairs": 0,
                }
            )
            with AgentRunner(
                planner,
                ToolRegistry([Tool("inspect", "Inspect", lambda: "ok"), command_tool(commands), stdin_tool(commands)]),
                workspace_root=directory,
                sandbox_launcher=SandboxLauncher(broker=broker),
                sandbox_config={"network_mode": "no_network", "proxy_port": 17831},
            ) as runner:
                runtime = runner.new_runtime(
                    task="Budget probe", acceptance=policy, interrupt=lambda _: InterruptDecision("continue")
                )
                runtime.state.permission_mode = "workspace_write"
                runner.run(runtime)
            passed = (
                runtime.run.status == ("failed" if busy else "completed")
                and planner.calls == 1
                and len(runtime.run.actions) <= 3
            )
            passed = passed and runtime.services.job_scope.registry.active_count(scope=runtime.services.job_scope) == 0
            if busy:
                passed = passed and runtime.run.acceptance.status == "blocked" and not runtime.run.acceptance.reports
            else:
                passed = passed and budget_snapshot(runtime)["acceptance_used"] == 1
            results.append(
                {
                    "name": "busy-tool-handoff-blocked" if busy else "reserved-handoff",
                    "status": "passed" if passed else "failed",
                    "run_status": runtime.run.status,
                    "budget": budget_snapshot(runtime),
                }
            )
    resource = broker.resource_request("resource_status")
    clean = resource.get("queued") == 0 and resource.get("active_jobs") == 0 and not resource.get("permit_present")
    return {
        "provenance": source_provenance(),
        "external_model_requests": 0,
        "results": results,
        "owned_resources_clean": clean,
        "status": "passed" if clean and all(r["status"] == "passed" for r in results) else "failed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; use a new path")
    args.temp_root.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(args.temp_root.resolve())
    result = probe()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"status": result["status"], "cases": len(result["results"]), "external_model_requests": 0}))


if __name__ == "__main__":
    main()
