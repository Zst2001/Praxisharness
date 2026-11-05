"""Bounded inspection of declared/touched files; never scans a whole workspace."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from backend.domain.file_paths import ScopedPaths

if TYPE_CHECKING:
    from .core.context import RuntimeState

MAX_FILES = 64
MAX_FILE_BYTES = 262_144


def workspace_fingerprints(state: RuntimeState) -> dict[str, str]:
    """Hash explicit paths without storing contents. Commands/unmentioned files
    are outside this observation boundary; unreadable entries remain unchecked.
    """
    run = state.current_run
    if run is None or not state.workspace_root:
        return {}
    paths = ScopedPaths(Path(state.workspace_root), Path(state.project_cwd) if state.project_cwd else None)
    candidates = (
        {name for name in (run.checkpoint.workspace_files or {}) if not name.startswith("<")}
        if run.checkpoint
        else set()
    )
    if run.acceptance:
        candidates.update(check.path for check in run.acceptance.contract.checks if hasattr(check, "path"))
    actions = [*run.actions, *(state.active_message.tool_messages if state.active_message else [])]
    for action in actions:
        if action.name not in {"read_file", "file_operation"}:
            continue
        value = action.arguments.get("path")
        if isinstance(value, str):
            candidates.add(value)
    normalized: set[str] = set()
    unavailable = False
    for value in candidates:
        try:
            normalized.add(paths.format(paths.resolve(value)))
        except (OSError, ValueError, RuntimeError):
            unavailable = True
    result: dict[str, str] = {"<unavailable-path>": "unchecked:unavailable"} if unavailable else {}
    for value in sorted(normalized)[:MAX_FILES]:
        try:
            target = paths.resolve(value)
            name = paths.format(target)
            info = target.stat()
            if not stat.S_ISREG(info.st_mode):
                result[name] = "unchecked:non_regular"
                continue
            with target.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            result[name] = (
                "unchecked:too_large" if len(raw) > MAX_FILE_BYTES else "sha256:" + hashlib.sha256(raw).hexdigest()
            )
        except FileNotFoundError:
            result[paths.format(target)] = "missing"
        except (OSError, ValueError, RuntimeError):
            result["<unavailable-path>"] = "unchecked:unavailable"
    if len(normalized) > MAX_FILES:
        result["<file-limit>"] = "unchecked:file_limit"
    return result
