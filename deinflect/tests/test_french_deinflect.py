"""French deinflection — the second language the shared engine drives (#254 W2).

Unlike Japanese, Yomitan ships NO ``french-transforms.test.js`` at the pinned commit
(``dump_french_transforms.mjs``'s ``c0c3702963c2``) — only the ``french-transforms.js`` *data*. So there
is no upstream suite to steal (the JP ``test_yomitan_transform_corpus`` move doesn't apply). These vectors
are therefore HAND-AUTHORED from the descriptor's own transform set — representative conjugations plus
negative controls — not a conformance corpus. They pin that the French data loads and the engine reduces
real inflections to their dictionary form; they do NOT claim upstream parity.

Scope note: the descriptor covers present/imperfect/future/conditional/subjunctive/preterite, plural, and
the present participle — but NOT the past participle or feminine agreement, so ``mangé``/``heureuse`` do
not deinflect (asserted below as the honest boundary, a negative control that would break if someone
"helpfully" widened the data without vectors).
"""

from __future__ import annotations

import pytest
from saitenka_deinflect import get_deinflector, inflection_chain

# (surface, lemma, expected transform chain) — each verified against the shipped french_transforms.json.
_CASES = [
    ("parle", "parler", ["present indicative"]),
    ("parlons", "parler", ["present indicative"]),
    ("finis", "finir", ["present indicative"]),
    ("suis", "être", ["present indicative"]),
    ("avez", "avoir", ["present indicative"]),
    ("chats", "chat", ["plural"]),
]


@pytest.mark.parametrize(("surface", "lemma", "chain"), _CASES)
def test_french_inflection_chain(surface: str, lemma: str, chain: list[str]):
    assert get_deinflector("fr").inflection_chain(surface, lemma) == chain


def test_module_level_dispatch_selects_french():
    # The overlay's chokepoint calls the module-level function with language= — it must route to French,
    # not the Japanese default (which has no path from "parlons").
    assert inflection_chain("parlons", "parler", language="fr") == ["present indicative"]
    assert inflection_chain("parlons", "parler") == []  # default ja: no French rules


def test_french_deinflect_reaches_lemma_with_matching_pos():
    fr = get_deinflector("fr")
    verb = fr.condition_flags("v")
    reached = [d for d in fr.deinflect("parlons") if d.text == "parler" and d.conditions & verb]
    assert reached, "parlons should deinflect to the verb parler"


@pytest.mark.parametrize("surface", ["xyzzy", "mangé", "heureuse"])
def test_negative_controls_do_not_overreach(surface: str):
    # A non-word and two forms outside the descriptor's scope (past participle, feminine) must NOT
    # fabricate a chain to a plausible lemma — guards the engine against over-generation.
    fr = get_deinflector("fr")
    assert fr.inflection_chain(surface, "manger", "heureux", "parler") == []


def test_japanese_and_french_engines_are_independent():
    # Distinct instances, distinct rule sets — a French surface must not leak into JP transforms.
    assert get_deinflector("fr") is not get_deinflector("ja")
    assert get_deinflector("fr").transforms.keys() != get_deinflector("ja").transforms.keys()
