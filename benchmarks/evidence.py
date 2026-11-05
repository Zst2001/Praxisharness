"""Bounded local candidate snapshots and sandbox-only offline regrading."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.domain import redact_sensitive_text
from backend.domain.file_paths import ScopedPaths
from backend.tools.filesystem.paths import workspace_relative_parts

MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 4_194_304
MAX_FILES = 16
_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".git"}
_AUXILIARY = {
    "diff.patch",
    "tools.json",
    "grading.json",
    "trial.json",
    "acceptance.json",
    "feedback.json",
    "independent.json",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scrub(text: str, secret: str = "") -> str:
    return redact_sensitive_text(text.replace(secret, "[REDACTED]") if secret else text)


def safe_value(value, secret: str = "", depth: int = 0):
    if depth > 8:
        return "[DEPTH_LIMIT]"
    if isinstance(value, str):
        return scrub(value, secret)[:4096]
    if isinstance(value, dict):
        return {
            str(k): (
                "[REDACTED]"
                if re.search(r"api.key|authorization|cookie|password|secret", str(k), re.I)
                else safe_value(v, secret, depth + 1)
            )
            for k, v in list(value.items())[:128]
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(v, secret, depth + 1) for v in value[:256]]
    return value if value is None or type(value) in (int, float, bool) else "[UNSUPPORTED]"


def _plain_path(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Evidence paths must not traverse links or reparse points.")


def _write_json(path: Path, value) -> str:
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)
    return digest(data)


def tool_trace(actions, telemetry) -> list[dict]:
    rows = []
    for action in actions[:256]:
        arguments = {
            k: v
            for k, v in action.arguments.items()
            if k in {"cmd", "path", "operation", "session_id", "yield_time_ms"}
        }
        content = action.content or ""
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            payload = {}
        exit_code = payload.get("exit_code") if isinstance(payload, dict) else None
        rows.append(
            safe_value(
                {
                    "call_id": action.call_id,
                    "tool": action.name,
                    "interaction": ("poll" if not action.arguments.get("chars", "") else "stdin")
                    if action.name == "write_stdin"
                    else None,
                    "arguments": arguments,
                    "status": action.status,
                    "failure_code": action.failure_code,
                    "phase": "acceptance" if action.call_id in telemetry.acceptance_calls else "agent",
                    "duration_ms": telemetry.tool_durations.get(action.call_id),
                    "exit_code": exit_code if type(exit_code) is int else None,
                    "result_preview": "[ENVIRONMENT_OUTPUT_OMITTED]"
                    if re.search(r"\b(?:set|env|printenv)\b|environ|Env:", str(arguments.get("cmd", "")), re.I)
                    else content[:8192],
                },
                telemetry.secret,
            )
        )
    return rows


class TrialEvidence:
    def __init__(
        self,
        root: Path,
        workspace: Path,
        trial: dict,
        *,
        suite_sha256: str,
        provenance: dict,
        secret: str = "",
        round_index: int | None = None,
    ) -> None:
        root = Path(os.path.abspath(root))
        _plain_path(root)
        if root == workspace or root.is_relative_to(workspace):
            raise ValueError("Evidence must be outside the agent workspace.")
        for key in ("experiment_id", "task_id", "arm"):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trial[key]):
                raise ValueError("Invalid evidence trial identifier.")
        if type(trial["attempt"]) is not int or not 1 <= trial["attempt"] <= 20:
            raise ValueError("Invalid evidence attempt")
        self.path = root / trial["experiment_id"] / trial["task_id"] / f"{trial['attempt']}-{trial['arm']}"
        self.root = root
        if round_index is not None:
            if type(round_index) is not int or not 1 <= round_index <= 6:
                raise ValueError("Evidence round must be between 1 and 6")
            self.path = self.path / "rounds" / f"{round_index:02d}"
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / "candidate").mkdir()
        self.secret = secret
        self.manifest = {
            "schema_version": 1,
            "trial": trial,
            "suite_sha256": suite_sha256,
            "provenance": provenance,
            "files": [],
            "auxiliary": {},
            "replayable": True,
            "limits": {"files": MAX_FILES, "file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES},
        }

    def capture(self, workspace: Path, task: dict, trace: list[dict], *, previous: TrialEvidence | None = None) -> None:
        names = {item["path"] for item in (*task.get("files", []), *task["demo_outputs"])}
        pending, visited = [workspace], 0
        while pending and visited < 256:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 256:
                        self.manifest["replayable"] = False
                        break
                    path = Path(entry.path)
                    if entry.name in _CACHE_DIRS:
                        continue
                    if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                        if getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                            self.manifest["replayable"] = False
                        else:
                            pending.append(path)
                    else:
                        names.add(path.relative_to(workspace).as_posix())
        total, diffs = 0, []
        seeds = {item["path"]: item["content"] for item in task.get("files", [])}
        if previous is not None:
            seeds = {}
            for record in previous.manifest["files"]:
                if record["status"] == "captured":
                    source = previous.path / "candidate" / record["path"]
                    _plain_path(source)
                    with source.open("rb") as stream:
                        data = stream.read(MAX_FILE_BYTES + 1)
                    if digest(data) != record["stored_sha256"] or len(data) > MAX_FILE_BYTES:
                        raise ValueError("Previous snapshot integrity check failed")
                    seeds[record["path"]] = data.decode("utf-8")
            names.update(seeds)
        self.manifest["diff_base"] = "previous_round" if previous is not None else "initial_fixture"
        if len(names) > MAX_FILES or pending:
            self.manifest["replayable"] = False
        for name in sorted(names)[:MAX_FILES]:
            workspace_relative_parts(name)
            record = {"path": name, "status": "missing"}
            try:
                source = workspace / name
                _plain_path(source)
                source = ScopedPaths(workspace).resolve(name)
                if source.is_file():
                    if source.stat().st_nlink > 1:
                        raise ValueError("Hard-linked candidate files are not captured")
                    with source.open("rb") as stream:
                        data = stream.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES or total + len(data) > MAX_TOTAL_BYTES:
                        raise ValueError("Candidate exceeds evidence byte limits")
                    text = data.decode("utf-8")
                    stored = scrub(text, self.secret).encode("utf-8")
                    if len(stored) > MAX_FILE_BYTES or total + max(len(data), len(stored)) > MAX_TOTAL_BYTES:
                        raise ValueError("Redacted candidate exceeds evidence byte limits")
                    target = self.path / "candidate" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(stored)
                    record.update(
                        status="captured",
                        source_sha256=digest(data),
                        stored_sha256=digest(stored),
                        bytes=len(stored),
                        redacted=stored != data,
                    )
                    total += max(len(data), len(stored))
                    if stored != data:
                        self.manifest["replayable"] = False
                    diff = difflib.unified_diff(
                        scrub(seeds.get(name, ""), self.secret)[:8192].splitlines(True),
                        stored.decode("utf-8")[:8192].splitlines(True),
                        fromfile="before/" + name,
                        tofile="after/" + name,
                    )
                    diffs.append("".join(diff)[:8192])
                elif source.exists():
                    raise ValueError("Candidate is not a regular file")
                elif name in seeds:
                    diffs.append(
                        "".join(
                            difflib.unified_diff(
                                scrub(seeds[name], self.secret)[:8192].splitlines(True),
                                [],
                                fromfile="before/" + name,
                                tofile="after/" + name,
                            )
                        )[:8192]
                    )
            except (OSError, ValueError, UnicodeError) as exc:
                record.update(status="omitted", reason=type(exc).__name__)
                self.manifest["replayable"] = False
            self.manifest["files"].append(record)
        diff_bytes = "".join(diffs).encode("utf-8")[:65536]
        (self.path / "diff.patch").write_bytes(diff_bytes)
        self.manifest["auxiliary"]["diff.patch"] = digest(diff_bytes)
        self.manifest["auxiliary"]["tools.json"] = _write_json(self.path / "tools.json", trace)
        self.save_manifest()

    def save_manifest(self) -> dict:
        manifest = self.path / "manifest.json"
        _plain_path(manifest)
        data = json.dumps(self.manifest, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        manifest.write_bytes(data)
        return {
            "manifest": str(manifest.resolve()),
            "manifest_sha256": digest(data),
            "replayable": self.manifest["replayable"],
        }

    def attach(self, name: str, value) -> None:
        if name not in _AUXILIARY:
            raise ValueError("Unexpected evidence attachment")
        self.manifest["auxiliary"][name] = _write_json(self.path / name, safe_value(value, self.secret))
        self.save_manifest()

    def finish(self, row: dict) -> dict:
        for name, value in (("grading.json", row["grading"]), ("trial.json", row)):
            self.attach(name, value)
        return self.save_manifest()


def regrade(manifest_path: Path, suite_path: Path, *, sandbox_launcher=None, round_index: int | None = None) -> dict:
    from .harness import _discover_sandbox, load_suite
    from .harness_grading import grade
    from .provenance import source_provenance

    _plain_path(manifest_path)
    with manifest_path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Evidence manifest too large")
    manifest = json.loads(data)
    if round_index is not None:
        if type(round_index) is not int or not 1 <= round_index <= 6:
            raise ValueError("Invalid round index")
        item = next((r for r in manifest.get("rounds", []) if r["round"] == round_index), None)
        if item is None:
            raise ValueError("Evidence round not found")
        child = manifest_path.parent / "rounds" / f"{round_index:02d}" / "manifest.json"
        _plain_path(child)
        with child.open("rb") as stream:
            child_data = stream.read(MAX_FILE_BYTES + 1)
        if len(child_data) > MAX_FILE_BYTES or digest(child_data) != item["manifest_sha256"]:
            raise ValueError("Round manifest integrity check failed")
        return regrade(child, suite_path, sandbox_launcher=sandbox_launcher)
    if manifest.get("schema_version") != 1 or not manifest.get("replayable"):
        raise ValueError("Evidence is incomplete or redacted; exact regrading refused")
    suite, suite_hash = load_suite(suite_path)
    if suite_hash != manifest["suite_sha256"]:
        raise ValueError("Suite fingerprint differs from original evidence")
    task = next((item for item in suite["tasks"] if item["id"] == manifest["trial"]["task_id"]), None)
    if task is None:
        raise ValueError("Evidence task is absent from the selected suite")
    if not isinstance(manifest["files"], list) or len(manifest["files"]) > MAX_FILES:
        raise ValueError("Invalid evidence file count")
    for name, expected in manifest.get("auxiliary", {}).items():
        if name not in _AUXILIARY:
            raise ValueError("Unexpected evidence auxiliary file")
        auxiliary = manifest_path.parent / name
        _plain_path(auxiliary)
        with auxiliary.open("rb") as stream:
            content = stream.read(MAX_TOTAL_BYTES + 1)
        if len(content) > MAX_TOTAL_BYTES or digest(content) != expected:
            raise ValueError("Evidence auxiliary integrity check failed")
    with TemporaryDirectory(prefix="praxis-regrade-") as directory:
        workspace = Path(directory)
        total, seen = 0, set()
        for record in manifest["files"]:
            name = record["path"]
            workspace_relative_parts(name)
            if name in seen:
                raise ValueError("Duplicate evidence path")
            seen.add(name)
            if record["status"] == "missing":
                continue
            if record["status"] != "captured" or record.get("redacted"):
                raise ValueError("Candidate is not losslessly captured")
            source = manifest_path.parent / "candidate" / name
            _plain_path(source)
            with source.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            total += len(content)
            if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or digest(content) != record["stored_sha256"]:
                raise ValueError("Evidence integrity or byte limit check failed")
            target = ScopedPaths(workspace).resolve(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        result = grade(
            workspace,
            task,
            sandbox_launcher=sandbox_launcher if sandbox_launcher is not None else _discover_sandbox(),
            sandbox_config={"network_mode": "no_network", "proxy_port": 17831},
        )
    return {
        "mode": "offline_regrade",
        "external_model_requests": 0,
        "original_trial": manifest["trial"],
        "evidence_sha256": digest(data),
        "suite_sha256": suite_hash,
        "provenance": source_provenance(),
        "grading": result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--round", type=int, dest="round_index", help="Regrade a saved pre-acceptance round")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Regrade output already exists")
    report = regrade(args.manifest, args.suite, round_index=args.round_index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(args.output, report)
    print(json.dumps({"mode": report["mode"], "status": report["grading"]["status"], "external_model_requests": 0}))


if __name__ == "__main__":
    main()
