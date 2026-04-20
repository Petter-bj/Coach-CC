"""FIT-parser-tester mot ekte FIT-fixtures fra Garmin (ZIP) og Concept2 (rå)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.fit_parser import parse_fit_to_samples

# Fixtures ligger i ~/Library/Application Support/Trening/fit_files/ etter spike-kjøring
APP = Path.home() / "Library" / "Application Support" / "Trening" / "fit_files"
GARMIN_FIT = next(APP.glob("garmin_spike_*.fit"), None)
CONCEPT2_FIT = next(APP.glob("concept2_spike_*.fit"), None)


@pytest.mark.skipif(GARMIN_FIT is None, reason="Garmin FIT-fixture mangler — kjør spikes/garmin_login.py")
def test_parse_garmin_zip_wrapped_fit() -> None:
    samples, summary = parse_fit_to_samples(GARMIN_FIT)
    assert len(samples) > 100  # løpetur har tusenvis av sekund-records
    assert summary.record_count == len(samples)

    # Første sample skal ha t_offset_sec == 0
    assert samples[0]["t_offset_sec"] == 0
    # Offset skal øke monotont
    offsets = [s["t_offset_sec"] for s in samples]
    assert offsets == sorted(offsets)

    # Minst én sample har HR > 0 (løping innendørs eller ute)
    assert any(s["hr"] is not None and s["hr"] > 0 for s in samples)
    # Minst én har distance
    assert any(s["distance_m"] is not None for s in samples)


@pytest.mark.skipif(CONCEPT2_FIT is None, reason="Concept2 FIT-fixture mangler — kjør spikes/concept2_oauth.py")
def test_parse_concept2_raw_fit() -> None:
    samples, summary = parse_fit_to_samples(CONCEPT2_FIT)
    assert len(samples) > 500  # skierg-økt har hundrevis av stroke-samples

    # Concept2 har power og cadence (stroke rate)
    with_power = [s for s in samples if s["power_w"] is not None and s["power_w"] > 0]
    assert len(with_power) > 100, "Forventet power-verdier fra Concept2"

    with_cadence = [s for s in samples if s["cadence"] is not None and s["cadence"] > 0]
    assert len(with_cadence) > 100


@pytest.mark.skipif(GARMIN_FIT is None, reason="Garmin FIT-fixture mangler")
def test_garmin_samples_have_pace() -> None:
    """Pace regnes fra speed — skal være satt for løping."""
    samples, _ = parse_fit_to_samples(GARMIN_FIT)
    paces = [s["pace_sec_per_km"] for s in samples if s["pace_sec_per_km"]]
    assert len(paces) > 10
    # Realistisk løpepace: 3-10 min/km = 180-600 sek/km
    median = sorted(paces)[len(paces) // 2]
    assert 120 < median < 900, f"Urimelig median pace: {median} sek/km"


def test_invalid_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_fit.bin"
    bad.write_bytes(b"totally random bytes here")
    with pytest.raises(ValueError):
        parse_fit_to_samples(bad)
