"""Persistent local Memory scheduling with a six-hour inactivity gate."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.domain import redact_sensitive_text, safe_error_message
from backend.domain.memory import MemoryJob, MemoryJobKind, MemoryJobStatus, MemorySettings
from backend.jobs import AdmissionPolicy, JobLane, QueueMode, ThreadJob
from backend.providers import ModelConfigurationError, ModelTransportError
from backend.storage.memory import MemoryConflictError, MemoryNotFoundError

from .consolidation import ManualMemoryConsolidator, MemoryConsolidationRequest
from .extraction import EpisodicExtractionRequest, ManualEpisodicExtractor, MemorySessionSnapshot
from .provider_models import MemoryModelUnavailable, ProviderMemoryModel
from .source import MemoryConversation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryAutomationSettings:
    idle_seconds: int = 6 * 60 * 60
    scan_interval_seconds: float = 30.0
    phase1_concurrency: int = 2
    lease_seconds: int = 900
    retry_base_seconds: int = 30
    retry_max_seconds: int = 1800


@dataclass(frozen=True)
class _DeferredMemoryModel:
    factory: Callable[[], ProviderMemoryModel]

    def extract_episodic(self, request: EpisodicExtractionRequest) -> Mapping[str, object]:
        return self.factory().extract_episodic(request)

    def consolidate_memories(self, request: MemoryConsolidationRequest) -> Mapping[str, object]:
        return self.factory().consolidate_memories(request)


class MemoryAutomationService:
    def __init__(self, state, model_factory: Callable[[], ProviderMemoryModel], *, settings=None, clock=None) -> None:
        self._state = state
        self._model_factory = model_factory
        self.settings = settings or MemoryAutomationSettings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._active: dict[tuple[str, int], tuple[MemoryJobKind, ThreadJob]] = {}
        self._service_job: ThreadJob | None = None
        self._wake_event = threading.Event()
        self._queue_lock = threading.Lock()
        self._worker_id = f"memory_worker_{uuid4().hex[:16]}"

    def start(self) -> None:
        if self._service_job is not None:
            return
        self._service_job = ThreadJob("memory_automation_service", self._run_loop)
        self._state.system_job_scope.submit(
            self._service_job,
            lane=JobLane.SERVICE,
            admission=AdmissionPolicy(queue_mode=QueueMode.REJECT, queue_timeout_seconds=0),
        )

    def close(self) -> None:
        if self._service_job is not None:
            self._service_job.cancel("memory automation closed")
        self._wake_event.set()

    def wake(self) -> None:
        self._wake_event.set()

    def enqueue_extract(self, thread_id: str) -> MemoryJob:
        conversation = self._eligible_conversation(thread_id)
        snapshot = self._eligible_snapshot(conversation)
        job = MemoryJob.new(kind=MemoryJobKind.EXTRACT, source_id=thread_id, project_id=conversation.project_id)
        stored = self._enqueue(job, input_digest=snapshot.input_digest)
        self.wake()
        return stored

    def enqueue_consolidate(self, *, project_id: str | None = None) -> MemoryJob:
        self._require_enabled()
        job = MemoryJob.new(
            kind=MemoryJobKind.CONSOLIDATE,
            source_id=f"scope_{project_id or 'global'}",
            project_id=project_id,
        )
        stored = self._enqueue(job)
        self.wake()
        return stored

    def cancel(self, job_id: str) -> MemoryJob:
        job = self._state.memory_store.cancel_job(job_id, reason="user_cancelled")
        for (active_id, _), (_, carrier) in self._active.copy().items():
            if active_id == job_id:
                carrier.cancel("user requested cancellation")
        return job

    def stop(self) -> None:
        with self._queue_lock:
            self._state.memory_store.cancel_active_jobs()
            for _, carrier in self._active.copy().values():
                carrier.cancel("memory disabled")

    def _enqueue(self, job: MemoryJob, *, input_digest: str | None = None, automatic: bool = False) -> MemoryJob:
        with self._queue_lock:
            self._require_enabled()
            stored, _ = self._state.memory_store.enqueue_job_if_absent(
                job, input_digest=input_digest, automatic=automatic
            )
            return stored

    def clear(self) -> None:
        self.stop()
        self._state.memory_store.clear_all()
        self._state.memory_store.rebuild_projections()

    def scan_once(self) -> None:
        settings = self._settings()
        if not settings.can_generate:
            return
        extractor = ManualEpisodicExtractor.from_settings(
            self._state.memory_store, _DeferredMemoryModel(self._model_factory), settings
        )
        cutoff = self._clock() - timedelta(seconds=self.settings.idle_seconds)
        for conversation in self._state.memory_source.list():
            if (
                conversation.archived
                or conversation.deleted
                or conversation.running
                or conversation.updated_at > cutoff
            ):
                continue
            try:
                snapshot = self._eligible_snapshot(conversation)
                eligible = extractor.eligibility(snapshot).eligible
            except (_MemoryConversationIneligible, MemoryConflictError):
                continue
            if eligible:
                self._enqueue(
                    MemoryJob.new(
                        kind=MemoryJobKind.EXTRACT,
                        source_id=conversation.thread_id,
                        project_id=conversation.project_id,
                    ),
                    input_digest=snapshot.input_digest,
                    automatic=True,
                )
        self._dispatch()

    def _run_loop(self, *, is_cancelled: Callable[[], bool]) -> None:
        while not is_cancelled():
            try:
                self.scan_once()
            except Exception as exc:
                logger.warning("memory scan failed safely: %s", exc.__class__.__name__)
            self._wake_event.wait(self.settings.scan_interval_seconds)
            self._wake_event.clear()

    def _dispatch(self) -> None:
        active = self._active.copy().values()
        extract_active = sum(kind is MemoryJobKind.EXTRACT for kind, _ in active)
        capacity = self.settings.phase1_concurrency - extract_active
        while capacity > 0:
            job = self._claim(MemoryJobKind.EXTRACT)
            if job is None:
                break
            self._submit(job)
            capacity -= 1
        if not any(kind is MemoryJobKind.CONSOLIDATE for kind, _ in active):
            job = self._claim(MemoryJobKind.CONSOLIDATE)
            if job is not None:
                self._submit(job)

    def _claim(self, kind: MemoryJobKind) -> MemoryJob | None:
        return self._state.memory_store.claim_job(
            f"{self._worker_id}_{uuid4().hex}", kind=kind, lease_seconds=self.settings.lease_seconds
        )

    def _submit(self, job: MemoryJob) -> None:
        carrier = ThreadJob(f"{job.job_id}_attempt_{job.attempts}", self._execute, args=(job,))
        self._active[job.job_id, job.attempts] = (job.kind, carrier)
        try:
            self._state.system_job_scope.submit(
                carrier,
                lane=JobLane.BACKGROUND,
                admission=AdmissionPolicy(queue_mode=QueueMode.REJECT, queue_timeout_seconds=0),
            )
        except Exception as exc:
            self._active.pop((job.job_id, job.attempts), None)
            self._retry(job, exc)

    def _execute(self, job: MemoryJob, *, is_cancelled: Callable[[], bool]) -> None:
        try:
            self._verify_job_active(job, is_cancelled)
            if job.kind is MemoryJobKind.EXTRACT:
                conversation = self._eligible_conversation(job.source_id or "")
                if conversation.project_id != job.project_id:
                    raise _MemoryConversationIneligible("Conversation project changed.")
                snapshot = self._eligible_snapshot(conversation)
                self._state.memory_store.bind_extraction_input(job, snapshot.input_digest)
                before = (conversation.updated_at, snapshot)
                result = ManualEpisodicExtractor.from_settings(
                    self._state.memory_store,
                    _DeferredMemoryModel(self._model_factory),
                    self._settings(),
                ).extract(
                    snapshot,
                    before_commit=lambda: self._verify_extract_commit(
                        job,
                        conversation.thread_id,
                        before,
                        is_cancelled,
                    ),
                    job_id=job.job_id,
                    lease_owner=job.lease_owner,
                )
                if result.model_called and result.records:
                    self.enqueue_consolidate(project_id=conversation.project_id)
            else:
                ManualMemoryConsolidator.from_settings(
                    self._state.memory_store,
                    _DeferredMemoryModel(self._model_factory),
                    self._settings(),
                ).consolidate(
                    project_id=job.project_id,
                    before_commit=lambda: self._verify_job_active(job, is_cancelled),
                    job_id=job.job_id,
                    lease_owner=job.lease_owner,
                )
            self._verify_job_active(job, is_cancelled)
            self._state.memory_store.complete_job(job.job_id, job.lease_owner)
            # The active job deduplicates enqueue requests while its model is running.
            # Release it before checking for arrivals or the next bounded batch.
            if job.kind is MemoryJobKind.CONSOLIDATE and self._settings().can_generate:
                if self._state.memory_store.list_phase1_records(project_id=job.project_id, limit=1):
                    self.enqueue_consolidate(project_id=job.project_id)
        except (_MemoryWorkCancelled, _MemoryConversationIneligible):
            self._cancel_safely(job, "conversation_changed")
        except MemoryModelUnavailable as exc:
            self._cancel_safely(job, str(exc))
        except ModelConfigurationError:
            self._cancel_safely(job, "provider_unavailable")
        except ModelTransportError as exc:
            if exc.status_code in {402, 429}:
                self._cancel_safely(job, "quota_unavailable")
            elif exc.status_code in {401, 403, 404}:
                self._cancel_safely(job, "provider_unavailable")
            else:
                self._retry(job, exc)
        except Exception as exc:
            self._retry(job, exc)
        finally:
            self._active.pop((job.job_id, job.attempts), None)

    def _eligible_conversation(self, thread_id: str) -> MemoryConversation:
        self._require_enabled()
        conversation = self._state.memory_source.get(thread_id)
        if conversation is None or conversation.archived or conversation.deleted:
            raise _MemoryConversationIneligible("Conversation is in the recycle bin or does not exist.")
        if conversation.running:
            raise _MemoryConversationIneligible("Conversation is still running.")
        if conversation.updated_at > self._clock() - timedelta(seconds=self.settings.idle_seconds):
            raise _MemoryConversationIneligible("Conversation must be inactive for at least 6 hours.")
        return conversation

    def _eligible_snapshot(self, conversation: MemoryConversation) -> MemorySessionSnapshot:
        snapshot = self._state.memory_source.snapshot(conversation)
        if snapshot.used_external_context and self._settings().disable_on_external_context:
            raise _MemoryConversationIneligible("Memory extraction is disabled for external context.")
        return snapshot

    def _verify_extract_commit(
        self,
        job: MemoryJob,
        thread_id: str,
        expected: tuple[datetime, MemorySessionSnapshot],
        is_cancelled: Callable[[], bool],
    ) -> None:
        self._verify_job_active(job, is_cancelled)
        current = self._eligible_conversation(thread_id)
        if (current.updated_at, self._eligible_snapshot(current)) != expected:
            raise _MemoryWorkCancelled()

    def _verify_job_active(self, job: MemoryJob, is_cancelled: Callable[[], bool]) -> None:
        if is_cancelled() or not self._settings().can_generate:
            raise _MemoryWorkCancelled()
        current = self._state.memory_store.get_job(job.job_id)
        if current is None or current.status is not MemoryJobStatus.RUNNING or current.lease_owner != job.lease_owner:
            raise _MemoryWorkCancelled()

    def _settings(self) -> MemorySettings:
        return MemorySettings.from_mapping(self._state.settings.memory_config())

    def _require_enabled(self) -> None:
        if not self._settings().can_generate:
            raise ValueError("Memory model processing is disabled.")

    def _cancel_safely(self, job: MemoryJob, reason: str) -> None:
        try:
            self._state.memory_store.cancel_job(job.job_id, reason=reason, lease_owner=job.lease_owner)
        except (MemoryConflictError, MemoryNotFoundError):
            pass

    def _retry(self, job: MemoryJob, exc: Exception) -> None:
        delay = min(
            self.settings.retry_max_seconds,
            self.settings.retry_base_seconds * (2 ** max(job.attempts - 1, 0)),
        )
        retry_at = (self._clock() + timedelta(seconds=delay)).isoformat()
        diagnostics = getattr(exc, "diagnostics", {}) or {}
        details = {
            "error": f"{exc.__class__.__name__}:{safe_error_message(exc)}",
            "status_code": getattr(exc, "status_code", None),
            "retryable": getattr(exc, "retryable", None),
            **{
                name: diagnostics[name]
                for name in ("response_detail", "request_id", "provider_name", "model", "operation")
                if name in diagnostics
            },
        }
        failure = " | ".join(
            f"{name}={redact_sensitive_text(str(value)).replace(chr(10), ' ').replace(chr(13), ' ')[:500]}"
            for name, value in details.items()
            if value is not None
        )
        try:
            self._state.memory_store.fail_job(
                job.job_id,
                job.lease_owner,
                failure,
                retry_at=retry_at,
            )
        except (MemoryConflictError, MemoryNotFoundError):
            return
        logger.warning(
            "memory job failed safely job_id=%s source_id=%s attempt=%s/%s details=%s",
            job.job_id,
            job.source_id,
            job.attempts,
            job.max_attempts,
            failure,
        )


class _MemoryWorkCancelled(RuntimeError):
    pass


class _MemoryConversationIneligible(ValueError):
    pass


__all__ = ["MemoryAutomationService", "MemoryAutomationSettings"]
