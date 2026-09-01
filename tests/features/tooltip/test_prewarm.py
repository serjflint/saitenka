"""Offline prewarm job (#149): the atlas-only decoupling contract.

`prewarm(atlas_only=True)` fills ONLY the glyph mask atlas — every word is built + rastered so its
glyphs/words land in the atlas — while the byte-ceiling-bounded render cache is left untouched (so a
`--limit 0` full-corpus atlas fill can't grow it). These assert the observable behaviour of the job's
per-word step against a constructed fake reader, no dicts / no mpv.
"""

from __future__ import annotations

import sys

from saitenka.app.features.tooltip.prefetch import TipScale
from saitenka.app.prewarm import PrewarmTuning, _popular_terms, _PrewarmJob, _startup_plan


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

    def __init__(
        self, *, growth: int = 0, disk_seq: list[int] | None = None, dup_per_word: int = 0
    ) -> None:
        self.done: set[tuple[float, str]] = set()
        self.masks = 0
        self.ignored = (
            0  # masks produced but already cached (INSERT OR IGNORE), read by the heartbeat
        )
        self._growth = growth
        self._dup_per_word = dup_per_word  # already-cached masks re-produced per rastered word
        self._disk_seq = disk_seq  # scripted per-checkpoint disk_bytes (models lumpy page growth)
        self._disk_calls = 0

    def is_done(self, scale: float, word: str) -> bool:
        return (scale, word) in self.done

    def mark_done(self, scale: float, word: str) -> None:
        self.done.add((scale, word))
        if (
            scale == 1.0
        ):  # reference pass fires once per fresh word → model mask growth here, not twice
            self.masks += self._growth
            self.ignored += self._dup_per_word

    def count(self) -> int:
        return self.masks

    def disk_bytes(self) -> int:
        if self._disk_seq is None:
            return self.masks * 700  # ~compressed bytes per mask (smooth)
        i = min(self._disk_calls, len(self._disk_seq) - 1)
        self._disk_calls += 1
        return self._disk_seq[i]

    def checkpoint(self) -> None:
        pass

    def done_words(self, scale: float) -> set[str]:
        return {w for (s, w) in self.done if s == scale}


class _FakePreparation:
    """Records each panel build; a render-cache access would raise."""

    def __init__(self, *, fail: bool = False) -> None:
        self.panel = _FakePanel()
        self.calls: list[tuple] = []
        self._fail = fail

    scale = TipScale(display=1.0, raster=1.0, cap=260)

    def panel_for(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._fail:
            raise ValueError("pathological entry")
        return self.panel

    def panel_key(self, *_args, **_kwargs):  # only the native-raster path reaches this
        return ("key",)


def _job(preparation_factory, *, atlas=None, on_progress=None, **kw) -> _PrewarmJob:
    return _PrewarmJob(
        preparation_factory=preparation_factory,
        cache=None,  # atlas-only opens no render cache
        atlas=atlas,
        gate=512,
        sig="sig",
        ceiling=1 << 30,
        on_progress=on_progress,
        tuning=PrewarmTuning(atlas_only=True, **kw),
    )


def test_atlas_only_rasters_every_word_without_touching_the_render_cache():
    reader = _FakePreparation()
    job = _job(lambda: reader)
    job.render(("cat", "kyatto"))
    # the word was rastered (its glyphs feed the atlas) and no render-cache path was exercised
    assert reader.panel.rastered == [260]
    assert job.measured == 1
    assert job.skipped == 0


def test_atlas_only_survives_a_pathological_entry():
    # a single failing render must not abort the whole prebuild (best-effort per-word)
    job = _job(lambda: _FakePreparation(fail=True))
    job.render(("boom", ""))  # does not raise
    assert job.measured == 1


def test_native_scale_rasters_the_native_panel_into_the_atlas():
    # scale > 1 → each word ALSO composites its reference panel at the native scale (one-panel arch), so
    # size×scale glyph masks land in the atlas and the hi-dpi crisp upgrade loads from disk.
    reader = _FakePreparation()
    job = _job(lambda: reader, atlas=_FakeAtlas(), native_scale=1.5)
    job.render(("cat", "kyatto"))
    assert reader.panel.native_scales == [1.5]  # composited once at the configured native scale


def test_native_scale_is_a_noop_at_reference_scale():
    reader = _FakePreparation()
    job = _job(lambda: reader, atlas=_FakeAtlas(), native_scale=1.0)  # default = reference only
    job.render(("cat", "kyatto"))
    assert reader.panel.native_scales == []  # no native compose at scale 1.0


def test_atlas_only_skips_a_word_when_reference_and_native_both_done():
    # Fully skipped only when BOTH passes are done: the 1× reference AND the native scale (a stopped
    # `--limit 0` re-run picks up where it left off instead of re-rastering from the start).
    reader = _FakePreparation()
    atlas = _FakeAtlas()
    atlas.mark_done(1.0, "cat")  # reference pass done
    atlas.mark_done(1.5, "cat")  # native pass done
    job = _job(lambda: reader, atlas=atlas, native_scale=1.5)
    job.render(("cat", "kyatto"))
    assert reader.panel.rastered == []  # not rastered — skipped
    assert job.skipped == 1 and job.measured == 0


def test_atlas_only_marks_both_reference_and_native_after_rastering():
    reader = _FakePreparation()
    atlas = _FakeAtlas()
    job = _job(lambda: reader, atlas=atlas, native_scale=1.5)
    job.render(("cat", "kyatto"))
    assert atlas.is_done(1.0, "cat") and atlas.is_done(1.5, "cat")  # both passes marked
    assert job.measured == 1


def test_atlas_only_skips_the_reference_a_different_scale_already_built():
    # The cheap read check: scale 1.0 needs only the reference; if another scale's run built it
    # (done(1.0)), the word is skipped WITHOUT re-rastering — no getmask2, no wasted CPU.
    reader = _FakePreparation()
    atlas = _FakeAtlas()
    atlas.mark_done(1.0, "cat")  # reference built by, e.g., a prior 1.5 run
    job = _job(lambda: reader, atlas=atlas, native_scale=1.0)
    job.render(("cat", "kyatto"))
    assert reader.panel.rastered == [] and job.skipped == 1  # skipped, not re-rastered


def test_atlas_only_higher_scale_skips_reference_but_builds_native():
    # Reference already built (done(1.0)) but native at 2.0 not → skip the reference raster, do ONLY
    # the native pass. Saves re-rastering the 1× the earlier scale already produced.
    reader = _FakePreparation()
    atlas = _FakeAtlas()
    atlas.mark_done(1.0, "cat")
    job = _job(lambda: reader, atlas=atlas, native_scale=2.0)
    job.render(("cat", "kyatto"))
    assert reader.panel.rastered == []  # reference NOT re-rastered
    assert reader.panel.native_scales == [2.0]  # native pass ran
    assert atlas.is_done(2.0, "cat") and job.measured == 1


def test_native_scale_survives_a_pathological_native_raster():
    class _BoomPanel(_FakePanel):
        def viewport(self, *_a, **_k):
            raise ValueError("bad native raster")

    reader = _FakePreparation()
    reader.panel = _BoomPanel()
    job = _job(lambda: reader, atlas=_FakeAtlas(), native_scale=1.5)
    job.render(("boom", ""))  # must not raise — best-effort per word
    assert job.measured == 1


def test_popular_terms_empty_without_freq_dicts():
    ds = type("DS", (), {"freq_titles": []})()
    assert _popular_terms(ds, 100) == []


def _drive(job: _PrewarmJob, n: int) -> None:
    """Feed ``n`` distinct words through the job (each rasters, unless the job has stopped)."""
    for i in range(n):
        job.render((f"w{i}", ""))


def test_heartbeat_reports_new_masks_skipped_and_real_bytes():
    # Every checkpoint carries the delta, the resume skips, and REAL bytes (atlas mode used to send 0).
    atlas = _FakeAtlas(growth=3)  # +3 masks per rastered word → +6 per 2-word checkpoint
    atlas.done.update({(1.0, "w1"), (1.5, "w1")})  # one word fully done (both passes) → skipped
    beats: list = []
    job = _job(
        lambda: _FakePreparation(),
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


def test_heartbeat_reports_masks_already_cached_and_progress_denominator():
    # The transparency fix: a re-scale run rasters words whose masks already exist → +0 NEW but N
    # "already cached" (the INSERT OR IGNORE layer that `skipped` never reflected). Also the m/to_raster
    # denominator. growth=0 → nothing stored; dup_per_word models the IGNORE'd (already-present) masks.
    atlas = _FakeAtlas(growth=0, dup_per_word=5)
    beats: list = []
    job = _job(
        lambda: _FakePreparation(),
        atlas=atlas,
        on_progress=beats.append,
        native_scale=1.5,
        checkpoint_every=2,
        total=100,
    )
    _drive(job, 4)  # checkpoints at m=2, m=4
    assert [b.new_rows for b in beats] == [0, 0]  # nothing stored
    assert [b.dup_masks for b in beats] == [
        10,
        10,
    ]  # 5 already-cached per word × 2 words/checkpoint
    assert beats[0].to_raster == 100 and beats[0].skipped == 0  # denominator; ledger skip untouched


def test_atlas_plateau_stops_after_consecutive_dry_checkpoints():
    # growth=0 → no new masks → every checkpoint is "dry"; stop after `plateau_stop` of them.
    atlas = _FakeAtlas(growth=0)
    job = _job(
        lambda: _FakePreparation(),
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
        lambda: _FakePreparation(),
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
    job = _job(lambda: _FakePreparation(), atlas=atlas, native_scale=1.5, checkpoint_every=2)
    _drive(job, 8)
    assert job.stop is False and job.measured == 8


def test_startup_plan_splits_done_from_remaining_in_atlas_mode():
    atlas = _FakeAtlas()
    atlas.mark_done(1.0, "cat")  # fully done = both passes: reference…
    atlas.mark_done(1.5, "cat")  # …and native
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


def test_projection_uses_cumulative_rate_not_a_single_checkpoint_delta():
    # Disk grows in LUMPS (SQLite page allocation), so the last checkpoint's Δ is a noisy estimator;
    # the projection must extrapolate the CUMULATIVE bytes/word since the run's start instead.
    start = 1_000_000
    atlas = _FakeAtlas(growth=10, disk_seq=[1_020_000, 1_024_000])  # m=2 → 1.02M, m=4 → 1.024M
    beats: list = []
    job = _job(
        lambda: _FakePreparation(),
        atlas=atlas,
        on_progress=beats.append,
        native_scale=1.5,
        checkpoint_every=2,
        total=1002,  # to_raster = 1002 (nothing pre-done)
        start_nbytes=start,
    )
    _drive(job, 4)
    # m=4: cumulative rate = (1,024,000 - 1,000,000) / 4 = 6,000 B/word; left = 1002 - 4 = 998
    #   → 1,024,000 + 6,000 * 998 = 7,012,000. A single-checkpoint Δ would give only 3,020,000.
    assert beats[1].projected_bytes == 7_012_000


def test_projection_is_stable_under_steady_growth():
    # The convergence guarantee: under a constant bytes/word rate the cumulative projection is
    # IDENTICAL at every checkpoint (start + rate·to_raster) — it converges, it does not oscillate.
    start, rate, every = 1_000_000, 500, 2
    atlas = _FakeAtlas(growth=10, disk_seq=[start + rate * (k * every) for k in range(1, 6)])
    beats: list = []
    job = _job(
        lambda: _FakePreparation(),
        atlas=atlas,
        on_progress=beats.append,
        native_scale=1.5,
        checkpoint_every=every,
        total=1000,
        start_nbytes=start,
    )
    _drive(job, 10)  # checkpoints at m = 2,4,6,8,10
    projs = [b.projected_bytes for b in beats]
    assert len(set(projs)) == 1  # same estimate every heartbeat → no oscillation
    assert projs[0] == start + rate * 1000  # = start + rate · to_raster


def test_benchmark_headless_stand_in_answers_the_job_port() -> None:
    """A stand-in must REFUSE a lane, not lack the method.

    Twelve features used to reach the runtime through
    `getattr(ipc, "register_runtime_job_lane", None)`, which reads a headless stand-in and a renamed
    method as the same thing — so a rename would have silently disabled every lane in the process.
    They call the method now, which means anything passed in as an ipc has to have it.
    """
    import importlib.util
    from pathlib import Path

    from saitenka.runtime.jobs import JobLanePolicy, configure_lane

    spec = importlib.util.spec_from_file_location(
        "bench_responsiveness",
        Path(__file__).resolve().parents[3] / "examples" / "bench_responsiveness.py",
    )
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    sys.modules["bench_responsiveness"] = bench
    spec.loader.exec_module(bench)

    stand_in = bench.FakeIPC()
    assert stand_in.register_runtime_job_lane("any", JobLanePolicy(capacity=1), None) is False
    assert stand_in.submit_runtime_job() is False
    assert stand_in.close_runtime_job_lane("any") is False
    assert stand_in.schedule_runtime_timer(timer="any") is False
    assert stand_in.cancel_runtime_timer("any") is False
    assert configure_lane(stand_in, "any", JobLanePolicy(capacity=1), None) is None
