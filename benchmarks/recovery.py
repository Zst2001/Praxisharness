"""Real subprocess crash probes with SQLite checkpoints and local file effects.

No model API is used. The command probe is reported blocked unless the real
Broker permission smoke passes; no unsandboxed command fallback is permitted.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from backend.api.state import WebAppState
from backend.configuration import ClientPaths
from backend.domain import AssistantMessage, ToolMessage
from backend.domain.acceptance import AcceptanceContract
from backend.planning.llm import LLMPlanner
from backend.providers import LLMClient, ModelConfig
from backend.runtime import AgentRunner, ConversationService
from backend.runtime.core.contracts import InterruptDecision
from backend.storage.sqlite import SQLiteSessionStore
from backend.tools import Tool, ToolRegistry

from .provenance import source_provenance

SCENARIOS = ("model_wait", "post_write", "repair_boundary", "external_edit")


class FinalPlanner:
    name = "recovery-local-script"

    def decide(self, _runtime):
        return AssistantMessage(content="Inspected saved state; no side effects replayed.")


def worker(root: Path, scenario: str) -> None:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "result.json").write_text('{"ok":false}', encoding="utf-8")
    store = SQLiteSessionStore(ClientPaths(root / "data"))
    service = None

    def barrier() -> None:
        assert service is not None and service.active_session is not None
        marker = {"session_id": service.active_session.session_id, "turn_id": service.runtime.run.turn_id}
        pending = root / "ready.tmp"
        pending.write_text(json.dumps(marker), encoding="utf-8")
        pending.replace(root / "ready.json")
        time.sleep(120)
        raise RuntimeError("Crash controller did not terminate worker.")

    def effect() -> str:
        target = workspace / "effect.txt"
        with target.open("a", encoding="utf-8") as stream:
            stream.write("x")
        barrier()
        return "unreachable"

    class CrashPlanner(FinalPlanner):
        def decide(self, runtime):
            if scenario == "model_wait":
                barrier()
            if scenario == "post_write":
                return AssistantMessage(
                    tool_messages=[ToolMessage(name="append_marker", call_id="effect_once", arguments={})]
                )
            if scenario == "external_edit" and not runtime.run.actions:
                return AssistantMessage(
                    tool_messages=[
                        ToolMessage(
                            name="file_operation",
                            call_id="write_once",
                            arguments={"operation": "write", "path": "effect.txt", "content": "original"},
                        )
                    ]
                )
            return super().decide(runtime)

    tools = ToolRegistry(workspace)
    tools.register(Tool("append_marker", "Append a local disposable marker.", effect))
    planner = CrashPlanner()
    if scenario == "model_wait":

        class WaitingEndpoint(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers["Content-Length"]))
                barrier()

        endpoint = ThreadingHTTPServer(("127.0.0.1", 0), WaitingEndpoint)
        Thread(target=endpoint.serve_forever, daemon=True).start()
        client = LLMClient(ModelConfig("local-fixture", f"http://127.0.0.1:{endpoint.server_port}/v1", "local-wait"))
        planner = LLMPlanner(client, tools.specs(), tools.read_only_specs())
    with AgentRunner(
        planner,
        tools,
        checkpoints=store,
        workspace_root=str(workspace),
        skills_enabled=False,
        max_model_turns=4,
        max_tool_calls=5,
    ) as runner:
        service = ConversationService(runner, store)

        def started():
            service.runtime.state.permission_mode = "workspace_write"
            service.runtime.services.acceptance_contract = (
                AcceptanceContract.model_validate(
                    {
                        "checks": [
                            {"id": "result", "kind": "json_equals", "path": "result.json", "expected": {"ok": True}}
                        ],
                        "max_repairs": 2,
                    }
                )
                if scenario == "repair_boundary"
                else None
            )

        def event(item):
            if (scenario == "repair_boundary" and item.kind == "acceptance_repair") or (
                scenario == "external_edit" and item.kind == "tool_result"
            ):
                barrier()

        service.run_task(
            "Inspect and complete the local recovery probe",
            mode="agent",
            on_started=started,
            on_event=event,
            interrupt=lambda _request: InterruptDecision("continue"),
        )


def run_probe(root: Path, scenario: str) -> dict:
    root.mkdir(parents=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "benchmarks.recovery", "--worker", str(root), "--scenario", scenario],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 25
        while not (root / "ready.json").exists():
            if process.poll() is not None:
                raise RuntimeError(f"Probe worker exited before checkpoint ({process.returncode}).")
            if time.monotonic() >= deadline:
                raise TimeoutError("Probe worker did not reach checkpoint.")
            time.sleep(0.05)
        marker = json.loads((root / "ready.json").read_text(encoding="utf-8"))
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    # The same startup path used by HTTP discovers the stopped run and keeps
    # pending tool evidence before marking its Turn paused.
    web = WebAppState(root / "data")
    try:
        web.access_session(marker["session_id"])
        store = web.session_store
        turn = store.find_node(marker["turn_id"])

        class InspectingPlanner(FinalPlanner):
            def decide(self, runtime):
                if scenario in {"post_write", "repair_boundary", "external_edit"}:
                    assert any(
                        "[Recovery inspection]" in (message.content or "") for message in runtime.model_messages()
                    )
                return super().decide(runtime)

        with AgentRunner(
            InspectingPlanner(),
            ToolRegistry(root / "workspace"),
            checkpoints=store,
            workspace_root=str(root / "workspace"),
            max_model_turns=100,
            max_tool_calls=100,
        ) as runner:
            service = ConversationService(runner, store)
            before = service.prepare_resume(marker["session_id"])
            calls_before = store.load_runtime(marker["session_id"]).current_run.model_calls
            stale_rejected = False
            if scenario == "external_edit":
                (root / "workspace/effect.txt").write_text("user edit", encoding="utf-8")
                try:
                    service.resume_session(
                        marker["session_id"], resume_confirmed=True, recovery_token=before.recovery_token
                    )
                except ValueError:
                    stale_rejected = True
            preview = service.prepare_resume(marker["session_id"])
            if scenario == "repair_boundary":
                # External operator resolves the file; resume still verifies it.
                (root / "workspace/result.json").write_text('{"ok":true}', encoding="utf-8")
                preview = service.prepare_resume(marker["session_id"])
            result = service.resume_session(
                marker["session_id"], resume_confirmed=True, recovery_token=preview.recovery_token
            )
            effect = root / "workspace/effect.txt"
            preserved = (
                (effect.read_text(encoding="utf-8") == ("user edit" if scenario == "external_edit" else "x"))
                if effect.exists()
                else True
            )
            saved = service.runtime.state.runner_settings
            passed = (
                turn.status == "paused"
                and result.status == "completed"
                and result.provenance.attempt == 2
                and result.model_turns == before.budgets["model_turns_used"] + 1
                and saved.max_model_turns == 4
                and saved.max_tool_calls == 5
                and preserved
            )
            if scenario == "post_write":
                passed = passed and preview.indeterminate_call_ids == ("effect_once",) and effect.exists()
            if scenario == "model_wait":
                passed = passed and calls_before == result.model_calls == 1
            if scenario == "external_edit":
                passed = passed and stale_rejected and "workspace:effect.txt" in preview.changed_files
            if scenario == "repair_boundary":
                passed = (
                    passed
                    and result.acceptance.repairs == 1
                    and [r.status for r in result.acceptance.reports] == ["failed", "passed"]
                )
            return {
                "scenario": scenario,
                "status": "passed" if passed else "failed",
                "process_killed": True,
                "discovered_turn_status": turn.status,
                "run_status": result.status,
                "indeterminate_call_ids": list(preview.indeterminate_call_ids),
                "changed_files": list(preview.changed_files),
                "model_turns_before": before.budgets["model_turns_used"],
                "model_turns_after": result.model_turns,
                "model_calls_before": calls_before,
                "model_calls_after": result.model_calls,
                "max_model_turns": saved.max_model_turns,
                "max_tool_calls": saved.max_tool_calls,
                "side_effect_preserved": preserved,
                "stale_confirmation_rejected": stale_rejected,
            }
    finally:
        web.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/output/recovery-report.json"))
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.scenario)
        return
    rows = []
    with TemporaryDirectory(prefix="praxis-recovery-") as directory:
        for scenario in SCENARIOS:
            rows.append(run_probe(Path(directory) / scenario, scenario))
    report = {
        "schema_version": 1,
        "mode": "local_fault_injection",
        "external_model_requests": 0,
        "local_http_requests": 1,
        "provenance": source_provenance(),
        "results": rows,
        "command_running": {
            "status": "not_run",
            "reason": "requires independently passing real Broker command admission; see preflight.json",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if any(row["status"] != "passed" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
