from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from backend.sandbox.native_windows.audit_policy import SandboxAuditUnavailable
from backend.sandbox.native_windows.permission_audit import CommandAudit, permission_denial

SID = "S-1-5-21-1-2-3-1001"
LOGON = 0x100000002


def security_event(*, pid=123, logon=LOGON, sid=SID, record=11, failure=True, event_id=4656):
    event = ET.Element("Event", xmlns="http://schemas.microsoft.com/win/2004/08/events/event")
    system = ET.SubElement(event, "System")
    ET.SubElement(system, "Provider", Guid="{54849625-5478-4994-a5ba-3e3b0328c30d}")
    for name, value in {
        "EventID": event_id,
        "EventRecordID": record,
        "Channel": "Security",
        "Keywords": "0x8010000000000000" if failure else "0x8020000000000000",
    }.items():
        ET.SubElement(system, name).text = str(value)
    data = ET.SubElement(event, "EventData")
    fields = {
        "SubjectUserSid": sid,
        "SubjectLogonId": hex(logon),
        "ProcessId": hex(pid),
        "ObjectType": "File",
        "ObjectName": "C:/private/proof.txt",
        "AccessMask": "0x120089",
    }
    for name, value in fields.items():
        ET.SubElement(data, "Data", Name=name).text = value
    # Rendered text is irrelevant, including text deliberately contradicting the event fields.
    ET.SubElement(event, "RenderingInfo").text = "Access denied. Permission denied. Success."
    return ET.tostring(event, encoding="unicode")


@pytest.mark.parametrize(
    "change", [dict(logon=LOGON + 1), dict(sid=SID + "0"), dict(record=10), dict(failure=False), dict(event_id=4663)]
)
def test_rejects_other_command_old_success_and_wrong_event_records(change):
    assert permission_denial(security_event(**change), account_sid=SID, logon_id=LOGON, after_record=10) is None


def test_accepts_root_and_child_processes_only_in_the_reserved_logon():
    results = [
        permission_denial(security_event(pid=pid), account_sid=SID, logon_id=LOGON, after_record=10)
        for pid in (123, 456)
    ]
    assert [entry["process_id"] for entry in results] == [123, 456]


def test_security_log_reads_enable_broker_audit_privilege(monkeypatch):
    from contextlib import contextmanager

    from backend.sandbox.native_windows import permission_audit as implementation

    events = []

    @contextmanager
    def privilege():
        events.append("enabled")
        yield
        events.append("restored")

    monkeypatch.setattr(implementation, "security_privilege", privilege)
    source = object.__new__(implementation.SecurityEventSource)

    def read(*_args, **_kwargs):
        assert events == ["enabled"]
        return ["event"]

    source._read_events = read
    assert source._events("*") == ["event"]
    assert events == ["enabled", "restored"]


def test_policy_uses_advanced_audit_api_without_lsa_write_access(monkeypatch):
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    from backend.sandbox.native_windows import audit_policy as implementation

    monkeypatch.setitem(sys.modules, "win32security", SimpleNamespace(ConvertStringSidToSid=lambda _sid: b"sid"))
    monkeypatch.setattr(implementation, "security_privilege", nullcontext)
    policy = object.__new__(implementation.WindowsAuditPolicy)
    calls = []
    policy._ensure_system_policy = lambda: calls.append("system")
    policy._ensure_user_policy = lambda _sid: calls.append("user")
    policy._ensure_global_sacl = lambda kind, *_args: calls.append(kind)
    policy.ensure(SID)
    assert calls == ["system", "user", "File", "Key"]


@pytest.mark.parametrize("query_error", [2, 5, 87])
def test_missing_user_policy_is_created_but_permission_errors_still_block(monkeypatch, query_error):
    import ctypes
    from types import SimpleNamespace

    from backend.sandbox.native_windows import audit_policy as implementation

    monkeypatch.setattr(ctypes, "set_last_error", lambda _: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: query_error, raising=False)
    changes = []
    policy = object.__new__(implementation.WindowsAuditPolicy)
    policy.api = SimpleNamespace(
        AuditQueryPerUserPolicy=lambda *_: False,
        AuditSetPerUserPolicy=lambda _sid, values, count: (
            changes.append([values[i].flags for i in range(count)]) or True
        ),
        AuditFree=lambda _: pytest.fail("No query buffer was allocated"),
    )
    if query_error == 2:
        policy._ensure_user_policy(ctypes.create_string_buffer(b"sid"))
        assert changes == [[4, 4]]
    else:
        with pytest.raises(SandboxAuditUnavailable, match=f"code {query_error}"):
            policy._ensure_user_policy(ctypes.create_string_buffer(b"sid"))
        assert changes == []


def test_existing_user_policy_preserves_success_and_frees_query_buffer(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    from backend.sandbox.native_windows import audit_policy as implementation

    monkeypatch.setattr(ctypes, "set_last_error", lambda _: None, raising=False)
    existing = (implementation._Policy * 2)()
    existing[0].flags, existing[1].flags = 0x11, 0x08
    events = []

    def query(_sid, _guids, _count, destination):
        ctypes.cast(destination, ctypes.POINTER(ctypes.POINTER(implementation._Policy)))[0] = ctypes.cast(
            existing, ctypes.POINTER(implementation._Policy)
        )
        return True

    policy = object.__new__(implementation.WindowsAuditPolicy)
    policy.api = SimpleNamespace(
        AuditQueryPerUserPolicy=query,
        AuditSetPerUserPolicy=lambda _sid, values, count: (
            events.append([values[i].flags for i in range(count)]) or True
        ),
        AuditFree=lambda _: events.append("free"),
    )
    policy._ensure_user_policy(ctypes.create_string_buffer(b"sid"))
    assert events == [[5, 4], "free"]


@pytest.mark.parametrize("query_error", [2, 5])
def test_global_audit_sacl_initialization_requires_persisted_account_ace(monkeypatch, query_error):
    import ctypes
    from types import SimpleNamespace

    from backend.sandbox.native_windows import audit_policy as implementation

    monkeypatch.setattr(ctypes, "set_last_error", lambda _: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: query_error, raising=False)
    sid_bytes = b"fixture-sid"
    ace = bytes([2, 0x80]) + (8 + len(sid_bytes)).to_bytes(2, "little") + (7).to_bytes(4, "little") + sid_bytes
    acl = bytes([4, 0]) + (8 + len(ace)).to_bytes(2, "little") + bytes([1, 0, 0, 0]) + ace
    persisted = ctypes.create_string_buffer(acl)
    events = []

    def query(_kind, pointer):
        events.append("query")
        if events.count("query") == 1:
            return False
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(persisted)
        return True

    policy = object.__new__(implementation.WindowsAuditPolicy)
    policy.api = SimpleNamespace(
        AuditQueryGlobalSaclW=query,
        InitializeAcl=lambda *_: True,
        AddAuditAccessAceEx=lambda *_: True,
        AuditSetGlobalSaclW=lambda *_: events.append("set") or True,
    )
    policy.kernel = SimpleNamespace(LocalFree=lambda _: events.append("free"))
    if query_error == 2:
        policy._ensure_global_sacl("File", ctypes.create_string_buffer(sid_bytes), 7)
        assert events == ["query", "set", "query", "free"]
    else:
        with pytest.raises(SandboxAuditUnavailable, match="code 5"):
            policy._ensure_global_sacl("File", ctypes.create_string_buffer(sid_bytes), 7)
        assert events == ["query"]


def test_readback_requires_a_failure_ace_for_the_same_account_and_mask():
    from backend.sandbox.native_windows.audit_policy import _has_failure_ace

    sid = b"fixture-sid"
    ace = bytes([2, 0x80]) + (8 + len(sid)).to_bytes(2, "little") + (7).to_bytes(4, "little") + sid
    acl = bytes([4, 0]) + (8 + len(ace)).to_bytes(2, "little") + bytes([1, 0, 0, 0]) + ace
    assert _has_failure_ace(acl, sid, 7)
    assert not _has_failure_ace(acl, b"another-sid", 7)
    assert not _has_failure_ace(acl, sid, 15)
    with pytest.raises(SandboxAuditUnavailable):
        _has_failure_ace(acl[:-1], sid, 7)


def test_audit_source_failure_is_not_treated_as_no_permission_denial():
    class Unavailable:
        def latest_record(self):
            raise SandboxAuditUnavailable()

    with pytest.raises(SandboxAuditUnavailable):
        CommandAudit(Unavailable(), SID, LOGON, 10).collect(timeout=0)


def test_reused_pid_from_another_command_is_not_evidence():
    class Source:
        def latest_record(self):
            return 13

        def lifecycle(self, _after_record, _logon_id):
            event = ET.fromstring(security_event(event_id=4634, failure=False))
            data = event.find("{*}EventData")
            ET.SubElement(
                data, "{http://schemas.microsoft.com/win/2004/08/events/event}Data", Name="TargetUserSid"
            ).text = SID
            ET.SubElement(
                data, "{http://schemas.microsoft.com/win/2004/08/events/event}Data", Name="TargetLogonId"
            ).text = hex(LOGON)
            return [ET.tostring(event, encoding="unicode")]

        def denials(self, _after_record, _logon_id):
            return [security_event(pid=123, logon=LOGON + 1), security_event(pid=456, record=12)]

    records = CommandAudit(Source(), SID, LOGON, 10, root_pid=456).collect(timeout=0)
    assert len(records) == 1
    assert records[0]["process_id"] == 456


@pytest.mark.parametrize("overwritten", [False, True])
def test_missing_audit_completion_or_checkpoint_fails_closed(overwritten):
    class Source:
        def latest_record(self):
            return 20

        def record_time(self, _record):
            return None

        def lifecycle(self, _after, _logon):
            return []

        def denials(self, _after, _logon):
            return []

    scope = CommandAudit(Source(), SID, LOGON, 10, root_pid=123, start_time="original" if overwritten else None)
    with pytest.raises(SandboxAuditUnavailable):
        scope.collect(timeout=0)
