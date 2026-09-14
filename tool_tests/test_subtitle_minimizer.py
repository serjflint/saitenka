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
