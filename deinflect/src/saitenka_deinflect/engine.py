"""Japanese deinflector → the inflection chain Yomitan shows (🧩 ``-て « -いる « -た``).

Derived from Yomitan (https://github.com/yomidevs/yomitan): a faithful port of
``ext/js/language/ja/language-transformer.js``. The rule data in
``data/japanese_transforms.json`` is dumped verbatim from
``ext/js/language/ja/japanese-transforms.js`` (regenerate with ``compare/dump_transforms.mjs``).
Copyright the Yomitan Authors and contributors. Licensed under GPL-3.0-or-later — see ``LICENSE``.

The transform rules are the complete Yomitan Japanese descriptor (54 transforms, 834 rules — incl.
causative/passive/potential, ～ちゃう/～すぎる/～たい, classical ～ぬ/～ず and Kansai-ben). This module is
the engine + loader: a BFS that peels condition-gated suffix/whole-word inflections off a surface,
accumulating the transform names applied. The consumer already has a reliable dictionary form from the
tokenizer lemma, so the job is the **chain**: run it on the surface, find the shortest path that lands
on the lemma, and show those names in inflection order (dict→surface). Rules are pure data — the
lemma-match filters over-generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files

_DATA = files("saitenka_deinflect").joinpath("data/japanese_transforms.json")
_RAW = json.loads(_DATA.read_text(encoding="utf-8"))


def _build_flags(tree: dict[str, list[str]]) -> dict[str, int]:
    def leaves(name: str) -> list[str]:
        subs = tree.get(name, [])
        return [name] if not subs else [x for s in subs for x in leaves(s)]

    leafbit: dict[str, int] = {}
    for name in sorted({x for n in tree for x in leaves(n)}):
        leafbit[name] = 1 << len(leafbit)
    return {name: sum(leafbit[x] for x in leaves(name)) for name in tree}


_FLAGS = _build_flags({k: v["sub"] for k, v in _RAW["conditions"].items()})


def _flags(names) -> int:
    f = 0
    for n in names:
        f |= _FLAGS.get(n, 0)
    return f


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


def _load() -> dict[str, list[Rule]]:
    out: dict[str, list[Rule]] = {}
    for name, rules in _RAW["transforms"].items():
        rs: list[Rule] = []
        for r in rules:
            src = r["re"]  # suffix: "…$"; wholeWord: "^…$"
            if r["type"] == "wholeWord":
                rs.append(
                    Rule(
                        is_suffix=False,
                        inflected=src.strip("^$"),
                        deinflected=r.get("de", ""),
                        cond_in=_flags(r["in"]),
                        cond_out=_flags(r["out"]),
                    )
                )
            else:
                rs.append(
                    Rule(
                        is_suffix=True,
                        inflected=src.removesuffix("$"),
                        deinflected=r.get("de", ""),
                        cond_in=_flags(r["in"]),
                        cond_out=_flags(r["out"]),
                    )
                )
        out[name] = rs
    return out


TRANSFORMS = _load()


@dataclass(frozen=True)
class Deinflection:
    text: str
    conditions: int
    chain: tuple[str, ...]  # transform names, newest-first == inflection (dict→surface) order


def deinflect(text: str) -> list[Deinflection]:
    """All ways to peel inflections off ``text``, faithful to Yomitan's ``LanguageTransformer.transform``.

    A **per-chain cycle guard** — a rule ``(name, index)`` may not re-apply to a text already in its own
    ancestry — is the termination bound (as upstream), so two transforms reaching the SAME ``(text,
    conditions)`` via DIFFERENT traces both survive: 来られる keeps both ``passive`` and ``potential or
    passive`` (the old ``(text, cond)`` dedup dropped the second — #152). The global ``seen`` dedup is kept
    but keyed by ``(text, cond_out, chain)``: it removes only genuine duplicates (identical reached state
    AND identical name-chain, which the corpus oracle and :func:`inflection_chain` can't tell apart), an
    efficiency win over upstream's no-dedup that cannot suppress a distinct trace."""
    results = [Deinflection(text, 0, ())]
    # Per-node ancestry of applied rules, frame = (name, rule_index, text_applied_to) — the cycle guard.
    traces: list[tuple[tuple[str, int, str], ...]] = [()]
    seen = {(text, 0, ())}
    i = 0
    while i < len(results):
        cur, tr = results[i], traces[i]
        i += 1
        for name, rules in TRANSFORMS.items():
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
                # current descriptor (verified against a no-dedup port); a future rule edit adding BOTH a
                # within-transform duplicate rule AND a grammar cycle back to it could make this coarser
                # than the trace — the corpus gate would catch it.
                if key in seen:
                    continue
                seen.add(key)
                results.append(Deinflection(nt, r.cond_out, ch))
                traces.append(((name, j, cur.text), *tr))
    return results


def condition_flags(*names: str) -> int:
    """Bitset for Yomitan condition / part-of-speech names (port of Yomitan's
    ``getConditionFlagsFromConditionType``) — e.g. ``condition_flags("v1")`` for an ichidan verb.
    A dictionary term of that POS accepts a :class:`Deinflection` iff :func:`conditions_match`."""
    return _flags(names)


def conditions_match(current: int, expected: int) -> bool:
    """Yomitan's ``LanguageTransformer.conditionsMatch``: an unconstrained result (``current == 0``)
    fits any POS, else the flags must overlap. Pairs a :attr:`Deinflection.conditions` with the
    :func:`condition_flags` of a candidate dictionary POS."""
    return _match(current, expected)


def inflection_chain(surface: str, *targets: str) -> list[str]:
    """Transform-name chain that reduces ``surface`` to one of ``targets`` (usually the lemma), in the
    order Yomitan displays (dict→surface). Empty if the surface is already a target (uninflected) or no
    path is found. The lemma-match makes over-generated candidates harmless."""
    goals = {t for t in targets if t}
    if not surface or surface in goals:
        return []
    best: tuple[str, ...] | None = None
    for d in deinflect(surface):
        if d.chain and d.text in goals and (best is None or len(d.chain) < len(best)):
            best = d.chain
    return list(best) if best else []
