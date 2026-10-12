"""Tests for config file persistence, corruption recovery, and permissions."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


def test_save_config_file_merges_existing_values_and_preserves_unknown_keys(tmp_path):
    from shelfmark.core.settings_registry import load_config_file, save_config_file

    config_dir = tmp_path
    plugins_dir = config_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "downloads.json").write_text(
        '{"existing": "value", "unknown": {"nested": true}}'
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", config_dir)
        assert save_config_file("downloads", {"existing": "updated", "new": "value"}) is True
        result = load_config_file("downloads")

    assert result == {
        "existing": "updated",
        "new": "value",
        "unknown": {"nested": True},
    }


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("missing", None),
        ("empty", ""),
        ("broken", "{ invalid json }"),
        ("partial", '{"key": "value"'),
    ],
)
def test_load_config_file_returns_empty_dict_for_missing_or_corrupted_files(
    tmp_path: Path,
    filename: str,
    contents: str | None,
):
    from shelfmark.core.settings_registry import load_config_file

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True)
    if contents is not None:
        (plugins_dir / f"{filename}.json").write_text(contents)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)
        result = load_config_file(filename)

    assert result == {}


@pytest.mark.parametrize(
    "tab_name",
    ["../escape", "nested/plugin", "..", "."],
)
def test_get_config_file_path_rejects_path_traversal(tmp_path: Path, tab_name: str):
    from shelfmark.core.settings_registry import _get_config_file_path

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)
        with pytest.raises(ValueError, match="Invalid tab name"):
            _get_config_file_path(tab_name)


def test_is_config_dir_writable_tracks_directory_state(tmp_path: Path):
    from shelfmark.config.env import _is_config_dir_writable

    writable_dir = tmp_path / "writable"
    writable_dir.mkdir()
    missing_dir = tmp_path / "missing"

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", writable_dir)
        assert _is_config_dir_writable() is True

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", missing_dir)
        assert _is_config_dir_writable() is False


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="Permission tests are unreliable as root"
)
def test_save_config_file_returns_false_when_config_dir_is_not_writable(tmp_path: Path):
    from shelfmark.core.settings_registry import save_config_file

    config_dir = tmp_path / "readonly"
    config_dir.mkdir()
    config_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

    try:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", config_dir)
            assert save_config_file("downloads", {"key": "value"}) is False
    finally:
        config_dir.chmod(stat.S_IRWXU)


def test_covers_cache_writability_probe_is_reused(tmp_path: Path):
    """Per-result cover checks must not create and delete a probe file on every call."""
    from unittest.mock import patch

    from shelfmark.config import env

    first_dir = tmp_path / "first"
    first_dir.mkdir()
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    env._writable_probe_cache.clear()

    with (
        pytest.MonkeyPatch.context() as monkeypatch,
        patch.object(env, "_is_config_dir_writable", return_value=True) as probe,
    ):
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", first_dir)
        for _ in range(50):
            assert env._is_config_dir_writable_cached() is True
        assert probe.call_count == 1

        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", second_dir)
        assert env._is_config_dir_writable_cached() is True
        assert probe.call_count == 2

        monkeypatch.setattr(env, "_WRITABLE_PROBE_TTL_SECONDS", 0.0)
        env._writable_probe_cache.clear()
        env._is_config_dir_writable_cached()
        env._is_config_dir_writable_cached()
        assert probe.call_count == 4

    env._writable_probe_cache.clear()
