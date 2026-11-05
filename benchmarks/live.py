"""Explicit paid smoke/pilot using an encrypted local Provider and a CNY ledger."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from backend.configuration import ClientPaths
from backend.domain.acceptance import AcceptanceContract
from backend.planning.llm import LLMPlanner
from backend.providers import ModelConfig
from backend.runtime import AgentRunner
from backend.runtime.core.contracts import InterruptDecision
from backend.storage.settings import LocalSettingsStore
from backend.tools import ToolRegistry
from backend.tools.default_tools.filesystem import filesystem_mutation_tools, filesystem_read_tools
from backend.tools.filesystem import WorkspaceFiles

from .harness import DEFAULT_SUITE, evaluate, load_suite, write_report
from .provenance import source_provenance
from .spending import SpendingBudget, SpendingClient
from .telemetry import TrialTelemetry

MODEL = "deepseek-v4-1-flash-261017"
BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
PRICE_SOURCE = "https://docs.volcengine.com/docs/ark/model-pricing?lang=zh&redirect=1"
PILOT_TASKS = ("normalize-config", "write-report", "release-version")


def local_settings() -> LocalSettingsStore:
    paths = ClientPaths.from_home()
    return LocalSettingsStore(paths.state_db, paths.config_file)


def smoke(config: ModelConfig, budget: SpendingBudget) -> dict:
    """Exercise the real provider, tool call, local file and acceptance gate."""
    with TemporaryDirectory(prefix="praxis-volcengine-smoke-") as directory:
        workspace = Path(directory)
        files = WorkspaceFiles(workspace)
        tools = ToolRegistry((*filesystem_read_tools(files), *filesystem_mutation_tools(files)))
        client = SpendingClient(
            replace(config, max_tokens=1024, context_size=32768),
            budget,
            trial={"experiment_id": uuid4().hex, "task_id": "provider-smoke", "arm": "smoke", "attempt": 1},
        )
        with AgentRunner(
            LLMPlanner(client, tools.specs(), tools.read_only_specs()),
            tools,
            workspace_root=str(workspace),
            skills_enabled=False,
            max_model_turns=4,
            max_tool_calls=6,
            max_transport_retries=0,
            resources=(client.transport.session,),
        ) as runner:
            runtime = runner.new_runtime(
                task='Create result.json containing exactly {"ok": true} using the file tools, then finish.',
                acceptance=AcceptanceContract.model_validate(
                    {
                        "checks": [
                            {"id": "result", "kind": "json_equals", "path": "result.json", "expected": {"ok": True}}
                        ],
                        "max_repairs": 1,
                    }
                ),
                interrupt=lambda _: InterruptDecision("continue"),
            )
            runtime.state.permission_mode = "workspace_write"
            runtime.state.request_parameters["thinking"] = {"type": "disabled"}
            telemetry = TrialTelemetry(config.api_key)
            runtime.services.on_event = telemetry.observe
            try:
                run = runner.run(runtime)
                passed = run.status == "completed" and run.acceptance.status == "passed"
                return {
                    "passed": passed,
                    "run_status": run.status,
                    "model_calls": run.model_calls,
                    "tool_calls": len(run.actions),
                    "usage": telemetry.usage()[0],
                    "usage_accounting": telemetry.usage()[1],
                    "model_errors": telemetry.errors,
                    "spending": client.trial_spending(),
                    "response_model": runtime.exchange.prepared_response.model
                    if runtime.exchange.prepared_response
                    else None,
                    "acceptance": run.acceptance.model_dump(mode="json"),
                }
            except Exception as exc:
                return {"passed": False, "error_type": type(exc).__name__}


def pilot(
    config: ModelConfig,
    budget: SpendingBudget,
    output: Path,
    *,
    suite_path: Path = DEFAULT_SUITE,
    task_ids: tuple[str, ...] = PILOT_TASKS,
    repeat: int = 1,
    max_model_turns: int = 6,
    file_tools_only: bool = True,
    trial_timeout_seconds: int = 120,
    max_parallel_tools: int = 1,
    experiment_budget_cny: str = "4",
) -> dict:
    """Persist each completed trial so interruption does not discard evidence."""
    config = replace(config, context_size=32768, max_tokens=2048, timeout_seconds=60)
    provenance, experiment_id = source_provenance(), uuid4().hex
    suite, suite_digest = load_suite(suite_path)
    selected = [task for task in suite["tasks"] if task["id"] in task_ids]
    needs_commands = any(check["kind"] == "command" for task in selected for check in task["checks"]) or any(
        rule["kind"] == "python_cases" for task in selected for rule in task["rubric"]
    )
    if needs_commands:
        if file_tools_only:
            raise ValueError("Executable suites require --with-commands and a passing real Broker preflight.")
        from backend.sandbox import WindowsBrokerClient

        from .preflight import sandbox_smoke

        broker = WindowsBrokerClient.from_system()
        if not broker.status().healthy or sandbox_smoke(broker)["status"] != "passed":
            raise ValueError("Real Broker permission preflight failed; no paid model request was sent.")
    budget.configure_experiment(experiment_id, experiment_budget_cny)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(".trials.jsonl").open("w", encoding="utf-8") as trials:
        trials.write(
            json.dumps(
                {
                    "type": "experiment",
                    "schema_version": 7,
                    "evaluation_protocol": suite.get("evaluation_protocol", "end_to_end_v1"),
                    "experiment_id": experiment_id,
                    "provenance": provenance,
                    "selected_task_ids": task_ids,
                    "suite_sha256": suite_digest,
                    "mode": "llm",
                    "model": config.model,
                    "protocol": config.protocol,
                    "temperature": config.temperature,
                    "repeat": repeat,
                    "max_model_turns": max_model_turns,
                    "max_tool_calls": 12,
                    "max_repairs": 0
                    if suite.get("evaluation_protocol") in {"retry_feedback_v1", "budget_finish_v1"}
                    else 2,
                    "reserved_tool_calls_by_arm": {"shared": 0, "reserved": 2}
                    if suite.get("evaluation_protocol") == "budget_finish_v1"
                    else None,
                    "experiment_budget_cny": experiment_budget_cny,
                    "trial_timeout_seconds": trial_timeout_seconds,
                    "max_parallel_tools": max_parallel_tools,
                }
            )
            + "\n"
        )
        trials.flush()

        def checkpoint(row: dict) -> None:
            trials.write(json.dumps({"type": "trial", **row}, ensure_ascii=False) + "\n")
            trials.flush()

        report = evaluate(
            suite_path,
            config=config,
            task_ids=task_ids,
            repeat=repeat,
            max_model_turns=max_model_turns,
            max_tool_calls=12,
            max_transport_retries=0,
            request_parameters={"thinking": {"type": "disabled"}},
            client_factory=lambda c, trial: SpendingClient(c, budget, trial=trial),
            file_tools_only=file_tools_only,
            on_trial=checkpoint,
            experiment_id=experiment_id,
            provenance=provenance,
            evidence_dir=output.with_suffix(".evidence"),
            trial_timeout_seconds=trial_timeout_seconds,
            max_parallel_tools=max_parallel_tools,
        )
    report.update(
        spending=budget.summary(),
        experiment_spending=budget.experiment_summary(experiment_id),
        price_source=PRICE_SOURCE,
        scope="filesystem_only" if file_tools_only else "real_broker_commands",
    )
    write_report(report, output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="volcengine-deepseek")
    parser.add_argument("--budget-cny", default="10")
    parser.add_argument("--experiment-budget-cny", default="4")
    parser.add_argument("--temp-root", type=Path)
    parser.add_argument("--ledger", type=Path, default=Path("benchmarks/output/volcengine-live-budget.db"))
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-model-turns", type=int, default=6)
    parser.add_argument("--trial-timeout-seconds", type=int, default=120)
    parser.add_argument("--max-parallel-tools", type=int, default=1)
    parser.add_argument("--with-commands", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/output/volcengine-pilot.json"))
    args = parser.parse_args()
    if args.temp_root is not None:
        args.temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(args.temp_root.resolve())
    if any(
        path.exists()
        for path in (args.output, args.output.with_suffix(".md"), args.output.with_suffix(".trials.jsonl"))
    ):
        parser.error("Output already exists; select a new output path and retain the shared budget ledger.")
    store = local_settings()
    config = store.model_config(args.provider)
    if config.model != MODEL or config.base_url.rstrip("/") != BASE_URL or config.protocol != "chat_completions":
        raise ValueError("This price policy is pinned to the documented Volcengine DeepSeek revision and endpoint.")
    budget = SpendingBudget(
        args.ledger,
        limit_cny=args.budget_cny,
        model=MODEL,
        endpoint=BASE_URL + "/chat/completions",
        input_cny_per_million="2",
        output_cny_per_million="8",
    )
    if args.smoke:
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "provider": args.provider,
            "model": MODEL,
            "mode": "real_provider_smoke",
            "provenance": source_provenance(),
            "result": smoke(config, budget),
            "spending": budget.summary(),
            "price_source": PRICE_SOURCE,
        }
        if report["result"]["passed"]:
            profile = next(p for p in store.provider_configs() if p["provider_name"] == args.provider)
            store.activate_provider_config(profile["id"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        task_ids = (
            tuple(args.task_id)
            if args.task_id
            else PILOT_TASKS
            if args.suite == DEFAULT_SUITE
            else tuple(task["id"] for task in load_suite(args.suite)[0]["tasks"])
        )
        report = pilot(
            config,
            budget,
            args.output,
            suite_path=args.suite,
            task_ids=task_ids,
            repeat=args.repeat,
            max_model_turns=args.max_model_turns,
            file_tools_only=not args.with_commands,
            trial_timeout_seconds=args.trial_timeout_seconds,
            max_parallel_tools=args.max_parallel_tools,
            experiment_budget_cny=args.experiment_budget_cny,
        )
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "result": report.get("result", {}).get("passed"),
                "summary": {
                    a: {"trials": s["trials"], "passed": s["passed"], "outcomes": s["outcomes"]}
                    for a, s in report.get("summary", {}).items()
                },
                "spending": budget.summary(),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    if args.smoke and not report["result"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
