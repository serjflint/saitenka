# /// script
# requires-python = ">=3.11"
# ///
"""Transcribe Yomitan's own Japanese-transform conformance vectors into JSON.

`engine.py` is a verbatim port of Yomitan's `language-transformer.js`, and `japanese_transforms.json`
is dumped from its `japanese-transforms.js`. Yomitan ships the matching conformance suite —
`test/language/japanese-transforms.test.js`, ~1300 `{term, source, rule, reasons}` vectors (every
conjugation class, irregulars, classical + Kansai-ben, and negative "incorrect chain" cases). Those
are the *external* oracle: passing them proves the port reproduces upstream, not just our own reading
of the grammar. Same "steal the real corpus" move as overlay's vendored UAX #14 `LineBreakTest.txt`
(issue #112) and taffylite's vendored gentest fixtures (#150).

The GPL test file is NOT vendored (it is fully derivable from the pinned commit, and the committed
JSON is the reviewable artifact — like taffylite #150 reading a taffy checkout rather than committing
taffy's 533 source files). This reads it from a Yomitan checkout at `YOMITAN_COMMIT` and emits
`tests/fixtures/japanese_transforms_cases.json` (one flat row per vector). Regenerate after bumping
the pin (keep it in sync with the `japanese_transforms.json` dump's Yomitan version):

    git clone https://github.com/yomidevs/yomitan /tmp/yomitan-src
    git -C /tmp/yomitan-src checkout 3af775bda1df
    uv run deinflect/tools/gen_yomitan_cases.py /tmp/yomitan-src
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Pin: read the test file from a yomidevs/yomitan checkout at this commit (the version the shipped
# japanese_transforms.json was dumped from — the corpus must match the data, not Yomitan HEAD).
YOMITAN_COMMIT = "3af775bda1df"

_CHECKOUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/yomitan-src")
SRC = _CHECKOUT / "test" / "language" / "japanese-transforms.test.js"
# `$CORPUS_OUT` redirects the write (the drift guard regenerates into a temp file to diff, never clobber).
OUT = (
    Path(os.environ["CORPUS_OUT"])
    if os.environ.get("CORPUS_OUT")
    else (
        Path(__file__).resolve().parent.parent
        / "tests"
        / "fixtures"
        / "japanese_transforms_cases.json"
    )
)

# One category object: `{ category: '..', valid: true|false, tests: [ {term..}, .. ] }`. The lookahead
# split keys on the category objects (vectors start `{term:`), so each chunk carries one valid flag.
_CATEGORY = re.compile(r"category:\s*'([^']*)'.*?valid:\s*(true|false)", re.DOTALL)
_VECTOR = re.compile(
    r"\{\s*term:\s*'([^']*)',\s*source:\s*'([^']*)',"
    r"\s*rule:\s*(?:'([^']*)'|null),\s*reasons:\s*\[([^\]]*)\]\s*\}"
)


def _reasons(inner: str) -> list[str]:
    return re.findall(r"'([^']*)'", inner)


def build_cases(js: str) -> list[dict]:
    start = js.index("const tests = [")
    block = js[start : js.index("\n];", start)]
    cases: list[dict] = []
    for chunk in re.split(r"(?=\{\s*category:)", block)[1:]:
        cat = _CATEGORY.search(chunk)
        if not cat:
            continue
        category, valid = cat.group(1), cat.group(2) == "true"
        for term, source, rule, reasons in _VECTOR.findall(chunk):
            cases.append(
                {
                    "category": category,
                    "valid": valid,
                    "source": source,
                    "term": term,
                    "rule": rule or None,
                    "reasons": _reasons(reasons),
                }
            )
    return cases


def main() -> None:
    cases = build_cases(SRC.read_text(encoding="utf-8"))
    header = (
        f"Transcribed from Yomitan's test/language/japanese-transforms.test.js @ {YOMITAN_COMMIT} "
        "(github.com/yomidevs/yomitan, GPL-3.0). Each row is one conformance vector: `deinflect(source)` "
        "must (valid=true) / must not (valid=false) yield a candidate reaching `term` whose condition "
        "flags match `rule` and whose transform chain equals `reasons`. Regenerate from a Yomitan "
        "checkout at that commit with deinflect/tools/gen_yomitan_cases.py (see its docstring)."
    )
    OUT.write_text(
        json.dumps({"_source": header, "cases": cases}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    valid = sum(c["valid"] for c in cases)
    print(f"wrote {len(cases)} vectors to {OUT} ({valid} valid, {len(cases) - valid} negative)")


if __name__ == "__main__":
    main()
