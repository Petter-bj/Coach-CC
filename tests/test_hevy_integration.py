"""End-to-end test for Hevy → DB-insert uten å treffe nettet.

Stubber ut httpx.get med fixture-respons, kjører HevySource._fetch_workouts
mot in-memory DB og verifiserer at workouts + strength_sessions +
strength_sets er riktig koblet.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.connection import configure
from src.db.migrations import migrate
from src.sources.hevy import HevySource


FIX = Path("tests/fixtures/hevy/raw")


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    yield c
    c.close()


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload


def test_fetch_workouts_inserts_canonical_rows(conn, monkeypatch) -> None:
    payload = json.loads((FIX / "workouts_page.json").read_text())

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResp(payload)

    monkeypatch.setenv("HEVY_API_KEY", "fake-test-key")

    src = HevySource()
    with patch("src.sources.hevy.httpx.get", side_effect=fake_get):
        ins, upd = src._fetch_workouts(conn, since_date="2025-10-01")

    assert ins == 1
    assert upd == 0

    # workouts-rad
    row = conn.execute(
        "SELECT * FROM workouts WHERE source='hevy'"
    ).fetchone()
    assert row["external_id"] == "edb2ff65-0797-46e5-b356-11d62411f031"
    assert row["type"] == "strength_training"
    assert row["duration_sec"] == 2479

    # strength_sessions-rad
    sess = conn.execute(
        "SELECT * FROM strength_sessions WHERE workout_id=?", (row["id"],)
    ).fetchone()
    assert sess is not None

    # 5 sett (Plank-settet droppes fordi reps=null)
    sets = conn.execute(
        "SELECT exercise, set_num, reps, weight_kg, rpe, e1rm_kg "
        "FROM strength_sets WHERE session_id=? ORDER BY exercise, set_num",
        (sess["id"],),
    ).fetchall()
    assert len(sets) == 5

    shoulder_set1 = next(s for s in sets
                         if s["exercise"] == "Shoulder Press (Dumbbell)"
                         and s["set_num"] == 1)
    assert shoulder_set1["reps"] == 6
    assert shoulder_set1["weight_kg"] == 30
    assert shoulder_set1["rpe"] == 8
    assert shoulder_set1["e1rm_kg"] == 36.0


def test_fetch_workouts_idempotent_rerun(conn, monkeypatch) -> None:
    """Å kjøre sync to ganger skal ikke duplisere — andre kjøring er upd=1."""
    payload = json.loads((FIX / "workouts_page.json").read_text())

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResp(payload)

    monkeypatch.setenv("HEVY_API_KEY", "fake-test-key")
    src = HevySource()

    with patch("src.sources.hevy.httpx.get", side_effect=fake_get):
        ins1, upd1 = src._fetch_workouts(conn, since_date="2025-10-01")
        ins2, upd2 = src._fetch_workouts(conn, since_date="2025-10-01")

    assert (ins1, upd1) == (1, 0)
    assert (ins2, upd2) == (0, 1)

    # Fortsatt bare én rad
    count = conn.execute("SELECT COUNT(*) FROM workouts WHERE source='hevy'").fetchone()[0]
    assert count == 1

    # Og fortsatt bare én strength_sessions-rad for denne workout
    sess_count = conn.execute(
        "SELECT COUNT(*) FROM strength_sessions s "
        "JOIN workouts w ON s.workout_id=w.id WHERE w.source='hevy'"
    ).fetchone()[0]
    assert sess_count == 1

    # Sett-antall uendret (5, ikke 10) — delete-and-insert-mønsteret fungerer
    set_count = conn.execute("SELECT COUNT(*) FROM strength_sets").fetchone()[0]
    assert set_count == 5


def test_fetch_workouts_skips_old_workouts(conn, monkeypatch) -> None:
    """Økter eldre enn since_date skal droppes (stopper paginering)."""
    old_payload = {
        "page": 1,
        "page_count": 1,
        "workouts": [{
            "id": "old-workout",
            "title": "Old",
            "start_time": "2024-01-01T10:00:00+00:00",
            "end_time": "2024-01-01T11:00:00+00:00",
            "exercises": [],
        }],
    }

    monkeypatch.setenv("HEVY_API_KEY", "fake-test-key")
    src = HevySource()

    with patch("src.sources.hevy.httpx.get",
               return_value=_FakeResp(old_payload)):
        ins, upd = src._fetch_workouts(conn, since_date="2025-10-01")

    assert (ins, upd) == (0, 0)
    assert conn.execute(
        "SELECT COUNT(*) FROM workouts WHERE source='hevy'"
    ).fetchone()[0] == 0


def test_fetch_workouts_401_is_fatal(conn, monkeypatch) -> None:
    from src.sources.base import FatalError

    monkeypatch.setenv("HEVY_API_KEY", "fake-test-key")
    src = HevySource()

    with patch("src.sources.hevy.httpx.get",
               return_value=_FakeResp({}, status=401)):
        with pytest.raises(FatalError):
            src._fetch_workouts(conn, since_date="2025-10-01")


def test_missing_api_key_is_fatal(conn, monkeypatch) -> None:
    from src.sources.base import FatalError

    monkeypatch.delenv("HEVY_API_KEY", raising=False)
    src = HevySource()

    with pytest.raises(FatalError, match="HEVY_API_KEY"):
        src._fetch_workouts(conn, since_date="2025-10-01")
