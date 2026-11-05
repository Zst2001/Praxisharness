from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from benchmarks.spending import EvaluationBudgetExceeded, SpendingBudget, SpendingTransport

ENDPOINT = "http://127.0.0.1:1/chat/completions"


def budget(path, **kwargs):
    return SpendingBudget(
        path,
        limit_cny="10",
        model="test",
        endpoint=ENDPOINT,
        input_cny_per_million="2",
        output_cny_per_million="8",
        **kwargs,
    )


def payload(**kwargs):
    return {"model": "test", "max_tokens": 2048, "messages": [{"role": "user", "content": "hello"}], **kwargs}


def test_persistent_unknown_attempts_stop_before_another_request(tmp_path):
    path = tmp_path / "budget.db"
    first = budget(path)
    for _ in range(4):
        first.reserve(ENDPOINT, payload())
    reopened = budget(path)
    with pytest.raises(EvaluationBudgetExceeded):
        reopened.reserve(ENDPOINT, payload())
    assert reopened.summary()["http_attempts"] == 4
    assert reopened.summary()["unsettled_attempts"] == 4


def test_reservations_are_atomic_across_clients(tmp_path):
    path = tmp_path / "budget.db"
    shared = budget(path)

    def reserve(_):
        try:
            return budget(path).reserve(ENDPOINT, payload())
        except EvaluationBudgetExceeded:
            return None

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(reserve, range(12)))
    assert sum(r is not None for r in results) == 4
    assert shared.summary()["http_attempts"] == 4


def test_experiment_cap_is_atomic_and_unknown_usage_keeps_reservation(tmp_path):
    shared = budget(tmp_path / "scoped.db")
    shared.configure_experiment("study", "4")
    with ThreadPoolExecutor(8) as pool:

        def reserve(_):
            try:
                return shared.reserve(ENDPOINT, payload(), context={"experiment_id": "study"})
            except EvaluationBudgetExceeded:
                return None

        results = list(pool.map(reserve, range(8)))
    assert sum(r is not None for r in results) == 1
    shared.settle(next(r for r in results if r is not None), None)
    with pytest.raises(EvaluationBudgetExceeded, match="experiment"):
        shared.reserve(ENDPOINT, payload(), context={"experiment_id": "study"})
    assert shared.experiment_summary("study")["estimated_and_reserved_cny"] == "2.113536"


def test_experiment_cannot_reset_cap_and_does_not_bypass_global_limit(tmp_path):
    shared = budget(tmp_path / "scoped.db")
    shared.configure_experiment("study", "4")
    with pytest.raises(ValueError, match="differs"):
        shared.configure_experiment("study", "5")
    for _ in range(4):
        shared.reserve(ENDPOINT, payload())
    with pytest.raises(EvaluationBudgetExceeded, match="global"):
        shared.reserve(ENDPOINT, payload(), context={"experiment_id": "study"})
    assert shared.experiment_summary("study")["http_attempts"] == 0


def test_known_usage_releases_only_its_experiment_reservation(tmp_path):
    shared = budget(tmp_path / "scoped.db")
    shared.configure_experiment("study", "4")
    first = shared.reserve(ENDPOINT, payload(), context={"experiment_id": "study"})
    shared.settle(first, {"input_tokens": 100, "output_tokens": 50})
    shared.reserve(ENDPOINT, payload(), context={"experiment_id": "study"})
    assert shared.experiment_summary("study")["http_attempts"] == 2
    assert shared.summary()["http_attempts"] == 2


def test_experiment_cap_cannot_relabel_an_existing_unscoped_attempt(tmp_path):
    shared = budget(tmp_path / "scoped.db")
    shared.reserve(ENDPOINT, payload(), context={"experiment_id": "existing"})
    with pytest.raises(ValueError, match="before its first"):
        shared.configure_experiment("existing", "4")
    assert shared.summary()["http_attempts"] == 1


def test_usage_reconciliation_is_conservative_and_cannot_double_refund(tmp_path):
    shared = budget(tmp_path / "budget.db")
    attempt = shared.reserve(ENDPOINT, payload())
    shared.settle(attempt, {"input_tokens": 100, "cached_tokens": 20, "output_tokens": 50, "reasoning_tokens": 10})
    assert shared.summary()["estimated_and_reserved_cny"] == "0.00072"
    with pytest.raises(ValueError):
        shared.settle(attempt, {"input_tokens": 0, "output_tokens": 0})
    second = shared.reserve(ENDPOINT, payload())
    shared.settle(second, {})
    assert shared.summary()["unsettled_attempts"] == 1


def test_chat_completions_usage_releases_only_the_known_reservation(tmp_path):
    shared = budget(tmp_path / "budget.db")
    failed = shared.reserve(ENDPOINT, payload())
    shared.settle(failed, None)
    succeeded = shared.reserve(ENDPOINT, payload())
    shared.settle(
        succeeded,
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 10},
        },
    )
    assert shared.summary()["estimated_and_reserved_cny"] == "2.114256"
    assert shared.summary()["unsettled_attempts"] == 1


def test_reopening_cannot_change_the_existing_experiment_budget(tmp_path):
    path = tmp_path / "budget.db"
    budget(path).reserve(ENDPOINT, payload())
    with pytest.raises(ValueError, match="policy differs"):
        SpendingBudget(
            path,
            limit_cny="11",
            model="test",
            endpoint=ENDPOINT,
            input_cny_per_million="2",
            output_cny_per_million="8",
        )
    assert budget(path).summary()["http_attempts"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"model": "other"},
        {"max_tokens": None},
        {"max_tokens": 2049},
        {"store": True},
        {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/img"}}]}
            ]
        },
        {"tools": [{"type": "web_search"}]},
    ],
)
def test_uncapped_or_extra_billing_requests_are_rejected(tmp_path, change):
    shared = budget(tmp_path / "budget.db")
    with pytest.raises(ValueError):
        shared.reserve(ENDPOINT, payload(**change))
    assert shared.summary()["http_attempts"] == 0


def test_budget_exhaustion_prevents_transport_network_io(tmp_path, monkeypatch):
    shared = budget(tmp_path / "budget.db")
    for _ in range(4):
        shared.reserve(ENDPOINT, payload())
    calls = []
    monkeypatch.setattr(requests.Session, "send", lambda *_a, **_k: calls.append(True))
    transport = SpendingTransport(shared)
    request = requests.Request("POST", ENDPOINT, json=payload()).prepare()
    try:
        with pytest.raises(EvaluationBudgetExceeded):
            transport.session.send(request)
    finally:
        transport.session.close()
    assert calls == []


def test_ledger_does_not_store_credentials_or_prompt_contents(tmp_path):
    shared = budget(tmp_path / "budget.db")
    shared.reserve(ENDPOINT, payload(messages=[{"role": "user", "content": "private fixture text"}]))
    assert "private fixture text" not in (tmp_path / "budget.db").read_bytes().decode("utf-8", errors="ignore")
    assert "secret" not in json.dumps(shared.summary())


def test_live_pilot_keeps_completed_trial_when_evaluation_is_interrupted(tmp_path, monkeypatch):
    from backend.providers import ModelConfig
    from benchmarks import live

    def interrupted(*_args, **kwargs):
        kwargs["on_trial"]({"experiment_id": kwargs["experiment_id"], "task_id": "first", "passed": True})
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(live, "evaluate", interrupted)
    output = tmp_path / "pilot.json"
    with pytest.raises(RuntimeError, match="simulated interruption"):
        live.pilot(
            ModelConfig.from_mapping({"model": "test", "base_url": ENDPOINT}), budget(tmp_path / "budget.db"), output
        )
    records = [json.loads(line) for line in output.with_suffix(".trials.jsonl").read_text().splitlines()]
    assert records[0]["type"] == "experiment"
    assert records[1]["type"] == "trial" and records[1]["passed"] is True
    assert records[0]["experiment_id"] == records[1]["experiment_id"]
    assert not output.exists()


@pytest.mark.parametrize("file_only", [True, False])
def test_executable_pilot_refuses_before_paid_calls_without_real_admission(tmp_path, monkeypatch, file_only):
    from pathlib import Path
    from types import SimpleNamespace

    from backend.providers import ModelConfig
    from backend.sandbox import WindowsBrokerClient
    from benchmarks import live

    monkeypatch.setattr(
        WindowsBrokerClient, "from_system", lambda: SimpleNamespace(status=lambda: SimpleNamespace(healthy=False))
    )
    monkeypatch.setattr(live, "evaluate", lambda *_a, **_k: pytest.fail("Must refuse before paid evaluation"))
    shared = budget(tmp_path / "budget.db")
    with pytest.raises(ValueError, match="Broker"):
        live.pilot(
            ModelConfig("fixture", ENDPOINT, "test"),
            shared,
            tmp_path / "pilot.json",
            suite_path=Path("benchmarks/harness_challenge_v1.json"),
            task_ids=("merge-layers",),
            file_tools_only=file_only,
        )
    assert shared.summary()["http_attempts"] == 0
