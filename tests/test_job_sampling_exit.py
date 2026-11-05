"""A process may exit between Job enumeration and per-PID accounting."""

from __future__ import annotations

import os

import pytest

from backend.sandbox.native_windows.jobs import WindowsJobObject


@pytest.mark.skipif(os.name != "nt", reason="pywin32 accounting")
@pytest.mark.parametrize("method", ["_private_memory", "_handle_count"])
def test_sampling_exited_pid_handles_real_pywin32_error(method, monkeypatch):
    import pywintypes
    import win32api

    def exited(*_args):
        raise pywintypes.error(87, "OpenProcess", "Process already exited")

    monkeypatch.setattr(win32api, "OpenProcess", exited)
    assert getattr(WindowsJobObject, method)(123) == 0


@pytest.mark.skipif(os.name != "nt", reason="pywin32 accounting")
@pytest.mark.parametrize("method", ["_private_memory", "_handle_count"])
@pytest.mark.parametrize("code", [5, 6])
def test_sampling_other_win32_errors_remain_fail_closed(method, code, monkeypatch):
    import pywintypes
    import win32api

    def denied(*_args):
        raise pywintypes.error(code, "OpenProcess", "Accounting unavailable")

    monkeypatch.setattr(win32api, "OpenProcess", denied)
    with pytest.raises(pywintypes.error) as captured:
        getattr(WindowsJobObject, method)(123)
    assert captured.value.winerror == code


@pytest.mark.skipif(os.name != "nt", reason="pywin32 accounting")
def test_sampling_absent_pid_with_real_windows_api():
    assert WindowsJobObject._handle_count(0x7FFFFFFF) == 0
    assert WindowsJobObject._private_memory(0x7FFFFFFF) == 0
