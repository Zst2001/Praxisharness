"""Read version-scoped acceptance evidence without replaying any tools."""

from __future__ import annotations

from backend.domain.acceptance import AcceptanceProgress, AcceptanceReport


def acceptance_record(store, session_id: str, thread_id: str, turn_id: str, data_idx: int) -> dict:
    progress = None
    usage = {}
    runtime = store.load_runtime(session_id, thread_id=thread_id)
    run = runtime.current_run if runtime is not None else None
    if run is not None and (run.turn_id, run.data_idx) == (turn_id, data_idx):
        progress = run.acceptance
        usage = {
            "model_turns": run.model_turns,
            "model_calls": run.model_calls,
            "tool_calls": len(run.actions),
            "tokens": runtime.usage or runtime.turn_usage,
            "max_model_turns": min(
                runtime.runner_settings.max_model_turns or 128, run.acceptance.contract.max_model_turns
            )
            if run.acceptance
            else runtime.runner_settings.max_model_turns,
            "max_tool_calls": min(
                runtime.runner_settings.max_tool_calls or 1000, run.acceptance.contract.max_tool_calls
            )
            if run.acceptance
            else runtime.runner_settings.max_tool_calls,
        }
    trace = store.load_thread_trace(session_id, thread_id, turn_id, data_idx)
    reconstructed = None
    previous_run_id = None
    finished_usage = None
    repaired_checks: set[tuple[str, int]] = set()
    last_check = None
    repairing_attempt = checking_attempt = None
    contract_digest = None
    for entry in trace.items if trace is not None else ():
        item = entry.item
        if entry.role != "runtime" or item.get("type") != "runtime_event":
            continue
        event, data = item.get("event"), item.get("data", {})
        if event == "acceptance_started" and data.get("contract"):
            initial = AcceptanceProgress.model_validate({"contract": data["contract"]})
            digest = initial.contract.fingerprint()
            continues = (
                data.get("workflow_trigger") == "resume"
                and previous_run_id
                and previous_run_id == data.get("source_run_id")
                and reconstructed is not None
                and reconstructed.contract == initial.contract
                and contract_digest == digest == data.get("sha256")
            )
            if not continues:
                reconstructed = initial
                repaired_checks.clear()
                last_check = repairing_attempt = checking_attempt = None
            contract_digest = digest if data.get("sha256") == digest else None
            previous_run_id = data.get("run_id")
            finished_usage = None
        elif event == "acceptance_checking":
            checking_attempt = data.get("attempt")
        elif event == "acceptance_checked" and reconstructed is not None:
            report = AcceptanceReport.model_validate(data["report"])
            if (
                last_check is not None
                and last_check.attempt == repairing_attempt
                and report.attempt == last_check.attempt + 1
                and checking_attempt == report.attempt
            ):
                passed_ids = {check.id for check in report.checks if check.status == "passed"}
                repaired_checks.update(
                    (check.id, last_check.attempt)
                    for check in last_check.checks
                    if check.status == "failed" and check.id in passed_ids
                )
            last_check = report if checking_attempt == report.attempt else None
            checking_attempt = repairing_attempt = None
            reconstructed = reconstructed.model_copy(
                update={"status": report.status, "reports": (*reconstructed.reports, report)}
            )
        elif event == "acceptance_repair" and reconstructed is not None:
            repairing_attempt = data.get("repair") if last_check and last_check.status == "failed" else None
            reconstructed = reconstructed.model_copy(update={"status": "repairing", "repairs": int(data["repair"])})
        elif event == "error" and reconstructed is not None and data.get("acceptance_status"):
            reconstructed = reconstructed.model_copy(update={"status": data["acceptance_status"]})
        elif event == "run_finished" and data.get("acceptance"):
            previous_run_id = data.get("run_id") or previous_run_id
            reconstructed = AcceptanceProgress.model_validate(data["acceptance"])
            finished_usage = data.get("acceptance_usage", {})
        elif event == "run_resumed":
            # The paused attempt's totals expire as soon as the next attempt starts.
            finished_usage = None
        elif reconstructed is not None and (
            event == "cancelled" or event == "run_finished" and data.get("status") == "cancelled"
        ):
            reconstructed = reconstructed.model_copy(update={"status": "cancelled"})
    finished = finished_usage is not None and (progress is None or not previous_run_id or previous_run_id == run.run_id)
    if finished:
        progress, usage = reconstructed, finished_usage
    elif progress is None:
        progress = reconstructed
    verified_repairs = []
    if repaired_checks and finished and progress and progress.contract.fingerprint() == contract_digest:
        valid_ids = {rule.id for rule in progress.contract.checks}
        for failed, following in zip(progress.reports, progress.reports[1:]):
            if following.attempt != failed.attempt + 1:
                continue
            passed = {check.id for check in following.checks if check.status == "passed"} & valid_ids
            verified_repairs.extend(
                {"check_id": check.id, "failed_attempt": failed.attempt, "contract_sha256": contract_digest}
                for check in failed.checks
                if check.status == "failed" and check.id in passed and (check.id, failed.attempt) in repaired_checks
            )
    return {
        "schema_version": 1,
        "session_id": session_id,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "data_idx": data_idx,
        "verified_repairs": verified_repairs,
        "acceptance": progress.model_dump(mode="json") if progress else None,
        "usage": usage,
    }
