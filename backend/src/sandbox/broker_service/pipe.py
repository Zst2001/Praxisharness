"""Synchronous authenticated Windows named-pipe server."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock
from typing import Any

from ..errors import SandboxInitializationError
from .service import WindowsBrokerService

logger = logging.getLogger(__name__)


class WindowsNamedPipeServer:
    """Minimal synchronous named-pipe loop for the standalone service.

    The security descriptor is supplied by the service installer, so the
    runtime never widens an existing ACL.  The adapter is intentionally lazy:
    importing the module on Linux or before pywin32 installation remains safe,
    while attempting to serve without those capabilities fails closed.
    """

    def __init__(
        self,
        service: WindowsBrokerService,
        *,
        pipe_handle_factory: Callable[[], Any] | None = None,
        security_attributes_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.service = service
        self.pipe_handle_factory = pipe_handle_factory
        self.security_attributes_factory = security_attributes_factory
        self._closed = False
        self._stopping = Event()
        self._service_closed = False
        self._listener_lock = RLock()
        self._listener_handle: Any | None = None

    def serve_once(self) -> None:
        if os.name != "nt":
            raise SandboxInitializationError("Windows named pipes are unavailable on this platform")
        handle = (
            self.pipe_handle_factory() if self.pipe_handle_factory is not None else self._create_pipe(overlapped=False)
        )
        try:
            import win32file  # type: ignore[import-not-found]
            import win32pipe  # type: ignore[import-not-found]

            win32pipe.ConnectNamedPipe(handle, None)
            _, payload = win32file.ReadFile(handle, 1024 * 1024)
            response = self.service.handle(payload)
            win32file.WriteFile(handle, response)
        except SandboxInitializationError:
            raise
        except Exception:  # pragma: no cover - Windows-only adapter
            raise
        finally:
            try:
                import win32file  # type: ignore[import-not-found]

                win32file.CloseHandle(handle)
            except Exception:
                pass

    def serve_forever(self, *, stop: Callable[[], bool] | None = None) -> None:
        workers = ThreadPoolExecutor(max_workers=16, thread_name_prefix="sandbox-broker-pipe")
        try:
            while not self._closed and not (stop is not None and stop()):
                handle = self.pipe_handle_factory() if self.pipe_handle_factory is not None else self._create_pipe()
                with self._listener_lock:
                    if self._closed:
                        self._close_handle(handle)
                        break
                    self._listener_handle = handle
                try:
                    self._transfer(handle, "connect")
                except Exception:
                    self._close_handle(handle)
                    if self._closed:
                        break
                    raise
                finally:
                    with self._listener_lock:
                        if self._listener_handle is handle:
                            self._listener_handle = None
                workers.submit(self._serve_connected, handle)
        finally:
            try:
                self.close()
            finally:
                # Queued handlers also own pipe handles; let them observe the
                # stop event and close those handles instead of cancelling them.
                workers.shutdown(wait=True)

    def request_stop(self) -> None:
        """Signal I/O loops without closing a handle in use by another thread."""
        self._closed = True
        self._stopping.set()

    def close(self) -> None:
        self.request_stop()
        with self._listener_lock:
            if self._service_closed:
                return
            self._service_closed = True
        self.service.close()

    def _create_pipe(self, *, overlapped: bool = True) -> Any:
        import win32con  # type: ignore[import-not-found]
        import win32pipe  # type: ignore[import-not-found]

        try:
            return win32pipe.CreateNamedPipe(
                self.service.configuration.pipe_name,
                win32con.PIPE_ACCESS_DUPLEX | (win32con.FILE_FLAG_OVERLAPPED if overlapped else 0),
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                1024 * 1024,
                1024 * 1024,
                0,
                self._security_attributes(),
            )
        except Exception as exc:  # pragma: no cover - Windows-only adapter
            # The service host does not configure Python logging handlers, so
            # retain only the numeric Win32 failure in the Application log.
            # This makes field diagnosis possible without leaking the pipe
            # name, ACL/SDDL, or any filesystem paths.
            try:
                import servicemanager  # type: ignore[import-not-found]

                winerror = getattr(exc, "winerror", None)
                servicemanager.LogErrorMsg(f"Broker named-pipe creation failed (winerror={winerror!s})")
            except Exception:
                pass
            logger.error(
                "Broker named-pipe creation failed type=%s winerror=%s",
                type(exc).__name__,
                getattr(exc, "winerror", None),
                exc_info=False,
            )
            raise

    def _serve_connected(self, handle: Any) -> None:
        try:
            payload = self._transfer(handle, "read")
            if not self._stopping.is_set():
                self._transfer(handle, "write", self.service.handle(payload))
        except Exception:
            logger.warning("Broker named-pipe request failed", exc_info=False)
        finally:
            self._close_handle(handle)

    def _transfer(self, handle: Any, kind: str, data: bytes = b"") -> bytes:
        """Use cancellable overlapped I/O for both listeners and idle clients."""
        import pywintypes
        import win32event
        import win32file
        import win32pipe

        if self._stopping.is_set():
            raise InterruptedError("Broker is stopping")
        overlapped = pywintypes.OVERLAPPED()
        event = win32event.CreateEvent(None, True, False, None)
        overlapped.hEvent = event
        pending, completed = False, False
        buffer = b""
        try:
            if kind == "connect":
                try:
                    code = win32pipe.ConnectNamedPipe(handle, overlapped)
                except pywintypes.error as exc:
                    if exc.winerror != 535:  # ERROR_PIPE_CONNECTED: client won the accept race.
                        raise
                    return b""
                if code == 535:
                    return b""
            elif kind == "read":
                _, buffer = win32file.ReadFile(handle, 1024 * 1024, overlapped)
            else:
                win32file.WriteFile(handle, data, overlapped)
            pending = True
            while not self._stopping.is_set():
                status = win32event.WaitForSingleObject(event, 100)
                if status == win32event.WAIT_OBJECT_0:
                    size = win32file.GetOverlappedResult(handle, overlapped, False)
                    completed = True
                    return bytes(buffer[:size]) if kind == "read" else b""
                if status != win32event.WAIT_TIMEOUT:
                    raise OSError("Broker pipe wait failed")
            raise InterruptedError("Broker is stopping")
        finally:
            if pending and not completed:
                try:
                    # This operation was issued on this thread, and each pipe
                    # has only one outstanding transfer. CancelIo is sufficient
                    # and is available in pywin32 builds without CancelIoEx.
                    win32file.CancelIo(handle)
                except pywintypes.error as exc:
                    if exc.winerror != 1168:  # ERROR_NOT_FOUND: completion won the cancel race.
                        raise
                finally:
                    # Keep OVERLAPPED and its buffer alive until the kernel is done.
                    win32event.WaitForSingleObject(event, win32event.INFINITE)
            event.Close()

    @staticmethod
    def _close_handle(handle: Any) -> None:
        try:
            import win32file  # type: ignore[import-not-found]

            win32file.CloseHandle(handle)
        except Exception:
            pass

    def _security_attributes(self) -> Any:
        if self.security_attributes_factory is None:
            raise SandboxInitializationError("Broker named-pipe ACL is not configured")
        return self.security_attributes_factory()
