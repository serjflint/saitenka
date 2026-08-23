"""Reading libass's own resolution decisions back out of mpv's log."""

from __future__ import annotations

from saitenka.app import font_resolution as fr

# Verbatim shape from a real mpv `--log-file`, both libass instances present. The prefix is mpv's
# and the text after `fontselect:` is libass's.
LOG = """\
[   0.032][d][sub/ass] ASS library version: 0x1705000 (runtime 0x1705000)
[   0.049][d][sub/ass] fontselect: (sans-serif, 400, 0) -> /System/Library/Fonts/Helvetica.ttc, -1, Helvetica
[   0.050][d][sub/ass] fontselect: failed to find any fallback with glyph 0x5B57 for font: (sans-serif, 400, 0)
[   0.051][d][sub/ass] fontselect: (Gandhi Sans, 700, 0) -> /tmp/attachments/GandhiSans-Bold.otf, 0, GandhiSans-Bold
[   0.060][d][osd/libass] fontselect: (sans-serif, 400, 0) -> /System/Library/Fonts/Supplemental/Arial.ttf, -1, ArialMT
"""


def test_only_the_subtitle_renderers_choices_are_read_by_default() -> None:
    """mpv runs two libass instances and tags each line. The OSD one can never receive a container
    attachment, so it substituting a face is expected — reading both as one would report that
    expected substitution as a defect."""
    subtitle = fr.parse(LOG)
    osd = fr.parse(LOG, module=fr.OSD_RENDERER)

    assert fr.FontRequest("Gandhi Sans", 700, 0) in subtitle
    assert fr.FontRequest("Gandhi Sans", 700, 0) not in osd
    assert osd[fr.FontRequest("sans-serif", 400, 0)].psname == "ArialMT"
    assert subtitle[fr.FontRequest("sans-serif", 400, 0)].psname == "Helvetica"


def test_a_face_carries_its_path_and_collection_index() -> None:
    """A `.ttc` is several faces in one file, so the index is part of the answer: two requests can
    resolve to the same path and still be different faces."""
    resolved = fr.parse(LOG)[fr.FontRequest("Gandhi Sans", 700, 0)]

    assert resolved == fr.ResolvedFace("/tmp/attachments/GandhiSans-Bold.otf", 0, "GandhiSans-Bold")
    assert fr.parse(LOG)[fr.FontRequest("sans-serif", 400, 0)].index == -1


def test_the_last_answer_wins() -> None:
    """libass re-resolves after a track or size change; an early answer names a face nothing is
    drawing with any more."""
    later = LOG + (
        "[   9.000][d][sub/ass] fontselect: (sans-serif, 400, 0) -> "
        "/System/Library/Fonts/Hiragino.ttc, -1, HiraginoSans-W4\n"
    )

    assert fr.parse(later)[fr.FontRequest("sans-serif", 400, 0)].psname == "HiraginoSans-W4"


def test_a_missing_glyph_is_reported_with_the_family_that_wanted_it() -> None:
    assert fr.unmatched_glyphs(LOG) == [("0x5B57", fr.FontRequest("sans-serif", 400, 0))]


def test_a_substituted_face_is_a_divergence() -> None:
    theirs = fr.parse(LOG)
    ours = dict(theirs)
    ours[fr.FontRequest("Gandhi Sans", 700, 0)] = fr.ResolvedFace(
        "/System/Arial.ttf", -1, "ArialMT"
    )

    divergences = fr.compare(theirs, ours)

    assert [item.request.family for item in divergences] == ["Gandhi Sans"]
    assert divergences[0].theirs is not None
    assert divergences[0].theirs.psname == "GandhiSans-Bold"


def test_a_family_only_one_side_asked_for_is_a_divergence_too() -> None:
    """A request only one side made means the two laid out different text, which is larger than a
    substituted face and would otherwise read as agreement."""
    theirs = fr.parse(LOG)

    divergences = fr.compare(theirs, {})

    assert {item.request.family for item in divergences} == {"sans-serif", "Gandhi Sans"}
    assert all(item.ours is None for item in divergences)


def test_identical_resolutions_diverge_nowhere() -> None:
    theirs = fr.parse(LOG)

    assert fr.compare(theirs, dict(theirs)) == []


def test_an_uncaptured_log_reads_as_unverified_not_as_agreement() -> None:
    """The channel has no API guarantee. If libass stops narrating, the report has to say the check
    did not run — reporting "agreed" would turn a lost verification into a false clean bill."""
    line = fr.summary("", {})

    assert "nothing to verify" in line
    assert "same face" not in line


def test_the_summary_names_both_sides_of_a_divergence() -> None:
    theirs = fr.parse(LOG)
    ours = dict(theirs)
    ours[fr.FontRequest("Gandhi Sans", 700, 0)] = fr.ResolvedFace(
        "/System/Arial.ttf", -1, "ArialMT"
    )

    line = fr.summary(LOG, ours)

    assert "mpv=GandhiSans-Bold ours=ArialMT" in line
    assert "0x5B57 in sans-serif" in line
