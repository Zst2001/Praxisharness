"""Deterministic, opt-in completion at a budget boundary; never call a model."""

from __future__ import annotations

from backend.domain import AssistantMessage

from ..core.context import AgentRuntime
from .acceptance import _publish, verify_acceptance
from .lifecycle.cancellation import cancel_if_requested
from .lifecycle.outcomes import complete_run, fail_run
from .workflows.budgets import budget_snapshot


def finalize_budget_handoff(runtime: AgentRuntime, reason: str) -> None:
    progress = runtime.run.acceptance
    assert progress is not None and progress.contract.reserved_tool_calls > 0
    if cancel_if_requested(runtime):
        return

    def stop(detail: str, status: str = "budget_exhausted") -> None:
        current = runtime.run.acceptance
        runtime.run.acceptance = current.model_copy(update={"status": status})
        fail_run(
            runtime, detail, stop_reason="budget_exhausted", acceptance_status=status, budget=budget_snapshot(runtime)
        )

    if progress.handoff_reason is not None:
        stop("Budget handoff already started; verification commands cannot be automatically replayed.")
        return
    if runtime.operation_interrupted():
        stop("Budget handoff interrupted before verification.", "blocked")
        return
    runtime.run.acceptance = progress.model_copy(update={"handoff_reason": reason})
    _publish(runtime, "acceptance_handoff", "已达到预算边界，使用剩余额度验收。", budget_snapshot(runtime))
    scope = runtime.services.job_scope
    if scope is not None and scope.registry.active_count(scope=scope):
        stop("Budget handoff blocked by unfinished tools.", "blocked")
        return
    store = runtime.services.todo_store
    if (
        store is not None
        and runtime.run.turn_id
        and store.snapshot(runtime.state.session_id, runtime.run.turn_id).unfinished
    ):
        stop("Budget handoff cannot complete unfinished Todo items.", "blocked")
        return
    report = verify_acceptance(runtime)
    if cancel_if_requested(runtime):
        return
    if report.status != "passed" or runtime.operation_interrupted():
        stop(
            "Budget handoff did not pass all fixed acceptance checks; no further model call was made.",
            report.status if report.status != "passed" else "blocked",
        )
        return
    complete_run(runtime, AssistantMessage(content="执行达到预算边界；固定验收条件全部通过。"))
