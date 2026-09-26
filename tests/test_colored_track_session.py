"""The colored copy as the Japanese track, driven through a session over the track simulator."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import dicthelp
import pytest
from saitenka_subtitles.colored_track import INJECTED, strip_colors
from saitenka_wordstate import KnownWords, Scorer
from session_builder import build_session, install_profile_dependencies
from test_subtitle_modes import EN, FakeIPC

from saitenka.app import backlog, colored_subs, subtitle_modes
from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
from saitenka.app.scoring import Coloring
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner
from saitenka.runtime.events import SubtitleTracksDiscovered

ASS = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1280\n"
    "PlayResY: 720\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
    "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,"
    "1,2,10,10,30,1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,私は本を読む\n"
    "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\fad(200,200)}看板\n"
)


class ColorJobs:
    """The colored-track lane, run on demand so a test decides when generation lands."""

    def __init__(self) -> None:
        self.accepted: list[dict] = []

    def submit(self, **kwargs) -> bool:
        self.accepted.append(kwargs)
        return True

    def finish(
        self, index: int = -1, *, result: colored_subs.ColorResult | None = None
    ) -> colored_subs.ColorResult:
        accepted = self.accepted[index]
        if result is None:
            result = colored_subs.generate(accepted["request"], threading.Event())
        accepted["on_finished"](
            EffectFinished(
                EffectId(1),
                Owner.SUBTITLE,
                accepted["identity"],
                EffectOutcome.SUCCEEDED,
                result=result,
            )
        )
        return result


def _origin(tmp_path: Path) -> tuple[Path, dict]:
    path = tmp_path / "episode.ja.ass"
    path.write_text(ASS, encoding="utf-8")
    track = {
        "id": 2,
        "type": "sub",
        "lang": "jpn",
        "title": "Japanese",
        "codec": "ass",
        "external": True,
        "external-filename": str(path),
    }
    return path, track


def _session(tmp_path, monkeypatch, *, coloring="whole-cue-auto", tracks=None, real_index=False):
    origin, track = _origin(tmp_path)
    ipc = FakeIPC(tracks if tracks is not None else [EN.copy(), track])
    ipc.props["path"] = "/videos/episode.mkv"
    ipc.props["sub-text"] = ""
    jobs = ColorJobs()
    monkeypatch.setattr(
        "saitenka.app.session.builder.configure_colored_track_job", lambda _ipc: jobs.submit
    )
    if not real_index:
        monkeypatch.setattr(
            "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
        )
    reader = build_session(
        ipc,
        options=ReaderOptions(
            subtitle_geometry=SubtitleGeometryOptions(native_visible=True, coloring=coloring),
            prefetch=False,
        ),
    )
    toasts: list[str] = []
    monkeypatch.setattr(reader.graph.notifications, "show", lambda text, *_a: toasts.append(text))
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    return reader, ipc, jobs, origin, toasts


def _ready(reader) -> None:
    install_profile_dependencies(
        reader,
        scorer=Coloring(Scorer(known=KnownWords.from_set(["私", "本"]), jlpt=dicthelp.load_jlpt())),
        dictionaries=object(),
    )


def _colored_track(reader):
    return reader.graph.profile_integration.colored_track


def _added(ipc) -> list[tuple]:
    return [command for command in ipc.commands if command[0] == "sub-add"]


def test_a_generated_copy_becomes_the_japanese_track(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, origin, toasts = _session(tmp_path, monkeypatch)
    _ready(reader)

    jobs.finish()

    [(_, path, flag, title, lang)] = _added(ipc)
    assert (flag, title, lang) == ("select", "Japanese (colored)", "jpn")
    assert Path(path).parent == colored_subs.colored_subs_dir()
    copy = Path(path).read_text(encoding="utf-8")
    assert strip_colors(copy) == origin.read_text(encoding="utf-8")
    assert reader.graph.track_commands.current().jp_sid == 9
    assert subtitle_modes.discover_tracks(ipc).jp_sid == 9
    # Our own swap: no track announcement once mpv echoes it, and the file's timing is untouched.
    ipc.set_prop("sid", 9)
    reader.pump()
    assert not any(text.startswith("subtitles:") for text in toasts)
    assert not any(command[:2] == ("set_property", "sub-delay") for command in ipc.commands)


def test_the_copy_colors_static_events_and_leaves_the_rest_to_the_live_path(
    tmp_path, monkeypatch
) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)

    jobs.finish()

    rows = [
        line
        for line in Path(_added(ipc)[0][1]).read_text(encoding="utf-8").splitlines()
        if "Dialogue" in line
    ]
    assert INJECTED.search(rows[0])
    assert rows[1] == "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\fad(200,200)}看板"


def test_a_ready_copy_waits_for_a_cue_gap(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    ipc.set_prop("sub-text", "私は本を読む")
    reader.pump()

    jobs.finish()
    assert _added(ipc) == []

    ipc.set_prop("sub-text", "")
    reader.pump()
    assert len(_added(ipc)) == 1


def test_a_pause_on_a_line_is_not_a_gap(tmp_path, monkeypatch) -> None:
    # Swapping retires the cue, and with it the tooltip the user paused to read.
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    ipc.set_prop("sub-text", "私は本を読む")
    ipc.set_prop("pause", value=True)
    reader.pump()

    jobs.finish()

    assert _added(ipc) == []


def test_a_swap_through_the_real_index_rebuild_does_not_regenerate(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch, real_index=True)
    _ready(reader)

    jobs.finish()

    assert len(_added(ipc)) == 1
    assert len(jobs.accepted) == 1


def test_the_live_copy_is_not_regenerated_for_its_own_index(tmp_path, monkeypatch) -> None:
    reader, _ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()

    reader.graph.profile_integration.warm_episode()

    assert len(jobs.accepted) == 1


def test_new_dependencies_regenerate_and_replace_the_copy(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()
    first = _added(ipc)[0][1]

    install_profile_dependencies(
        reader,
        scorer=Coloring(Scorer(known=KnownWords.from_set(["私", "本", "読む"]))),
        dictionaries=object(),
    )
    ipc.set_prop("sub-text", "私は本を読む")
    reader.pump()
    jobs.finish()
    # mpv still has the old copy selected until the gap.
    assert Path(first).exists()
    ipc.set_prop("sub-text", "")
    reader.pump()

    assert len(jobs.accepted) == 2
    assert ("sub-remove", 9) in ipc.commands
    assert _added(ipc)[-1][1] != first
    assert not Path(first).exists()


def test_choosing_the_origin_stands_the_copy_down(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()

    ipc.command("set_property", "sid", 2)
    _colored_track(reader).request()

    assert len(jobs.accepted) == 1


def test_only_whole_cue_auto_makes_a_copy(tmp_path, monkeypatch) -> None:
    reader, _ipc, jobs, _origin_path, _toasts = _session(
        tmp_path, monkeypatch, coloring="whole-cue-osd"
    )

    _ready(reader)

    assert jobs.accepted == []


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("sub-ass-override", "strip"),
        ("sub-ass-override", "force"),
        ("sub-filter-sdh", True),
        ("sub-ass-style-overrides", ["Default.PrimaryColour=&H0000FF&"]),
    ],
)
def test_an_option_that_repaints_the_track_refuses_the_copy(
    tmp_path, monkeypatch, option, value
) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    ipc.props[f"options/{option}"] = value

    _ready(reader)

    assert jobs.accepted == []


def test_resyncing_the_copy_retimes_its_origin(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, origin, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()

    assert subtitle_modes._current_external_sub(ipc) == origin


def test_replacing_the_copy_removes_its_origin_too(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()
    fresh = tmp_path / "episode.resynced.ass"
    fresh.write_text(ASS, encoding="utf-8")

    subtitle_modes._replace_target_track(reader.graph.track_commands.ports(), fresh, "resynced")

    assert ("sub-remove", 9) in ipc.commands
    assert ("sub-remove", 2) in ipc.commands


def test_a_bookmark_names_the_origin_not_the_copy(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, origin, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()

    metadata = backlog._track_metadata(ipc.tracks, 9)

    assert metadata["external-filename"] == str(origin)


@pytest.mark.parametrize(
    ("options", "painted"),
    [
        ({}, True),
        ({"options/sub-ass-override": "strip"}, False),
        ({"options/sub-filter-sdh": True}, False),
    ],
)
def test_only_a_frame_the_copy_colors_whole_is_the_tracks(
    tmp_path, monkeypatch, options, painted
) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()
    rows = [
        line
        for line in Path(_added(ipc)[0][1]).read_text(encoding="utf-8").splitlines()
        if line.startswith("Dialogue")
    ]
    observed = {"options/sub-ass-override": "no", "track-list": ipc.tracks, "sid": 9, **options}

    assert colored_subs.track_paints({**observed, "sub-text/ass-full": rows[0]}.get) is painted
    # The faded sign stays uncolored, so a frame showing it is not the track's alone.
    assert not colored_subs.track_paints({**observed, "sub-text/ass-full": "\n".join(rows)}.get)
    # An authored track can carry the injected form itself; only a copy's rows are the track's.
    assert not colored_subs.track_paints({**observed, "sid": 2, "sub-text/ass-full": rows[0]}.get)


def test_an_embedded_track_is_colored_from_its_extraction(tmp_path, monkeypatch) -> None:
    from saitenka.app.embedded_subs import embedded_subs_cache_dir, embedded_subs_cache_key

    embedded = {"id": 2, "type": "sub", "lang": "jpn", "codec": "ass", "ff-index": 3}
    reader, ipc, jobs, _origin_path, _toasts = _session(
        tmp_path, monkeypatch, tracks=[EN.copy(), embedded]
    )
    extracted = embedded_subs_cache_dir() / embedded_subs_cache_key(
        "/videos/episode.mkv", 3, ".ass"
    )
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.write_text(ASS, encoding="utf-8")
    _ready(reader)

    jobs.finish()

    [(_, path, *_rest)] = _added(ipc)
    assert colored_subs.copy_origin(ipc.tracks[-1]) == "embedded:3"
    assert strip_colors(Path(path).read_text(encoding="utf-8")) == ASS
    assert subtitle_modes.discover_tracks(ipc).jp_sid == 9
    # There is no authored file to re-time; Ctrl+Shift+T asks the providers instead.
    assert subtitle_modes._current_external_sub(ipc) is None


def test_a_stale_generation_neither_swaps_nor_colors_live_cues(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    terms = SimpleNamespace(terms_exist=lambda _forms: set(), dicts=())
    install_profile_dependencies(reader, scorer=_scorer("私", "本"), dictionaries=terms)
    install_profile_dependencies(reader, scorer=_scorer("私", "本", "読む"), dictionaries=terms)

    # A newer request supersedes it before it tokenizes anything under the old dependencies.
    assert jobs.finish(0).reason == "superseded"
    assert _added(ipc) == []

    ipc.set_prop("sub-text", "私は本を読む")
    reader.pump()
    cue = reader.graph.subtitle_presentation.cue.current
    assert cue.styles is not None
    tags = {token.surface: style.tag for token, style in zip(cue.tokens, cue.styles, strict=True)}
    assert tags["読む"].startswith("known")


def test_replacing_the_origin_also_retires_its_copy(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()
    ipc.command("set_property", "sid", 2)
    reader.graph.track_commands.declare(SubtitleTracksDiscovered(2, 1))
    fresh = tmp_path / "episode.resynced.ass"
    fresh.write_text(ASS, encoding="utf-8")

    subtitle_modes._replace_target_track(reader.graph.track_commands.ports(), fresh, "resynced")

    assert ("sub-remove", 2) in ipc.commands
    assert ("sub-remove", 9) in ipc.commands


def _scorer(*known: str) -> Coloring:
    return Coloring(Scorer(known=KnownWords.from_set(list(known)), jlpt=dicthelp.load_jlpt()))


def test_a_copy_that_cannot_be_remade_is_stood_down(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()
    install_profile_dependencies(reader, scorer=_scorer("私", "本", "読む"), dictionaries=object())

    jobs.finish(result=colored_subs.ColorResult(None, "nothing-colored"))

    # The old copy's colors are no longer the reader's; the authored track comes back.
    assert ipc.props["sid"] == 2
    assert ("sub-remove", 9) in ipc.commands
    reader.graph.profile_integration.warm_episode()
    assert len(jobs.accepted) == 2  # a refused key is not retried on every warm signal


def test_retiring_the_scorer_stands_the_copy_down(tmp_path, monkeypatch) -> None:
    reader, ipc, jobs, _origin_path, _toasts = _session(tmp_path, monkeypatch)
    _ready(reader)
    jobs.finish()

    install_profile_dependencies(reader, scorer=None, dictionaries=object())

    assert ipc.props["sid"] == 2
    assert subtitle_modes.discover_tracks(ipc).jp_sid == 2


def test_a_row_with_a_unicode_line_separator_is_still_one_colored_row() -> None:
    row = "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\1c&H010203&\\2c&H010203&}猫\u2028犬"

    assert colored_subs.frame_colored(row)
