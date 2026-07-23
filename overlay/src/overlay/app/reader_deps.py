"""Build the Reader's collaborators (scorer, anki, mine config, dict set) from a loaded config.

``run`` assembles these from CLI flags interleaved with progress prints; ``attach``/plugin mode has
no flags, so it needs the same objects derived purely from ``overlay.toml``. Without them the overlay
is a bare subtitle renderer — no FSRS/known coloring, no JLPT underlines, no frequency pills, no
dictionary tooltips, no mining. Anki-dependent pieces degrade to None (logged) when Anki is closed,
so a missing Anki never blocks attaching.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def build_reader_deps(cfg: dict, *, color: bool = True, mine: bool = True):
    """Return ``(scorer, anki, mine_conf, dict_set)`` from ``cfg``. ``scorer`` + ``dict_set`` power
    coloring/underlines/pills/tooltips; ``anki`` + ``mine_conf`` power mining.

    ``cfg``'s ``dicts``/``freq``/``pitch`` are dictionary **titles** resolved against the consolidated
    :class:`~overlay.app.dictdb.DictionaryDb` — imported once by ``saitenka-overlay import``, never built
    here. A configured title with no imported dictionary is warned and skipped."""
    dict_titles = list(cfg.get("dicts") or [])
    freq_titles = list(cfg.get("freq") or [])
    pitch_titles = list(cfg.get("pitch") or [])
    known_cfg = cfg.get("known")

    _mc = cfg.get("mine")
    mc = _mc if isinstance(_mc, dict) else {}
    # If mining or Anki-backed coloring is configured, try to start Anki for the user (warn, never
    # block) so they don't have to remember to launch it before playing.
    if (mine and mc) or known_cfg:
        from overlay.app.anki import ensure_anki_running

        if not ensure_anki_running():
            log.warning("Anki not reachable — coloring falls back to freq+JLPT, mining disabled")

    from overlay.app.dictdb import DictionaryDb

    db = DictionaryDb.open()

    dict_set = None
    freq_rows = None
    if dict_titles or freq_titles or pitch_titles:
        from overlay.app.dictionary import DictionarySet

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

    scorer = None
    if color or known_cfg or freq_titles:
        from overlay.app.scoring import Scorer
        from overlay.app.wordlists import FreqDict, JlptDict, KnownWords

        kw = None
        if known_cfg:
            try:
                kw = KnownWords.from_ankiconnect(known_cfg)
            except Exception:  # Anki closed / AnkiConnect down — color by freq+JLPT only
                log.warning("known-word load from Anki failed; coloring without a known set")
        if kw is None:
            kw = KnownWords.from_set([])
        # freq_rows is set iff we resolved dict sources above; the coloring band uses the first freq.
        if freq_rows is None:
            freq_rows, _ = db.resolve(freq_titles)
        fd = FreqDict.from_db(db, freq_rows[0]) if freq_rows else None
        scorer = Scorer(known=kw, freq=fd, jlpt=JlptDict.load(db))

    anki = mine_conf = None
    if mine and mc:
        try:
            from overlay.app.anki import Anki, MineConfig

            anki = Anki()
            mine_conf = MineConfig(
                deck=mc.get("deck", "Saitenka::Mining"), model=mc.get("model", "Lapis")
            )
        except Exception:  # never let mining setup block attach
            log.warning("mining setup failed (Anki closed?); attach continues without mining")
            anki = mine_conf = None

    return scorer, anki, mine_conf, dict_set
