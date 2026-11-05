from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from backend.sandbox.broker_service.pipe import WindowsNamedPipeServer
from backend.sandbox.control.pipe_connection import connect_pipe
from backend.sandbox.control.status_request import status_request

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Real Windows pipe shutdown")


@pytest.mark.parametrize("silent_clients", [0, 1, 20])
def test_real_pipe_stop_cancels_listener_and_idle_clients(silent_clients):
    name = rf"\\.\pipe\praxis-stop-test-{uuid.uuid4().hex}"
    created = Event()
    closed = []
    failures = []
    service = SimpleNamespace(
        configuration=SimpleNamespace(pipe_name=name), close=lambda: closed.append(True), handle=lambda _: b"ok"
    )
    server = WindowsNamedPipeServer(service, security_attributes_factory=lambda: None)
    native_create = server._create_pipe

    def create():
        handle = native_create()
        created.set()
        return handle

    server.pipe_handle_factory = create

    def serve():
        try:
            server.serve_forever()
        except Exception as exc:
            failures.append(type(exc).__name__)

    worker = Thread(target=serve, daemon=True)
    clients = []
    worker.start()
    try:
        assert created.wait(3)
        for _ in range(silent_clients):
            clients.append(connect_pipe(name, deadline=time.monotonic() + 3))
        # A real request also proves ordinary accept/read/write still works
        # while other clients occupy pipe instances (up to the worker limit).
        if silent_clients < 16:
            assert status_request(name, b"request", 3) == b"ok"
        started = time.monotonic()
        server.request_stop()
        worker.join(3)
        assert not worker.is_alive(), "Broker did not cancel pending named-pipe I/O"
        assert time.monotonic() - started < 3
        assert not failures and closed == [True]
        server.close()
        assert closed == [True]
    finally:
        server.request_stop()
        for client in clients:
            client.Close()
        worker.join(3)


def test_real_pipe_overlapped_server_keeps_concurrent_messages_intact():
    name = rf"\\.\pipe\praxis-stop-roundtrip-{uuid.uuid4().hex}"
    created = Event()
    service = SimpleNamespace(
        configuration=SimpleNamespace(pipe_name=name), close=lambda: None, handle=lambda body: body
    )
    server = WindowsNamedPipeServer(service, security_attributes_factory=lambda: None)
    native_create = server._create_pipe

    def create():
        handle = native_create()
        created.set()
        return handle

    server.pipe_handle_factory = create
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert created.wait(3)
        bodies = [f"message-{n}".encode() for n in range(24)]
        with ThreadPoolExecutor(8) as pool:
            assert list(pool.map(lambda body: status_request(name, body, 3), bodies)) == bodies
    finally:
        server.request_stop()
        worker.join(3)
        assert not worker.is_alive()


def test_service_stop_handler_signals_before_any_resource_cleanup():
    import win32event

    from backend.sandbox.service_main import PraxisSandboxBrokerService

    stopped = win32event.CreateEvent(None, True, False, None)
    calls = []
    server = SimpleNamespace(
        request_stop=lambda: calls.append("signal"), close=lambda: pytest.fail("Cleanup must run in service thread")
    )
    service = object.__new__(PraxisSandboxBrokerService)
    service.stop_event, service.server = stopped, server
    service.ReportServiceStatus = lambda _: calls.append("pending")
    try:
        service.SvcStop()
        assert win32event.WaitForSingleObject(stopped, 0) == win32event.WAIT_OBJECT_0
        assert calls == ["pending", "signal"]
    finally:
        stopped.Close()


def test_real_status_timeout_cancels_its_own_pending_read():
    name = rf"\\.\pipe\praxis-status-cancel-{uuid.uuid4().hex}"
    created, release = Event(), Event()

    def handle(_body):
        release.wait(3)
        return b"late"

    service = SimpleNamespace(configuration=SimpleNamespace(pipe_name=name), close=release.set, handle=handle)
    server = WindowsNamedPipeServer(service, security_attributes_factory=lambda: None)
    native_create = server._create_pipe

    def create():
        pipe = native_create()
        created.set()
        return pipe

    server.pipe_handle_factory = create
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert created.wait(3)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            status_request(name, b"wait", 0.1)
        assert time.monotonic() - started < 1
    finally:
        release.set()
        server.request_stop()
        worker.join(3)
        assert not worker.is_alive()
