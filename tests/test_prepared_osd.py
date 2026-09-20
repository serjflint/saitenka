"""Final ASS cache conformance: changed inputs never reuse stale presentation bytes."""

from dataclasses import replace

import pytest
from saitenka_subtitles import decoration, whole_cue

from saitenka.app.prepared_osd import OsdInputs, PreparedOsdCache


def inputs():
    return OsdInputs(
        (whole_cue.OsdEvent("猫", r"\an7\pos(10,20)\fs24", ((0, 1, 0),)),),
        (1, 1, 0, 0),
        (1280, 720),
        ((0, 0xFF0000),),
        (decoration.TokenRule(10, 20, 30, 40, 0x00FF00),),
    )


def test_warmed_payload_is_published_without_lowering_ass_again(monkeypatch):
    cache = PreparedOsdCache()
    cold = cache.prepare(inputs())

    def unavailable(*_args):
        pytest.fail("a prepared artifact must not lower ASS on publication")

    monkeypatch.setattr(whole_cue, "osd_payload", unavailable)
    monkeypatch.setattr(decoration, "payload", unavailable)
    warm = cache.prepare(inputs())

    assert warm == cold
    assert "猫" in warm.payload and r"\p1" in warm.payload


@pytest.mark.parametrize(
    "change",
    [
        {"events": (whole_cue.OsdEvent("犬", r"\an7\pos(10,20)\fs24", ((0, 1, 0),)),)},
        {"mapping": (2, 2, 0, 0)},
        {"resolution": (2560, 1440)},
        {"colors": ((0, 0x0000FF),)},
        {"colors": ()},
        {"rules": (decoration.TokenRule(40, 50, 30, 40, 0x00FF00),)},
        {"rules": ()},
    ],
)
def test_changed_input_rebuilds_exactly_the_cold_artifact(change):
    cache = PreparedOsdCache()
    original = cache.prepare(inputs())
    changed = replace(inputs(), **change)

    rebuilt = cache.prepare(changed)

    assert rebuilt == PreparedOsdCache().prepare(changed)
    assert rebuilt != original


def test_evicted_payload_is_rebuilt_instead_of_reusing_another_cue(monkeypatch):
    cache = PreparedOsdCache(capacity=1)
    first = inputs()
    cache.prepare(first)
    second = replace(first, colors=((0, 0x0000FF),))
    expected = cache.prepare(second)
    cache.prepare(first)
    real = whole_cue.osd_payload

    def lower(cue, colors):
        assert colors == second.colors
        return real(cue, colors) + "rebuilt"

    monkeypatch.setattr(whole_cue, "osd_payload", lower)
    rebuilt = cache.prepare(second)

    assert rebuilt.payload == expected.payload.replace("\n", "rebuilt\n", 1)
