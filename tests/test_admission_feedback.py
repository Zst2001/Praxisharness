from __future__ import annotations

import sys

import pytest

from backend.domain import AssistantMessage
from backend.domain.acceptance import AcceptanceContract
from backend.runtime import AgentRunner
from backend.runtime.core.contracts import InterruptDecision
from backend.runtime.execution.acceptance import verify_acceptance
from backend.sandbox.runtime.aggregate import AggregateResources
from backend.sandbox.runtime.launcher import SandboxLauncher
from backend.tools import ToolRegistry
from benchmarks.harness_grading import shell_command
from tests.testing_sandbox import DirectTestSandboxLauncher


def test_abandoned_granted_permit_expires_and_late_launch_is_rejected():
    now = [0.0]
    resources = AggregateResources(clock=lambda: now[0], start=False)
    assert resources.acquire("owner", "lost")["granted"]
    assert resources.acquire("owner", "next")["wait_reason"] == "permit_busy"
    now[0] = 31
    resources.tick()
    with pytest.raises(ValueError, match="permit"):
        resources.require_permit("owner", "lost")
    assert resources.acquire("owner", "next")["granted"]
    assert "expired" in [e["event"] for e in resources.status("owner")["recent_events"]]


def test_trusted_launch_is_pinned_and_cancellation_cannot_admit_a_second_launch():
    now = [0.0]
    resources = AggregateResources(clock=lambda: now[0], start=False)
    resources.acquire("owner", "first")
    resources.claim("owner", "first")
    now[0] = 40
    resources.tick()
    resources.require_permit("owner", "first")
    resources.cancel("owner", "first")
    assert resources.acquire("owner", "second")["wait_reason"] == "launch_in_progress"
    with pytest.raises(ValueError, match="permit"):
        resources.register("owner", "first", "job", lambda: {}, lambda _: None)
    resources.finish_claim("owner", "first")
    assert resources.acquire("owner", "second")["granted"]
    assert not resources.jobs


def test_admission_diagnostics_distinguish_quota_cleanup_error_and_other_owners():
    resources = AggregateResources(start=False)
    resources.configure("owner", {"memory_mib": 64, "processes": 1, "handles": 10})
    resources.acquire("owner", "first")
    resources.register(
        "owner", "first", "job", lambda: {"memory_bytes": 1, "processes": 1, "handles": 1}, lambda _: None
    )
    assert resources.acquire("owner", "second")["wait_reason"] == "quota_reached"
    resources.jobs["job"].stopping = True
    assert resources.status("owner")["wait_reason"] == "cleanup_pending"
    resources.errors["owner"] = {"code": "sample_failed"}
    assert resources.status("owner")["wait_reason"] == "sampling_error"
    assert resources.status("other")["recent_events"] == []


def test_production_admission_timeout_removes_only_its_own_ticket(tmp_path):
    class Broker:
        cancelled = []

        def resource_request(self, operation, **values):
            if operation == "resource_cancel":
                self.cancelled.append(values["ticket"])
                return {}
            return {"granted": False, "queued": 1, "wait_reason": "permit_busy", "nonce": "must-not-be-forwarded"}

    broker = Broker()
    launcher = SandboxLauncher(broker=broker, lease_store_path=tmp_path / "leases.json")
    notifications = []
    with pytest.raises(Exception, match="admission deadline"):
        launcher.wait_resources("owned", timeout_seconds=0.01, notify=notifications.append)
    assert broker.cancelled == ["owned"]
    assert all("nonce" not in row for row in notifications)


class NoModel:
    name = "no-model"

    def decide(self, runtime):
        return AssistantMessage(content="Done")


def test_real_streamed_failure_output_survives_empty_final_poll(tmp_path):
    script = tmp_path / "check.py"
    script.write_text(
        "import time\nprint('assertion: wrong tie order', flush=True)\ntime.sleep(5.3)\nraise SystemExit(1)\n",
        encoding="utf-8",
    )
    with AgentRunner(
        NoModel(), ToolRegistry(tmp_path), workspace_root=str(tmp_path), sandbox_launcher=DirectTestSandboxLauncher()
    ) as runner:
        runtime = runner.new_runtime(
            task="Check",
            acceptance=AcceptanceContract.model_validate(
                {
                    "checks": [
                        {
                            "id": "public",
                            "kind": "command",
                            "cmd": shell_command([sys.executable, str(script)]),
                            "timeout_seconds": 10,
                        }
                    ]
                }
            ),
            interrupt=lambda _: InterruptDecision("continue"),
        )
        runtime.state.permission_mode = "workspace_write"
        report = verify_acceptance(runtime)
        assert report.status == "failed" and "assertion: wrong tie order" in report.checks[0].detail
        assert len(runtime.run.actions) == 2


def test_acceptance_aggregates_split_sensitive_output_before_redaction(monkeypatch, tmp_path):
    import backend.runtime.execution.acceptance as module

    chunks = iter(
        [
            ({"session_id": "command", "output": "Authorization: Bea"}, "blocked"),
            ({"session_id": "command", "output": "rer abcdefghijklmnopqrstuvwxyz123456"}, "blocked"),
            ({"exit_code": 1, "output": "\nassertion failed"}, "blocked"),
        ]
    )
    monkeypatch.setattr(module, "_command_call", lambda *_: next(chunks))
    with AgentRunner(NoModel(), ToolRegistry(tmp_path), workspace_root=str(tmp_path)) as runner:
        runtime = runner.new_runtime(
            task="Check",
            acceptance=AcceptanceContract.model_validate(
                {"checks": [{"id": "public", "kind": "command", "cmd": "test"}]}
            ),
        )
        result = verify_acceptance(runtime)
        assert "assertion failed" in result.checks[0].detail
        assert "abcdefghijklmnopqrstuvwxyz123456" not in result.checks[0].detail


def test_output_is_bounded_and_operation_deadline_restores_existing_callback(monkeypatch, tmp_path):
    import backend.runtime.execution.acceptance as module

    monkeypatch.setattr(module, "_command_call", lambda *_: ({"exit_code": 0, "output": "x" * 10000}, "blocked"))
    with AgentRunner(NoModel(), ToolRegistry(tmp_path), workspace_root=str(tmp_path)) as runner:
        runtime = runner.new_runtime(
            task="Check",
            acceptance=AcceptanceContract.model_validate(
                {"checks": [{"id": "public", "kind": "command", "cmd": "test"}]}
            ),
        )

        def previous():
            return False

        runtime.services.operation_interrupted = previous
        result = verify_acceptance(runtime)
        assert result.status == "passed" and len(result.checks[0].detail) < 2100
        assert "truncated" in result.checks[0].detail.lower()
        assert runtime.services.operation_interrupted is previous
