"""Build the Reader's collaborators (scorer, anki, mine config, dict set) from a loaded config, and
the Reader-side glue that loads them progressively in the background.

``run`` assembles these from CLI flags interleaved with progress prints; ``attach``/plugin mode has
no flags, so it needs the same objects derived purely from ``overlay.toml``. Without them the overlay
is a bare subtitle renderer — no FSRS/known coloring, no JLPT underlines, no frequency pills, no
dictionary tooltips, no mining. Anki-dependent pieces degrade to None (logged) when Anki is closed,
so a missing Anki never blocks attaching.

``load_deps_async``/``apply_deps``/``draw_loading`` take ``reader: Reader`` (the AGENTS.md seam
pattern) with thin delegating methods on Reader.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from typing import TYPE_CHECKING

from overlay import otel_metrics
from overlay.app.overlay_ids import OverlayId

if TYPE_CHECKING:
    from overlay.app.controller import Reader
    from overlay.app.fsrs import KnownSnap

log = logging.getLogger(__name__)

# Four workers cap the independent dependency loads. No worker calls .result() on another future.
_DEPS_DAG_WIDTH = 4


_ANKI_WATCH_S = 60.0  # background watcher's patience for an auto-launched Anki to answer


def _maybe_start_anki(mc: dict, known_cfg, *, mine: bool, on_unreachable=None) -> None:
    """If mining or Anki-backed coloring is configured and AnkiConnect isn't up, launch Anki
    fire-and-forget and warn — never block startup on the poll. A background watcher (see
    :func:`_spawn_anki_seed_watcher`) backfills mining once Anki answers, so the up-to-20s launch poll
    stays off the dict/coloring critical path (it used to gate the whole dep build → apply_deps → the
    'dictionaries loaded' feedback by the full wait). ``on_unreachable(*, launched)`` lets an
    interactive caller (``run``) print a console note — ``launched`` is False when Anki couldn't even
    be started (not found / launch failed), a distinct warning — in addition to the log-only warning
    (``attach`` is detached, so it passes no callback)."""
    if not ((mine and mc) or known_cfg):
        return
    from overlay.app.anki import anki_reachable, launch_anki

    with otel_metrics.traced("anki_ensure_running"):
        if anki_reachable():
            return
        launched = launch_anki()  # fire-and-forget; the seed watcher polls for it to come up
        if launched:
            log.warning("Anki not reachable — launching it; mining/coloring enables once it's up")
        else:  # couldn't find the Anki binary or the launch itself failed — it won't come up on its own
            log.warning(
                "Anki is unavailable and couldn't be started (not found or failed to launch) — "
                "mining/coloring stays off until you open Anki manually"
            )
        if on_unreachable is not None:
            on_unreachable(launched=launched)


def _anki_seed_watch(reader: Reader) -> None:
    """Wait for AnkiConnect to answer, then flag the reader to backfill the mined ⊕→✓ set on its next
    poll tick (:meth:`Reader._apply_pending_anki_seed`). Instant when Anki is already up; otherwise
    polls the just-launched Anki up to ``_ANKI_WATCH_S`` and logs a console warning on timeout. The
    seed itself must run on the main thread, so this only sets the cross-thread flag."""
    from overlay.app.anki import anki_reachable, wait_until_anki_up

    if anki_reachable():
        reader._pending_anki_seed = True
        return
    if wait_until_anki_up(wait=_ANKI_WATCH_S):
        log.info("Anki is up — mining enabled")
        reader._pending_anki_seed = True
    else:
        log.warning(
            "Anki didn't come up within %.0fs — mining stays off until you start it", _ANKI_WATCH_S
        )


def _spawn_anki_seed_watcher(reader: Reader) -> None:
    """Fire-and-forget the Anki seed watcher on a daemon thread so it never holds up the poll loop or
    shutdown."""
    threading.Thread(
        target=_anki_seed_watch, args=(reader,), name="saitenka-anki-seed", daemon=True
    ).start()


def _build_dict_set(db, dict_titles: list[str], freq_titles: list[str], pitch_titles: list[str]):
    """Returns ``(dict_set, freq_rows)`` — ``freq_rows`` is reused by ``_load_freq_dict`` so it isn't
    re-resolved. A configured title with no imported dictionary is warned and skipped."""
    dict_set = None
    freq_rows = None
    if not (dict_titles or freq_titles or pitch_titles):
        return dict_set, freq_rows

    from overlay.app.dictionary import DictionarySet

    with otel_metrics.traced("build_dict_set"):
        d_rows, d_miss = db.resolve(dict_titles)
        freq_rows, f_miss = db.resolve(freq_titles)
        p_rows, p_miss = db.resolve(pitch_titles)
        for kind, miss in (("dict", d_miss), ("freq", f_miss), ("pitch", p_miss)):
            if miss:
                import sys

                from overlay.app.dictionary import _MISSING_HINT

                msg = (
                    f"{kind}(s) not imported, skipped: {', '.join(repr(m) for m in miss)}. "
                    f"{_MISSING_HINT}"
                )
                log.warning(msg)
                print(msg, file=sys.stderr, flush=True)
        if d_rows or freq_rows or p_rows:
            dict_set = DictionarySet.from_rows(db, d_rows, freq_rows, p_rows)
    return dict_set, freq_rows


def _spawn_known_refresh(db, known_cfg) -> None:
    """Background: reconcile the known-word cache against Anki (subset mod-time diff) so the NEXT launch
    reads a current set off disk. Fire-and-forget — a failure (Anki down) leaves the cache as-is; this
    session already colored from it. Daemon so it never holds up shutdown."""
    from overlay.app.anki import is_unreachable
    from overlay.app.wordlists import refresh_known_cache

    def _refresh() -> None:
        try:
            refresh_known_cache(db, known_cfg)
        except Exception as e:
            # Anki down is expected — one compact line, traceback only for a real fault.
            log.debug(
                "background known-word cache refresh failed: %s", e, exc_info=not is_unreachable(e)
            )

    threading.Thread(target=_refresh, name="saitenka-known-refresh", daemon=True).start()


def _load_known_words(db, known_cfg, *, fallback_words=(), on_error=None):
    """Cache-first: serve the last-known set from our SQLite cache (~1 ms) and reconcile in the
    background, so the ~190 ms IO-bound AnkiConnect load is off the startup critical path. A cache miss
    (first launch / changed config) falls back to a blocking full load that populates the cache.

    ``fallback_words`` is ``run``'s plain ``--known word1,word2`` list (``attach`` has none);
    ``on_error`` lets ``run`` print a console note instead of the default log-only Anki-failure warning."""
    from overlay.app.anki import ANKI_DOWN_ERRORS
    from overlay.app.wordlists import KnownWords, refresh_known_cache

    if not known_cfg:
        return KnownWords.from_set(fallback_words)
    try:
        cached = KnownWords.from_cache(db, known_cfg)
    except Exception:
        log.debug("known-word cache read failed; doing a full load", exc_info=True)
        cached = None
    if cached is not None:
        _spawn_known_refresh(
            db, known_cfg
        )  # freshen the cache for next launch, off the critical path
        return cached
    # Anki closed / AnkiConnect down (incl. the client's _AnkiRetryable, an AnkiError → covered by the
    # SSOT) / malformed reply — color by freq+JLPT only. The retryable is what used to escape this catch
    # (the old literal omitted AnkiError) and surface as a full startup traceback for an expected-down Anki.
    known_load_errors: tuple[type[Exception], ...] = (
        *ANKI_DOWN_ERRORS,
        AttributeError,
        KeyError,
        TypeError,
    )
    try:  # cache miss: full load NOW (populates the cache + signature for next time)
        return refresh_known_cache(db, known_cfg)
    except known_load_errors as e:
        if on_error is not None:
            on_error(e)
        else:
            log.warning("known-word load from Anki failed; coloring without a known set")
    return KnownWords.from_set(fallback_words)


def _load_fsrs_snapshot(cfg: dict) -> KnownSnap | None:
    raw = cfg.get("fsrs")
    fsrs_cfg: dict = raw if isinstance(raw, dict) else {}
    collection = fsrs_cfg.get("collection")
    if not collection:
        return None
    from overlay.app.fsrs import load_knownness

    return load_knownness(collection)


def _load_freq_dict(db, freq_rows, freq_titles: list[str]):
    from overlay.app.scoring import FREQ_BAND_TOP_X
    from overlay.app.wordlists import FreqDict

    with otel_metrics.traced("load_freq_dict"):
        # freq_rows is set iff we resolved dict sources above; the coloring band uses the first freq.
        if freq_rows is None:
            freq_rows, _ = db.resolve(freq_titles)
        # Cap at the band's top_x: rarer ranks can't color a word (banded freq_mode), so loading them
        # is ~200ms of dead startup work on a big freq like JPDBv2 — the dep-load critical path.
        return FreqDict.from_db(db, freq_rows[0], top_x=FREQ_BAND_TOP_X) if freq_rows else None


def _load_jlpt_dict(db):
    from overlay.app.wordlists import JlptDict

    with otel_metrics.traced("load_jlpt_dict"):
        return JlptDict.load(db)


def _mine_config_from(mc: dict):
    """Build a :class:`~overlay.app.anki.MineConfig` from the ``[mine]`` table. An optional ``preset``
    (Lapis/Kiku) supplies the field map + default card kind; explicit
    ``deck``/``model``/``fields``/``card_kind``/``normalize_audio``/``animated_*`` keys override it.
    Pure — the same reader for both the ``attach`` and ``run`` seams (run reaches here via the raw
    ``[mine]`` table threaded through ``effective_cfg``)."""
    from overlay.app.anki import MineConfig
    from overlay.app.media import AnimatedClip

    preset = mc.get("preset")
    base = MineConfig.from_preset(str(preset)) if preset else MineConfig()
    raw_fields = mc.get("fields")
    fields = dict(raw_fields) if isinstance(raw_fields, dict) and raw_fields else base.fields
    raw_format = mc.get("card_format")
    card_format = dict(raw_format) if isinstance(raw_format, dict) else {}
    return MineConfig(
        deck=mc.get("deck", base.deck),
        model=mc.get("model", base.model),
        normalize_audio=bool(mc.get("normalize_audio")),
        animated=AnimatedClip(
            enabled=bool(mc.get("animated_screenshot")),
            height=int(mc.get("animated_height", 480)),
            fps=int(mc.get("animated_fps", 12)),
            quality=int(mc.get("animated_quality", 75)),
            max_secs=float(mc.get("animated_max_secs", 4.0)),
            fmt=str(mc.get("animated_format", "webp")).lower(),
        ),
        card_kind=str(mc.get("card_kind", base.card_kind)),
        fields=fields,
        card_format=card_format,
    )


def _validate_mine_fields(anki, mine_conf) -> None:
    """Drop + warn about configured field-map targets that don't exist on the note type, so mining
    writes a valid note instead of silently emptying (or failing on) an unknown field. Best-effort:
    an AnkiConnect hiccup or an unreadable model leaves the map untouched."""
    from overlay.app.anki import is_unreachable

    try:
        real = set(anki.model_field_names(mine_conf.model))
    except Exception as e:  # a hiccup reading fields must skip validation, never disable mining
        log.debug(
            "couldn't read %r fields for validation; keeping map: %s",
            mine_conf.model,
            e,
            exc_info=not is_unreachable(e),
        )
        return
    if not real:  # couldn't read the model's fields — can't validate, don't guess
        return
    bad = [logical for logical, name in mine_conf.fields.items() if name not in real]
    if bad:
        log.warning(
            "mining note type %r is missing field(s) %s — those values won't be written",
            mine_conf.model,
            sorted(mine_conf.fields[k] for k in bad),
        )
        for logical in bad:
            mine_conf.fields.pop(logical, None)


def _build_mining(mc: dict, *, mine: bool):
    if not (mine and mc):
        return None, None
    with otel_metrics.traced("build_mining"):
        try:
            from overlay.app.anki import Anki

            anki = Anki()
            mine_conf = _mine_config_from(mc)
            if (
                not mine_conf.card_format
            ):  # card_format wins wholesale — the `fields` map is unused, so
                _validate_mine_fields(anki, mine_conf)  # validating/dropping it here would mislead
            return anki, mine_conf
        except Exception:  # never let mining setup block attach
            log.warning(
                "mining setup failed (Anki closed?); attach continues without mining", exc_info=True
            )
            return None, None


def build_reader_deps(
    cfg: dict,
    *,
    color: bool = True,
    mine: bool = True,
    known_words: str = "",
    on_anki_unreachable=None,
    on_known_words_error=None,
):
    """Return ``(scorer, anki, mine_conf, dict_set)`` from ``cfg``. ``scorer`` + ``dict_set`` power
    coloring/underlines/pills/tooltips; ``anki`` + ``mine_conf`` power mining.

    ``cfg``'s ``dicts``/``freq``/``pitch`` are dictionary **titles** resolved against the consolidated
    :class:`~overlay.app.dictdb.DictionaryDb` — imported once by ``saitenka import``, never built
    here. A configured title with no imported dictionary is warned and skipped.

    ``known_words`` is ``run``'s plain ``--known word1,word2`` CLI flag (a fallback known-set when
    there's no Anki deck, or Anki isn't reachable) — ``attach`` has no such flag, so its callers just
    leave this empty. ``on_anki_unreachable``/``on_known_words_error`` let ``run`` print a console
    note on those two failure paths instead of the default log-only warning (``attach`` is detached,
    so logging is all it can do) — see :func:`_maybe_start_anki`/:func:`_load_known_words`. This one
    implementation backs both ``run`` and ``attach`` (`cli_run.py`'s own copy of this used to drift
    out of sync with it — see CHANGELOG)."""
    dict_titles = list(cfg.get("dicts") or [])
    freq_titles = list(cfg.get("freq") or [])
    pitch_titles = list(cfg.get("pitch") or [])
    known_cfg = cfg.get("known")
    fallback_words = [w for w in known_words.split(",") if w]

    _mc = cfg.get("mine")
    mc = _mc if isinstance(_mc, dict) else {}
    want_scorer = color or known_cfg or freq_titles or bool(fallback_words)

    from concurrent.futures import ThreadPoolExecutor

    from overlay.app.dictdb import DictionaryDb

    with otel_metrics.traced("dictdb_open"):
        db = DictionaryDb.open()
    # Fan the independent pieces of this out across threads (free-threaded build → real parallelism,
    # not just I/O interleaving): Anki launch/poll, dict-title resolution, and the JLPT table load
    # don't depend on each other, so this turns load_deps_async's wall time from their SUM into their
    # MAX. known-words + the mining Anki object both need Anki reachability decided first, so they
    # wait on that future before their own submit.
    with ThreadPoolExecutor(max_workers=_DEPS_DAG_WIDTH, thread_name_prefix="saitenka-deps") as ex:
        anki_ready = ex.submit(
            _maybe_start_anki, mc, known_cfg, mine=mine, on_unreachable=on_anki_unreachable
        )
        dictset_fut = ex.submit(_build_dict_set, db, dict_titles, freq_titles, pitch_titles)
        jlpt_fut = ex.submit(_load_jlpt_dict, db) if want_scorer else None
        fsrs_fut = ex.submit(_load_fsrs_snapshot, cfg)

        dict_set, freq_rows = dictset_fut.result()
        fd_fut = ex.submit(_load_freq_dict, db, freq_rows, freq_titles) if want_scorer else None

        anki_ready.result()
        kw_fut = (
            ex.submit(
                _load_known_words,
                db,
                known_cfg,
                fallback_words=fallback_words,
                on_error=on_known_words_error,
            )
            if want_scorer
            else None
        )
        mining_fut = ex.submit(_build_mining, mc, mine=mine)

        scorer = None
        if want_scorer:
            from overlay.app.scoring import Palette, Scorer

            assert kw_fut is not None and fd_fut is not None and jlpt_fut is not None  # want_scorer
            scorer = Scorer(
                known=kw_fut.result(),
                freq=fd_fut.result(),
                jlpt=jlpt_fut.result(),
                palette=Palette.from_config(cfg.get("palette")),
                fsrs_snap=fsrs_fut.result(),
            )
        anki, mine_conf = mining_fut.result()

    return scorer, anki, mine_conf, dict_set


def warm_tokenizer() -> None:
    """fugashi/unidic-lite's first-ever ``tokenize()`` call does one-time MeCab tagger/dictionary
    setup that hasn't declared free-threading safety. Measured: ~13ms alone, but ~600ms (46x) when
    it happens to run concurrently with :func:`build_reader_deps`'s background thread pool — real
    contention, not GIL-reactivation (confirmed off throughout) or general system load (confirmed by
    an isolated same-conditions timing) — mutual, too: it slowed the DAG's own tasks down as much as
    they slowed it.

    Callers (`cli_run.py`/`cli.py`) spawn this on its OWN thread, as early as possible — ideally
    before mpv even launches, so it overlaps mpv's own launch/connect dead time instead of sitting
    on the critical path. This is a race, not a guarantee: if mpv comes up unusually fast, the real
    first subtitle line's own ``tokenize()`` call could still overlap this one. In every session
    observed so far mpv's own startup comfortably outlasts this call, so the race resolves in our
    favor in practice."""
    with otel_metrics.traced("warm_tokenizer"):
        from overlay.app.tokenize import tokenize

        tokenize(" ")


def begin_deps_build(cfg: dict, build=None) -> Future[dict]:
    """Start the dep build (coloring/dict/mining collaborators — none touch the mpv IPC) on its OWN
    thread and return a Future for the result. This is the HOISTABLE half of progressive startup:
    ``run`` calls it BEFORE launching mpv so the CPU/IO build overlaps mpv's launch/connect dead time
    (same trick as :func:`warm_tokenizer`), then hands the Future to :func:`load_deps_async` once the
    reader exists.

    ``build`` is a zero-arg callable returning ``(scorer, anki, mine_cfg, dict_set)``; defaults to
    ``build_reader_deps(cfg)`` (attach/plugin mode). ``run`` passes its own closure to honour CLI flags
    (``--dict/--freq/--anki-decks/--mine`` …). The one rule: the builder must NOT touch the mpv IPC.
    The Future resolves to the deps dict, or ``{}`` if the build raised (stay subs-only) — it never
    rejects, so a consumer's ``result()`` can't fault."""
    fut: Future[dict] = Future()

    def _run() -> None:
        try:
            with otel_metrics.traced("load_deps_async"):
                scorer, anki, mine_cfg, dict_set = build() if build else build_reader_deps(cfg)
            fut.set_result(
                {"scorer": scorer, "anki": anki, "mine_cfg": mine_cfg, "dict_set": dict_set}
            )
        except Exception:
            log.warning("background dep load failed — staying subs-only", exc_info=True)
            fut.set_result({})  # signal "done" so the spinner stops

    threading.Thread(target=_run, name="saitenka-deps", daemon=True).start()
    return fut


def load_deps_async(
    reader: Reader, cfg: dict, build=None, *, prebuilt: Future[dict] | None = None
) -> None:
    """Wire a background dep build into the reader: when it lands, the poll loop injects it on the main
    thread (:func:`apply_deps`). Plain subs draw meanwhile; a spinner shows until it lands.

    ``prebuilt`` is a Future from a HOISTED :func:`begin_deps_build` (``run`` starts the build before
    mpv launches so it overlaps launch dead time); without it the build starts now (attach/plugin mode,
    already well past mpv connect). The done-callback sets ``_pending_deps`` from the build thread — the
    same cross-thread hand-off to the poll loop the previous inline version used.

    Callers should have already fired :func:`warm_tokenizer` on its own thread as early as possible."""
    reader._loading = True
    fut = prebuilt if prebuilt is not None else begin_deps_build(cfg, build)
    fut.add_done_callback(lambda f: setattr(reader, "_pending_deps", f.result()))


def apply_deps(reader: Reader, deps: dict) -> None:
    """Inject loaded deps on the main thread and light up coloring/tooltips/mining in place."""
    reader._loading = False
    reader.ov.hide(OverlayId.LOADING)
    reader.scorer = deps.get("scorer")
    reader.anki = deps.get("anki")
    reader.mine_cfg = deps.get("mine_cfg")
    reader.dict_set = deps.get("dict_set")
    from overlay.app import analysis_overlay

    analysis_overlay.on_vocabulary_changed(reader)
    if reader.sub_text:  # re-tokenise + re-score the CURRENT cue so coloring appears now
        reader.set_subtitle(reader.sub_text)
    if reader.anki is not None:
        # Backfill ⊕→✓ from past mining once Anki answers — off the critical path so a not-yet-up
        # (auto-launched) Anki never stalls startup; the watcher flips it on when Anki comes up.
        _spawn_anki_seed_watcher(reader)
    reader.start_prefetch()  # spin up prefetch now that dict_set exists (no-op if still None)
    reader._announce_runtime()  # workers are up now — print the banner with the real count (once)


def draw_loading(reader: Reader) -> None:
    """Draw the throttled top-left spinner while deps load (main thread, from the poll loop)."""
    now = time.monotonic()
    if now < reader._load_next:
        return
    reader._load_next = now + 0.08
    from overlay.app.loading import loading_image

    img = loading_image("saitenka loading dictionaries", reader._load_frame)
    reader._load_frame += 1
    try:
        reader.ov.show(img, x=24, y=24, oid=OverlayId.LOADING)
    except Exception:
        log.debug("loading spinner draw failed", exc_info=True)
