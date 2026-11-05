"""Local memory scheduling boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest

from backend.configuration import ClientPaths
from backend.domain.memory import MemoryCandidateStatus, MemoryJobKind, MemoryJobStatus
from backend.domain.runtime_state import RuntimeState
from backend.jobs import JobRegistry
from backend.runtime.memory import (
    MemoryAutomationService,
    MemoryAutomationSettings,
    MemoryConversation,
    MemoryConversationSource,
    MemorySessionSnapshot,
    MemorySourceMessage,
)
from backend.storage.memory import MemoryStore
from backend.storage.projects import ProjectStore
from tests.local_store import session_store
from tests.test_memory_phase2 import _AddingModel, _episodic, _record

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class _Settings:
    def __init__(self) -> None:
        self.enabled = True
        self.model_processing = True

    def memory_config(self):
        return {"enabled": self.enabled, "model_processing_enabled": self.model_processing}


class _Source:
    def __init__(self, conversations: list[MemoryConversation]) -> None:
        self.conversations = {value.thread_id: value for value in conversations}
        self.messages = {
            value.thread_id: (
                MemorySourceMessage(
                    1, f"{value.thread_id}_v1_user", "user", "Remember this durable preference " + "x" * 80
                ),
                MemorySourceMessage(2, f"{value.thread_id}_v1_assistant", "assistant", "Understood."),
                MemorySourceMessage(
                    3, f"{value.thread_id}_v2_user", "user", "Keep using this preference in future reports " + "y" * 80
                ),
            )
            for value in conversations
        }

    def list(self):
        return list(self.conversations.values())

    def get(self, thread_id: str):
        return self.conversations.get(thread_id)

    def snapshot(self, conversation: MemoryConversation):
        return MemorySessionSnapshot(
            conversation.thread_id,
            self.messages[conversation.thread_id],
            "running" if conversation.running else "completed",
            conversation.project_id,
        )


class _State:
    def __init__(self, root: Path, source: _Source) -> None:
        self.settings = _Settings()
        self.memory_store = MemoryStore(ClientPaths(root))
        self.memory_source = source
        self.job_registry = JobRegistry()
        self.system_job_scope = self.job_registry.root_scope()


class _Model:
    def __init__(self, callback=lambda: None) -> None:
        self.callback = callback

    def extract_episodic(self, request):
        self.callback()
        user_id = [message.message_id for message in request.messages if message.role == "user"][-1]
        return {
            "candidates": [
                {
                    "title": "Stable preference",
                    "content": "The user prefers concise technical reports.",
                    "summary": "Concise reports",
                    "confidence": 0.9,
                    "tags": ["preference"],
                    "evidence_message_ids": [user_id],
                    "rediscoverable_from_source": False,
                }
            ]
        }

    def consolidate_memories(self, request):
        return {
            "added": [],
            "retained": [],
            "removed": [],
            "rejected_candidate_ids": [value.candidate_id for value in request.candidates],
        }


def _conversation(thread_id: str, *, age: timedelta, archived=False, deleted=False, running=False):
    return MemoryConversation(
        session_id=f"session_{thread_id}",
        thread_id=thread_id,
        project_id=None,
        updated_at=NOW - age,
        running=running,
        archived=archived,
        deleted=deleted,
    )


def _service(tmp_path: Path, source: _Source, model: _Model | None = None):
    state = _State(tmp_path / "data", source)
    service = MemoryAutomationService(
        state,
        lambda: model or _Model(),
        settings=MemoryAutomationSettings(phase1_concurrency=0),
        clock=lambda: NOW,
    )
    return state, service


@pytest.mark.parametrize(("kind", "tool"), [("tool_call", "mcp_docs_search"), ("tool_result", "web_search")])
def test_external_tool_in_ancestor_blocks_automatic_and_manual_extraction_on_user_fork(tmp_path, kind, tool):
    sessions = session_store(tmp_path / "sessions")
    source = MemoryConversationSource(sessions, ProjectStore(tmp_path / "projects.db"))
    session = sessions.create_session("External context")
    sid = session.session_id
    sessions.create_sidebar_thread(session_id=sid, thread_id=sid, title=session.title)
    root = sessions.ensure_root_node(sid)
    item = {"type": kind, "call_id": "fixture", "status": "success", "name" if kind == "tool_call" else "tool": tool}
    external = RuntimeState.create(
        session_id=sid,
        thread_id=sid,
        parent=root,
        user_content="Look up documentation",
        provider_name="local",
        data=[
            [
                {"role": "user", "content": [{"type": "text", "text": "Look up documentation", "status": "success"}]},
                {"role": "assistant", "content": [item]},
            ]
        ],
    )
    external.status = "success"
    sessions.create_finalized_nodes([external])
    next_turn = RuntimeState.create(
        session_id=sid, thread_id=sid, parent=external, user_content="What did we learn?", provider_name="local"
    )
    next_turn.status = "success"
    sessions.create_finalized_nodes([next_turn])
    fork = sessions.fork_turn_node(next_turn.id, session_id=sid)
    sessions.create_sidebar_thread(session_id=sid, thread_id=fork.thread_id, title="User fork")
    state, service = _service(tmp_path, source)
    service._clock = lambda: datetime.now(UTC) + timedelta(days=1)
    try:
        assert {conversation.thread_id for conversation in source.list()} == {sid, fork.thread_id}
        for thread in (sid, fork.thread_id):
            assert source.snapshot(source.get(thread)).used_external_context
            with pytest.raises(ValueError, match="external context"):
                service.enqueue_extract(thread)
        service.scan_once()
        assert not state.memory_store.list_jobs()
    finally:
        state.job_registry.close_all(timeout=2)


def test_memory_failure_persists_provider_details(tmp_path: Path, caplog) -> None:
    from backend.providers import ModelTransportError

    source = _Source([_conversation("thread_a", age=timedelta(hours=7))])
    state, service = _service(tmp_path, source)
    try:
        queued = service.enqueue_extract("thread_a")
        claimed = state.memory_store.claim_job(service._worker_id, now="2030-01-01T00:00:00+00:00")
        error = ModelTransportError(
            "Bad Request api_key=private-key",
            retryable=False,
            status_code=400,
            diagnostics={
                "response_detail": "unsupported response_format; token=private-token",
                "request_id": "request-123",
                "operation": "summarize",
                "model": "test-model",
            },
        )
        with caplog.at_level("WARNING"):
            service._retry(claimed, error)
        persisted = state.memory_store.get_job(queued.job_id)
        assert "unsupported response_format" in persisted.last_error
        assert "request_id=request-123" in persisted.last_error
        assert "status_code=400" in persisted.last_error
        assert "operation=summarize" in persisted.last_error
        assert "private-key" not in persisted.last_error and "private-token" not in persisted.last_error
        assert "[REDACTED]" in persisted.last_error
        assert persisted.last_error in caplog.text
        assert persisted.status is MemoryJobStatus.PENDING
    finally:
        state.job_registry.close_all(timeout=2)


def test_six_hour_boundary_applies_to_manual_extraction(tmp_path: Path) -> None:
    source = _Source(
        [
            _conversation("exact", age=timedelta(hours=6)),
            _conversation("recent", age=timedelta(hours=6) - timedelta(seconds=1)),
        ]
    )
    state, service = _service(tmp_path, source)
    try:
        assert service.enqueue_extract("exact").source_id == "exact"
        with pytest.raises(ValueError, match="6 hours"):
            service.enqueue_extract("recent")
    finally:
        state.job_registry.close_all(timeout=2)


def test_switch_off_and_on_during_consolidation_drops_inflight_response(tmp_path):
    entered, release = Event(), Event()

    class DelayedModel(_AddingModel):
        def consolidate_memories(self, request):
            entered.set()
            assert release.wait(5)
            return super().consolidate_memories(request)

    state, service = _service(tmp_path, _Source([]))
    service._model_factory = DelayedModel
    record = _episodic("conversation", "pending")
    _record(state.memory_store, record)
    queued = service.enqueue_consolidate()
    claimed = state.memory_store.claim_job(service._worker_id)
    worker = Thread(target=service._execute, args=(claimed,), kwargs={"is_cancelled": lambda: False}, daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        state.settings.model_processing = False
        service.stop()
        state.settings.model_processing = True
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert state.memory_store.get_job(queued.job_id).status is MemoryJobStatus.CANCELLED
        assert state.memory_store.list_items() == [record.item]
        assert state.memory_store.get_candidate(record.candidate.candidate_id) == record.candidate
    finally:
        release.set()
        worker.join(5)
        state.job_registry.close_all(timeout=2)


def test_scan_times_each_thread_and_excludes_recycle_bin_and_running(tmp_path: Path) -> None:
    source = _Source(
        [
            _conversation("old", age=timedelta(hours=7)),
            _conversation("recent", age=timedelta(hours=1)),
            _conversation("archived", age=timedelta(hours=7), archived=True),
            _conversation("deleted", age=timedelta(hours=7), deleted=True),
            _conversation("running", age=timedelta(hours=7), running=True),
        ]
    )
    state, service = _service(tmp_path, source)
    try:
        service.scan_once()
        assert [job.source_id for job in state.memory_store.list_jobs()] == ["old"]
        with pytest.raises(ValueError, match="recycle bin"):
            service.enqueue_extract("archived")
        with pytest.raises(ValueError, match="recycle bin"):
            service.enqueue_extract("deleted")
    finally:
        state.job_registry.close_all(timeout=2)


def test_short_conversations_are_scanned_without_creating_jobs(tmp_path: Path) -> None:
    conversation = _conversation("short", age=timedelta(hours=7))
    source = _Source([conversation])
    eligible = source.messages["short"]
    state, service = _service(tmp_path, source)
    try:
        for messages in ((), eligible[:1], (replace(eligible[0], content=""), eligible[1])):
            source.messages["short"] = messages
            service.scan_once()
            assert state.memory_store.list_jobs() == []
        source.messages["short"] = eligible
        service.scan_once()
        assert len(state.memory_store.list_jobs()) == 1
        job = state.memory_store.claim_job(service._worker_id, now="2030-01-01T00:00:00+00:00")
        source.messages["short"] = ()
        service._model_factory = lambda: pytest.fail("An ineligible conversation initialized a model")
        service._execute(job, is_cancelled=lambda: False)
        assert state.memory_store.get_job(job.job_id).status is MemoryJobStatus.SUCCEEDED
        service.scan_once()
        assert len(state.memory_store.list_jobs()) == 1
    finally:
        state.job_registry.close_all(timeout=2)


def test_consolidation_follows_up_candidates_arriving_during_a_run(tmp_path):
    state, service = _service(tmp_path, _Source([]))
    first, later = _episodic("thread_a", "first"), _episodic("thread_b", "later")
    _record(state.memory_store, first)

    class Model(_Model):
        def consolidate_memories(self, request):
            _record(state.memory_store, later)
            assert service.enqueue_consolidate().job_id == running.job_id
            return super().consolidate_memories(request)

    try:
        service.enqueue_consolidate()
        running = state.memory_store.claim_job(service._worker_id, now="2030-01-01T00:00:00+00:00")
        service._model_factory = Model
        service._execute(running, is_cancelled=lambda: False)
        follow_up = state.memory_store.claim_job(
            service._worker_id, kind=MemoryJobKind.CONSOLIDATE, now="2030-01-01T00:00:00+00:00"
        )
        assert follow_up is not None and follow_up.job_id != running.job_id
        service._model_factory = _Model
        service._execute(follow_up, is_cancelled=lambda: False)
        assert state.memory_store.get_candidate(later.candidate.candidate_id).status is MemoryCandidateStatus.REJECTED
        assert not state.memory_store.list_jobs(status=MemoryJobStatus.PENDING)
    finally:
        state.job_registry.close_all(timeout=2)


def test_expired_worker_cannot_cancel_a_reclaimed_memory_job(tmp_path):
    conversation = _conversation("thread_a", age=timedelta(hours=7))
    state, service = _service(tmp_path, _Source([conversation]))
    try:
        service.enqueue_extract(conversation.thread_id)
        old = state.memory_store.claim_job(service._worker_id, now="2030-01-01T00:00:00+00:00", lease_seconds=1)
        new = state.memory_store.claim_job("other_worker", now="2030-01-01T00:00:02+00:00")
        assert old.job_id == new.job_id
        service._execute(old, is_cancelled=lambda: True)
        assert state.memory_store.get_job(new.job_id) == new
        assert state.memory_store.get_watermark(conversation.thread_id) is None
    finally:
        state.job_registry.close_all(timeout=2)


@pytest.mark.parametrize("reason", ["user_cancelled", "provider_unavailable", "quota_unavailable", "failed"])
def test_scan_respects_terminal_extraction_until_manual_retry_or_new_input(tmp_path, reason):
    conversation = _conversation("thread_a", age=timedelta(hours=7))
    source = _Source([conversation])
    state, service = _service(tmp_path, source)
    store = state.memory_store
    try:
        service.scan_once()
        (job,) = store.list_jobs()
        if reason == "failed":
            for _ in range(job.max_attempts):
                lease = store.claim_job("worker", now="2030-01-01T00:00:00+00:00")
                store.fail_job(lease.job_id, "worker", "fixture failure")
        else:
            store.cancel_job(job.job_id, reason=reason)
        # A scanner restart must not discard the user's cancellation or the retry limit.
        restarted, scanner = _service(tmp_path, source)
        try:
            scanner.scan_once()
            assert not store.list_jobs(status=MemoryJobStatus.PENDING)
            manual = scanner.enqueue_extract(conversation.thread_id)
            assert manual.job_id != job.job_id
            scanner.cancel(manual.job_id)
            messages = source.messages[conversation.thread_id]
            source.messages[conversation.thread_id] = (
                *messages[:-1],
                replace(messages[-1], content="New evidence " * 9),
            )
            scanner.scan_once()
            assert len(store.list_jobs(status=MemoryJobStatus.PENDING)) == 1
        finally:
            restarted.job_registry.close_all(timeout=2)
    finally:
        state.job_registry.close_all(timeout=2)


@pytest.mark.parametrize(
    "change", ["message", "same_position_edit", "project_rebound", "archived", "deleted", "cancelled", "processing_off"]
)
def test_queued_change_prevents_write_and_watermark(tmp_path: Path, change: str) -> None:
    conversation = _conversation("thread_a", age=timedelta(hours=7))
    source = _Source([conversation])
    state, service = _service(tmp_path, source)
    queued = service.enqueue_extract("thread_a")
    claimed = state.memory_store.claim_job(service._worker_id, now="2030-01-01T00:00:00+00:00")
    assert claimed is not None

    def mutate() -> None:
        if change == "message":
            source.messages["thread_a"] += (
                MemorySourceMessage(4, "thread_a_v3_user", "user", "A new interaction arrived after queueing."),
            )
            source.conversations["thread_a"] = replace(conversation, updated_at=NOW)
        elif change == "same_position_edit":
            messages = source.messages["thread_a"]
            source.messages["thread_a"] = (*messages[:-1], replace(messages[-1], content="Edited in place."))
        elif change == "project_rebound":
            source.conversations["thread_a"] = replace(conversation, project_id="private_project")
        elif change in {"archived", "deleted"}:
            source.conversations["thread_a"] = replace(conversation, **{change: True})
        elif change == "processing_off":
            state.settings.model_processing = False
            service.stop()
        else:
            state.memory_store.cancel_job(queued.job_id, reason="test_cancel")

    service._model_factory = lambda: _Model(mutate)
    try:
        service._execute(claimed, is_cancelled=lambda: False)
        assert state.memory_store.get_watermark("thread_a") is None
        assert state.memory_store.list_items(include_deleted=True) == []
        assert state.memory_store.get_job(queued.job_id).status is MemoryJobStatus.CANCELLED
    finally:
        state.job_registry.close_all(timeout=2)
