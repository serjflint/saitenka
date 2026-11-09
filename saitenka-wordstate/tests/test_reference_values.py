"""Retrievability and frequency reference datasets, independent of application wiring."""

import json
from pathlib import Path

import pytest
from saitenka_wordstate import freq_band
from saitenka_wordstate.fsrs import harmonic_of, harmonic_rank, rareness_band, retrievability

DATA = json.loads((Path(__file__).parent / "fixtures" / "reference_values.json").read_text())


@pytest.mark.parametrize("record", DATA["retrievability"], ids=lambda r: r["id"])
def test_retrievability_reference_values(record):
    v = record["input"]
    assert retrievability(v["s"], v["elapsed"], v["decay"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["harmonic"], ids=lambda r: r["id"])
def test_harmonic_mean_reference_values(record):
    assert harmonic_of(record["input"]["values"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["harmonic-rank"], ids=lambda r: r["id"])
def test_harmonic_rank_reference_values(record):
    v = record["input"]
    assert harmonic_rank(v["word"], v["tables"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["freq-band"], ids=lambda r: r["id"])
def test_frequency_band_reference_values(record):
    v = record["input"]
    assert freq_band(v["rank"], top_x=v["top"], bands=v["bands"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["rareness"], ids=lambda r: r["id"])
def test_rareness_reference_values(record):
    assert rareness_band(record["input"]["rank"]) == record["expected"]
