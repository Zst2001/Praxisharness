"""Descriptive action counts only; repeated commands are never skipped."""

from __future__ import annotations

import json


def action_efficiency(trace: list[dict], checks: list[dict], *, total_calls: int | None = None) -> dict:
    seen_reads, seen_commands, passed_public = set(), set(), set()
    public = {check["cmd"] for check in checks if check["kind"] == "command"}
    counts = {
        key: 0
        for key in (
            "repeated_read_arguments",
            "repeated_command_arguments",
            "public_self_tests",
            "public_retests_after_success_without_recorded_mutation",
            "editing_conflicts",
            "multiline_command_requests",
        )
    }
    for item in trace:
        if item["phase"] != "agent":
            continue
        args = item.get("arguments") or {}
        key = json.dumps(args, sort_keys=True)
        if item["tool"] == "read_file":
            counts["repeated_read_arguments"] += key in seen_reads
            seen_reads.add(key)
        if item["tool"] == "file_operation":
            if item["status"] == "succeeded":
                passed_public.clear()
            counts["editing_conflicts"] += "expected_content must contain" in (item.get("result_preview") or "")
        if item["tool"] != "run_command":
            continue
        cmd = args.get("cmd", "")
        counts["multiline_command_requests"] += "\n" in cmd or "\r" in cmd
        counts["repeated_command_arguments"] += key in seen_commands
        seen_commands.add(key)
        if cmd in public:
            counts["public_self_tests"] += 1
            counts["public_retests_after_success_without_recorded_mutation"] += cmd in passed_public
            if item.get("exit_code") == 0:
                passed_public.add(cmd)
    return {
        **counts,
        "trace_truncated": len(trace) < total_calls if total_calls is not None else None,
        "automatic_command_reuse": False,
    }
