import json
from dataclasses import replace

import pysubs2
import pytest
from corpus_check import CORPORA, _spec_failures
from synthetic_characters import SPEC, documents, generate


def test_generated_corpora_preserve_all_original_cases_separately(tmp_path):
    output = tmp_path / "corpus"
    corpora = generate(output)

    assert set(corpora) == {"ass", "srt"}
    assert corpora["ass"]["events"] == corpora["srt"]["events"] == 12
    assert corpora["ass"]["census_sha256"] == corpora["srt"]["census_sha256"]
    assert corpora["ass"]["manifest_sha256"] != corpora["srt"]["manifest_sha256"]
    assert corpora["ass"]["coordinates"][1]["text"] == "だ"
    assert sum(row["reason"] == "non-ink" for row in corpora["srt"]["coordinates"]) == 2


def test_ass_features_are_preserved_without_inventing_srt_style_support():
    spec = json.loads(SPEC.read_text())
    sources = documents(spec)
    ass = pysubs2.SSAFile.from_string(sources["ass"], format_="ass")
    srt = pysubs2.SSAFile.from_string(sources["srt"], format_="srt")

    assert ass.styles["negative-spacing"].spacing == -0.5
    assert r"\pos(101.25,101.75)" in ass.events[8].text
    assert [event.plaintext for event in ass] == [event.plaintext for event in srt]
    assert r"\pos" not in sources["srt"]


@pytest.mark.parametrize("drop_variant", [False, True])
def test_character_census_lock_detects_single_case_or_whole_variant_omission(drop_variant):
    spec = next(spec for spec in CORPORA if spec.name == "synthetic-characters")
    keys = spec.keys()
    truncated = keys[:12] if drop_variant else keys[:-1]

    failures = _spec_failures(replace(spec, keys=lambda: truncated))

    assert any("shrank" in failure for failure in failures)
    assert any("hash changed" in failure for failure in failures)


def test_generation_refuses_to_overwrite_an_existing_export(tmp_path):
    existing = tmp_path / "corpus"
    existing.mkdir()
    sentinel = existing / "mine.srt"
    sentinel.write_text("user-owned")
    with pytest.raises(FileExistsError):
        generate(existing)
    assert sentinel.read_text() == "user-owned"
