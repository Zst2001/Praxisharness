"""Trial-level provider usage and bounded diagnostics, without wire payloads."""

from __future__ import annotations

from collections.abc import Mapping

from backend.domain import redact_sensitive_text
from backend.providers.token_usage import USAGE_FIELDS, normalize_provider_usage


class TrialTelemetry:
    def __init__(self, secret: str = "") -> None:
        self.secret = secret
        self.kinds: set[str] = set()
        self.requests: dict[tuple[str, int], dict] = {}
        self.errors: list[dict] = []
        self.input_estimates: dict[str, int] = {}
        self.acceptance_calls: set[str] = set()
        self.tool_durations: dict[str, float] = {}
        self.resource_waits: list[dict] = []

    def safe(self, value: object) -> str:
        text = str(value or "")
        if self.secret:
            text = text.replace(self.secret, "[REDACTED]")
        return redact_sensitive_text(text)[:500]

    def observe(self, event) -> None:
        self.kinds.add(event.kind)
        if event.kind == "tool_resource_wait":
            data = event.data if isinstance(event.data, Mapping) else {}
            usage = data.get("resource_usage") or {}
            if isinstance(usage, Mapping) and len(self.resource_waits) < 128:
                self.resource_waits.append(
                    {
                        "call_id": self.safe(data.get("call_id")),
                        **{
                            key: usage.get(key)
                            for key in ("granted", "wait_reason", "queue_position", "queued", "elapsed_ms")
                        },
                    }
                )
            return
        if event.kind == "assistant_message":
            data = event.data if isinstance(event.data, Mapping) else {}
            message = data.get("message")
            if data.get("source") == "acceptance" and isinstance(message, Mapping):
                for tool in message.get("tool_messages", [])[:128]:
                    if isinstance(tool, Mapping) and isinstance(tool.get("call_id"), str):
                        self.acceptance_calls.add(tool["call_id"])
            return
        if event.kind in {"tool_result", "tool_failed"}:
            data = event.data if isinstance(event.data, Mapping) else {}
            duration = data.get("duration_ms")
            call_id = data.get("call_id")
            if isinstance(call_id, str) and type(duration) in (int, float) and duration >= 0:
                self.tool_durations[call_id] = duration
            return
        if event.kind == "context_usage":
            data = event.data if isinstance(event.data, Mapping) else {}
            value = data.get("estimated_input_tokens")
            if type(value) is int and value >= 0:
                self.input_estimates[self.safe(data.get("exchange_id"))] = value
            return
        if event.kind not in {"model_request", "model_response", "model_error", "model_retry"}:
            return
        data = event.data if isinstance(event.data, Mapping) else {}
        transport = data.get("transport")
        transport = transport if isinstance(transport, Mapping) else {}
        attempt = transport.get("attempt", data.get("attempt", 1))
        attempt = attempt if type(attempt) is int and attempt > 0 else 1
        exchange = self.safe(data.get("exchange_id"))
        key = (exchange, attempt)
        if event.kind == "model_request":
            self.requests.setdefault(
                key,
                {
                    "exchange_id": exchange,
                    "attempt": attempt,
                    "estimated_usage": {
                        "input_tokens": self.input_estimates.get(exchange),
                        "output_tokens": None,
                        "total_tokens": None,
                    },
                },
            )
        elif event.kind == "model_response":
            row = self.requests.setdefault(key, {"exchange_id": exchange, "attempt": attempt})
            row["usage"] = normalize_provider_usage(data.get("usage"))
            accounting = data.get("usage_accounting")
            if isinstance(accounting, Mapping):
                inputs, outputs = accounting.get("estimated_input_tokens"), accounting.get("estimated_output_tokens")
                if all(type(v) is int and v >= 0 for v in (inputs, outputs)):
                    row["estimated_usage"] = {
                        "input_tokens": inputs,
                        "output_tokens": outputs,
                        "total_tokens": inputs + outputs,
                    }
        else:
            if data.get("error_type") == "EvaluationBudgetExceeded":
                self.kinds.add("evaluation_budget_exhausted")
            diagnostics = data.get("diagnostics")
            diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
            status = diagnostics.get("status_code", transport.get("http_status", data.get("status_code")))
            self.errors.append(
                {
                    "exchange_id": exchange,
                    "attempt": attempt,
                    "stage": self.safe(data.get("operation") or "model_request"),
                    "type": self.safe(data.get("error_type") or "ModelError"),
                    "message": self.safe(data.get("error") or event.message),
                    "status_code": status if type(status) is int else None,
                    "retry": event.kind == "model_retry",
                }
            )

    def usage(self) -> tuple[dict, dict]:
        rows = list(self.requests.values())
        usage = {}
        for field in USAGE_FIELDS:
            values = [r.get("usage", {}).get(field) for r in rows]
            known = [v for v in values if type(v) is int and v >= 0]
            usage[field] = sum(known) if known else None
        reported = sum(r.get("usage", {}).get("total_tokens") is not None for r in rows)
        estimated = {}
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            values = [
                r.get("estimated_usage", {}).get(field) for r in rows if r.get("usage", {}).get("total_tokens") is None
            ]
            known = [v for v in values if type(v) is int and v >= 0]
            estimated[field] = sum(known) if known else None
        return usage, {
            "scope": "all_provider_attempts_in_trial",
            "provider_attempts": len(rows),
            "reported_attempts": reported,
            "unknown_attempts": len(rows) - reported,
            "complete": bool(rows) and reported == len(rows),
            "source": "provider",
            "estimated_usage_for_unknown_attempts": estimated,
        }
