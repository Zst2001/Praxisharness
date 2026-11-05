"""Conservative per-experiment CNY reservations, applied to every HTTP attempt.

Only text/function-tool Chat Completions are permitted. Reservations use the
model's documented maximum input window, not a local token estimate. Unknown
outcomes retain their entire reservation. This bounds this experiment at the
configured rates; it is not a provider account quota or an audited invoice.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from backend.providers import LLMClient, ModelConfig
from backend.providers.errors import ModelTransportError
from backend.providers.token_usage import normalize_provider_usage
from backend.providers.transport import JsonHttpTransport


class EvaluationBudgetExceeded(ModelTransportError):
    def __init__(self, *, scope: str = "global") -> None:
        super().__init__(f"Evaluation {scope} budget exhausted; no HTTP request was sent.", retryable=False)


def _micro(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


class SpendingBudget:
    def __init__(
        self,
        path: Path,
        *,
        limit_cny: str,
        model: str,
        endpoint: str,
        input_cny_per_million: str,
        output_cny_per_million: str,
        input_window: int = 1_048_576,
        max_output_tokens: int = 2048,
    ) -> None:
        self.path = Path(path)
        self.limit = Decimal(limit_cny)
        self.input_rate = Decimal(input_cny_per_million)
        self.output_rate = Decimal(output_cny_per_million)
        if not all(v.is_finite() and v > 0 for v in (self.limit, self.input_rate, self.output_rate)):
            raise ValueError("Budget and prices must be finite and positive.")
        if not 1 <= max_output_tokens < input_window <= 1_048_576:
            raise ValueError("Invalid documented input/output limits.")
        self.limit_micro = int(self.limit * 1_000_000)
        self.model, self.endpoint = model, endpoint
        self.input_window, self.max_output_tokens = input_window, max_output_tokens
        policy = json.dumps(
            {
                "limit_micro": self.limit_micro,
                "model": model,
                "endpoint": endpoint,
                "input_rate": str(self.input_rate),
                "output_rate": str(self.output_rate),
                "input_window": input_window,
                "max_output_tokens": max_output_tokens,
            },
            sort_keys=True,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as c:
            c.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, reserved INTEGER NOT NULL, charged INTEGER, input_tokens INTEGER, output_tokens INTEGER)"
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS attempt_details (
                    attempt_id INTEGER PRIMARY KEY, experiment_id TEXT, task_id TEXT, arm TEXT,
                    trial_attempt INTEGER, exchange_id TEXT, http_attempt INTEGER,
                    created_at TEXT NOT NULL, ended_at TEXT, status_code INTEGER,
                    request_id TEXT, error_type TEXT)"""
            )
            c.execute("INSERT OR IGNORE INTO policy VALUES (1,?)", (policy,))
            if c.execute("SELECT value FROM policy WHERE id=1").fetchone()[0] != policy:
                raise ValueError("Existing budget policy differs; do not reset this experiment's ledger.")
            c.execute(
                "CREATE TABLE IF NOT EXISTS experiment_limits (experiment_id TEXT PRIMARY KEY, limit_micro INTEGER NOT NULL)"
            )

    def configure_experiment(self, experiment_id: str, limit_cny: str) -> None:
        value = Decimal(limit_cny)
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", experiment_id)
            or not value.is_finite()
            or not 0 < value <= self.limit
        ):
            raise ValueError("Invalid experiment spending cap")
        micro = value * 1_000_000
        if micro != micro.to_integral_value():
            raise ValueError("Experiment cap supports at most six decimal places")
        with self._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT limit_micro FROM experiment_limits WHERE experiment_id=?", (experiment_id,)
            ).fetchone()
            if (
                existing is None
                and c.execute(
                    "SELECT 1 FROM attempt_details WHERE experiment_id=? LIMIT 1", (experiment_id,)
                ).fetchone()
                is not None
            ):
                raise ValueError("Configure the experiment cap before its first HTTP attempt")
            c.execute("INSERT OR IGNORE INTO experiment_limits VALUES (?,?)", (experiment_id, int(micro)))
            if c.execute(
                "SELECT limit_micro FROM experiment_limits WHERE experiment_id=?", (experiment_id,)
            ).fetchone()[0] != int(micro):
                raise ValueError("Experiment cap differs; do not reset an existing cap")

    def experiment_summary(self, experiment_id: str) -> dict:
        with self._connection() as c:
            limit = c.execute(
                "SELECT limit_micro FROM experiment_limits WHERE experiment_id=?", (experiment_id,)
            ).fetchone()
            count, used = c.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(a.charged,a.reserved)),0) FROM attempts a JOIN attempt_details d ON a.id=d.attempt_id WHERE d.experiment_id=?",
                (experiment_id,),
            ).fetchone()
        return {
            "experiment_id": experiment_id,
            "limit_cny": str(Decimal(limit[0]) / 1_000_000) if limit else None,
            "http_attempts": count,
            "estimated_and_reserved_cny": str(Decimal(used) / 1_000_000),
            "remaining_cny": str(Decimal(limit[0] - used) / 1_000_000) if limit else None,
        }

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=10)
        try:
            c.execute("PRAGMA synchronous=FULL")
            with c:
                yield c
        finally:
            c.close()

    def reserve(self, endpoint: str, payload: dict, *, context: dict | None = None) -> int:
        context = dict(context or {})
        allowed = {"experiment_id", "task_id", "arm", "trial_attempt", "exchange_id", "http_attempt"}
        if set(context) - allowed:
            raise ValueError("Unexpected budget correlation fields.")
        for key, value in context.items():
            if key in {"trial_attempt", "http_attempt"}:
                if type(value) is not int or value < 1:
                    raise ValueError("Invalid attempt correlation.")
            elif not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise ValueError("Invalid budget correlation identifier.")
        if endpoint != self.endpoint or payload.get("model") != self.model:
            raise ValueError("Budgeted requests must use the fixed endpoint and model.")
        output = payload.get("max_tokens")
        if type(output) is not int or not 1 <= output <= self.max_output_tokens:
            raise ValueError("Budgeted requests require a bounded max_tokens.")
        if any(key in payload for key in ("store", "cache", "caching", "max_completion_tokens")):
            raise ValueError("Extra billing or output-limit options are not permitted.")
        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) > 256:
            raise ValueError("Budgeted requests require bounded text messages.")
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else False
            if content is not None and not isinstance(content, str):
                raise ValueError("This budget policy covers text-only messages.")
        tools = payload.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(t, dict) or t.get("type") != "function" for t in tools):
            raise ValueError("Only local function tools are covered by this budget.")
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 262_144:
            raise ValueError("Budgeted payload exceeds the local 256 KiB ceiling.")
        # Rates are CNY / million tokens; the same numeric value is micro-CNY / token.
        reserved = _micro(self.input_window * self.input_rate + output * self.output_rate)
        with self._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            used = c.execute("SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM attempts").fetchone()[0]
            if used + reserved > self.limit_micro:
                raise EvaluationBudgetExceeded()
            experiment = context.get("experiment_id")
            scoped_limit = c.execute(
                "SELECT limit_micro FROM experiment_limits WHERE experiment_id=?", (experiment,)
            ).fetchone()
            if scoped_limit is not None:
                scoped_used = c.execute(
                    "SELECT COALESCE(SUM(COALESCE(a.charged,a.reserved)),0) FROM attempts a JOIN attempt_details d ON a.id=d.attempt_id WHERE d.experiment_id=?",
                    (experiment,),
                ).fetchone()[0]
                if scoped_used + reserved > scoped_limit[0]:
                    raise EvaluationBudgetExceeded(scope="experiment")
            attempt_id = c.execute("INSERT INTO attempts(reserved) VALUES (?)", (reserved,)).lastrowid
            c.execute(
                "INSERT INTO attempt_details(attempt_id,experiment_id,task_id,arm,trial_attempt,exchange_id,http_attempt,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    *(
                        context.get(k)
                        for k in ("experiment_id", "task_id", "arm", "trial_attempt", "exchange_id", "http_attempt")
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
            return attempt_id

    def record_response(self, attempt_id: int, *, status_code=None, request_id=None, error_type=None) -> None:
        # Correlation only: never persist headers, request/response bodies or error text.
        request_id = (
            request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id) else None
        )
        error_type = (
            error_type if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z_]{1,80}", error_type) else None
        )
        with self._connection() as c:
            c.execute(
                "UPDATE attempt_details SET ended_at=?,status_code=?,request_id=?,error_type=? WHERE attempt_id=?",
                (
                    datetime.now(UTC).isoformat(),
                    status_code if type(status_code) is int else None,
                    request_id,
                    error_type,
                    attempt_id,
                ),
            )

    def trial_spending(self, trial: dict) -> dict:
        with self._connection() as c:
            c.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in c.execute(
                    """SELECT d.*,a.reserved,a.charged FROM attempt_details d
                JOIN attempts a ON a.id=d.attempt_id
                WHERE experiment_id=? AND task_id=? AND arm=? AND trial_attempt=? ORDER BY attempt_id""",
                    tuple(trial[k] for k in ("experiment_id", "task_id", "arm", "trial_attempt")),
                )
            ]
        return {
            "currency": "CNY",
            "attempt_amount_unit": "micro_CNY",
            "http_attempts": len(rows),
            "estimated_cny": str(Decimal(sum(row["charged"] or 0 for row in rows)) / 1_000_000),
            "reserved_unknown_cny": str(
                Decimal(sum(row["reserved"] for row in rows if row["charged"] is None)) / 1_000_000
            ),
            "account_bill_verified": False,
            "attempts": rows,
        }

    def settle(self, attempt_id: int, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        usage = normalize_provider_usage(usage)
        input_count, output_count = usage.get("input_tokens"), usage.get("output_tokens")
        if any(type(v) is not int or v < 0 for v in (input_count, output_count)):
            return
        cached, reasoning = usage.get("cached_tokens") or 0, usage.get("reasoning_tokens") or 0
        if any(type(v) is not int or v < 0 for v in (cached, reasoning)):
            return
        # Charge cache at the full input rate and include reasoning conservatively.
        input_count += cached
        output_count += reasoning
        estimated = _micro(input_count * self.input_rate + output_count * self.output_rate)
        with self._connection() as c:
            row = c.execute("SELECT reserved, charged FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or row[1] is not None:
                raise ValueError("Unknown or already settled reservation.")
            c.execute(
                "UPDATE attempts SET charged=?,input_tokens=?,output_tokens=? WHERE id=?",
                (min(estimated, row[0]), input_count, output_count, attempt_id),
            )

    def summary(self) -> dict:
        with self._connection() as c:
            count, used, pending, known, unknown = c.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(charged,reserved)),0),SUM(charged IS NULL),COALESCE(SUM(charged),0),COALESCE(SUM(CASE WHEN charged IS NULL THEN reserved ELSE 0 END),0) FROM attempts"
            ).fetchone()
        return {
            "currency": "CNY",
            "limit": str(self.limit),
            "http_attempts": count,
            "estimated_and_reserved_cny": str(Decimal(used) / 1_000_000),
            "estimated_cny": str(Decimal(known) / 1_000_000),
            "reserved_unknown_cny": str(Decimal(unknown) / 1_000_000),
            "remaining_cny": str(Decimal(self.limit_micro - used) / 1_000_000),
            "unsettled_attempts": pending or 0,
            "account_bill_verified": False,
            "input_cny_per_million": str(self.input_rate),
            "output_cny_per_million": str(self.output_rate),
        }


class SpendingTransport(JsonHttpTransport):
    def __init__(self, budget: SpendingBudget) -> None:
        super().__init__()
        self.reservations: list[int] = []
        self.context: dict = {}
        self.http_attempt = 0
        original_send = self.session.send

        def send(request, **kwargs):
            if request.method != "POST":
                raise ValueError("Only completion POST requests are allowed.")
            payload = json.loads(request.body)
            self.http_attempt += 1
            attempt_id = budget.reserve(
                request.url, payload, context={**self.context, "http_attempt": self.http_attempt}
            )
            self.reservations.append(attempt_id)
            try:
                response = original_send(request, **kwargs)
            except Exception as exc:
                budget.record_response(attempt_id, error_type=type(exc).__name__)
                raise
            budget.record_response(
                attempt_id,
                status_code=response.status_code,
                request_id=response.headers.get("x-request-id") or response.headers.get("x-tt-logid"),
            )
            return response

        self.session.send = send


class SpendingClient(LLMClient):
    def __init__(self, config: ModelConfig, budget: SpendingBudget, *, trial: dict | None = None) -> None:
        self.budget = budget
        self.trial = dict(trial or {})
        if "attempt" in self.trial:
            self.trial["trial_attempt"] = self.trial.pop("attempt")
        self.spending_transport = SpendingTransport(budget)
        super().__init__(config, transport=self.spending_transport)

    def run(self, runtime):
        if runtime.exchange.exchange_id is None:
            runtime.exchange.exchange_id = runtime.next_exchange_id()
        self.spending_transport.context = {**self.trial, "exchange_id": runtime.exchange.exchange_id}
        self.spending_transport.http_attempt = 0
        before = len(self.spending_transport.reservations)
        result = super().run(runtime)
        if len(self.spending_transport.reservations) > before:
            self.budget.settle(self.spending_transport.reservations[-1], result.usage)
        return result

    def trial_spending(self) -> dict:
        return self.budget.trial_spending(self.trial)
