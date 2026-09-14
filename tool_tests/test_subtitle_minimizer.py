import pysubs2
import pytest
from minimize_subtitle_repro import main, minimize


def test_minimizer_preserves_mark_failure_and_discards_irrelevant_events():
    source = "1\n00:00:01,000 --> 00:00:02,000\n猫が犬\n\n2\n00:00:03,000 --> 00:00:04,000\n空\n"

    reduced, evidence = minimize(source, "srt", lambda text: "failed" if "が" in text else "passed")

    document = pysubs2.SSAFile.from_string(reduced, format_="srt")
    assert [row.text for row in document] == ["が"]
    assert evidence["checks"] <= 128
    assert source != reduced


def test_minimizer_preserves_native_mask_authority_counterexample():
    import json
    from dataclasses import replace

    import numpy as np
    from compare_cached_characters import geometry_request_for
    from native_mask_benchmark import canvas
    from saitenka_subtitles import FontProvider, FontSetup, GeometryPaletteEntry
    from saitenka_subtitles.libass_backend import LibassGeometryBackend
    from synthetic_characters import ROOT, SPEC, documents

    from saitenka.app.subtitle_fonts import FontEnvironment

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    source = documents(
        {
            **spec,
            "cases": [
                {"id": "interior", "text": "あだあ", "tags": "", "spacing": 0},
                {"id": "unrelated", "text": "猫", "tags": "", "spacing": 0},
            ],
        }
    )["ass"]
    fonts = FontEnvironment(
        FontSetup(default_family=spec["font"]["family"], font_provider=FontProvider.NONE),
        attachments=(("corpus.ttf", (ROOT / spec["font"]["path"]).read_bytes()),),
    )
    faulty, oracle = LibassGeometryBackend(), LibassGeometryBackend()

    def check(candidate):
        active = [
            event
            for event in pysubs2.SSAFile.from_string(candidate, format_="ass")
            if event.start <= 1500 < event.end and event.plaintext.strip()
        ]
        if not active:
            return "passed"  # This candidate removed the failing event, not an oracle dependency.
        try:
            inputs, _tokens = geometry_request_for(candidate, 1500, (1280, 720), fonts)
            original = replace(
                inputs,
                ass=inputs.native_ass,
                native_ass=b"",
                reserved_rgb=(),
                palette=(GeometryPaletteEntry(inputs.palette[0].event_id, 0, 0xFFFFFF),),
            )
            expected = canvas(oracle.render(original), inputs.frame_size)
            # Inject the retired pixel-authority bug, not a text-presence predicate.
            actual = canvas(faulty.render(replace(inputs, native_ass=b"")), inputs.frame_size)
        except (ValueError, IndexError):
            return "inconclusive"
        return "failed" if np.any(actual != expected) else "passed"

    try:
        reduced, evidence = minimize(source, "ass", check, max_checks=32)

        assert check(reduced) == "failed"
        assert len(pysubs2.SSAFile.from_string(reduced, format_="ass")) == 1
        assert evidence["checks"] <= 32
        assert len(reduced) < len(source)
    finally:
        faulty.close()
        oracle.close()


@pytest.mark.parametrize("verdict", ["passed", "inconclusive"])
def test_minimizer_refuses_missing_or_unqualified_failure(verdict):
    with pytest.raises(ValueError, match=r"does not reproduce|inconclusive"):
        minimize("", "srt", lambda _text: verdict)


def test_minimizer_budget_never_promotes_an_untested_candidate():
    source = "1\n00:00:01,000 --> 00:00:02,000\n猫犬木\n"
    checked = []

    def check(text):
        checked.append(text)
        return "failed"

    reduced, evidence = minimize(source, "srt", check, max_checks=2)

    assert reduced == checked[-1]
    assert evidence["checks"] == len(checked) == 2
    assert evidence["budget_exhausted"]


def test_minimizer_refuses_a_failure_destroyed_by_serialization():
    source = "1\r\n00:00:01,000 --> 00:00:02,000\r\n猫\r\n"

    with pytest.raises(ValueError, match="serialization changes the failure"):
        minimize(source, "srt", lambda text: "failed" if "\r\n" in text else "passed")


def test_minimizer_does_not_create_an_export_when_validation_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\n猫\n", encoding="utf-8")
    output = tmp_path / "new-export"
    monkeypatch.setattr(
        "sys.argv",
        [
            "minimize",
            str(source),
            "--output",
            str(output),
            "--max-checks",
            "1",
            "--checker",
            "unused",
            "{input}",
        ],
    )

    with pytest.raises(ValueError, match="max_checks"):
        main()

    assert not output.exists()
    assert source.read_text(encoding="utf-8") == "1\n00:00:01,000 --> 00:00:02,000\n猫\n"
