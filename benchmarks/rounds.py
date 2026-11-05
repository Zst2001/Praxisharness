"""Per-acceptance snapshots; independent grades are computed only after exit."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from time import perf_counter

from backend.domain import safe_error_message

from .evidence import TrialEvidence, regrade, safe_value, tool_trace


class RoundEvidence:
    def __init__(self, evidence: TrialEvidence, runtime, task: dict, telemetry, client=None) -> None:
        self.evidence, self.runtime, self.task = evidence, runtime, task
        self.telemetry, self.client = telemetry, client
        self.snapshots: list[TrialEvidence] = []
        self.rows: list[dict] = []
        self.first_check_time: float | None = None

    def counters(self) -> dict:
        usage, accounting = self.telemetry.usage()
        spending = self.client.trial_spending() if hasattr(self.client, "trial_spending") else {}
        return {
            "model_turns": self.runtime.run.model_turns,
            "model_calls": self.runtime.run.model_calls,
            "tool_calls": len(self.runtime.run.actions),
            "provider_attempts": accounting["provider_attempts"],
            "unknown_usage_attempts": accounting["unknown_attempts"],
            "total_tokens": usage.get("total_tokens") if accounting["provider_attempts"] else 0,
            "estimated_cny": spending.get("estimated_cny"),
            "reserved_unknown_cny": spending.get("reserved_unknown_cny"),
        }

    def _update(self) -> None:
        for row, snapshot in zip(self.rows, self.snapshots, strict=True):
            ref = snapshot.save_manifest()
            row.update(
                manifest=f"rounds/{row['round']:02d}/manifest.json",
                manifest_sha256=ref["manifest_sha256"],
                replayable=ref["replayable"],
            )
        # Full independent grades live in child attachments and the report;
        # keep the parent index small even with six rounds.
        self.evidence.manifest["rounds"] = safe_value(
            [{k: v for k, v in row.items() if k != "independent"} for row in self.rows], self.evidence.secret
        )
        self.evidence.save_manifest()

    def observe(self, event) -> None:
        if event.kind == "acceptance_checking":
            number = event.data["attempt"]
            if number != len(self.snapshots) + 1:
                raise ValueError("Nonsequential acceptance evidence")
            if self.first_check_time is None:
                self.first_check_time = perf_counter()
            snapshot = TrialEvidence(
                self.evidence.root,
                Path(self.runtime.state.workspace_root),
                self.evidence.manifest["trial"],
                suite_sha256=self.evidence.manifest["suite_sha256"],
                provenance=self.evidence.manifest["provenance"],
                secret=self.evidence.secret,
                round_index=number,
            )
            counters = self.counters()
            snapshot.manifest.update(stage="pre_acceptance", round_index=number, counters_before=counters)
            snapshot.capture(
                Path(self.runtime.state.workspace_root),
                self.task,
                tool_trace(self.runtime.run.actions, self.telemetry),
                previous=self.snapshots[-1] if self.snapshots else None,
            )
            self.snapshots.append(snapshot)
            self.rows.append({"round": number, "counters_before": counters, "feedback_repair": None})
            self._update()
        elif event.kind == "acceptance_checked" and self.snapshots:
            report = event.data["report"]
            if report["attempt"] != len(self.snapshots):
                raise ValueError("Acceptance report does not match snapshot")
            self.snapshots[-1].attach("acceptance.json", report)
            self.rows[-1].update(public_status=report["status"], counters_after=self.counters())
            self._update()
        elif event.kind == "acceptance_repair" and self.snapshots:
            # This is the actual public report and repair allocation. It is not
            # an independent rubric or a reconstructed model wire transcript.
            progress = self.runtime.run.acceptance
            self.snapshots[-1].attach(
                "feedback.json",
                {
                    "source": "public_acceptance",
                    "repair_event": event.data,
                    "contract": progress.contract.model_dump(mode="json"),
                    "repairs_used": progress.repairs,
                    "last_report": progress.reports[-1].model_dump(mode="json"),
                },
            )
            self.rows[-1]["feedback_repair"] = event.data["repair"]
            self._update()

    def grade_after_exit(self, suite_path: Path, launcher) -> dict:
        final_counters = self.counters()
        elapsed = (
            round((perf_counter() - self.first_check_time) * 1000, 3) if self.first_check_time is not None else None
        )
        transitions, previous = [], None
        for row, snapshot in zip(self.rows, self.snapshots, strict=True):
            try:
                grading = regrade(snapshot.path / "manifest.json", suite_path, sandbox_launcher=launcher)["grading"]
            except Exception as exc:
                grading = {"status": "blocked", "case_results": [], "error": safe_error_message(exc)}
            grading = safe_value(grading, self.evidence.secret)
            snapshot.attach("independent.json", grading)
            row.update(independent_status=grading["status"], independent=grading)
            current = {(c["check_id"], c["case_id"]): c["status"] for c in grading["case_results"]}
            if previous is not None:
                solved, introduced = [], []
                for key in previous.keys() & current.keys():
                    if previous[key] == "failed" and current[key] == "passed":
                        solved.append(list(key))
                    elif previous[key] == "passed" and current[key] == "failed":
                        introduced.append(list(key))
                transitions.append(
                    {
                        "from_round": row["round"] - 1,
                        "to_round": row["round"],
                        "resolved_cases": sorted(solved),
                        "new_failed_cases": sorted(introduced),
                    }
                )
            previous = current
        self._update()
        first = self.rows[0]["counters_before"] if self.rows else None
        extra = None
        if first is not None:
            extra = {
                k: final_counters[k] - first[k]
                for k in ("model_turns", "model_calls", "tool_calls", "provider_attempts")
            }
            extra["total_tokens"] = (
                final_counters["total_tokens"] - first["total_tokens"]
                if not first["unknown_usage_attempts"]
                and not final_counters["unknown_usage_attempts"]
                and first["total_tokens"] is not None
                and final_counters["total_tokens"] is not None
                else None
            )
            for key in ("estimated_cny", "reserved_unknown_cny"):
                extra[key] = (
                    str(Decimal(final_counters[key]) - Decimal(first[key]))
                    if first[key] is not None and final_counters[key] is not None
                    else None
                )
        return {
            "scope": "after_first_submission_including_public_checks",
            "first_public_status": self.rows[0].get("public_status") if self.rows else None,
            "first_independent_status": self.rows[0].get("independent_status") if self.rows else None,
            "feedback_repairs_allocated": sum(r["feedback_repair"] is not None for r in self.rows),
            "elapsed_after_first_submission_ms": elapsed,
            "extra_after_first_submission": extra,
            "transitions": transitions,
            "rounds": self.rows,
        }

    @staticmethod
    def compare_final(analysis: dict, grading: dict) -> None:
        """Include changed candidates that never reached another public check."""
        if not analysis["rounds"]:
            return
        last = analysis["rounds"][-1]
        before = {(c["check_id"], c["case_id"]): c["status"] for c in last["independent"]["case_results"]}
        after = {(c["check_id"], c["case_id"]): c["status"] for c in grading.get("case_results", [])}
        solved = sorted(
            [list(k) for k in before.keys() & after.keys() if before[k] == "failed" and after[k] == "passed"]
        )
        introduced = sorted(
            [list(k) for k in before.keys() & after.keys() if before[k] == "passed" and after[k] == "failed"]
        )
        if solved or introduced:
            analysis["transitions"].append(
                {
                    "from_round": last["round"],
                    "to_stage": "final_candidate",
                    "resolved_cases": solved,
                    "new_failed_cases": introduced,
                }
            )
