"""Build the SessionController's collaborators (scorer, anki, mine config, dict set) from a loaded config, and
the SessionController-side glue that loads them progressively in the background.

``run`` assembles these from CLI flags interleaved with progress prints; ``attach``/plugin mode has
no flags, so it needs the same objects derived purely from ``overlay.toml``. Without them the overlay
is a bare subtitle renderer — no FSRS/known coloring, no JLPT underlines, no frequency pills, no
dictionary tooltips, no mining. Anki-dependent pieces degrade to None (logged) when Anki is closed,
so a missing Anki never blocks attaching.

``load_deps_async`` takes a ``DepsLoad`` port; injecting what it built is the SessionController's own state
transition and lives there.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.features.mining.mining_controller import MiningIdentity, MiningSpec, MiningTarget
from saitenka.app.mining_config import mine_config_from
from saitenka.app.overlay_ids import OverlayId

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka_wordstate.fsrs import KnownSnap

    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.scoring import Coloring

log = logging.getLogger(__name__)

# Four workers cap the independent dependency loads. No worker calls .result() on another future.
_DEPS_DAG_WIDTH = 4


def _maybe_start_anki(mc: dict, known_cfg, *, mine: bool, on_unreachable=None) -> None:
    """If mining or Anki-backed coloring is configured and AnkiConnect isn't up, launch Anki
    fire-and-forget and warn — never block startup on the poll. The session capability probe later
    backfills mining once Anki answers. ``on_unreachable(*, launched)`` lets an
    interactive caller (``run``) print a console note — ``launched`` is False when Anki couldn't even
    be started (not found / launch failed), a distinct warning — in addition to the log-only warning
    (``attach`` is detached, so it passes no callback)."""
    if not ((mine and mc) or known_cfg):
        return
    from saitenka.app.anki import anki_reachable, launch_anki

    with otel_metrics.traced("anki_ensure_running"):
        if anki_reachable():
            return
        launched = launch_anki()
        if launched:
            log.warning("Anki not reachable — launching it; mining/coloring enables once it's up")
        else:  # couldn't find the Anki binary or the launch itself failed — it won't come up on its own
            log.warning(
                "Anki is unavailable and couldn't be started (not found or failed to launch) — "
                "mining/coloring stays off until you open Anki manually"
            )
        if on_unreachable is not None:
            on_unreachable(launched=launched)


def _build_dict_set(
    db,
    dict_titles: list[str],
    freq_titles: list[str],
    pitch_titles: list[str],
    language: str = "jp",
):
    """Returns ``(dict_set, freq_rows)`` — ``freq_rows`` is reused by ``_load_freq_dict`` so it isn't
    re-resolved. A configured title with no imported dictionary is warned and skipped."""
    dict_set = None
    freq_rows = None
    if not (dict_titles or freq_titles or pitch_titles):
        return dict_set, freq_rows

    from saitenka.app.dictionary import DictionarySet

    with otel_metrics.traced("build_dict_set"):
        d_rows, d_miss = db.resolve(dict_titles)
        freq_rows, f_miss = db.resolve(freq_titles)
        p_rows, p_miss = db.resolve(pitch_titles)
        for kind, miss in (("dict", d_miss), ("freq", f_miss), ("pitch", p_miss)):
            if miss:
                import sys

                from saitenka.app.dictionary import _MISSING_HINT

                msg = (
                    f"{kind}(s) not imported, skipped: {', '.join(repr(m) for m in miss)}. "
                    f"{_MISSING_HINT}"
                )
                log.warning(msg)
                print(msg, file=sys.stderr, flush=True)
        if d_rows or freq_rows or p_rows:
            dict_set = DictionarySet.from_rows(db, d_rows, freq_rows, p_rows, language=language)
    return dict_set, freq_rows


def make_dict_scoper(cfg: dict):
    """A ``profile → DictionarySet | None`` callable the live switcher uses to re-scope dictionaries on
    a profile cycle (#254 W3). Captures the raw ``cfg`` and one DB handle; each call resolves that
    profile's scoped ``dicts``/``freq``/``pitch`` titles (``None`` when it scopes none, i.e. inherits the
    top-level set — the reader then keeps its current dict set). Cheap: ``from_db`` resolves titles to
    rows, it doesn't bulk-load (lookups stay lazy SQL)."""
    from saitenka.app.dictdb import DictionaryDb
    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.profiles import scope_config

    db = DictionaryDb.open()

    def scope(profile):
        override = None if profile.name == "default" else profile.name
        scoped = scope_config(cfg, override=override)
        dicts = scoped.get("dicts") or []
        freq = scoped.get("freq") or []
        pitch = scoped.get("pitch") or []
        if not (dicts or freq or pitch):
            return None
        return DictionarySet.from_db(db, dicts, freq, pitch, language=profile.langs.main)

    return scope


def _spawn_known_refresh(store, known_cfg) -> None:
    """Background: reconcile the known-word cache against Anki (subset mod-time diff) so the NEXT launch
    reads a current set off disk. Fire-and-forget — a failure (Anki down) leaves the cache as-is; this
    session already colored from it. Daemon so it never holds up shutdown."""
    from saitenka_wordstate.known import refresh_known_cache

    from saitenka.app.anki import is_unreachable

    def _refresh() -> None:
        try:
            client, trace = _client_and_trace()
            refresh_known_cache(store, known_cfg, client, trace=trace)
        except Exception as e:
            # Anki down is expected — one compact line, traceback only for a real fault.
            log.debug(
                "background known-word cache refresh failed: %s", e, exc_info=not is_unreachable(e)
            )

    threading.Thread(target=_refresh, name="saitenka-known-refresh", daemon=True).start()


def _client_and_trace():
    """The AnkiConnect client the known-word loader reads through, plus our tracing.

    The word-state package takes both as inputs: it decides what a known word IS, never where to
    reach for one or how the call is instrumented. Host and api-key resolution stay with `Anki`, the
    one place that owns them.
    """
    from saitenka import otel_metrics
    from saitenka.app.anki import Anki

    return Anki()._client, otel_metrics.traced


def _load_known_words(store, known_cfg, *, fallback_words=(), on_error=None):
    """Cache-first: serve the last-known set from our SQLite cache (~1 ms) and reconcile in the
    background, so the ~190 ms IO-bound AnkiConnect load is off the startup critical path. A cache miss
    (first launch / changed config) falls back to a blocking full load that populates the cache.

    ``fallback_words`` is ``run``'s plain ``--known word1,word2`` list (``attach`` has none);
    ``on_error`` lets ``run`` print a console note instead of the default log-only Anki-failure warning."""
    from saitenka_wordstate.known import KnownWords, refresh_known_cache

    from saitenka.app.anki import ANKI_DOWN_ERRORS

    if not known_cfg:
        return KnownWords.from_set(fallback_words)
    try:
        cached = KnownWords.from_cache(store, known_cfg)
    except Exception:
        log.debug("known-word cache read failed; doing a full load", exc_info=True)
        cached = None
    if cached is not None:
        _spawn_known_refresh(
            store, known_cfg
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
        client, trace = _client_and_trace()
        return refresh_known_cache(store, known_cfg, client, trace=trace)
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
    from saitenka_wordstate.fsrs import load_knownness

    return load_knownness(collection)


def _load_freq_dict(db, freq_rows, freq_titles: list[str]):
    from saitenka_wordstate import FREQ_BAND_TOP_X

    from saitenka.app.dict_meta import FreqDict

    with otel_metrics.traced("load_freq_dict"):
        # freq_rows is set iff we resolved dict sources above; the coloring band uses the first freq.
        if freq_rows is None:
            freq_rows, _ = db.resolve(freq_titles)
        # Cap at the band's top_x: rarer ranks can't color a word (banded freq_mode), so loading them
        # is ~200ms of dead startup work on a big freq like JPDBv2 — the dep-load critical path.
        return FreqDict.from_db(db, freq_rows[0], top_x=FREQ_BAND_TOP_X) if freq_rows else None


def _load_jlpt_dict(db):
    from saitenka.app.dict_meta import JlptDict

    with otel_metrics.traced("load_jlpt_dict"):
        return JlptDict.load(db)


def mining_spec_from_config(
    cfg: dict,
    identity: MiningIdentity,
    *,
    enabled: bool = True,
) -> MiningSpec:
    """Resolve the desired local target without contacting Anki."""
    raw = cfg.get("mine")
    mc = raw if isinstance(raw, dict) else {}
    if not enabled or not mc:
        return MiningSpec.disabled(identity)
    prepared = mine_config_from(mc)
    effective = {**mc, "deck": prepared.deck, "model": prepared.model}
    return MiningSpec(identity, effective)


def _validate_mine_fields(anki, mine_conf) -> None:
    """Drop + warn about configured field-map targets that don't exist on the note type, so mining
    writes a valid note instead of silently emptying (or failing on) an unknown field. Best-effort:
    an AnkiConnect hiccup or an unreadable model leaves the map untouched."""
    from saitenka.app.anki import is_unreachable

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
    if mine_conf.word_audio_pack and mine_conf.word_audio_field not in real:
        log.warning(
            "mining note type %r has no %r field — word-audio stays off",
            mine_conf.model,
            mine_conf.word_audio_field,
        )
        mine_conf.word_audio_pack = None


def _build_mining(mc: dict, *, mine: bool):
    if not (mine and mc):
        return None, None
    with otel_metrics.traced("build_mining"):
        try:
            from saitenka.app.anki import Anki

            anki = Anki()
            mine_conf = mine_config_from(mc)
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
    language: str | None = None,
):
    """Return ``(scorer, anki, mine_conf, dict_set)`` from ``cfg``. ``scorer`` + ``dict_set`` power
    coloring/underlines/pills/tooltips; ``anki`` + ``mine_conf`` power mining.

    ``cfg``'s ``dicts``/``freq``/``pitch`` are dictionary **titles** resolved against the consolidated
    :class:`~saitenka.app.dictdb.DictionaryDb` — imported once by ``saitenka import``, never built
    here. A configured title with no imported dictionary is warned and skipped.

    ``known_words`` is ``run``'s plain ``--known word1,word2`` CLI flag (a fallback known-set when
    there's no Anki deck, or Anki isn't reachable) — ``attach`` has no such flag, so its callers just
    leave this empty. ``on_anki_unreachable``/``on_known_words_error`` let ``run`` print a console
    note on those two failure paths instead of the default log-only warning (``attach`` is detached,
    so logging is all it can do) — see :func:`_maybe_start_anki`/:func:`_load_known_words`. This one
    implementation backs both ``run`` and ``attach`` (the run launcher's own copy used to drift
    out of sync with it — see CHANGELOG)."""
    from saitenka.app.profiles import resolve_profile

    dict_titles = list(cfg.get("dicts") or [])
    freq_titles = list(cfg.get("freq") or [])
    pitch_titles = list(cfg.get("pitch") or [])
    # `language` is passed explicitly by the run path (its effective_cfg drops the profile table, so
    # resolve_profile here would wrongly return the JP default); attach passes the full cfg and lets it
    # resolve. Either way it routes the deinflection chain to the right rule set.
    if language is None:
        language = resolve_profile(cfg).langs.main
    known_cfg = cfg.get("known")
    fallback_words = [w for w in known_words.split(",") if w]

    _mc = cfg.get("mine")
    mc = _mc if isinstance(_mc, dict) else {}
    want_scorer = color or known_cfg or freq_titles or bool(fallback_words)

    from concurrent.futures import ThreadPoolExecutor

    from saitenka_wordstate.cache import KnownWordCache

    from saitenka.app.dictdb import DictionaryDb

    with otel_metrics.traced("dictdb_open"):
        db = DictionaryDb.open()
    known_cache = KnownWordCache.open(db.path.with_name("anki-known.sqlite"), legacy_path=db.path)
    # Fan the independent pieces of this out across threads (free-threaded build → real parallelism,
    # not just I/O interleaving): Anki launch/poll, dict-title resolution, and the JLPT table load
    # don't depend on each other, so this turns load_deps_async's wall time from their SUM into their
    # MAX. known-words + the mining Anki object both need Anki reachability decided first, so they
    # wait on that future before their own submit.
    with ThreadPoolExecutor(max_workers=_DEPS_DAG_WIDTH, thread_name_prefix="saitenka-deps") as ex:
        anki_ready = ex.submit(
            _maybe_start_anki, mc, known_cfg, mine=mine, on_unreachable=on_anki_unreachable
        )
        dictset_fut = ex.submit(
            _build_dict_set, db, dict_titles, freq_titles, pitch_titles, language
        )
        jlpt_fut = ex.submit(_load_jlpt_dict, db) if want_scorer else None
        fsrs_fut = ex.submit(_load_fsrs_snapshot, cfg)

        dict_set, freq_rows = dictset_fut.result()
        fd_fut = ex.submit(_load_freq_dict, db, freq_rows, freq_titles) if want_scorer else None

        anki_ready.result()
        kw_fut = (
            ex.submit(
                _load_known_words,
                known_cache,
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
            from saitenka_wordstate import Scorer

            from saitenka.app.scoring import Coloring, Palette

            assert kw_fut is not None and fd_fut is not None and jlpt_fut is not None  # want_scorer
            scorer = Coloring(
                Scorer(
                    known=kw_fut.result(),
                    freq=fd_fut.result(),
                    jlpt=jlpt_fut.result(),
                    fsrs_snap=fsrs_fut.result(),
                ),
                Palette.from_config(cfg.get("palette")),
            )
        anki, mine_conf = mining_fut.result()

    return scorer, anki, mine_conf, dict_set


def warm_tokenizer(tokenizer: str = "unidic") -> None:
    """fugashi/unidic-lite's first-ever ``tokenize()`` call does one-time MeCab tagger/dictionary
    setup that hasn't declared free-threading safety. Measured: ~13ms alone, but ~600ms (46x) when
    it happens to run concurrently with :func:`build_reader_deps`'s background thread pool — real
    contention, not GIL-reactivation (confirmed off throughout) or general system load (confirmed by
    an isolated same-conditions timing) — mutual, too: it slowed the DAG's own tasks down as much as
    they slowed it.

    Run and attach start this on its own thread before mpv launch. The annotation coordinator retains
    the completion handle and serializes the first real tokenization after it, so startup overlap cannot
    turn into concurrent initialization.

    ``tokenizer`` is the active profile's tokenizer name (#254). The warm cost being amortised here is
    fugashi-specific, so a non-``unidic`` strategy is a no-op — nothing to prime ahead of mpv."""
    if tokenizer != "unidic":
        return
    with otel_metrics.traced("warm_tokenizer"):
        from saitenka_tokenize.japanese import tokenize

        tokenize(" ")


def begin_tokenizer_warm(tokenizer: str = "unidic") -> Future[None]:
    """Start tokenizer initialization and retain its completion for annotation serialization."""
    future: Future[None] = Future()

    def _run() -> None:
        try:
            warm_tokenizer(tokenizer)
        except Exception as error:  # noqa: BLE001  # becomes a bounded annotation failure
            future.set_exception(error)
        else:
            future.set_result(None)

    threading.Thread(
        target=_run,
        name="saitenka-tokenizer-warm",
        daemon=True,
    ).start()
    return future


@dataclass(frozen=True, slots=True)
class DependencyBundle:
    """A full collaborator set qualified by the profile generation it was built for."""

    identity: MiningIdentity
    scorer: Coloring | None = None
    mining: MiningTarget | None = None
    dictionaries: DictionarySet | None = None
    failed: bool = False


def begin_deps_build(
    cfg: dict,
    build=None,
    *,
    identity: MiningIdentity | None = None,
) -> Future[DependencyBundle]:
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
    target_identity = identity or MiningIdentity("default", 0)
    fut: Future[DependencyBundle] = Future()

    def _run() -> None:
        try:
            with otel_metrics.traced("load_deps_async"):
                scorer, anki, mine_cfg, dict_set = build() if build else build_reader_deps(cfg)
            target = (
                MiningTarget(target_identity, anki, mine_cfg)
                if anki is not None and mine_cfg is not None
                else None
            )
            fut.set_result(DependencyBundle(target_identity, scorer, target, dict_set))
        except Exception:
            log.warning("background dep load failed — staying subs-only", exc_info=True)
            fut.set_result(DependencyBundle(target_identity, failed=True))

    threading.Thread(target=_run, name="saitenka-deps", daemon=True).start()
    return fut


@dataclass(frozen=True, slots=True)
class DepsLoad:
    """What starting a background dep load needs: say we are loading, and hand the result over.

    Five members, cut facts-from-acts. `publish` and `announce` are deliberately two: the order
    between them is the whole hand-off, and one combined act would hide which came first.
    """

    #: Draw plain subs and run the spinner until the build lands.
    begin_loading: Callable[[], None]
    #: Annotation goes off-thread for the duration — the build is holding the CPU.
    enable_async_annotation: Callable[[], None]
    #: Publish the built deps. Called from the *build* thread, so it must not act on them.
    publish: Callable[[DependencyBundle], None]
    #: Arm the deadline that injects them on the session turn. Returns whether it armed; nothing
    #: reads that, because with no timer the deps still land on the next session turn.
    announce: Callable[[], object]


def load_deps_async(
    ports: DepsLoad,
    cfg: dict,
    build=None,
    *,
    prebuilt: Future[DependencyBundle] | None = None,
    identity: MiningIdentity | None = None,
) -> None:
    """Wire a background dep build into the session: when it lands, the session turn injects it on
    the main thread (:func:`apply_deps`). Plain subs draw meanwhile; a named timer animates the spinner.

    ``prebuilt`` is a Future from a HOISTED :func:`begin_deps_build` (``run`` starts the build before
    mpv launches so it overlaps launch dead time); without it the build starts now (attach/plugin mode,
    already well past mpv connect). The done-callback hands the result over from the build thread and
    arms the deadline that injects it, so the injection still happens on the session turn.

    Callers should have already fired :func:`warm_tokenizer` on its own thread as early as possible."""
    ports.begin_loading()
    ports.enable_async_annotation()
    fut = prebuilt if prebuilt is not None else begin_deps_build(cfg, build, identity=identity)

    def landed(finished: Future[DependencyBundle]) -> None:
        # Order matters and is the whole hand-off: publish the value, then say so. Announcing first
        # would let the due event fire against deps that are still None.
        ports.publish(finished.result())
        ports.announce()

    fut.add_done_callback(landed)


@dataclass(frozen=True, slots=True)
class ProfileDependencyApply:
    """Owner-thread acts used when a profile-qualified dependency bundle settles."""

    load_ports: Callable[[], DepsLoad]
    selected_profile: Callable[[], object]
    select_mining: Callable[[MiningSpec], None]
    retire_current: Callable[[], None]
    stop_loading: Callable[[], None]
    install: Callable[[DependencyBundle], None]
    arrived: Callable[[], None]


class ProfileDependencies:
    """Own profile-qualified dependency admission, handoff, and stale-result refusal."""

    def __init__(self, identity: MiningIdentity, apply: ProfileDependencyApply) -> None:
        self._identity = identity
        self._apply = apply
        self._builder_for: (
            Callable[[object, MiningIdentity], tuple[dict, Callable[[], tuple]]] | None
        ) = None
        self._spec_for: Callable[[object, MiningIdentity], MiningSpec] | None = None
        self._inflight: set[MiningIdentity] = set()
        self._settled_identity: MiningIdentity | None = None
        self._pending: list[DependencyBundle] = []
        self._pending_lock = threading.Lock()

    @property
    def identity(self) -> MiningIdentity:
        return self._identity

    @property
    def ready(self) -> bool:
        with self._pending_lock:
            return bool(self._pending)

    @property
    def pending(self) -> tuple[DependencyBundle, ...]:
        with self._pending_lock:
            return tuple(self._pending)

    def configure(self, builder_for, spec_for) -> None:
        self._builder_for = builder_for
        self._spec_for = spec_for
        self._apply.select_mining(
            self._resolve_spec(self._apply.selected_profile(), self._identity)
        )

    def select(self, profile) -> None:
        self._identity = MiningIdentity(profile.name, self._identity.generation + 1)
        self._settled_identity = None
        if self._spec_for is not None:
            self._apply.select_mining(self._resolve_spec(profile, self._identity))
        self._apply.retire_current()
        self._submit(profile, self._identity)

    def _resolve_spec(self, profile, identity: MiningIdentity) -> MiningSpec:
        assert self._spec_for is not None
        try:
            return self._spec_for(profile, identity)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("mining disabled for profile %s: %s", profile.name, exc)
            return MiningSpec.disabled(identity, str(exc))

    def _submit(self, profile, identity: MiningIdentity) -> None:
        if self._builder_for is None or identity in self._inflight:
            return
        cfg, build = self._builder_for(profile, identity)
        self._inflight.add(identity)
        load_deps_async(self._apply.load_ports(), cfg, build, identity=identity)

    def load(self, cfg: dict, build=None, *, prebuilt=None) -> None:
        self._inflight.add(self._identity)
        load_deps_async(
            self._apply.load_ports(),
            cfg,
            build,
            prebuilt=prebuilt,
            identity=self._identity,
        )

    def publish(self, bundle: DependencyBundle) -> None:
        """Publish from a build thread; owner-thread application happens in :meth:`drain`."""
        with self._pending_lock:
            self._pending.append(bundle)

    def drain(self) -> None:
        with self._pending_lock:
            pending, self._pending = self._pending, []
        for bundle in pending:
            self.accept(bundle)

    def accept(self, bundle: DependencyBundle) -> bool:
        """Apply one owner-thread bundle, refusing stale profile generations."""
        self._inflight.discard(bundle.identity)
        if bundle.identity != self._identity:
            if self._builder_for is None:
                self._apply.stop_loading()
            elif self._settled_identity != self._identity:
                self._submit(self._apply.selected_profile(), self._identity)
            return False
        self._apply.stop_loading()
        self._apply.install(bundle)
        self._apply.arrived()
        self._settled_identity = bundle.identity
        return True


def draw_loading(surfaces, frame: int) -> None:
    """Draw one top-left spinner frame after its named timer becomes due."""
    from saitenka.app.loading import loading_image

    img = loading_image("saitenka loading dictionaries", frame)
    try:
        surfaces.present(img, 24, 24, oid=OverlayId.LOADING)
    except Exception:
        log.debug("loading spinner draw failed", exc_info=True)
