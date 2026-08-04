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

    def precompose_head(self, cap: int) -> None:  # the raster that feeds the mask atlas
        self.rastered.append(cap)


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


def test_native_scale_rasters_the_native_panel_into_the_atlas(monkeypatch):
    # scale > 1 → each word also builds its native-scale panel, so size×scale glyph masks land in the
    # atlas and the hi-dpi crisp upgrade loads from disk instead of paying getmask2 on first paint.
    from overlay.app import tooltip

    seen: list[float] = []
    monkeypatch.setattr(
        tooltip,
        "build_native_panel",
        lambda *a, **_k: seen.append(a[5]),  # a[5] == scale
    )
    reader = _FakeReader()
    job = _job(lambda: reader, atlas=object(), native_scale=1.5)
    job.render(("cat", "kyatto"))
    assert seen == [1.5]  # rastered once at the configured native scale


def test_native_scale_is_a_noop_at_reference_scale(monkeypatch):
    from overlay.app import tooltip

    seen: list = []
    monkeypatch.setattr(tooltip, "build_native_panel", lambda *a, **_k: seen.append(a))
    job = _job(lambda: _FakeReader(), atlas=object(), native_scale=1.0)  # default = reference only
    job.render(("cat", "kyatto"))
    assert seen == []  # no native raster at scale 1.0


def test_native_scale_survives_a_pathological_native_raster(monkeypatch):
    from overlay.app import tooltip

    def _boom(*_a, **_k):
        raise ValueError("bad native raster")

    monkeypatch.setattr(tooltip, "build_native_panel", _boom)
    job = _job(lambda: _FakeReader(), atlas=object(), native_scale=1.5)
    job.render(("boom", ""))  # must not raise — best-effort per word
    assert job.measured == 1


def test_popular_terms_empty_without_freq_dicts():
    ds = type("DS", (), {"freqs": []})()
    assert _popular_terms(ds, 100) == []
