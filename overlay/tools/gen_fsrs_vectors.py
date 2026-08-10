# /// script
# requires-python = ">=3.11"
# dependencies = ["fsrs==6.3.1"]
# ///
"""Emit FSRS-6 retrievability reference vectors from py-fsrs — the external oracle for `app/fsrs.py`.

`app.fsrs.retrievability` is a hand-copy of FSRS-6's `R = (1 + (0.9^(1/decay) - 1)·t/S)^decay`,
"matches py-fsrs card.py exactly". The existing unit test only checks it against a re-transcription of
that same formula (self-consistent, not independent). This drives the **real** py-fsrs
(`Scheduler.get_card_retrievability`, an independent code path) over a grid of `(stability,
elapsed_days, decay)` and vendors its outputs, so the assertion is against upstream — the same
"generate from the authoritative implementation" move as taffy's Chrome-derived gentest (#150).

py-fsrs is used here **ephemerally** (PEP-723 inline dep), never added to the project env — the
committed JSON is the artifact; the test reads it with no new dependency. `decay` is recorded as the
value `retrievability` actually receives at runtime: Anki stores the positive `w20`, and
`_build_card_info` passes its negation, so decay is negative (`-w20`).

Regenerate:  uv run overlay/tools/gen_fsrs_vectors.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from fsrs import Card, Scheduler, State  # provided by the PEP-723 dep above

# `$CORPUS_OUT` redirects the write (the drift guard regenerates into a temp file to diff, never clobber).
OUT = (
    Path(os.environ["CORPUS_OUT"])
    if os.environ.get("CORPUS_OUT")
    else (
        Path(__file__).resolve().parent.parent
        / "tests"
        / "fixtures"
        / "fsrs"
        / "py_fsrs_retrievability.json"
    )
)

# w20 is FSRS-6's decay parameter (Anki stores it positive; runtime decay = -w20). 0.1542 is py-fsrs's
# default; 0.5 is the fixed FSRS-4.5 decay; the others probe the curve's sensitivity.
_W20 = [0.1542, 0.1, 0.2, 0.5]
_STABILITY = [1.0, 5.0, 10.0, 50.0, 200.0, 1000.0]
_ELAPSED_DAYS = [0, 1, 7, 30, 100, 365, 1000]

_EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _py_fsrs_r(stability: float, elapsed_days: int, w20: float) -> float:
    params = list(Scheduler().parameters)
    params[20] = w20
    scheduler = Scheduler(parameters=params, enable_fuzzing=False)
    card = Card(
        state=State.Review,
        stability=stability,
        last_review=_EPOCH - dt.timedelta(days=elapsed_days),
    )
    return scheduler.get_card_retrievability(card, _EPOCH)


def main() -> None:
    import importlib.metadata as md

    cases = [
        {"s": s, "elapsed": float(e), "decay": -w, "r": _py_fsrs_r(s, e, w)}
        for w in _W20
        for s in _STABILITY
        for e in _ELAPSED_DAYS
    ]
    header = (
        f"FSRS-6 retrievability reference vectors from py-fsrs {md.version('fsrs')} "
        "(github.com/open-spaced-repetition/py-fsrs, MIT) via Scheduler.get_card_retrievability. "
        "Each row: app.fsrs.retrievability(s, elapsed, decay) must equal `r` (decay = -w20, the "
        "negated value the card-data path passes). Regenerate with overlay/tools/gen_fsrs_vectors.py."
    )
    OUT.write_text(
        json.dumps({"_source": header, "cases": cases}, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(cases)} vectors to {OUT}")


if __name__ == "__main__":
    main()
