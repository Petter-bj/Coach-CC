"""Tests for portable runtime-path configuration."""

from __future__ import annotations

import importlib
import stat
from pathlib import Path

import pytest

import src.paths as paths


@pytest.fixture(autouse=True)
def reload_paths_after_test() -> None:
    """Restore module constants after tests that reload with changed env vars."""
    yield
    importlib.reload(paths)


def test_macos_defaults_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRENING_DATA_DIR", raising=False)
    monkeypatch.delenv("TRENING_LOG_DIR", raising=False)
    monkeypatch.delenv("TRENING_CACHE_DIR", raising=False)
    importlib.reload(paths)

    home = Path.home()
    assert paths.APP_SUPPORT == home / "Library" / "Application Support" / "Trening"
    assert paths.LOGS == home / "Library" / "Logs" / "Trening"
    assert paths.CACHES == home / "Library" / "Caches" / "Trening"


def test_absolute_overrides_control_all_runtime_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("TRENING_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TRENING_LOG_DIR", str(log_dir))
    monkeypatch.setenv("TRENING_CACHE_DIR", str(cache_dir))
    importlib.reload(paths)

    assert paths.APP_SUPPORT == data_dir
    assert paths.LOGS == log_dir
    assert paths.CACHES == cache_dir
    assert paths.DB_PATH == data_dir / "health.db"
    assert paths.CREDENTIALS_DIR == data_dir / "credentials"
    assert paths.FIT_FILES_DIR == data_dir / "fit_files"
    assert paths.BACKUPS_DIR == data_dir / "backups"
    assert paths.SCREENSHOT_CACHE_DIR == cache_dir / "strength_screenshots"

    paths.ensure_runtime_dirs()

    for directory in (
        data_dir,
        log_dir,
        cache_dir,
        paths.CREDENTIALS_DIR,
        paths.FIT_FILES_DIR,
        paths.BACKUPS_DIR,
        paths.SCREENSHOT_CACHE_DIR,
    ):
        assert directory.is_dir()
    assert stat.S_IMODE(paths.CREDENTIALS_DIR.stat().st_mode) == 0o700


def test_relative_runtime_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRENING_DATA_DIR", "runtime-data")

    with pytest.raises(ValueError, match="TRENING_DATA_DIR.*absolutt"):
        importlib.reload(paths)
