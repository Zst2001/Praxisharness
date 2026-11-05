from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.routes import turns as turn_routes
from backend.api.state import WebAppState
from backend.domain import ToolMessage
from backend.domain.acceptance import AcceptanceContract
from backend.runtime import AgentRunner, ConversationService
from backend.runtime.core.context import RuntimeState
from backend.runtime.recovery_workspace import MAX_FILE_BYTES, workspace_fingerprints
from backend.storage.sqlite import SQLiteSessionStore
from backend.tools import ToolRegistry
from benchmarks.harness import DEFAULT_SUITE, evaluate, load_suite
from benchmarks.provenance import source_provenance
from benchmarks.recovery import SCENARIOS, FinalPlanner, run_probe
from tests.local_store import session_store


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_real_process_kill_preserves_recovery_evidence(tmp_path: Path, scenario: str):
    report = run_probe(tmp_path / scenario, scenario)
    assert report["status"] == "passed", report


def test_resume_cannot_reset_exhausted_model_budget_or_tool_limit(tmp_path):
    store = session_store(tmp_path / "data")
    with AgentRunner(
        FinalPlanner(),
        ToolRegistry(),
        checkpoints=store,
        workspace_root=str(tmp_path),
        max_model_turns=2,
        max_tool_calls=3,
    ) as initial:
        conversation = ConversationService(initial, store)
        conversation.run_task("pause", mode="agent", suspend_requested=lambda: True)
        state = store.load_runtime(conversation.active_session.session_id)
        state.current_run.model_turns = 2
        store.save_runtime(state)
        sid = state.session_id

    class MustNotCall(FinalPlanner):
        def decide(self, _runtime):
            pytest.fail("An exhausted resumed workflow must not call the model.")

    with AgentRunner(
        MustNotCall(),
        ToolRegistry(),
        checkpoints=store,
        workspace_root=str(tmp_path),
        max_model_turns=100,
        max_tool_calls=100,
    ) as reopened:
        conversation = ConversationService(reopened, store, session_id=sid)
        preview = conversation.prepare_resume(sid)
        assert preview.budgets["max_model_turns"] == 2
        result = conversation.resume_session(sid, resume_confirmed=True, recovery_token=preview.recovery_token)
        assert result.status == "failed" and result.stop_reason == "budget_exhausted"
        assert result.model_turns == 2
        assert conversation.runtime.state.runner_settings.max_tool_calls == 3


def test_file_observation_is_bounded_scoped_and_never_exports_contents(tmp_path):
    with AgentRunner(FinalPlanner(), ToolRegistry(), workspace_root=str(tmp_path)) as runner:
        runtime = runner.new_runtime(
            task="inspect",
            acceptance=AcceptanceContract.model_validate(
                {"checks": [{"id": "file", "kind": "file_exists", "path": "big.txt"}]}
            ),
        )
        (tmp_path / "big.txt").write_bytes(b"secret content" + b"x" * MAX_FILE_BYTES)
        runtime.run.actions.append(
            ToolMessage(name="read_file", call_id="outside", arguments={"path": "../outside.txt"})
        )
        snapshot = workspace_fingerprints(runtime.state)
        assert snapshot["workspace:big.txt"] == "unchecked:too_large"
        assert snapshot["<unavailable-path>"] == "unchecked:unavailable"
        assert "secret content" not in json.dumps(snapshot)
        assert "outside.txt" not in json.dumps(snapshot)
        assert RuntimeState.from_dict(runtime.state.to_dict()).current_run.acceptance == runtime.run.acceptance


def test_recovery_http_rejects_stale_preview_before_starting_execution(tmp_path, monkeypatch):
    web = WebAppState(tmp_path / "data")
    with TestClient(create_app(web)) as client:
        sid = client.post("/api/sidebar-threads", json={}).json()["session_id"]
        workspace = web.paths.session_workspace(sid)
        store = SQLiteSessionStore(web.paths)
        with AgentRunner(FinalPlanner(), ToolRegistry(), checkpoints=store, workspace_root=str(workspace)) as runner:
            conversation = ConversationService(runner, store, session_id=sid)

            def started():
                conversation.runtime.services.acceptance_contract = AcceptanceContract.model_validate(
                    {"checks": [{"id": "file", "kind": "file_exists", "path": "result.txt"}]}
                )

            paused = conversation.run_task("pause", mode="agent", suspend_requested=lambda: True, on_started=started)
        path = f"/api/turns/{paused.turn_id}"
        preview = client.get(path + "/recovery", params={"session_id": sid})
        assert preview.status_code == 200
        assert preview.headers["cache-control"] == "no-store"
        calls = []
        monkeypatch.setattr(turn_routes, "_stream_turn", lambda *_args, **_kwargs: calls.append(True))
        assert client.post(path + "/resume", json={}).status_code == 422
        (workspace / "result.txt").write_text("external edit", encoding="utf-8")
        assert (
            client.post(path + "/resume", json={"recovery_token": preview.json()["recovery_token"]}).status_code == 409
        )
        assert calls == []
        fresh = client.get(path + "/recovery", params={"session_id": sid}).json()
        assert fresh["changed_files"] == ["workspace:result.txt"]
        assert client.post(path + "/resume", json={"recovery_token": fresh["recovery_token"]}).status_code == 202
        assert calls == [True]
        other = client.post("/api/sidebar-threads", json={}).json()["session_id"]
        assert client.get(path + "/recovery", params={"session_id": other}).status_code == 404


def test_source_identifier_includes_uncommitted_edits_and_lockfiles(tmp_path):
    (tmp_path / "backend/src").mkdir(parents=True)
    source = tmp_path / "backend/src/demo.py"
    source.write_text("value = 1", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("lock", encoding="utf-8")
    before = source_provenance(tmp_path)
    assert before == source_provenance(tmp_path)
    source.write_text("value = 2", encoding="utf-8")
    after = source_provenance(tmp_path)
    assert before["source_sha256"] != after["source_sha256"]
    assert before["dependencies"] == after["dependencies"]
    assert after["git_head"] is None


def test_pilot_selection_is_explicit_and_shared_by_all_arms():
    suite, digest = load_suite(DEFAULT_SUITE)
    selected = suite["tasks"][0]["id"]
    report = evaluate(task_ids=(selected,))
    assert report["suite_sha256"] == digest
    assert report["selected_task_ids"] == [selected]
    assert len(report["results"]) == 3
    assert {row["experiment_id"] for row in report["results"]} == {report["experiment_id"]}
    assert report["provenance"]["source_sha256"]
    with pytest.raises(ValueError):
        evaluate(task_ids=("missing-task",))
