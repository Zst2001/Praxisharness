"""Broker-owned actual usage, FIFO admission and newest-first termination."""

import os
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, RLock, Thread
from time import monotonic

from backend.domain import error_report


def _system_memory_limit_mib() -> int:
    """Return the 80% machine-memory ceiling, with a conservative fallback."""

    total_bytes: int | None = None
    if os.name == "nt":
        try:
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_uint32),
                    ("memory_load", ctypes.c_uint32),
                    ("total_physical", ctypes.c_uint64),
                    ("available_physical", ctypes.c_uint64),
                    ("total_page_file", ctypes.c_uint64),
                    ("available_page_file", ctypes.c_uint64),
                    ("total_virtual", ctypes.c_uint64),
                    ("available_virtual", ctypes.c_uint64),
                    ("available_extended", ctypes.c_uint64),
                ]

            status = _MemoryStatus()
            status.length = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total_bytes = int(status.total_physical)
        except (AttributeError, OSError):
            total_bytes = None
    else:
        try:
            total_bytes = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        except (AttributeError, OSError, ValueError):
            total_bytes = None
    if total_bytes is None or total_bytes <= 0:
        return 1_000_000
    return max(128, int(total_bytes * 0.80 / (1024 * 1024)))


def default_aggregate_limits() -> dict[str, int]:
    return {"memory_mib": min(8192, _system_memory_limit_mib()), "processes": 512, "handles": 32768}


def aggregate_limits(values: dict) -> dict[str, int]:
    if set(values) != {"memory_mib", "processes", "handles"}:
        raise ValueError("Invalid aggregate resource fields.")
    for value in values.values():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("Aggregate resource limits must be positive integers.")
    return dict(values)


@dataclass
class Entry:
    owner: str
    sample: Callable
    terminate: Callable
    usage: dict = field(default_factory=dict)
    stopping: bool = False


class AggregateResources:
    def __init__(self, *, clock=monotonic, start: bool = True) -> None:
        self.lock = RLock()
        self.clock = clock
        self.jobs: OrderedDict[str, Entry] = OrderedDict()
        self.queues: dict[str, OrderedDict[str, float]] = {}
        self.permits: dict[str, str] = {}
        self.limits: dict[str, dict] = {}
        self.errors: dict[str, dict] = {}
        self.claims: dict[str, str] = {}
        self.cancelled_claims: set[tuple[str, str]] = set()
        self.queued_since: dict[tuple[str, str], float] = {}
        self.events = deque(maxlen=256)
        self._stop = Event()
        self._thread = Thread(target=self._run, name="sandbox-total-resources", daemon=True)
        if start:
            self._thread.start()

    def configure(self, owner: str, values: dict, *, initialize: bool = False) -> None:
        with self.lock:
            if not initialize or owner not in self.limits:
                self.limits[owner] = aggregate_limits(values)

    def _usage(self, owner: str) -> dict:
        return {
            key: sum(int(entry.usage.get(key, 0)) for entry in self.jobs.values() if entry.owner == owner)
            for key in ("memory_bytes", "processes", "handles")
        }

    def _bounds(self, owner: str) -> dict:
        limits = self.limits.get(owner, default_aggregate_limits())
        return {
            "memory_bytes": limits["memory_mib"] * 1024 * 1024,
            "processes": limits["processes"],
            "handles": limits["handles"],
        }

    def _record(self, owner: str, ticket: str, event: str) -> None:
        self.events.append((owner, {"ticket": ticket, "event": event, "at": self.clock()}))

    def _expire(self, owner: str) -> None:
        for ticket, touched in list(self.queues.get(owner, {}).items()):
            if self.clock() - touched > 30 and self.claims.get(owner) != ticket:
                self._drop_ticket(owner, ticket)
                self._record(owner, ticket, "expired")

    def status(self, owner: str, ticket: str | None = None) -> dict:
        with self.lock:
            usage, bounds = self._usage(owner), self._bounds(owner)
            queue = self.queues.get(owner, {})
            permit = self.permits.get(owner)
            stopping = sum(entry.stopping for entry in self.jobs.values() if entry.owner == owner)
            if owner in self.errors:
                reason = "sampling_error"
            elif stopping:
                reason = "cleanup_pending"
            elif any(usage[key] >= bounds[key] for key in bounds):
                reason = "quota_reached"
            elif owner in self.claims:
                reason = "launch_in_progress"
            elif permit is not None and permit != ticket:
                reason = "permit_busy"
            elif ticket is not None and queue and next(iter(queue)) != ticket:
                reason = "fifo_wait"
            else:
                reason = "granted" if ticket is not None and permit == ticket else "available"
            return {
                "usage": usage,
                "limits": self.limits.get(owner, default_aggregate_limits()),
                "queued": len(queue),
                "error_report": self.errors.get(owner),
                "diagnostics_version": 1,
                "wait_reason": reason,
                "queue_position": list(queue).index(ticket) + 1 if ticket in queue else None,
                "wait_ms": max(0, int((self.clock() - self.queued_since[(owner, ticket)]) * 1000))
                if (owner, ticket) in self.queued_since
                else 0,
                "permit_present": permit is not None,
                "active_jobs": sum(entry.owner == owner for entry in self.jobs.values()),
                "stopping_jobs": stopping,
                "recent_events": [event for key, event in self.events if key == owner][-32:],
            }

    def acquire(self, owner: str, ticket: str) -> dict:
        with self.lock:
            self._expire(owner)
            queue = self.queues.setdefault(owner, OrderedDict())
            self.queued_since.setdefault((owner, ticket), self.clock())
            queue[ticket] = self.clock()
            usage, bounds = self._usage(owner), self._bounds(owner)
            available = (
                owner not in self.errors
                and all(usage[key] < bounds[key] for key in bounds)
                and not any(entry.stopping for entry in self.jobs.values() if entry.owner == owner)
                and owner not in self.claims
            )
            if available and next(iter(queue)) == ticket and self.permits.get(owner) in (None, ticket):
                if self.permits.get(owner) != ticket:
                    self._record(owner, ticket, "granted")
                self.permits[owner] = ticket
            return {
                **self.status(owner, ticket),
                "granted": self.permits.get(owner) == ticket and owner not in self.claims,
            }

    def cancel(self, owner: str, ticket: str) -> None:
        with self.lock:
            self._record(owner, ticket, "cancelled")
            if self.claims.get(owner) == ticket:
                self.cancelled_claims.add((owner, ticket))
            else:
                self._drop_ticket(owner, ticket)

    def _drop_ticket(self, owner: str, ticket: str) -> None:
        self.queues.get(owner, {}).pop(ticket, None)
        self.queued_since.pop((owner, ticket), None)
        if self.permits.get(owner) == ticket:
            self.permits.pop(owner, None)

    def require_permit(self, owner: str, ticket: str) -> None:
        with self.lock:
            self._expire(owner)
            if self.permits.get(owner) != ticket or (owner, ticket) in self.cancelled_claims:
                raise ValueError("Model command resource permit is missing or expired.")

    def claim(self, owner: str, ticket: str) -> None:
        """Only the trusted native launcher pins a permit during launch."""
        with self.lock:
            self.require_permit(owner, ticket)
            if owner in self.claims:
                raise ValueError("A launch is already in progress")
            self.claims[owner] = ticket
            self._record(owner, ticket, "launch_claimed")

    def finish_claim(self, owner: str, ticket: str) -> None:
        with self.lock:
            if self.claims.get(owner) == ticket:
                self.claims.pop(owner)
            self.cancelled_claims.discard((owner, ticket))
            self._drop_ticket(owner, ticket)
            self._record(owner, ticket, "launch_finished")

    def register(self, owner: str, ticket: str, job_id: str, sample, terminate) -> None:
        with self.lock:
            self.require_permit(owner, ticket)
            entry = Entry(owner, sample, terminate)
            self.jobs[job_id] = entry
            try:
                entry.usage = sample()
            except Exception as exc:
                self.errors[owner] = error_report(exc)
                self._record(owner, ticket, "registration_failed")
                raise
            else:
                self._record(owner, ticket, "registered")
            finally:
                self._drop_ticket(owner, ticket)

    def release(self, job_id: str) -> None:
        with self.lock:
            entry = self.jobs.pop(job_id, None)
            if entry is not None:
                self._record(entry.owner, job_id, "released")

    def tick(self) -> None:
        with self.lock:
            # Polling renews waiters. Unclaimed grants must also expire when a
            # client disappears; a trusted launch remains pinned until finally.
            for owner in self.queues:
                self._expire(owner)
            owners = set(self.limits) | {entry.owner for entry in self.jobs.values()}
            for owner in owners:
                failure = None
                for entry in self.jobs.values():
                    if entry.owner != owner:
                        continue
                    try:
                        entry.usage = entry.sample()
                        if entry.usage.get("processes", 0) == 0:
                            entry.stopping = False
                    except Exception as exc:
                        failure = error_report(exc)
                if failure:
                    self.errors[owner] = failure
                    continue
                self.errors.pop(owner, None)
                usage, bounds = self._usage(owner), self._bounds(owner)
                exceeded = [key for key in bounds if usage[key] > bounds[key]]
                if not exceeded or any(e.stopping for e in self.jobs.values() if e.owner == owner):
                    continue
                for entry in reversed(self.jobs.values()):
                    if entry.owner != owner or not entry.usage.get("processes", 0):
                        continue
                    reason = "Model command aggregate resource limit exceeded: " + ", ".join(
                        f"{key}={usage[key]}, limit={bounds[key]}" for key in exceeded
                    )
                    try:
                        entry.terminate(reason)
                        entry.stopping = True
                    except Exception as exc:
                        self.errors[owner] = error_report(exc)
                    break

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            self.tick()

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
