"""Content-addressed source/dependency identifiers without credentials or remotes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
_ROOT_FILES = (
    "AGENTS.md",
    ".gitignore",
    "pyproject.toml",
    "uv.lock",
    "backend/pyproject.toml",
    "LICENSE",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
)
_FOLDERS = ("backend/src", "frontend/src", "tests", "benchmarks", ".github")
_SUFFIXES = {
    ".py",
    ".json",
    ".ts",
    ".js",
    ".cjs",
    ".mjs",
    ".tsx",
    ".css",
    ".toml",
    ".yml",
    ".yaml",
    ".md",
    ".txt",
    ".html",
    ".sql",
    ".j2",
    ".svg",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest(root: Path = SOURCE_ROOT) -> dict[str, str]:
    """Identify allowlisted code, prompts and build inputs, including local edits."""
    files = {root / name for name in _ROOT_FILES if (root / name).is_file()}
    for name in _FOLDERS:
        folder = root / name
        for directory, names, filenames in os.walk(folder, followlinks=False):
            names[:] = [
                name
                for name in names
                if name not in {"__pycache__", "node_modules", "output", "artifacts"}
                and not (Path(directory) / name).is_symlink()
                and not getattr((Path(directory) / name).stat(), "st_file_attributes", 0) & 0x400
            ]
            files.update(
                path for name in filenames if (path := Path(directory) / name).is_file() and path.suffix in _SUFFIXES
            )
    return {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(files)}


def source_provenance(root: Path = SOURCE_ROOT) -> dict:
    """The manifest works with an uncommitted archive; HEAD alone is insufficient."""
    manifest = source_manifest(root)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    head, dirty = None, None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=5
        )
        if result.returncode == 0:
            head = result.stdout.strip()
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if result.returncode == 0:
            dirty = bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "source_sha256": digest,
        "source_file_count": len(manifest),
        "git_head": head,
        "git_dirty": dirty,
        "dependencies": {name: manifest.get(name) for name in ("uv.lock", "frontend/package-lock.json")},
    }
