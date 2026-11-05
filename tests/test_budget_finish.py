from __future__ import annotations

import json
import sys

import pytest
from pydantic import ValidationError

from backend.domain import AssistantMessage, ToolMessage
from backend.domain.acceptance import AcceptanceContract
from backend.jobs import CommandError
from backend.runtime import AgentRunner, RuntimeState
from backend.runtime.core.contracts import InterruptDecision
from backend.runtime.execution.workflows.budgets import budget_snapshot
from backend.tools import Tool, ToolRegistry
from backend.tools.base import ToolError
from backend.tools.command import WorkspaceCommand
from backend.tools.default_tools.command import command_tool, stdin_tool
from benchmarks.harness_grading import shell_command
from tests.testing_sandbox import DirectTestSandboxLauncher


class InspectPlanner:
    name = "budget-inspection"

    def __init__(self, batches=(1,), final=False):
        self.decisions = 0
        self.batches = batches
        self.final = final
        self.snapshots = []

    def decide(self, runtime):
        self.snapshots.append(budget_snapshot(runtime))
        if runtime.run.acceptance is not None:
            content = runtime.services.context_suffix_messages[-1].content
            context = json.loads(content[content.index("{") :])
            assert context["budget"]["model_remaining"] == budget_snapshot(runtime)["model_remaining"]
        if self.final and self.decisions:
            return AssistantMessage(content="Done")
        count = self.batches[min(self.decisions, len(self.batches) - 1)]
        self.decisions += 1
        return AssistantMessage(
            tool_messages=[ToolMessage(name="inspect", call_id=runtime.next_tool_call_id()) for _ in range(count)]
        )


def policy(**updates):
    return AcceptanceContract.model_validate(
        {
            "checks": [{"id": "output", "kind": "file_exists", "path": "result.txt"}],
            "max_tool_calls": 3,
            "reserved_tool_calls": 1,
            "max_repairs": 5,
            **updates,
        }
    )


def run_case(tmp_path, planner, *, contract=None, on_event=None, configure=None):
    (tmp_path / "result.txt").write_text("ready")
    commands = WorkspaceCommand(tmp_path)
    with AgentRunner(
        planner,
        ToolRegistry([Tool("inspect", "Inspect", lambda: "ok"), command_tool(commands), stdin_tool(commands)]),
        workspace_root=str(tmp_path),
        sandbox_launcher=DirectTestSandboxLauncher(),
    ) as runner:
        runtime = runner.new_runtime(
            task="Inspect",
            acceptance=contract or policy(),
            on_event=on_event,
            interrupt=lambda _: InterruptDecision("continue"),
        )
        runtime.state.permission_mode = "workspace_write"
        if configure:
            configure(runtime)
        runner.run(runtime)
        return runtime


@pytest.mark.parametrize("reserved", [-1, True, "1", 4])
def test_reserve_is_strict_and_within_total(reserved):
    with pytest.raises(ValidationError):
        policy(reserved_tool_calls=reserved)


def test_agent_pool_hands_off_without_another_model_call(tmp_path):
    planner, events = InspectPlanner(), []
    runtime = run_case(tmp_path, planner, on_event=events.append)
    assert runtime.run.status == "completed" and planner.decisions == 2
    assert runtime.run.acceptance.handoff_reason == "agent_tool_limit"
    assert runtime.run.acceptance.repairs == 0
    assert len(runtime.run.actions) == 2 and runtime.run.model_turns == 2
    assert any(e.kind == "acceptance_handoff" for e in events)
    restored = RuntimeState.from_dict(runtime.state.to_dict()).current_run
    assert restored.acceptance == runtime.run.acceptance


def test_oversized_batch_is_rejected_without_partial_effects(tmp_path):
    planner = InspectPlanner((3,))
    runtime = run_case(tmp_path, planner)
    assert runtime.run.status == "completed" and not runtime.run.actions
    assert runtime.run.acceptance.handoff_reason == "tool_batch_limit"
    assert planner.decisions == 1


def test_model_turn_limit_uses_remaining_verification_budget(tmp_path):
    planner = InspectPlanner()
    runtime = run_case(tmp_path, planner, contract=policy(max_model_turns=1))
    assert runtime.run.status == "completed" and planner.decisions == 1
    assert runtime.run.acceptance.handoff_reason == "model_turn_limit"


def test_failed_handoff_does_not_grant_repairs(tmp_path):
    planner = InspectPlanner()
    runtime = run_case(
        tmp_path, planner, contract=policy(checks=[{"id": "missing", "kind": "file_exists", "path": "missing.txt"}])
    )
    assert runtime.run.status == "failed" and runtime.run.stop_reason == "budget_exhausted"
    assert runtime.run.acceptance.status == "failed" and runtime.run.acceptance.repairs == 0
    assert planner.decisions == 2


def test_zero_reserve_keeps_budget_failure(tmp_path):
    planner = InspectPlanner()
    runtime = run_case(tmp_path, planner, contract=policy(reserved_tool_calls=0))
    assert runtime.run.status == "failed" and len(runtime.run.actions) == 3
    assert runtime.run.acceptance.handoff_reason is None and not runtime.run.acceptance.reports


def test_cancellation_before_boundary_does_not_verify(tmp_path):
    planner = InspectPlanner()
    runtime = run_case(
        tmp_path, planner, configure=lambda r: setattr(r.services, "cancel_requested", lambda: len(r.run.actions) >= 2)
    )
    assert runtime.run.status == "cancelled" and not runtime.run.acceptance.reports


def test_deadline_before_boundary_does_not_verify(tmp_path):
    runtime = run_case(
        tmp_path,
        InspectPlanner(),
        configure=lambda r: setattr(r.services, "operation_interrupted", lambda: len(r.run.actions) >= 2),
    )
    assert runtime.run.status == "failed" and not runtime.run.acceptance.reports


def test_started_handoff_is_not_replayed_on_restore(tmp_path):
    def configure(runtime):
        runtime.run.acceptance = runtime.run.acceptance.model_copy(update={"handoff_reason": "agent_tool_limit"})

    planner = InspectPlanner()
    runtime = run_case(tmp_path, planner, configure=configure)
    assert runtime.run.status == "failed" and not runtime.run.acceptance.reports
    assert planner.decisions == 0 and not runtime.run.actions


def test_command_checks_share_total_and_phase_counts_survive_restore(tmp_path):
    runtime = run_case(
        tmp_path,
        InspectPlanner(),
        contract=policy(
            checks=[
                {"id": "command", "kind": "command", "cmd": shell_command([sys.executable, "-c", "print('verified')"])}
            ]
        ),
    )
    assert runtime.run.status == "completed" and len(runtime.run.actions) == 3
    assert budget_snapshot(runtime)["acceptance_used"] == 1
    state = RuntimeState.from_dict(runtime.state.to_dict())
    runtime.state = state
    assert budget_snapshot(runtime)["agent_used"] == 2
    assert budget_snapshot(runtime)["total_remaining"] == 0


def test_multiple_checks_cannot_exceed_total(tmp_path):
    command = shell_command([sys.executable, "-c", "pass"])
    runtime = run_case(
        tmp_path,
        InspectPlanner(),
        contract=policy(checks=[{"id": f"c{i}", "kind": "command", "cmd": command} for i in range(2)]),
    )
    assert runtime.run.status == "failed" and len(runtime.run.actions) == 3
    assert runtime.run.acceptance.status == "budget_exhausted"


def test_polling_uses_real_total_and_exhaustion_cleans_up(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\nprint('started', flush=True)\ntime.sleep(6)\n", encoding="utf-8")
    runtime = run_case(
        tmp_path,
        InspectPlanner(),
        contract=policy(
            checks=[
                {
                    "id": "slow",
                    "kind": "command",
                    "cmd": shell_command([sys.executable, str(script)]),
                    "timeout_seconds": 10,
                }
            ]
        ),
    )
    assert runtime.run.status == "failed" and len(runtime.run.actions) == 3
    assert runtime.run.acceptance.status == "budget_exhausted"
    assert budget_snapshot(runtime)["acceptance_used"] == 1
    assert runtime.services.job_scope.registry.active_count(scope=runtime.services.job_scope) == 0


def test_previous_acceptance_calls_do_not_spend_agent_pool_twice(tmp_path):
    from backend.tools.default_tools.filesystem import filesystem_mutation_tools
    from backend.tools.filesystem import WorkspaceFiles

    script = tmp_path / "verify.py"
    script.write_text("from pathlib import Path\nassert Path('target').exists()\n", encoding="utf-8")

    class Planner:
        name = "repair-pool"
        decisions = 0

        def decide(self, runtime):
            self.decisions += 1
            if self.decisions == 1:
                return AssistantMessage(content="Check first")
            return AssistantMessage(
                tool_messages=[
                    ToolMessage(
                        name="file_operation",
                        call_id=runtime.next_tool_call_id(),
                        arguments={"operation": "write", "path": "target", "content": "ok"},
                    )
                ]
            )

    commands, planner = WorkspaceCommand(tmp_path), Planner()
    tools = ToolRegistry(
        [*filesystem_mutation_tools(WorkspaceFiles(tmp_path)), command_tool(commands), stdin_tool(commands)]
    )
    with AgentRunner(
        planner, tools, workspace_root=str(tmp_path), sandbox_launcher=DirectTestSandboxLauncher()
    ) as runner:
        runtime = runner.new_runtime(
            task="Repair",
            acceptance=policy(
                max_tool_calls=4,
                reserved_tool_calls=2,
                checks=[{"id": "command", "kind": "command", "cmd": shell_command([sys.executable, str(script)])}],
            ),
            interrupt=lambda _: InterruptDecision("continue"),
        )
        runtime.state.permission_mode = "workspace_write"
        runner.run(runtime)
    assert runtime.run.status == "completed"
    assert [r.status for r in runtime.run.acceptance.reports] == ["failed", "passed"]
    assert budget_snapshot(runtime)["acceptance_used"] == 2
    assert budget_snapshot(runtime)["agent_used"] == 2
    assert len(runtime.run.actions) == 4


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_cmd_multiline_is_rejected_before_launch(tmp_path, newline):
    launched = []
    commands = WorkspaceCommand(
        tmp_path, is_windows=True, terminal_type="cmd", popen_factory=lambda *a, **k: launched.append(a)
    )
    with pytest.raises(ToolError, match="workspace script file"):
        commands.run("echo begin" + newline + "echo end")
    assert not launched


def test_multiline_script_file_executes_last_line_and_late_exception(tmp_path):
    path = tmp_path / "验证 script.py"
    path.write_text(
        "print('begin', flush=True)\nprint('末行', flush=True)\nraise RuntimeError('late-failure')\n", encoding="utf-8"
    )
    with pytest.raises(CommandError) as captured:
        WorkspaceCommand(tmp_path).run(shell_command([sys.executable, str(path)]))
    value = json.loads(captured.value.tool_output)
    assert value["exit_code"] != 0
    assert "begin" in value["output"] and "late-failure" in value["output"]
