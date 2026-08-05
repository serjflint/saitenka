"""Offline prewarm job (#149): the atlas-only decoupling contract.

`prewarm(atlas_only=True)` fills ONLY the glyph mask atlas — every word is built + rastered so its
glyphs/words land in the atlas — while the byte-ceiling-bounded render cache is left untouched (so a
`--limit 0` full-corpus atlas fill can't grow it). These assert the observable behaviour of the job's
per-word step against a constructed fake reader, no dicts / no mpv.
"""

from __future__ import annotations

from overlay.app.prewarm import _popular_terms, _PrewarmJob


class _FakePanel:
    def __init__(self) -> None:
        self.rastered: list[int] = []
        self.native_scales: list[float] = []

    def precompose_head(self, cap: int) -> None:  # the raster that feeds the mask atlas
        self.rastered.append(cap)

    def viewport(self, _scroll: int, _view_h: int, *, scale: float = 1.0):  # native raster → atlas
        self.native_scales.append(scale)


class _FakeAtlas:
    """Records the resume ledger; enough of MaskAtlas for the atlas-only prewarm path."""

    def __init__(self) -> None:
        self.done: set[tuple[float, str]] = set()

    def is_done(self, scale: float, word: str) -> bool:
        return (scale, word) in self.done

    def mark_done(self, scale: float, word: str) -> None:
        self.done.add((scale, word))


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


def _job(reader_factory, *, atlas=None, **kw) -> _PrewarmJob:
    return _PrewarmJob(
        reader_factory=reader_factory,
        cache=None,  # atlas-only opens no render cache
        atlas=atlas,
        gate=512,
        sig="sig",
        ceiling=1 << 30,
        on_progress=None,
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
