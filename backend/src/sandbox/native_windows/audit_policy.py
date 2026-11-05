"""Account-scoped Windows audit policy, using native APIs rather than auditpol output."""

from __future__ import annotations

import ctypes
import uuid
from contextlib import contextmanager
from ctypes import wintypes

from ..errors import SandboxInitializationError


class SandboxAuditUnavailable(SandboxInitializationError):
    def __init__(self, message: str = "Windows sandbox permission auditing is unavailable.") -> None:
        super().__init__(message)


class _Guid(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str) -> _Guid:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _Policy(ctypes.Structure):
    _fields_ = [("subcategory", _Guid), ("flags", wintypes.ULONG), ("category", _Guid)]


_FILE_SYSTEM = "0cce921d-69ae-11d9-bed3-505054503030"
_REGISTRY = "0cce921e-69ae-11d9-bed3-505054503030"


def _has_failure_ace(acl: bytes, sid: bytes, mask: int) -> bool:
    if not acl:
        return False
    if len(acl) < 8:
        raise SandboxAuditUnavailable("Windows returned an invalid audit ACL.")
    count = int.from_bytes(acl[4:6], "little")
    offset = 8
    for _ in range(count):
        if offset + 8 > len(acl):
            raise SandboxAuditUnavailable("Windows returned an invalid audit ACE.")
        size = int.from_bytes(acl[offset + 2 : offset + 4], "little")
        if size < 8 or offset + size > len(acl):
            raise SandboxAuditUnavailable("Windows returned an invalid audit ACE size.")
        ace = acl[offset : offset + size]
        if ace[0] == 2 and ace[1] & 0x80 and ace[8:] == sid and int.from_bytes(ace[4:8], "little") & mask == mask:
            return True
        offset += size
    return False


@contextmanager
def security_privilege():
    import win32api
    import win32event
    import win32security

    mutex = win32event.CreateMutex(None, False, "Global\\PraxisSandboxAuditPolicy")
    acquired = False
    token = None
    previous = None
    try:
        result = win32event.WaitForSingleObject(mutex, 10000)
        if result not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            raise SandboxAuditUnavailable("Windows audit policy is busy.")
        acquired = True
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY
        )
        privilege = win32security.LookupPrivilegeValue(None, "SeSecurityPrivilege")
        win32api.SetLastError(0)
        previous = win32security.AdjustTokenPrivileges(token, False, [(privilege, win32security.SE_PRIVILEGE_ENABLED)])
        error = win32api.GetLastError()
        if error:
            raise SandboxAuditUnavailable(
                f"Windows permission auditing requires SeSecurityPrivilege (Windows error {error})."
            )
        yield
    finally:
        if previous is not None:
            win32security.AdjustTokenPrivileges(token, False, previous)
        if token is not None:
            token.Close()
        if acquired:
            win32event.ReleaseMutex(mutex)
        mutex.Close()


class WindowsAuditPolicy:
    def __init__(self) -> None:
        self.api = ctypes.WinDLL("advapi32", use_last_error=True)
        self.api.AuditQueryPerUserPolicy.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_Guid),
            wintypes.ULONG,
            ctypes.POINTER(ctypes.POINTER(_Policy)),
        ]
        self.api.AuditSetPerUserPolicy.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Policy), wintypes.ULONG]
        self.api.AuditQuerySystemPolicy.argtypes = [
            ctypes.POINTER(_Guid),
            wintypes.ULONG,
            ctypes.POINTER(ctypes.POINTER(_Policy)),
        ]
        self.api.AuditSetSystemPolicy.argtypes = [ctypes.POINTER(_Policy), wintypes.ULONG]
        self.api.AuditQueryGlobalSaclW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        self.api.AuditSetGlobalSaclW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
        self.api.AuditFree.argtypes = [ctypes.c_void_p]
        self.api.AuditFree.restype = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        for name in (
            "AuditQueryPerUserPolicy",
            "AuditSetPerUserPolicy",
            "AuditQueryGlobalSaclW",
            "AuditSetGlobalSaclW",
            "AuditQuerySystemPolicy",
            "AuditSetSystemPolicy",
        ):
            getattr(self.api, name).restype = ctypes.c_ubyte
        self.api.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        self.api.AddAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        self.api.AddAuditAccessAceEx.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
        ]

    @staticmethod
    def _check(ok: object, operation: str = "audit API") -> None:
        if not ok:
            raise SandboxAuditUnavailable(f"Windows {operation} failed (code {ctypes.get_last_error()}).")

    def ensure(self, account_sid: str) -> None:
        import win32security

        sid = win32security.ConvertStringSidToSid(account_sid)
        raw_sid = ctypes.create_string_buffer(bytes(sid))
        with security_privilege():
            self._ensure_system_policy()
            self._ensure_user_policy(raw_sid)
            self._ensure_global_sacl("File", raw_sid, 0x001F01FF)
            self._ensure_global_sacl("Key", raw_sid, 0x000F003F)

    def _ensure_user_policy(self, sid) -> None:
        guids = (_Guid * 2)(_Guid.parse(_FILE_SYSTEM), _Guid.parse(_REGISTRY))
        existing = ctypes.POINTER(_Policy)()
        ctypes.set_last_error(0)
        found = self.api.AuditQueryPerUserPolicy(sid, guids, 2, ctypes.byref(existing))
        # ERROR_FILE_NOT_FOUND means this account has no per-user policy yet.
        # Bootstrap the required failure auditing; every other error still blocks.
        if not found and ctypes.get_last_error() != 2:
            self._check(found, "AuditQueryPerUserPolicy")
        if found and not existing:
            raise SandboxAuditUnavailable("Windows returned no per-user audit policy.")
        try:
            updated = (_Policy * 2)()
            changed = False
            for index in range(2):
                updated[index].subcategory = guids[index]
                # Include failures; preserve success policy. Do not change system-wide auditing.
                previous = existing[index].flags if existing else 0
                updated[index].flags = (previous & ~0x18) | 0x04
                changed |= updated[index].flags != previous
            if changed:
                self._check(self.api.AuditSetPerUserPolicy(sid, updated, 2), "AuditSetPerUserPolicy")
        finally:
            if existing:
                self.api.AuditFree(existing)

    def _ensure_system_policy(self) -> None:
        guids = (_Guid * 6)(
            *(
                _Guid.parse(value)
                for value in (
                    _FILE_SYSTEM,
                    _REGISTRY,
                    "0cce922b-69ae-11d9-bed3-505054503030",
                    "0cce9216-69ae-11d9-bed3-505054503030",
                    "0cce9226-69ae-11d9-bed3-505054503030",
                    "0cce922c-69ae-11d9-bed3-505054503030",
                )
            )
        )
        existing = ctypes.POINTER(_Policy)()
        self._check(self.api.AuditQuerySystemPolicy(guids, 6, ctypes.byref(existing)), "AuditQuerySystemPolicy")
        try:
            if not existing:
                raise SandboxAuditUnavailable("Windows returned no system audit policy.")
            changed = False
            for index, flags in enumerate((2, 2, 1, 1, 2, 1)):
                updated = (existing[index].flags & ~4) | flags
                changed |= updated != existing[index].flags
                existing[index].flags = updated
            if changed:
                self._check(self.api.AuditSetSystemPolicy(existing, 6), "AuditSetSystemPolicy")
        finally:
            self.api.AuditFree(existing)

    def _ensure_global_sacl(self, object_type: str, sid, mask: int) -> None:
        pointer = ctypes.c_void_p()
        ctypes.set_last_error(0)
        found = self.api.AuditQueryGlobalSaclW(object_type, ctypes.byref(pointer))
        # A fresh installation may have no global resource SACL. Still require
        # creating and reading back the account-specific audit ACE below.
        if not found and ctypes.get_last_error() != 2:
            self._check(found, "AuditQueryGlobalSaclW")
        try:
            old = (
                ctypes.string_at(pointer, ctypes.c_ushort.from_address(pointer.value + 2).value)
                if pointer.value
                else b""
            )
            count = int.from_bytes(old[4:6], "little") if old else 0
            sid_bytes = bytes(sid)[:-1]
            if _has_failure_ace(old, sid_bytes, mask):
                return
            offset = 8
            for _ in range(count):
                offset += int.from_bytes(old[offset + 2 : offset + 4], "little")
            # Copy every existing ACE, then add a failure-only ACE for this sandbox account.
            buffer = ctypes.create_string_buffer(max(8, len(old)) + 8 + len(sid_bytes))
            self._check(self.api.InitializeAcl(buffer, len(buffer), 4), "InitializeAcl")
            if count:
                ace_bytes = ctypes.create_string_buffer(old[8:offset])
                self._check(self.api.AddAce(buffer, 4, 0xFFFFFFFF, ace_bytes, offset - 8), "AddAce")
            self._check(self.api.AddAuditAccessAceEx(buffer, 4, 0, mask, sid, False, True), "AddAuditAccessAceEx")
            self._check(self.api.AuditSetGlobalSaclW(object_type, buffer), "AuditSetGlobalSaclW")
            verified = ctypes.c_void_p()
            self._check(self.api.AuditQueryGlobalSaclW(object_type, ctypes.byref(verified)), "AuditQueryGlobalSaclW")
            try:
                persisted = (
                    ctypes.string_at(verified, ctypes.c_ushort.from_address(verified.value + 2).value)
                    if verified.value
                    else b""
                )
                if not _has_failure_ace(persisted, sid_bytes, mask):
                    raise SandboxAuditUnavailable("Windows did not persist the required global audit SACL.")
            finally:
                if verified.value:
                    self.kernel.LocalFree(verified)
        finally:
            if pointer.value:
                self.kernel.LocalFree(pointer)
