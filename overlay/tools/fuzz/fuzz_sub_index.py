"""Coverage-guided fuzz target for the subtitle-cue parser (atheris / libFuzzer).

Contract under test: ``parse_cues`` (and the ``parse_srt`` / ``parse_ass`` it dispatches to) is robust —
for ANY input (malformed .srt/.ass, mixed encodings, control chars, truncated rows) it returns a
possibly-empty list of cues and NEVER raises. A crash here is a real robustness gap: a corrupt subtitle
file loaded mid-anime must never take down the overlay. This complements the Hypothesis property tests
(structured, generator-driven) with coverage-guided byte mutation that reaches paths a generator won't.

Run via ``uv run poe fuzz`` — pinned to CPython 3.13 (see the poe task) because atheris is a
C-extension (libFuzzer) that re-enables / can't load under the free-threaded 3.14t default runtime; the
target code itself is pure-Python and runs fine on 3.13. Opt-in, NOT part of ``poe all``.
"""

import sys

import atheris

with atheris.instrument_imports():
    from overlay.app import sub_index


def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    ext = fdp.PickValueInList([".srt", ".ass", ".vtt", ".txt", ""])
    content = fdp.ConsumeUnicodeNoSurrogates(fdp.remaining_bytes())
    cues = sub_index.parse_cues(content, "fuzz" + ext)  # the invariant: this must not raise
    idx = sub_index.SubIndex(cues)
    if len(idx):  # exercise the index/seek surface too when we parsed something
        idx.locate(0.0)
        idx.target(0, 1)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
