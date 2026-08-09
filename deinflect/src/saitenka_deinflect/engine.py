"""Deinflector → the inflection chain Yomitan shows (🧩 ``-て « -いる « -た``), per language.

Derived from Yomitan (https://github.com/yomidevs/yomitan): a faithful port of
``ext/js/language/*/language-transformer.js``. The rule data is dumped verbatim from each language's
transform descriptor — ``data/japanese_transforms.json`` from ``ja/japanese-transforms.js`` and
``data/french_transforms.json`` from ``fr/french-transforms.js`` (regenerate the French one with
``tools/dump_french_transforms.mjs``). Copyright the Yomitan Authors. GPL-3.0-or-later — see ``LICENSE``.

The Japanese descriptor is the complete Yomitan set (54 transforms, 834 rules — incl.
causative/passive/potential, ～ちゃう/～すぎる/～たい, classical ～ぬ/～ず and Kansai-ben); French adds
9 transforms (present/imperfect/future/conditional/subjunctive/preterite, plural, participle). One
engine serves both: a :class:`Deinflector` is a BFS that peels condition-gated suffix/whole-word
inflections off a surface, accumulating the transform names applied. The consumer already has a
dictionary form from the tokenizer lemma, so the job is the **chain**: run it on the surface, find the
shortest path landing on the lemma, and show those names in inflection order (dict→surface). Rules are
pure data — the lemma-match filters over-generation.

Module-level ``deinflect`` / ``inflection_chain`` / ``condition_flags`` / ``conditions_match`` default
to Japanese (back-compat); pass ``language=`` or use :func:`get_deinflector` for another language.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

# Language code → the committed transform-descriptor JSON dumped from Yomitan. The canonical internal
# code the overlay uses is ``jp``; Yomitan (and this add-on's data files) use ISO ``ja`` — both map here.
_DATA_FILES = {
    "ja": "japanese_transforms.json",
    "jp": "japanese_transforms.json",
    "fr": "french_transforms.json",
}


def _build_flags(tree: dict[str, list[str]]) -> dict[str, int]:
    def leaves(name: str) -> list[str]:
        subs = tree.get(name, [])
        return [name] if not subs else [x for s in subs for x in leaves(s)]

    leafbit: dict[str, int] = {}
    for name in sorted({x for n in tree for x in leaves(n)}):
        leafbit[name] = 1 << len(leafbit)
    return {name: sum(leafbit[x] for x in leaves(name)) for name in tree}


def _match(current: int, cond_in: int) -> bool:
    # Yomitan: current==0 (start, unconstrained) OR the current state overlaps the rule's input.
    return current == 0 or (current & cond_in) != 0


@dataclass(frozen=True)
class Rule:
    is_suffix: bool  # True = suffix match; False = whole-word match
    inflected: str
    deinflected: str
    cond_in: int
    cond_out: int


@dataclass(frozen=True)
class Deinflection:
    text: str
    conditions: int
    chain: tuple[str, ...]  # transform names, newest-first == inflection (dict→surface) order


class Deinflector:
    """One language's transform engine, built from its dumped descriptor. Holds the condition-flag map
    and compiled rules; :meth:`deinflect` is the BFS. One instance per language (see
    :func:`get_deinflector`) — the rule sets are independent, so nothing is shared between them."""

    def __init__(self, raw: dict) -> None:
        self._flags_map = _build_flags({k: v["sub"] for k, v in raw["conditions"].items()})
        self.transforms = self._load(raw)

    def _flags(self, names) -> int:
        f = 0
        for n in names:
            f |= self._flags_map.get(n, 0)
        return f

    def _load(self, raw: dict) -> dict[str, list[Rule]]:
        out: dict[str, list[Rule]] = {}
        for name, rules in raw["transforms"].items():
            rs: list[Rule] = []
            for r in rules:
                src = r["re"]  # suffix: "…$"; wholeWord: "^…$"
                is_suffix = r["type"] != "wholeWord"
                rs.append(
                    Rule(
                        is_suffix=is_suffix,
                        inflected=src.removesuffix("$") if is_suffix else src.strip("^$"),
                        deinflected=r.get("de", ""),
                        cond_in=self._flags(r["in"]),
                        cond_out=self._flags(r["out"]),
                    )
                )
            out[name] = rs
        return out

    def deinflect(self, text: str) -> list[Deinflection]:
        """All ways to peel inflections off ``text``, faithful to Yomitan's
        ``LanguageTransformer.transform``.

        A **per-chain cycle guard** — a rule ``(name, index)`` may not re-apply to a text already in its
        own ancestry — is the termination bound (as upstream), so two transforms reaching the SAME
        ``(text, conditions)`` via DIFFERENT traces both survive: 来られる keeps both ``passive`` and
        ``potential or passive`` (the old ``(text, cond)`` dedup dropped the second — #152). The global
        ``seen`` dedup is kept but keyed by ``(text, cond_out, chain)``: it removes only genuine
        duplicates (identical reached state AND identical name-chain, which the corpus oracle and
        :func:`inflection_chain` can't tell apart), an efficiency win over upstream's no-dedup that
        cannot suppress a distinct trace."""
        results = [Deinflection(text, 0, ())]
        # Per-node ancestry of applied rules, frame = (name, rule_index, text_applied_to) — cycle guard.
        traces: list[tuple[tuple[str, int, str], ...]] = [()]
        seen: set[tuple[str, int, tuple[str, ...]]] = {(text, 0, ())}
        i = 0
        while i < len(results):
            cur, tr = results[i], traces[i]
            i += 1
            for name, rules in self.transforms.items():
                for j, r in enumerate(rules):
                    if r.is_suffix:
                        if len(cur.text) < len(r.inflected) or not cur.text.endswith(r.inflected):
                            continue
                        nt = cur.text[: len(cur.text) - len(r.inflected)] + r.deinflected
                    elif cur.text != r.inflected:
                        continue
                    else:
                        nt = r.deinflected
                    if not _match(cur.conditions, r.cond_in):
                        continue
                    if not nt or (name, j, cur.text) in tr:  # empty result, or Yomitan's isCycle
                        continue
                    ch = (name, *cur.chain)
                    key = (nt, r.cond_out, ch)
                    # True duplicate: same reached state AND same observable name-chain. Lossless on the
                    # current descriptor (verified against a no-dedup port); a future rule edit adding BOTH
                    # a within-transform duplicate rule AND a grammar cycle back to it could make this
                    # coarser than the trace — the corpus gate would catch it.
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(Deinflection(nt, r.cond_out, ch))
                    traces.append(((name, j, cur.text), *tr))
        return results

    def condition_flags(self, *names: str) -> int:
        """Bitset for Yomitan condition / POS names (port of ``getConditionFlagsFromConditionType``)."""
        return self._flags(names)

    def inflection_chain(self, surface: str, *targets: str) -> list[str]:
        """Transform-name chain reducing ``surface`` to one of ``targets`` (usually the lemma), in the
        order Yomitan displays (dict→surface). Empty if the surface is already a target or no path
        exists. The lemma-match makes over-generated candidates harmless."""
        goals = {t for t in targets if t}
        if not surface or surface in goals:
            return []
        best: tuple[str, ...] | None = None
        for d in self.deinflect(surface):
            if d.chain and d.text in goals and (best is None or len(d.chain) < len(best)):
                best = d.chain
        return list(best) if best else []


@cache
def get_deinflector(language: str = "ja") -> Deinflector:
    """The :class:`Deinflector` for ``language`` (``ja``/``jp`` → Japanese, ``fr`` → French). Cached —
    building one parses+compiles its descriptor once. ``ValueError`` for a language with no shipped rules
    (the caller — the overlay's dictionary chokepoint — treats that as "no chain shown")."""
    try:
        filename = _DATA_FILES[language]
    except KeyError:
        raise ValueError(f"no deinflection rules for language {language!r}") from None
    raw = json.loads(
        files("saitenka_deinflect").joinpath(f"data/{filename}").read_text(encoding="utf-8")
    )
    return Deinflector(raw)


_JA = get_deinflector("ja")
TRANSFORMS = _JA.transforms  # back-compat: the module-level JP rule map


def deinflect(text: str, *, language: str = "ja") -> list[Deinflection]:
    """All deinflections of ``text`` in ``language`` (default Japanese, back-compat)."""
    return get_deinflector(language).deinflect(text)


def condition_flags(*names: str, language: str = "ja") -> int:
    """Condition-flag bitset for ``names`` in ``language`` (default Japanese, back-compat)."""
    return get_deinflector(language).condition_flags(*names)


def conditions_match(current: int, expected: int) -> bool:
    """Yomitan's ``conditionsMatch``: unconstrained (``current == 0``) fits any POS, else flags overlap.
    Language-independent (pure bit test), so it takes no ``language``."""
    return _match(current, expected)


def inflection_chain(surface: str, *targets: str, language: str = "ja") -> list[str]:
    """Shortest transform-name chain reducing ``surface`` to a target in ``language`` (default
    Japanese, back-compat) — the overlay's GPL chokepoint."""
    return get_deinflector(language).inflection_chain(surface, *targets)
