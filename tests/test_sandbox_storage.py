from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.api.state import WebAppState
from backend.configuration import ClientPaths, ConfigurationError, plain_directory
from backend.storage.settings import LocalSettingsStore, normalize_sandbox_config
from backend.storage.sqlite import SQLiteSessionStore


def test_config_roots_roundtrip_and_unrelated_update_preserves_paths(tmp_path):
    paths = ClientPaths(tmp_path / "data")
    paths.ensure()
    store = LocalSettingsStore(paths.state_db, paths.config_file)
    roots = {"temp_root": str(tmp_path / "scratch"), "workspace_root": str(tmp_path / "workspaces")}
    store.update_sandbox_config(roots)
    store.update_sandbox_config({"network_mode": "no_network"})
    assert {k: store.sandbox_config()[k] for k in roots} == roots
    state = WebAppState(paths.root)
    try:
        assert state.paths.workspace_root == Path(roots["workspace_root"])
    finally:
        state.close()


@pytest.mark.parametrize("value", ["relative/root", "../outside", 123, True])
def test_invalid_roots_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_sandbox_config(values={"temp_root": value})


def test_storage_file_and_links_are_rejected(tmp_path, monkeypatch):
    file = tmp_path / "file"
    file.write_text("keep")
    with pytest.raises(ConfigurationError):
        plain_directory(file)
    from backend.domain.file_paths import ScopedPaths

    def reject(*_args):
        raise ValueError("reparse point")

    monkeypatch.setattr(ScopedPaths, "reject_links", reject)
    with pytest.raises(ConfigurationError):
        plain_directory(tmp_path / "junction")


def test_external_session_pin_survives_restart_and_config_changes(tmp_path):
    paths = ClientPaths(tmp_path / "data", workspace_root=tmp_path / "external")
    paths.ensure()
    metadata = paths.ensure_session("one")
    workspace = paths.session_workspace("one")
    (workspace / "keep.txt").write_text("user content")
    assert metadata.parent == paths.runtime_dir
    assert workspace == tmp_path / "external/one/workspace"
    assert paths.session_db("one").exists()
    for restored in (ClientPaths(paths.root), ClientPaths(paths.root, tmp_path / "different")):
        restored.ensure_session("one")
        assert restored.session_workspace("one") == workspace
        assert (workspace / "keep.txt").read_text() == "user content"
    assert paths.session_uploads("one").is_relative_to(workspace)


def test_existing_session_workspace_is_preserved_when_new_root_enabled(tmp_path):
    original = ClientPaths(tmp_path / "data")
    original.ensure()
    original.ensure_session("old")
    (original.session_workspace("old") / "existing.txt").write_text("keep")
    configured = ClientPaths(original.root, tmp_path / "external")
    configured.ensure_session("old")
    configured.ensure_session("new")
    assert configured.session_workspace("old") == original.session_workspace("old")
    assert configured.session_workspace("new").is_relative_to(tmp_path / "external")


def test_external_workspace_fork_and_purge_leave_other_sessions(tmp_path):
    from backend.api.user_data import copy_session_files

    paths = ClientPaths(tmp_path / "data", tmp_path / "external")
    paths.ensure()
    store = SQLiteSessionStore(paths)
    first, second = store.create_session("first"), store.create_session("second")
    (paths.session_workspace(first.session_id) / "result.txt").write_text("copied")
    copy_session_files(paths, first.session_id, second.session_id)
    assert (paths.session_workspace(second.session_id) / "result.txt").read_text() == "copied"
    removed_workspace = paths.session_workspace(first.session_id)
    store.delete_session(first.session_id)
    store.purge_session(first.session_id)
    assert not removed_workspace.exists() and not paths.session_root(first.session_id).exists()
    assert paths.session_workspace(second.session_id).is_dir()


def test_unsafe_location_refuses_resolution_and_deletion(tmp_path):
    paths = ClientPaths(tmp_path / "data", tmp_path / "external")
    paths.ensure()
    metadata = paths.ensure_session("one")
    (metadata / "workspace-location.json").write_text(json.dumps({"schema_version": 1, "root": "../elsewhere"}))
    with pytest.raises(ConfigurationError):
        paths.session_workspace("one")
    with pytest.raises(ConfigurationError):
        paths.remove_session_data("one")
    assert metadata.exists()
