"""Corpus drift guard (`poe corpus-drift`) — regenerate each generated corpus and diff it.

Committed *generated* corpora go stale when their pinned upstream or generation grid changes without a
re-bless. This regenerates each into a temp file from the pinned source and byte-diffs it against the
committed artifact: the regenerate-and-diff sibling of `corpus-lock` (which census-locks *stolen* corpora
so they can't silently shrink; this guards *generated* ones so they can't silently drift).

Off the default gate, opt-in like `mutate` / `fuzz`: the transform corpora need Node + a Yomitan checkout
at the generator's pinned commit (resolved from `--yomitan` / `$SAITENKA_YOMITAN` / `~/workspace/yomitan`),
and are **skipped, not failed**, when it's absent or at the wrong commit — a skip is logged loudly, never
silent. The fsrs corpus fetches py-fsrs ephemerally (PEP-723) and always runs. Exit 1 only on real drift.

stdlib only. Run from `overlay/` via poe; the repo root is derived from this file, so cwd doesn't matter.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent  # overlay/tools -> repo root
_SLOT = "__checkout__"  # argv placeholder filled with the resolved checkout path (brace-free: not an f-string)


def _pin(source: Path) -> str:
    """The `YOMITAN_COMMIT = '…'` a generator pins (read from source so the guard can't out-of-sync it)."""
    m = re.search(
        r"""YOMITAN_COMMIT\s*=\s*['"]([0-9a-f]+)['"]""", source.read_text(encoding="utf-8")
    )
    if not m:
        raise ValueError(f"no YOMITAN_COMMIT pin in {source}")
    return m.group(1)


@dataclass(frozen=True)
class Corpus:
    name: str
    committed: Path  # the committed artifact, absolute
    argv: list[
        str
    ]  # regen command (writes to $CORPUS_OUT); `{checkout}` placeholder filled for yomitan
    pin_source: (
        Path | None
    )  # generator whose YOMITAN_COMMIT the checkout must match; None = no checkout


_DEINFLECT_TOOLS = _REPO / "deinflect" / "tools"
_CORPORA = [
    Corpus(
        name="fsrs-vectors",
        committed=_REPO / "overlay/tests/fixtures/fsrs/py_fsrs_retrievability.json",
        argv=["uv", "run", "--no-project", str(_REPO / "overlay/tools/gen_fsrs_vectors.py")],
        pin_source=None,
    ),
    Corpus(
        name="japanese-transforms",
        committed=_REPO / "deinflect/tests/fixtures/japanese_transforms_cases.json",
        argv=[
            "uv",
            "run",
            "--no-project",
            str(_DEINFLECT_TOOLS / "gen_yomitan_cases.py"),
            _SLOT,
        ],
        pin_source=_DEINFLECT_TOOLS / "gen_yomitan_cases.py",
    ),
    Corpus(
        name="french-transforms",
        committed=_REPO / "deinflect/tests/fixtures/french_transforms_cases.json",
        argv=["node", str(_DEINFLECT_TOOLS / "gen_transform_differential.mjs"), "fr", _SLOT],
        pin_source=_DEINFLECT_TOOLS / "gen_transform_differential.mjs",
    ),
]


def _resolve_checkout(cli: str | None) -> Path | None:
    raw = cli or os.environ.get("SAITENKA_YOMITAN") or str(Path.home() / "workspace" / "yomitan")
    p = Path(raw).expanduser()
    return p if (p / ".git").exists() else None


def _at_pin(checkout: Path, pin: str) -> bool:
    try:
        head = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return False
    return head.startswith(pin)


def _regen(corpus: Corpus, checkout: Path | None, out: Path) -> None:
    # _SLOT only appears for pin_source corpora, which _check already gated on a resolved checkout.
    argv = [str(checkout) if a == _SLOT else a for a in corpus.argv]
    subprocess.run(
        argv,
        cwd=_REPO,
        env={**os.environ, "CORPUS_OUT": str(out)},
        check=True,
        capture_output=True,
        text=True,
    )


def _check(corpus: Corpus, checkout: Path | None) -> tuple[str, str]:
    """(status, detail) — status in {ok, drift, skip, error}."""
    if corpus.pin_source is not None:
        pin = _pin(corpus.pin_source)
        if checkout is None:
            return "skip", "no Yomitan checkout (set --yomitan / $SAITENKA_YOMITAN)"
        if not _at_pin(checkout, pin):
            return "skip", f"checkout not at pinned {pin}"
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        out = Path(tmp.name)
        try:
            _regen(corpus, checkout, out)
        except subprocess.CalledProcessError as exc:
            return "error", (exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1]
        except OSError as exc:
            return "error", str(exc)
        got, want = out.read_bytes(), corpus.committed.read_bytes()
    return ("ok", "byte-identical") if got == want else ("drift", "regenerated output differs")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--yomitan", help="path to a yomidevs/yomitan checkout (for the transform corpora)"
    )
    ns = ap.parse_args(argv)
    checkout = _resolve_checkout(ns.yomitan)

    results = [(c.name, *_check(c, checkout)) for c in _CORPORA]
    drift = [r for r in results if r[1] in {"drift", "error"}]
    for name, status, detail in results:
        print(f"  [{status:5}] {name}: {detail}")
    if drift:
        print(
            f"corpus-drift: {len(drift)} corpus/corpora drifted — re-bless deliberately (regenerate + review)."
        )
        return 1
    skipped = sum(s == "skip" for _, s, _ in results)
    print(f"corpus-drift: OK ({len(results) - skipped} checked, {skipped} skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
