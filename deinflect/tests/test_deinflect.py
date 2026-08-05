"""Japanese deinflector: surface → dictionary form + the Yomitan-style inflection chain."""

from saitenka_deinflect import (
    condition_flags,
    conditions_match,
    deinflect,
    inflection_chain,
)


def _traces_to(surface: str, term: str, rule: str) -> set[tuple[str, ...]]:
    """Every transform chain that reduces ``surface`` to ``term`` with the ``rule`` POS conditions."""
    flags = condition_flags(rule)
    return {
        tuple(d.chain)
        for d in deinflect(surface)
        if d.text == term and conditions_match(d.conditions, flags)
    }


def test_chain_matches_yomitan_examples():
    # the two screenshots: 聞こえてた → 聞こえる and 預けた → 預ける
    assert inflection_chain("聞こえてた", "聞こえる") == ["-て", "-いる", "-た"]
    assert inflection_chain("預けた", "預ける") == ["-た"]


def test_common_conjugations():
    # Yomitan's own transform names (the full descriptor is ported verbatim)
    assert inflection_chain("食べない", "食べる") == ["negative"]
    assert inflection_chain("読まれる", "読む") == ["passive"]
    assert inflection_chain("走りたい", "走る") == ["-たい"]
    assert inflection_chain("読みます", "読む") == ["-ます"]
    assert inflection_chain("早く", "早い") == ["-く"]
    assert inflection_chain("食べちゃった", "食べる") == ["-ちゃう", "-た"]


def test_iku_irregular():
    # 行く takes って/った (not いて/いた) — handled natively by the ported descriptor
    assert inflection_chain("行った", "行く") == ["-た"]
    assert inflection_chain("行って", "行く") == ["-て"]


def test_classical_and_dialect_negatives():
    assert inflection_chain("習わぬ", "習う") == ["-ぬ"]  # literary negative
    assert inflection_chain("知らん", "知る") == ["-ん"]  # colloquial
    assert (
        inflection_chain("聞こえてへん", "聞こえる")[-1] == "kansai-ben negative"
    )  # Osaka dialect


def test_uninflected_has_no_chain():
    assert inflection_chain("本命", "本命") == []
    assert inflection_chain("猫", "猫") == []


def test_no_path_returns_empty():
    # surface that doesn't reduce to the given lemma → no chain, no crash
    assert inflection_chain("聞こえてたが", "聞こえる") == []


def test_deinflect_reaches_dictionary_form():
    forms = {d.text for d in deinflect("食べさせた")}
    assert "食べる" in forms  # causative + past peels back to the dictionary form


def test_kuru_keeps_both_potential_and_passive_traces():
    # #152: 来られる reaches 来る (vk) via BOTH the passive and the 'potential or passive' kuru rules, which
    # land on the identical (来る, vk) state. The old (text, conditions) dedup dropped whichever lost the
    # BFS race; Yomitan keeps both, so the port must too.
    assert _traces_to("来られる", "来る", "vk") >= {("passive",), ("potential or passive",)}
    # the kana + negative twin behaves identically
    assert _traces_to("こられない", "くる", "vk") >= {
        ("passive", "negative"),
        ("potential or passive", "negative"),
    }


def test_inflection_chain_prefers_shortest_label_after_trace_fix():
    # keeping the extra trace must NOT change the single label users see — the shortest chain still wins,
    # stably (passive is discovered before potential-or-passive), so the tooltip is unchanged.
    assert inflection_chain("来られる", "来る") == ["passive"]


def test_deinflect_terminates_with_bounded_growth():
    # loosening the state dedup to keep distinct traces must not explode the BFS: the per-chain cycle
    # guard is the termination bound (a rule can't re-apply to the same text within a chain).
    assert len(deinflect("くんなきゃ")) <= 40
