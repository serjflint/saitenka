from __future__ import annotations

PROTOCOL = "saitenka.wordstate/v0"
CAPABILITY = "wordstate-v0"
ANSWER_KEYS = frozenset({"expect"})
MODES = frozenset({"retrievability", "numeric-laws"})


def scenario_request(scenario: dict) -> dict:
    if scenario.get("mode") not in MODES or set(scenario) != {
        "scenario_id",
        "mode",
        "cases",
        "expect",
    }:
        raise ValueError("invalid wordstate-v0 scenario")
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
        or request.get("mode") not in MODES
        or set(request) != {"type", "protocol", "scenario_id", "mode", "cases"}
    ):
        raise ValueError("invalid wordstate-v0 request envelope")


def evaluate(request: dict) -> object:
    validate_request(request)
    from saitenka_wordstate import freq_band
    from saitenka_wordstate.fsrs import harmonic_of, harmonic_rank, rareness_band, retrievability

    if request["mode"] == "retrievability":
        return [
            retrievability(case["s"], case["elapsed"], case["decay"]) for case in request["cases"]
        ]
    results = []
    for case in request["cases"]:
        if case["op"] == "harmonic":
            results.append(harmonic_of(case["values"]))
        elif case["op"] == "harmonic-rank":
            results.append(harmonic_rank(case["word"], case["tables"]))
        elif case["op"] == "freq-band":
            results.append(freq_band(case["rank"], top_x=case["top"], bands=case["bands"]))
        elif case["op"] == "rareness":
            results.append(rareness_band(case["rank"]))
        else:
            raise ValueError(f"unknown numeric law: {case['op']!r}")
    return results
