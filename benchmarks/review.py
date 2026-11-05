"""Common public screening outside both review-arm model conversations."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.domain.acceptance import AcceptanceContract
from backend.domain.file_paths import ScopedPaths
from backend.runtime import AgentRunner
from backend.runtime.core.contracts import InterruptDecision
from backend.runtime.execution.acceptance import verify_acceptance
from backend.runtime.execution.lifecycle.publisher import RunEventPublisher
from backend.tools import ToolRegistry
from backend.tools.command import WorkspaceCommand
from backend.tools.default_tools.command import command_tool, stdin_tool

from .evidence import TrialEvidence, safe_value, tool_trace
from .harness_grading import _NoModel
from .telemetry import TrialTelemetry


def screen_candidates(
    suite: dict, suite_hash: str, experiment_id: str, provenance: dict, evidence_dir, launcher, sandbox_config
) -> dict:
    screened = {}
    for task in suite["tasks"]:
        with TemporaryDirectory(prefix="praxis-review-screen-") as directory:
            workspace = Path(directory)
            for seed in task["files"]:
                target = ScopedPaths(workspace).resolve(seed["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(seed["content"].encode())
            checks = json.loads(json.dumps(task["checks"]).replace("{python}", sys.executable.replace("\\", "/")))
            commands, telemetry = WorkspaceCommand(workspace), TrialTelemetry()
            with AgentRunner(
                _NoModel(),
                ToolRegistry([command_tool(commands), stdin_tool(commands)]),
                workspace_root=directory,
                sandbox_launcher=launcher,
                sandbox_config=sandbox_config,
            ) as runner:
                runtime = runner.new_runtime(
                    task="Frozen candidate public screening",
                    acceptance=AcceptanceContract.model_validate({"checks": checks, "max_repairs": 0}),
                    interrupt=lambda _: InterruptDecision("continue"),
                    on_event=telemetry.observe,
                )
                runtime.state.permission_mode = "workspace_write"
                runtime.services.publish = RunEventPublisher(runtime)
                report = verify_acceptance(runtime).model_dump(mode="json")
            if report["status"] != "failed":
                raise ValueError(
                    f"Frozen candidate {task['id']} must fail public screening; got {report['status']}. No model request sent."
                )
            for seed in task["files"]:
                if ScopedPaths(workspace).resolve(seed["path"]).read_bytes() != seed["content"].encode():
                    raise ValueError("Public screening modified the frozen candidate")
            public = json.dumps(safe_value(report), ensure_ascii=False)
            for spelling in (str(workspace), str(workspace).replace("\\", "/"), str(workspace).replace("\\", "\\\\")):
                public = public.replace(spelling, "<candidate>")
            reference = None
            if evidence_dir is not None:
                evidence = TrialEvidence(
                    evidence_dir,
                    workspace,
                    {"experiment_id": experiment_id, "task_id": task["id"], "arm": "screening", "attempt": 1},
                    suite_sha256=suite_hash,
                    provenance=provenance,
                )
                evidence.manifest["stage"] = "common_initial_submission"
                evidence.capture(workspace, task, tool_trace(runtime.run.actions, telemetry))
                evidence.attach("acceptance.json", report)
                reference = evidence.save_manifest()
            screened[task["id"]] = {
                "candidate_id": task["candidate_id"],
                "public_result": json.loads(public),
                "initial_files_sha256": hashlib.sha256(json.dumps(task["files"], sort_keys=True).encode()).hexdigest(),
                "evidence": reference,
                "model_requests": 0,
            }
    return screened


class ReviewDemoPlanner:
    name = "review-mechanism-demo"

    def __init__(self, task: dict) -> None:
        self.task, self.written = task, False

    def decide(self, runtime):
        from backend.domain import AssistantMessage, ToolMessage

        if self.written:
            return AssistantMessage(content="Review complete")
        self.written = True
        return AssistantMessage(
            tool_messages=[
                ToolMessage(
                    name="file_operation",
                    call_id=runtime.next_tool_call_id(),
                    arguments={"operation": "write", **output},
                )
                for output in self.task["demo_outputs"]
            ]
        )
