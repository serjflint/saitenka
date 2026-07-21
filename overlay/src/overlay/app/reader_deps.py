"""Build the Reader's collaborators (scorer, anki, mine config, dict set) from a loaded config.

``run`` assembles these from CLI flags interleaved with progress prints; ``attach``/plugin mode has
no flags, so it needs the same objects derived purely from ``overlay.toml``. Without them the overlay
is a bare subtitle renderer — no FSRS/known coloring, no JLPT underlines, no frequency pills, no
dictionary tooltips, no mining. Anki-dependent pieces degrade to None (logged) when Anki is closed,
so a missing Anki never blocks attaching.
"""

from __future__ import annotations

import logging

from overlay.app.config import expand_paths

log = logging.getLogger(__name__)


def build_reader_deps(cfg: dict, *, color: bool = True, mine: bool = True):
    """Return ``(scorer, anki, mine_conf, dict_set)`` from ``cfg``. ``scorer`` + ``dict_set`` power
    coloring/underlines/pills/tooltips; ``anki`` + ``mine_conf`` power mining."""
    dict_paths = expand_paths(cfg.get("dicts") or [])
    freq_paths = expand_paths(cfg.get("freq") or [])
    pitch_paths = expand_paths(cfg.get("pitch") or [])
    known_cfg = cfg.get("known")

    mc = cfg.get("mine") if isinstance(cfg.get("mine"), dict) else {}
    # If mining or Anki-backed coloring is configured, try to start Anki for the user (warn, never
    # block) so they don't have to remember to launch it before playing.
    if (mine and mc) or known_cfg:
        from overlay.app.anki import ensure_anki_running

        if not ensure_anki_running():
            log.warning("Anki not reachable — coloring falls back to freq+JLPT, mining disabled")

    dict_set = None
    if dict_paths:
        from overlay.app.dictionary import DictionarySet

        dict_set = DictionarySet.load(dict_paths, freq_paths=freq_paths, pitch_paths=pitch_paths)

    scorer = None
    if color or known_cfg or freq_paths:
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
        fd = FreqDict.load(freq_paths[0]) if freq_paths else None
        scorer = Scorer(known=kw, freq=fd, jlpt=JlptDict.load())

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
