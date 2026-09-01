"""How an :class:`EpisodeAnalysis` reads as label/value rows.

Which metrics the overlay shows, what they are called, and how a distribution is abbreviated are all
analysis-feature decisions. They lived in ``render/analysis.py``, which is why a renderer needed the
application's type to be understood at all; the renderer now takes rows and knows nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.features.analysis.episode_analysis import EpisodeAnalysis


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _distribution(value) -> str:
    compact = {"Unranked": "other", **{f"Band {i}": f"B{i}" for i in range(1, 6)}}
    return " · ".join(f"{compact.get(label, label)} {count}" for label, count in value if count)


def analysis_rows(result: EpisodeAnalysis | None, status: str) -> tuple[tuple[str, str], ...]:
    """Rows for the overlay. Before the analysis completes there is nothing to tabulate, so the
    status line *is* the whole table — a value-less row the renderer draws as a bare label."""
    if result is None:
        return ((status, ""),)
    return (
        ("Sentences / content tokens", f"{result.sentence_count} / {result.content_token_count}"),
        ("Unique lemmas / kanji", f"{len(result.unique_lemmas)} / {len(result.unique_kanji)}"),
        ("Unique unknown lemmas", str(len(result.unknown_lemmas))),
        ("Known token coverage", _percent(result.known_token_coverage)),
        ("Known type coverage", _percent(result.known_type_coverage)),
        ("N+1 / N+2 sentences", f"{result.n_plus_one_count} / {result.n_plus_two_count}"),
        (
            "JLPT",
            _distribution(result.jlpt_distribution)
            if result.jlpt_distribution is not None
            else "source unavailable",
        ),
        (
            "Frequency",
            _distribution(result.frequency_distribution)
            if result.frequency_distribution is not None
            else "source unavailable",
        ),
    )
