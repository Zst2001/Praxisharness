from types import SimpleNamespace

from benchmarks.telemetry import TrialTelemetry


def event(kind, exchange="first", attempt=1, **data):
    return SimpleNamespace(
        kind=kind, message="failure", data={"exchange_id": exchange, "transport": {"attempt": attempt}, **data}
    )


def test_usage_sums_every_attempt_and_retains_unknown_retry():
    telemetry = TrialTelemetry()
    telemetry.observe(event("model_request"))
    telemetry.observe(event("model_retry", error_type="TimeoutError"))
    telemetry.observe(event("model_request", attempt=2))
    telemetry.observe(event("model_response", attempt=2, usage={"prompt_tokens": 100, "completion_tokens": 10}))
    telemetry.observe(event("model_request", exchange="second"))
    telemetry.observe(event("model_response", exchange="second", usage={"input_tokens": 200, "output_tokens": 20}))
    usage, accounting = telemetry.usage()
    assert usage["input_tokens"] == 300 and usage["output_tokens"] == 30 and usage["total_tokens"] == 330
    assert usage["cached_tokens"] is None
    assert accounting["provider_attempts"] == 3 and accounting["unknown_attempts"] == 1
    assert accounting["complete"] is False


def test_error_projection_never_copies_wire_payload_or_credentials():
    telemetry = TrialTelemetry("plain-provider-secret")
    telemetry.observe(event("model_request", wire_request={"messages": ["private prompt"]}))
    telemetry.observe(
        event(
            "model_error",
            error_type="ModelTransportError",
            operation="decision",
            error="plain-provider-secret Authorization: Bearer another-secret",
            diagnostics={"status_code": 503, "wire_response": "private reply"},
        )
    )
    error = telemetry.errors[0]
    assert error["status_code"] == 503 and error["stage"] == "decision"
    assert "plain-provider-secret" not in str(error) and "another-secret" not in str(error)
    assert "private" not in str(telemetry.requests) + str(error)


def test_missing_usage_is_unknown_instead_of_zero():
    telemetry = TrialTelemetry()
    telemetry.observe(event("model_request"))
    telemetry.observe(event("model_response", usage=None))
    usage, accounting = telemetry.usage()
    assert all(value is None for value in usage.values())
    assert accounting["unknown_attempts"] == 1 and accounting["complete"] is False


def test_challenge_reference_solutions_pass_public_and_independent_tests():
    from pathlib import Path

    from benchmarks.harness import evaluate
    from tests.testing_sandbox import DirectTestSandboxLauncher

    report = evaluate(Path("benchmarks/harness_challenge_v1.json"), sandbox_launcher=DirectTestSandboxLauncher())
    assert report["mode"] == "scripted_demo"
    assert report["summary"]["repair"]["passed"] == 4
    assert report["summary"]["repair"]["repaired_successes"] == 4
    assert report["summary"]["baseline"]["passed"] == report["summary"]["verify"]["passed"] == 0
