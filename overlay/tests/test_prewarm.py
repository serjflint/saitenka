"""Offline prewarm job (#149): the atlas-only decoupling contract.

`prewarm(atlas_only=True)` fills ONLY the glyph mask atlas — every word is built + rastered so its
glyphs/words land in the atlas — while the byte-ceiling-bounded render cache is left untouched (so a
`--limit 0` full-corpus atlas fill can't grow it). These assert the observable behaviour of the job's
per-word step against a constructed fake reader, no dicts / no mpv.
"""

from __future__ import annotations

from overlay.app.prewarm import _popular_terms, _PrewarmJob, _startup_plan


class _FakePanel:
    def __init__(self) -> None:
        self.rastered: list[int] = []
        self.native_scales: list[float] = []

    def precompose_head(self, cap: int) -> None:  # the raster that feeds the mask atlas
        self.rastered.append(cap)

    def viewport(self, _scroll: int, _view_h: int, *, scale: float = 1.0):  # native raster → atlas
        self.native_scales.append(scale)


class _FakeAtlas:
    """Records the resume ledger + a scriptable mask count; enough of MaskAtlas for the atlas-only
    prewarm path AND its heartbeat (``count``/``disk_bytes``/``checkpoint``/``done_words``). ``growth`` =
    masks added per rastered word, so a test can model a productive run (>0) or a plateau (0)."""

    def __init__(self, *, growth: int = 0) -> None:
        self.done: set[tuple[float, str]] = set()
        self.masks = 0
        self._growth = growth

    def is_done(self, scale: float, word: str) -> bool:
        return (scale, word) in self.done

    def mark_done(self, scale: float, word: str) -> None:
        self.done.add((scale, word))
        self.masks += self._growth

    def count(self) -> int:
        return self.masks

    def disk_bytes(self) -> int:
        return self.masks * 700  # ~compressed bytes per mask

    def checkpoint(self) -> None:
        pass

    def done_words(self, scale: float) -> set[str]:
        return {w for (s, w) in self.done if s == scale}


class _FakeReader:
    """Records each `_panel_for` call; a render-cache access would raise (proving decoupling)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.panel = _FakePanel()
        self.calls: list[tuple] = []
        self._fail = fail

    def _tip_cap(self) -> int:
        return 260

    def _panel_for(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._fail:
            raise ValueError("pathological entry")
        return self.panel

    def _panel_key(self, *_args, **_kwargs):  # only the native-raster path reaches this
        return ("key",)


def _job(reader_factory, *, atlas=None, on_progress=None, **kw) -> _PrewarmJob:
    return _PrewarmJob(
        reader_factory=reader_factory,
        cache=None,  # atlas-only opens no render cache
        atlas=atlas,
        gate=512,
        sig="sig",
        ceiling=1 << 30,
        on_progress=on_progress,
        atlas_only=True,
        **kw,
    )


def test_atlas_only_rasters_every_word_without_touching_the_render_cache():
    reader = _FakeReader()
    job = _job(lambda: reader)
    job.render(("cat", "kyatto"))
    # the word was rastered (its glyphs feed the atlas) and no render-cache path was exercised
    assert reader.panel.rastered == [260]
    assert job.measured == 1
    assert job.skipped == 0


def test_atlas_only_survives_a_pathological_entry():
    # a single failing render must not abort the whole prebuild (best-effort per-word)
    job = _job(lambda: _FakeReader(fail=True))
    job.render(("boom", ""))  # does not raise
    assert job.measured == 1


def test_native_scale_rasters_the_native_panel_into_the_atlas():
    # scale > 1 → each word ALSO composites its reference panel at the native scale (one-panel arch), so
    # size×scale glyph masks land in the atlas and the hi-dpi crisp upgrade loads from disk.
    reader = _FakeReader()
    job = _job(lambda: reader, atlas=_FakeAtlas(), native_scale=1.5)
    job.render(("cat", "kyatto"))
    assert reader.panel.native_scales == [1.5]  # composited once at the configured native scale


def test_native_scale_is_a_noop_at_reference_scale():
    reader = _FakeReader()
    job = _job(lambda: reader, atlas=_FakeAtlas(), native_scale=1.0)  # default = reference only
    job.render(("cat", "kyatto"))
    assert reader.panel.native_scales == []  # no native compose at scale 1.0


def test_atlas_only_skips_a_word_already_marked_done():
    # Resume ledger: a word already rastered at this scale is skipped (a stopped `--limit 0` re-run
    # picks up where it left off instead of re-rastering from the start).
    reader = _FakeReader()
    atlas = _FakeAtlas()
    atlas.mark_done(1.5, "cat")
    job = _job(lambda: reader, atlas=atlas, native_scale=1.5)
    job.render(("cat", "kyatto"))
    assert reader.panel.rastered == []  # not rastered — skipped
    assert job.skipped == 1 and job.measured == 0


def test_atlas_only_marks_a_word_done_after_rastering():
    reader = _FakeReader()
    atlas = _FakeAtlas()
    job = _job(lambda: reader, atlas=atlas, native_scale=1.5)
    job.render(("cat", "kyatto"))
    assert atlas.is_done(1.5, "cat")  # marked → a re-run skips it
    assert job.measured == 1


def test_done_ledger_is_scale_scoped_in_the_job():
    # A word done at 1.5 is NOT skipped when prewarming at 2.0 (different masks).
    reader = _FakeReader()
    atlas = _FakeAtlas()
    atlas.mark_done(1.5, "cat")
    job = _job(lambda: reader, atlas=atlas, native_scale=2.0)
    job.render(("cat", "kyatto"))
    assert reader.panel.rastered == [260]  # still rastered at the new scale
    assert job.measured == 1


def test_native_scale_survives_a_pathological_native_raster():
    class _BoomPanel(_FakePanel):
        def viewport(self, *_a, **_k):
            raise ValueError("bad native raster")

    reader = _FakeReader()
    reader.panel = _BoomPanel()
    job = _job(lambda: reader, atlas=_FakeAtlas(), native_scale=1.5)
    job.render(("boom", ""))  # must not raise — best-effort per word
    assert job.measured == 1


def test_popular_terms_empty_without_freq_dicts():
    ds = type("DS", (), {"freqs": []})()
    assert _popular_terms(ds, 100) == []


def _drive(job: _PrewarmJob, n: int) -> None:
    """Feed ``n`` distinct words through the job (each rasters, unless the job has stopped)."""
    for i in range(n):
        job.render((f"w{i}", ""))


def test_heartbeat_reports_new_masks_skipped_and_real_bytes():
    # Every checkpoint carries the delta, the resume skips, and REAL bytes (atlas mode used to send 0).
    atlas = _FakeAtlas(growth=3)  # +3 masks per rastered word → +6 per 2-word checkpoint
    atlas.done.add((1.5, "w1"))  # one word pre-done (no mask bump) → skipped, not rastered
    beats: list = []
    job = _job(
        lambda: _FakeReader(),
        atlas=atlas,
        on_progress=beats.append,
        native_scale=1.5,
        checkpoint_every=2,
    )
    _drive(job, 5)  # w1 skipped; w0,w2,w3,w4 rastered → measured hits 2 then 4
    assert [b.measured for b in beats] == [2, 4]
    assert beats[0].skipped == 1  # the pre-done word
    assert beats[0].new_rows == 6 and beats[1].new_rows == 6  # two rastered words × 3 masks each
    assert beats[0].nbytes == 6 * 700 and beats[0].nbytes > 0  # REAL bytes, not old 0


def test_atlas_plateau_stops_after_consecutive_dry_checkpoints():
    # growth=0 → no new masks → every checkpoint is "dry"; stop after `plateau_stop` of them.
    atlas = _FakeAtlas(growth=0)
    job = _job(
        lambda: _FakeReader(),
        atlas=atlas,
        native_scale=1.5,
        checkpoint_every=2,
        plateau_stop=2,
        plateau_min=1,
    )
    _drive(job, 10)
    assert job.stop is True
    assert job.measured == 4  # stopped at the 2nd dry checkpoint; rest short-circuit


def test_a_productive_run_never_trips_the_plateau_stop():
    # growth keeps masks climbing above plateau_min every checkpoint → the sweep runs to completion.
    atlas = _FakeAtlas(growth=5)
    job = _job(
        lambda: _FakeReader(),
        atlas=atlas,
        native_scale=1.5,
        checkpoint_every=2,
        plateau_stop=2,
        plateau_min=1,
    )
    _drive(job, 8)
    assert job.stop is False and job.measured == 8


def test_plateau_stop_off_by_default_rasters_every_word():
    atlas = _FakeAtlas(growth=0)  # dry, but plateau_stop defaults to 0 → never stops
    job = _job(lambda: _FakeReader(), atlas=atlas, native_scale=1.5, checkpoint_every=2)
    _drive(job, 8)
    assert job.stop is False and job.measured == 8


def test_startup_plan_splits_done_from_remaining_in_atlas_mode():
    atlas = _FakeAtlas()
    atlas.mark_done(1.5, "cat")  # already rastered at this scale
    terms = [("cat", ""), ("dog", ""), ("fish", "")]
    plan, already_done, start_rows = _startup_plan(
        terms, None, atlas, atlas_only=True, atlas_scale=1.5
    )
    assert plan.total == 3
    assert plan.already_done == 1 and plan.remaining == 2 and already_done == 1
    assert plan.capped is False  # the atlas is uncapped
    assert start_rows == atlas.count()


def test_startup_plan_scale_scoped_done_count():
    # a word done at 2.0 does NOT count as done for a 1.5 plan (different masks).
    atlas = _FakeAtlas()
    atlas.mark_done(2.0, "cat")
    plan, already_done, _ = _startup_plan(
        [("cat", "")], None, atlas, atlas_only=True, atlas_scale=1.5
    )
    assert plan.already_done == 0 and already_done == 0 and plan.remaining == 1
