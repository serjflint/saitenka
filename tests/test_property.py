"""Property-based tests (Hypothesis) for the adversarial edge cases the hardening added — the exact
combinatorial spaces example-based tests miss: filename sanitization, IPC message framing under
arbitrary chunking, secret redaction, and the TOML writer vs the TOML parser.
"""

from __future__ import annotations

import json
import tomllib

from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from saitenka.app import paths, report
from saitenka.app.init_wizard import dumps_toml
from saitenka.mpvio.ipc import MpvIPC


@given(st.text())
def test_sanitize_filename_windows_output_is_windows_safe(name):
    out = paths._sanitize(name, "_", windows=True)  # force Windows rules regardless of host
    assert out  # never empty
    assert not paths._INVALID_WIN.search(out)  # no <>:"/\|?* or control chars
    assert not out.endswith((" ", "."))  # no trailing dot/space (Windows strips them)
    assert out.split(".")[0].upper() not in paths._WIN_RESERVED  # not a reserved device name


@given(st.text())
def test_sanitize_filename_posix_output_is_posix_safe(name):
    out = paths._sanitize(name, "_", windows=False)
    assert out  # never empty
    assert not paths._INVALID_POSIX.search(out)  # no "/" or control chars


# JSON-serialised events joined by '\n', then re-sliced at ARBITRARY byte boundaries, must reassemble
# exactly — this is the partial-read bug a MagicMock "returns the next message" fake would hide.
_event = st.builds(
    lambda n, d: {"event": "property-change", "name": n, "data": d},
    st.text(max_size=15),
    st.text(max_size=15),
)


@given(st.lists(_event, max_size=12), st.integers(min_value=1, max_value=7))
@settings(deadline=None)
def test_ipc_feed_reassembles_across_chunk_boundaries(msgs, chunk):
    ipc = MpvIPC("unused")
    blob = b"".join((json.dumps(m) + "\n").encode() for m in msgs)
    for i in range(0, max(len(blob), 1), chunk):
        ipc._feed(blob[i : i + chunk])
    assert ipc.drain_events() == msgs


@given(st.text(alphabet="abcdefABCDEF0123456789-_.", min_size=6, max_size=48))
def test_redaction_removes_secret_values(secret):
    assert secret not in report._redact_secrets(f'jimaku key = "{secret}"')
    assert secret not in report._redact_secrets(f"Authorization: Bearer {secret}")


_key = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8)
# realistic config scalars: no control chars (dumps_toml targets our constrained config, not arbitrary
# TOML) — quotes/backslashes ARE allowed and must round-trip via escaping.
_scalar = st.one_of(
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs")), max_size=25),
)


# A nested table: a dict whose values are themselves scalar tables — the shape of `[profiles.<name>]`
# (#254). The single-level strategy below never produced this, so the two-level writer path went
# untested and shipped a `TypeError: can't serialise dict to TOML` on any config with a named profile.
_subtables = st.dictionaries(_key, st.dictionaries(_key, _scalar, max_size=3), max_size=2)


@example(
    top={}, table={}, profiles={"french": {"nlang": "fr", "tip_height": 0.4}}
)  # the #254 crash
@given(
    st.dictionaries(_key, _scalar, max_size=4),
    st.dictionaries(_key, _scalar, max_size=3),
    _subtables,
)
def test_dumps_toml_round_trips_scalars_and_nested_tables(top, table, profiles):
    assume("mine" not in top and "profiles" not in top)  # the table names we graft on
    cfg = {**top, "mine": table, "profiles": profiles}
    parsed = tomllib.loads(dumps_toml(cfg))  # writer output must parse back to the same values
    for k, v in top.items():
        assert parsed[k] == v
    assert parsed.get("mine", {}) == table
    assert parsed.get("profiles", {}) == profiles  # the two-level `[profiles.<name>]` round-trips


def test_dumps_toml_load_dump_load_is_stable():
    """A realistic config (top-level scalars + a flat ``[mine]`` table + a nested ``[profiles.french]``)
    survives a load→dump→load cycle unchanged, and a second dump is byte-identical — the writer is
    idempotent, so re-writing an existing config never churns it."""
    src = (
        'slang = "ja,jpn,jp"\n'
        "tip_height = 0.4\n\n"
        "[mine]\n"
        'deck = "Saitenka::Known"\n\n'
        "[profiles.french]\n"
        'language = "fr"\n'
        'dicts = ["A", "B"]\n'
    )
    once = tomllib.loads(src)
    dumped = dumps_toml(once)
    twice = tomllib.loads(dumped)
    assert twice == once  # load → dump → load preserves every value, nested table included
    assert dumps_toml(twice) == dumped  # and re-dumping the reloaded config is byte-stable
