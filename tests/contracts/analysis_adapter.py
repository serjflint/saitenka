from __future__ import annotations

from itertools import starmap

PROTOCOL = "saitenka.analysis/v0"
CAPABILITY = "analysis-v0"
ANSWER_KEYS = frozenset({"expect"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario) != {"scenario_id", "mode", "cases", "expect"}
        or scenario.get("mode") != "episode"
    ):
        raise ValueError("invalid analysis-v0 scenario")
    return {
        "type": "run",
        "protocol": PROTOCOL,
        **{key: value for key, value in scenario.items() if key not in ANSWER_KEYS},
    }


def validate_request(request: dict) -> None:
    if (
        ANSWER_KEYS & request.keys()
        or request.get("type") != "run"
        or request.get("protocol") != PROTOCOL
        or request.get("mode") != "episode"
        or set(request) != {"type", "protocol", "scenario_id", "mode", "cases"}
    ):
        raise ValueError("invalid analysis-v0 request envelope")


def evaluate(request: dict) -> list[object]:
    validate_request(request)
    return [_evaluate_case(case) for case in request["cases"]]


def _evaluate_case(case: dict) -> object:
    from saitenka_dict import FreqDict, JlptDict
    from saitenka_subtitles import Cue, CueIndex
    from saitenka_tokenize.registry import UnidicTokenizer
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.fsrs import KnownSnap
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.features.analysis.episode_analysis import analysis_key, analyze_cues

    if case["kind"] == "key-relations":
        first = CueIndex(list(starmap(Cue, case["first"])))
        changed = CueIndex(list(starmap(Cue, case["changed"])))
        known = Scorer(known=KnownWords.from_set(case["known"]))
        changed_known = Scorer(known=KnownWords.from_set(case["changed_known"]))
        key = analysis_key(first, known)
        return {
            "stable": key == analysis_key(first, known),
            "track_changes": key != analysis_key(changed, known),
            "vocabulary_changes": key != analysis_key(first, changed_known),
        }

    scorer = Scorer(
        known=KnownWords.from_set(case.get("known", [])),
        fsrs_snap=KnownSnap.of(case.get("fsrs", {})) if case.get("fsrs") else None,
        jlpt=JlptDict(case["jlpt"]) if case.get("jlpt") is not None else None,
        freq=FreqDict(case["freq"], "contract") if case.get("freq") is not None else None,
    )
    result = analyze_cues(list(starmap(Cue, case["cues"])), scorer, UnidicTokenizer())
    observed: dict[str, object] = {}
    for field in case.get("observe", []):
        value = getattr(result, field)
        if isinstance(value, frozenset):
            value = sorted(value)
        elif isinstance(value, tuple):
            value = dict(value)
        observed[field] = value
    for field, members in case.get("contains", {}).items():
        values = getattr(result, field)
        observed[f"{field}_contains"] = all(member in values for member in members)
    for field, members in case.get("excludes", {}).items():
        values = getattr(result, field)
        observed[f"{field}_excludes"] = all(member not in values for member in members)
    for field, labels in case.get("distribution_values", {}).items():
        distribution = dict(getattr(result, field) or ())
        for label in labels:
            observed[f"{field}.{label}"] = distribution.get(label)
    if case.get("coverage_consistent"):
        observed["coverage_consistent"] = (
            result.known_token_coverage == result.known_token_count / result.content_token_count
        )
    if case.get("cue_count"):
        observed["cue_count"] = len(result.cues)
    return observed
