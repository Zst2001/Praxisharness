"""Project experience acceptance across persisted Turns, server restarts and loopback model HTTP."""

from __future__ import annotations

import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock

import httpx
import pytest
import uvicorn

from backend.api.app import create_app
from backend.api.session_store import session_store
from backend.api.state import WebAppState
from backend.providers import ModelConfig
from backend.runtime import AgentRunner, ConversationService
from backend.runtime.core.contracts import InterruptDecision
from backend.storage.message_queue import MemoryMessageQueue
from backend.storage.sqlite import SQLiteSessionStore
from backend.tools import ToolRegistry
from tests.test_acceptance import RepairPlanner, contract
from tests.test_benchmark_service import until
from tests.test_harness_evaluation import LocalModel

SITUATION = "配置开关需要输出 JSON 布尔值时"
RECOMMENDATION = "在结果中写入布尔值 true，再用 JSON 内容相等条件复验。"
ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def local_backend(web: WebAppState, *, browser_env=None):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        if browser_env is not None:
            browser_env.setenv("PRAXIS_ALLOWED_ORIGINS", f"http://127.0.0.1:{port}")
        # Match the production launcher so an open browser SSE stream cannot stall shutdown.
        server = uvicorn.Server(uvicorn.Config(create_app(web), log_level="error", timeout_graceful_shutdown=5))
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            until(lambda: server.started)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20) as client:
                yield client
        finally:
            server.should_exit = True
            worker.join(25)
            assert not worker.is_alive()


def resumed_repair(web: WebAppState, workspace: Path, project_id: str) -> dict:
    """Real missing JSON check, persisted pause, file write and second check."""
    store = session_store(web)
    pause = Event()
    with AgentRunner(
        RepairPlanner(), ToolRegistry(workspace), workspace_root=str(workspace), checkpoints=store
    ) as runner:
        service = ConversationService(runner, store)
        session = service.new_session("配置输出修复")
        session_id = session.session_id
        store.create_sidebar_thread(session_id=session_id, thread_id=session_id, title=session.title)
        web.projects.create_session(project_id, session_id)
        service.runtime.state.permission_mode = "workspace_write"
        service.runtime.services.acceptance_contract = contract()

        def after_event(event):
            if getattr(event, "kind", None) == "acceptance_repair":
                pause.set()

        run = service.run_task(
            "创建包含布尔开关的 result.json", mode="agent", on_event=after_event, suspend_requested=pause.is_set
        )
        assert run.stop_reason == "user_paused"
        resumed = ConversationService(runner, store, session_id=session_id).resume_session(
            resume_confirmed=True, interrupt=lambda _: InterruptDecision("continue")
        )
        assert resumed.provenance.source_run_id == run.run_id
        assert [r.status for r in resumed.acceptance.reports] == ["failed", "passed"]
        return {
            "session_id": session_id,
            "thread_id": resumed.thread_id,
            "turn_id": resumed.turn_id,
            "data_idx": resumed.data_idx,
        }


def received_chat(client, web, project_id, requests_seen):
    response = client.post(f"/api/projects/{project_id}/sessions", json={"title": "新会话召回"})
    response.raise_for_status()
    session_id = response.json()["session"]["session_id"]
    count = len(requests_seen)
    send = client.post(
        "/api/turns",
        json={
            "session_id": session_id,
            "thread_id": session_id,
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "配置开关怎样输出布尔值？"}],
            },
        },
    )
    send.raise_for_status()
    turn_id = send.json()["id"]
    store = session_store(web)
    until(lambda: store.get_node(session_id, turn_id).status != "running", timeout=20)
    assert store.get_node(session_id, turn_id).status == "success"
    assert len(requests_seen) == count + 1
    return "\n".join(str(message["content"]) for message in requests_seen[-1]["messages"])


def test_project_experience_save_restart_and_model_requests(tmp_path, local_sandbox_runtime, monkeypatch):
    class Recorder(LocalModel):
        requests_seen = []

    root = tmp_path / "state"
    web = WebAppState(root, message_queue=MemoryMessageQueue())
    background_model = Mock(side_effect=AssertionError("Unexpected background model in local-only mode"))
    web.memory_automation._model_factory = background_model
    folder_a, folder_b = tmp_path / "A", tmp_path / "B"
    folder_a.mkdir()
    folder_b.mkdir()
    project_a = web.projects.create(folder_a).project_id
    project_b = web.projects.create(folder_b).project_id
    coords = resumed_repair(web, folder_a, project_a)
    report_url = f"/api/turns/{coords['turn_id']}/acceptance"
    params = {key: coords[key] for key in ("session_id", "thread_id", "data_idx")}
    save_url = "/api/memory/project-experiences"
    with local_backend(web) as client:
        record = client.get(report_url, params=params).json()
        assert record["verified_repairs"] == [
            {"check_id": "output", "failed_attempt": 1, "contract_sha256": contract().fingerprint()}
        ]
        source = {**coords, **record["verified_repairs"][0]}
        body = {**source, "situation": SITUATION, "recommendation": RECOMMENDATION}
        assert client.post(save_url, json=body).status_code == 409
        settings = client.put("/api/settings/memory", json={"enabled": True}).json()
        assert settings["enabled"] and not settings["model_processing_enabled"]
        projectless = client.post("/api/sidebar-threads", json={}).json()
        assert (
            client.post(
                save_url, json={**body, "session_id": projectless["session_id"], "thread_id": projectless["thread_id"]}
            ).status_code
            == 409
        )
        for forged, status in [
            ({"project_id": project_b}, 422),
            ({"thread_id": "another-thread"}, 404),
            ({"data_idx": 1}, 404),
            ({"failed_attempt": 2}, 409),
            ({"contract_sha256": "0" * 64}, 409),
            ({"check_id": "unverified"}, 409),
            ({"recommendation": "password=fake-fixture"}, 422),
        ]:
            assert client.post(save_url, json={**body, **forged}).status_code == status
        store = session_store(web)
        trace = store.load_thread_trace(**coords)
        # Even a real final passing report cannot vouch for a missing repair boundary.
        incomplete = replace(
            trace, items=[entry for entry in trace.items if entry.item.get("event") != "acceptance_repair"]
        )
        with monkeypatch.context() as patch:
            patch.setattr(SQLiteSessionStore, "load_thread_trace", lambda *_: incomplete)
            missing = client.get(report_url, params=params).json()
            assert missing["acceptance"]["status"] == "passed" and not missing["verified_repairs"]
            assert client.post(save_url, json=body).status_code == 409
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: client.post(save_url, json=body), range(6)))
        assert all(r.status_code == 200 for r in responses), responses[0].text
        items = [r.json()["memory"] for r in responses]
        assert all(item == items[0] for item in items)
        assert (items[0]["scope"], items[0]["kind"], items[0]["project_id"]) == ("project", "procedural", project_a)
        memory_url = "/api/memory/items/" + items[0]["memory_id"]
        assert len(client.get("/api/memory/items").json()["items"]) == 1
        assert client.post(save_url, json={**body, "recommendation": "其他改法"}).status_code == 409

    restarted = WebAppState(root, message_queue=MemoryMessageQueue())
    restarted.memory_automation._model_factory = background_model
    with ThreadingHTTPServer(("127.0.0.1", 0), Recorder) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        restarted.model_config = lambda provider_name=None: ModelConfig(
            model="loopback", base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="local-test"
        )
        try:
            with local_backend(restarted) as client:
                (evidence,) = client.get(memory_url + "/evidence").json()["evidence"]
                assert evidence["repair_source"] == source and evidence["source_kind"] == "harness_acceptance"
                assert "第 1 轮失败 → 第 2 轮通过" in evidence["excerpt"] and "result.json" not in evidence["excerpt"]
                assert client.get(report_url, params=params).json()["verified_repairs"] == record["verified_repairs"]
                assert client.post("/api/memory/consolidate", json={}).is_error
                assert client.post("/api/memory/extract", json={"thread_id": source["thread_id"]}).is_error
                restarted.memory_automation.scan_once()
                assert not Recorder.requests_seen
                context = received_chat(client, restarted, project_a, Recorder.requests_seen)
                assert RECOMMENDATION in context and source["turn_id"] in context and evidence["excerpt"] in context
                assert RECOMMENDATION not in received_chat(client, restarted, project_b, Recorder.requests_seen)
                client.patch(memory_url, json={"enabled": False}).raise_for_status()
                assert RECOMMENDATION not in received_chat(client, restarted, project_a, Recorder.requests_seen)
                client.patch(memory_url, json={"enabled": True}).raise_for_status()
                client.delete(memory_url).raise_for_status()
                assert client.post(save_url, json=body).status_code == 409
                assert RECOMMENDATION not in received_chat(client, restarted, project_a, Recorder.requests_seen)
                client.post(memory_url + "/restore", json={}).raise_for_status()
                assert RECOMMENDATION in received_chat(client, restarted, project_a, Recorder.requests_seen)
                assert len(Recorder.requests_seen) == 5 and restarted.memory_store.list_jobs() == []
                background_model.assert_not_called()
        finally:
            server.shutdown()
            worker.join(5)


@pytest.mark.skipif(
    not (ROOT / "frontend/dist/index.html").is_file() or not (ROOT / "frontend/node_modules/playwright").is_dir(),
    reason="Browser acceptance requires the production build and local Playwright.",
)
def test_save_and_inspect_repair_evidence_in_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_FRONTEND_DIST", str(ROOT / "frontend/dist"))
    web = WebAppState(tmp_path / "data", message_queue=MemoryMessageQueue())
    workspace = tmp_path / "project"
    workspace.mkdir()
    pid = web.projects.create(workspace, name="记忆验证项目").project_id
    coords = resumed_repair(web, workspace, pid)
    web.settings.update_memory_config({"enabled": True})
    with local_backend(web, browser_env=monkeypatch) as client:
        run = subprocess.run(
            [
                shutil.which("node") or "node",
                str(ROOT / "tests/support/project_experience_browser_check.cjs"),
                str(client.base_url),
                coords["turn_id"],
                SITUATION,
                RECOMMENDATION,
            ],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=90,
        )
        assert run.returncode == 0, run.stderr
        (item,) = web.memory_store.list_items(project_id=pid)
        assert item.title == SITUATION and item.content == RECOMMENDATION
        assert web.memory_store.project_experience_source(item.memory_id).turn_id == coords["turn_id"]
