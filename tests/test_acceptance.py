from __future__ import annotations

import json
import sys
from threading import Event

import pytest
from pydantic import ValidationError

from backend.domain import AssistantMessage, ToolMessage
from backend.domain.acceptance import AcceptanceContract
from backend.runtime import AgentRunner, ConversationService, RuntimeState
from backend.runtime.conversation.acceptance import acceptance_record
from backend.runtime.core.contracts import InterruptDecision
from backend.tools import ToolRegistry
from tests.local_store import session_store
from tests.testing_sandbox import DirectTestSandboxLauncher


class FinalPlanner:
    name = "acceptance-test"

    def decide(self, runtime):
        return AssistantMessage(content="Done")


class RepairPlanner(FinalPlanner):
    def decide(self, runtime):
        if runtime.run.acceptance.reports and not runtime.run.actions:
            assert "last_report" in runtime.model_messages()[-1].content
            return AssistantMessage(
                tool_messages=[
                    ToolMessage(
                        name="file_operation",
                        call_id="repair_1",
                        arguments={"operation": "write", "path": "result.json", "content": '{"ok": true}'},
                    )
                ]
            )
        return super().decide(runtime)


def contract(**overrides):
    return AcceptanceContract.model_validate(
        {
            "checks": [{"id": "output", "kind": "json_equals", "path": "result.json", "expected": {"ok": True}}],
            **overrides,
        }
    )


def execute(
    tmp_path, *, policy=None, planner=None, sandbox=None, interrupt=None, on_event=None, permission="workspace_write"
):
    runner = AgentRunner(
        planner or FinalPlanner(), ToolRegistry(tmp_path), workspace_root=str(tmp_path), sandbox_launcher=sandbox
    )
    runtime = runner.new_runtime(
        task="Create result.json",
        acceptance=policy or contract(),
        interrupt=interrupt or (lambda _request: InterruptDecision("continue")),
        on_event=on_event,
    )
    runtime.state.permission_mode = permission
    try:
        runner.run(runtime)
        return runtime
    finally:
        runner.close()


def test_failure_feedback_repairs_real_file_and_roundtrips_state(tmp_path):
    events = []
    runtime = execute(tmp_path, planner=RepairPlanner(), on_event=events.append)
    assert runtime.run.status == "completed"
    assert json.loads((tmp_path / "result.json").read_text()) == {"ok": True}
    progress = runtime.run.acceptance
    assert progress.status == "passed" and progress.repairs == 1
    assert [report.status for report in progress.reports] == ["failed", "passed"]
    assert RuntimeState.from_dict(runtime.state.to_dict()).current_run.acceptance == progress
    assert [event.kind for event in events if event.kind.startswith("acceptance_")] == [
        "acceptance_started",
        "acceptance_checking",
        "acceptance_checked",
        "acceptance_repair",
        "acceptance_checking",
        "acceptance_checked",
    ]


def test_resumed_repair_shows_current_usage_before_final_report(tmp_path):
    store = session_store(tmp_path / "state")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pause = Event()
    live_usage = []

    class InspectRepair(RepairPlanner):
        def decide(self, runtime):
            if runtime.run.actions:
                run = runtime.run
                record = acceptance_record(store, runtime.state.session_id, run.thread_id, run.turn_id, run.data_idx)
                live_usage.append(record["usage"].get("tool_calls"))
            return super().decide(runtime)

    with AgentRunner(
        InspectRepair(), ToolRegistry(workspace), workspace_root=str(workspace), checkpoints=store
    ) as runner:
        service = ConversationService(runner, store)
        session = service.new_session("Repair recovery test")
        store.create_sidebar_thread(session_id=session.session_id, thread_id=session.session_id, title=session.title)
        service.runtime.state.permission_mode = "workspace_write"
        service.runtime.services.acceptance_contract = contract()

        def pause_after_failure(event):
            if getattr(event, "kind", None) == "acceptance_repair":
                pause.set()

        paused = service.run_task(
            "Create result.json", mode="agent", on_event=pause_after_failure, suspend_requested=pause.is_set
        )
        assert paused.stop_reason == "user_paused" and paused.acceptance.repairs == 1
        resumed = ConversationService(runner, store, session_id=session.session_id).resume_session(
            resume_confirmed=True, interrupt=lambda _: InterruptDecision("continue")
        )
        assert resumed.provenance.source_run_id == paused.run_id and resumed.turn_id == paused.turn_id
        assert [report.status for report in resumed.acceptance.reports] == ["failed", "passed"]
        assert live_usage == [1]


@pytest.mark.parametrize(("repairs", "expected"), [(0, "failed"), (2, "budget_exhausted")])
def test_premature_completion_is_bounded(tmp_path, repairs, expected):
    runtime = execute(tmp_path, policy=contract(max_repairs=repairs))
    assert runtime.run.status == "failed"
    assert runtime.run.acceptance.status == expected
    assert len(runtime.run.acceptance.reports) == repairs + 1
    assert runtime.run.model_turns == repairs + 1


def test_model_budget_stops_repair_before_another_model_call(tmp_path):
    runtime = execute(tmp_path, policy=contract(max_repairs=5, max_model_turns=1))
    assert runtime.run.acceptance.status == "budget_exhausted"
    assert runtime.run.model_turns == 1


def test_acceptance_checking_event_precedes_command_side_effect(tmp_path):
    from benchmarks.harness_grading import shell_command

    observed = []
    command = shell_command([sys.executable, "-c", "from pathlib import Path; Path('result.json').write_text('{}')"])
    runtime = execute(
        tmp_path,
        policy=contract(checks=[{"id": "write", "kind": "command", "cmd": command}]),
        sandbox=DirectTestSandboxLauncher(),
        on_event=lambda event: (
            observed.append((event.data["attempt"], (tmp_path / "result.json").exists()))
            if event.kind == "acceptance_checking"
            else None
        ),
    )
    assert runtime.run.acceptance.status == "passed"
    assert observed == [(1, False)] and (tmp_path / "result.json").exists()


@pytest.mark.parametrize("path", ["../outside.txt", "workspace:../outside.txt", "unknown:file"])
def test_path_escape_is_blocked_without_repair(tmp_path, path):
    runtime = execute(tmp_path, policy=contract(checks=[{"id": "file", "kind": "file_exists", "path": path}]))
    assert runtime.run.acceptance.status == "blocked"
    assert runtime.run.acceptance.repairs == 0


@pytest.mark.parametrize(
    ("content", "expected"), [("not json", "failed"), ('{"ok": 1}', "failed"), ('{"ok": true}', "passed")]
)
def test_json_is_validated_and_boolean_is_not_number(tmp_path, content, expected):
    (tmp_path / "result.json").write_text(content)
    runtime = execute(tmp_path, policy=contract(max_repairs=0))
    assert runtime.run.acceptance.status == expected


def test_large_file_is_blocked_and_text_checks_are_literal(tmp_path):
    (tmp_path / "result.json").write_bytes(b"x" * 1_048_577)
    assert execute(tmp_path).run.acceptance.status == "blocked"
    (tmp_path / "note.txt").write_text("value=a.*b", encoding="utf-8")
    policy = contract(checks=[{"id": "text", "kind": "file_contains", "path": "note.txt", "text": "a.*b"}])
    assert execute(tmp_path, policy=policy).run.acceptance.status == "passed"


@pytest.mark.parametrize(
    "change",
    [
        {"max_repairs": True},
        {"max_repairs": 6},
        {"max_model_turns": 0},
        {"checks": []},
        {"checks": [{"id": "same", "kind": "file_exists", "path": "a"}] * 2},
    ],
)
def test_invalid_contracts_are_rejected(change):
    with pytest.raises(ValidationError):
        contract(**change)


@pytest.mark.parametrize(("code", "expected"), [(0, "passed"), (3, "failed")])
def test_command_acceptance_uses_real_process_and_approval(tmp_path, code, expected):
    # Explicit test launcher verifies process integration, not Windows isolation.
    script = tmp_path / "verify.py"
    script.write_text(f"import sys; print('verified'); sys.exit({code})")
    command = f'"{sys.executable}" "{script}"'
    policy = contract(max_repairs=0, checks=[{"id": "command", "kind": "command", "cmd": command}])
    events = []
    runtime = execute(
        tmp_path, policy=policy, sandbox=DirectTestSandboxLauncher(), on_event=events.append, permission="read_only"
    )
    assert runtime.run.acceptance.status == expected
    assert "verified" in runtime.run.acceptance.reports[0].checks[0].detail
    assert any(event.kind == "approval_requested" for event in events)
    assert runtime.run.actions[0].name == "run_command"


def test_denied_command_and_missing_sandbox_are_blocked(tmp_path):
    policy = contract(checks=[{"id": "command", "kind": "command", "cmd": "echo test"}])
    denied = execute(
        tmp_path, policy=policy, interrupt=lambda _request: InterruptDecision("deny"), permission="read_only"
    )
    assert denied.run.acceptance.status == "blocked"
    missing = execute(tmp_path, policy=policy)
    assert missing.run.acceptance.status == "blocked"
    assert missing.run.acceptance.repairs == 0


def test_command_deadline_stops_real_process(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(20)")
    policy = contract(
        checks=[{"id": "slow", "kind": "command", "cmd": f'"{sys.executable}" "{script}"', "timeout_seconds": 1}]
    )
    runtime = execute(tmp_path, policy=policy, sandbox=DirectTestSandboxLauncher())
    assert runtime.run.acceptance.status == "blocked"
    assert "deadline" in runtime.run.acceptance.reports[0].checks[0].detail.lower()
    assert runtime.services.job_scope.registry.active_count(scope=runtime.services.job_scope) == 0


def test_cancel_during_acceptance_does_not_trigger_repair(tmp_path):
    stop = Event()
    runner = AgentRunner(FinalPlanner(), ToolRegistry(tmp_path), workspace_root=str(tmp_path))
    runtime = runner.new_runtime(task="test", acceptance=contract())
    runtime.services.cancel_requested = stop.is_set
    runtime.services.on_event = lambda event: stop.set() if event.kind == "acceptance_started" else None
    try:
        runner.run(runtime)
        assert runtime.run.status == "cancelled"
        assert runtime.run.acceptance.repairs == 0
    finally:
        runner.close()


def test_manual_completion_does_not_bypass_acceptance(tmp_path):
    runner = AgentRunner(FinalPlanner(), ToolRegistry(tmp_path), workspace_root=str(tmp_path))
    runtime = runner.new_runtime(task="test", acceptance=contract())
    runtime.services.complete_requested = lambda: True
    try:
        runner.run(runtime)
        assert runtime.run.status == "failed"
        assert runtime.run.acceptance.status == "blocked"
    finally:
        runner.close()


def test_tool_budget_exhaustion_cancels_pending_verification(tmp_path):
    from time import perf_counter

    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(20)")
    policy = contract(
        max_tool_calls=1, checks=[{"id": "slow", "kind": "command", "cmd": f'"{sys.executable}" "{script}"'}]
    )
    started = perf_counter()
    launcher = DirectTestSandboxLauncher()
    # The simple test launcher's default kills only cmd.exe. Use the existing
    # process group's real tree terminator, as the responsiveness tests do.
    launcher.terminate_tree = None
    runtime = execute(tmp_path, policy=policy, sandbox=launcher)
    assert runtime.run.acceptance.status == "budget_exhausted"
    assert len(runtime.run.actions) == 1
    assert perf_counter() - started < 8
