"""Tester for den separate Mac → VPS backup-installasjonen."""

from __future__ import annotations

import pytest

from launchd.install_vps_backup import _script, _validate_host


def test_script_reads_only_finished_vps_backups() -> None:
    script = _script("100.123.150.88")

    assert "100.123.150.88" in script
    assert "/var/lib/trening/backups/" in script
    assert "offsite-vps-backups" in script
    assert "credentials" not in script
    assert "--ignore-existing" in script


@pytest.mark.parametrize("host", ["", "host name", "host;rm", "host/../../x"])
def test_rejects_unsafe_host_values(host: str) -> None:
    with pytest.raises(ValueError):
        _validate_host(host)
