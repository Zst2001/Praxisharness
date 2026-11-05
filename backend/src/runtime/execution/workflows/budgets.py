"""Model and tool budget enforcement."""

from __future__ import annotations

from backend.domain import AssistantMessage, PlanningError, safe_error_message
from backend.planning import PlannerCapabilities

from ...core.context import AgentRuntime
from ..lifecycle.outcomes import fail_run
from .common import (
    _fail_pending_tools,
    _finish_assistant,
    _publish_repairs,
    _start_assistant,
    _truncate,
)


def _budget_fallback(runtime: AgentRuntime, reason: str) -> str:
    recent = ", ".join(f"{tool.name} ({tool.status})" for tool in runtime.run.actions[-3:])
    details = f" Recent tool calls: {recent}." if recent else ""
    return (
        f"Execution budget exhausted: {reason} "
        f"The run completed {len(runtime.run.actions)} tool calls before stopping.{details} "
        "Continue the task in a new turn if more work is required."
    )


def _fail_for_budget(runtime: AgentRuntime, limit_type: str, reason: str, *, handoff_reason: str | None = None) -> None:
    progress = runtime.run.acceptance
    if progress is not None and progress.contract.reserved_tool_calls:
        from ..budget_finish import finalize_budget_handoff

        finalize_budget_handoff(
            runtime, handoff_reason or ("model_turn_limit" if limit_type == "model_turns" else "agent_tool_limit")
        )
        return
    if runtime.run.acceptance is not None:
        runtime.run.acceptance = runtime.run.acceptance.model_copy(update={"status": "budget_exhausted"})
        fail_run(
            runtime,
            f"Task acceptance budget exhausted: {reason}",
            stop_reason="budget_exhausted",
            limit_type=limit_type,
            acceptance_status="budget_exhausted",
        )
        return
    settings = runtime.state.runner_settings
    limit = settings.max_tool_calls
    capabilities = PlannerCapabilities.from_planner(runtime.services.planner)
    answer = ""
    source = "fallback"
    finalization_error: str | None = None
    if capabilities.run_finalizer is not None:
        try:
            message = capabilities.run_finalizer.finalize(runtime, reason)
            if message.tool_messages or not (message.content and message.content.strip()):
                raise PlanningError("Budget finalizer returned invalid output.")
            runtime.state.messages.append(message)
            runtime.run.history = runtime.state.messages
            runtime.save()
            answer = message.content.strip()
            source = "planner"
        except Exception as exc:
            finalization_error = _truncate(safe_error_message(exc))
            _publish_repairs(runtime, capabilities)
    if not answer:
        answer = _budget_fallback(runtime, reason)
    data: dict[str, object] = {
        "limit_type": limit_type,
        "limit": limit,
        "model_turns": runtime.run.model_turns,
        "tool_calls": len(runtime.run.actions),
        "finalizer": source,
    }
    if finalization_error is not None:
        data["finalization_error"] = finalization_error
    fail_run(runtime, answer, stop_reason="budget_exhausted", **data)


def _claim_model_turn(runtime: AgentRuntime, operation: str) -> bool:
    progress = runtime.run.acceptance
    limit = runtime.state.runner_settings.max_model_turns
    if progress is not None:
        limit = (
            min(limit, progress.contract.max_model_turns) if limit is not None else progress.contract.max_model_turns
        )
    if limit is not None and runtime.run.model_turns >= limit:
        if progress is not None and progress.contract.reserved_tool_calls:
            _fail_for_budget(runtime, "model_turns", "Model-turn budget exhausted.")
            return False
        if progress is not None:
            runtime.run.acceptance = progress.model_copy(update={"status": "budget_exhausted"})
        fail_run(
            runtime,
            "Model-turn budget exhausted.",
            stop_reason="budget_exhausted",
            limit_type="model_turns",
            acceptance_status="budget_exhausted",
        )
        return False
    runtime.run.model_turns += 1
    del operation
    runtime.save()
    return True


def _tool_batch_fits(runtime: AgentRuntime, response: AssistantMessage) -> bool:
    remaining = budget_snapshot(runtime)["agent_remaining"]
    return remaining is None or len(response.tool_messages) <= remaining


def _reject_over_budget_tools(runtime: AgentRuntime, response: AssistantMessage) -> None:
    limit = effective_tool_limit(runtime)
    remaining = budget_snapshot(runtime)["agent_remaining"] or 0
    reason = (
        f"the model requested {len(response.tool_messages)} tool calls, but only "
        f"{remaining} of {limit} tool calls remained."
    )
    _start_assistant(runtime, response)
    _fail_pending_tools(runtime, response, f"Not executed because {reason}")
    _finish_assistant(runtime)
    _fail_for_budget(runtime, "tool_calls", reason, handoff_reason="tool_batch_limit")


def _ensure_tool_budget(runtime: AgentRuntime) -> bool:
    if runtime.run.acceptance is not None and runtime.run.acceptance.handoff_reason is not None:
        _fail_for_budget(runtime, "tool_calls", "Budget handoff was already started.")
        return False
    limit = effective_tool_limit(runtime)
    remaining = budget_snapshot(runtime)["agent_remaining"]
    if remaining is None or remaining > 0:
        return True
    _fail_for_budget(
        runtime,
        "tool_calls",
        f"the maximum of {limit} tool calls was reached before a final answer.",
    )
    return False


def effective_tool_limit(runtime: AgentRuntime) -> int | None:
    limit = runtime.state.runner_settings.max_tool_calls
    progress = runtime.run.acceptance
    if progress is not None:
        return min(limit, progress.contract.max_tool_calls) if limit is not None else progress.contract.max_tool_calls
    return limit


def budget_snapshot(runtime: AgentRuntime) -> dict:
    """Derive phase usage from durable call IDs; rejected batches cost no calls."""
    progress = runtime.run.acceptance
    limit = effective_tool_limit(runtime)
    reserved = min(progress.contract.reserved_tool_calls, limit) if progress and limit is not None else 0
    ids = set(progress.tool_call_ids) if progress else set()
    acceptance_used = sum(action.call_id in ids for action in runtime.run.actions)
    used = len(runtime.run.actions)
    agent_used = used - acceptance_used
    remaining = max(0, limit - used) if limit is not None else None
    agent_limit = limit - reserved if limit is not None else None
    model_limit = runtime.state.runner_settings.max_model_turns
    if progress is not None:
        model_limit = (
            min(model_limit, progress.contract.max_model_turns)
            if model_limit is not None
            else progress.contract.max_model_turns
        )
    return {
        "total_limit": limit,
        "total_used": used,
        "total_remaining": remaining,
        "agent_limit": agent_limit,
        "agent_used": agent_used,
        "agent_remaining": min(max(0, agent_limit - agent_used), remaining) if agent_limit is not None else None,
        "acceptance_used": acceptance_used,
        "reserved_tool_calls": reserved,
        "model_remaining": max(0, model_limit - runtime.run.model_turns) if model_limit is not None else None,
        "handoff_reason": progress.handoff_reason if progress else None,
    }
