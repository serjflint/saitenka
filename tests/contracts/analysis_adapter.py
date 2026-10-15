from __future__ import annotations

import sys
from itertools import starmap
from pathlib import Path

PROTOCOL = "saitenka.analysis/v0"
CAPABILITY = "analysis-v0"
ANSWER_KEYS = frozenset({"expect"})
MODES = frozenset({"episode", "lifecycle"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario) != {"scenario_id", "mode", "cases", "expect"}
        or scenario.get("mode") not in MODES
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
        or request.get("mode") not in MODES
        or set(request) != {"type", "protocol", "scenario_id", "mode", "cases"}
    ):
        raise ValueError("invalid analysis-v0 request envelope")


def evaluate(request: dict) -> list[object]:
    validate_request(request)
    if request["mode"] == "lifecycle":
        return [_evaluate_lifecycle(case) for case in request["cases"]]
    return [_evaluate_case(case) for case in request["cases"]]


def _evaluate_lifecycle(case: dict) -> object:
    from saitenka_subtitles import Cue, CueIndex
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    tests = Path(__file__).resolve().parents[1]
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    from session_builder import build_session
    from util import FakeIPC, await_ready, session_gateway

    from saitenka.app.bindings import ANALYSIS_MSG
    from saitenka.app.features.profiles.dependencies import DependencyBundle
    from saitenka.app.overlay_ids import OverlayId
    from saitenka.app.scoring import Coloring
    from saitenka.app.session.factory import SessionServices
    from saitenka.runtime.events import (
        SubtitleLanguageChanged,
        SubtitleStartupConfigured,
        SubtitleTracksDiscovered,
    )

    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    reader = build_session(
        ipc, services=SessionServices(scorer=Coloring(Scorer(known=KnownWords.from_set(["本"]))))
    )
    graph = reader.graph
    graph.track_commands.declare(SubtitleStartupConfigured(1, None, "jp", "ja,jpn,jp"))
    graph.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "私は本を読む。")])

    def settle() -> None:
        await_ready(lambda: graph.analysis.settled, "analysis did not settle", pump=reader.pump)

    try:
        op = case["op"]
        if op == "toggle":
            reader.command(ANALYSIS_MSG)
            settle()
            visible = OverlayId.ANALYSIS in graph.overlay._live
            forbidden = any(
                command
                and (command[0] in {"seek", "sub-seek"} or command[:2] == ("set_property", "pause"))
                for command in ipc.commands
            )
            reader.command(ANALYSIS_MSG)
            reader.pump()
            return {
                "status": graph.analysis.status,
                "has_result": graph.analysis.result is not None,
                "visible_before_close": visible,
                "visible_after_close": OverlayId.ANALYSIS in graph.overlay._live,
                "playback_changed": forbidden,
            }
        if op == "external-no-sid":
            graph.track_commands.declare(SubtitleTracksDiscovered(None, None))
            reader.command(ANALYSIS_MSG)
            settle()
            return {
                "status": graph.analysis.status,
                "has_result": graph.analysis.result is not None,
            }
        if op == "unavailable":
            if case["reason"] == "missing-index":
                graph.track_commands.navigation.current.sub_index = None
            else:
                graph.track_commands.declare(SubtitleLanguageChanged("en"))
            reader.command(ANALYSIS_MSG)
            return {
                "status": graph.analysis.status,
                "has_result": graph.analysis.result is not None,
                "settled": graph.analysis.settled,
            }
        if op == "background":
            graph.analysis_commands.invalidate()
            settle()
            return {
                "status": graph.analysis.status,
                "has_result": graph.analysis.result is not None,
                "open": graph.analysis.open,
                "visible": OverlayId.ANALYSIS in graph.overlay._live,
            }
        if op == "dependency-deferral":
            graph.profile.begin_loading()
            graph.analysis_commands.invalidate()
            before = {
                "settled": graph.analysis.settled,
                "has_result": graph.analysis.result is not None,
            }
            graph.profile.accept(DependencyBundle(graph.profile.identity))
            graph.analysis_commands.invalidate(vocabulary_changed=True)
            settle()
            return {
                "before": before,
                "after": {
                    "status": graph.analysis.status,
                    "has_result": graph.analysis.result is not None,
                },
            }
        if op == "cache-reopen":
            reader.command(ANALYSIS_MSG)
            settle()
            reader.command(ANALYSIS_MSG)
            reader.command(ANALYSIS_MSG)
            return {
                "status": graph.analysis.status,
                "has_result": graph.analysis.result is not None,
                "settled": graph.analysis.settled,
            }
        if op == "invalidate":
            reader.command(ANALYSIS_MSG)
            settle()
            graph.analysis_commands.invalidate(vocabulary_changed=True)
            during = graph.analysis.status
            settle()
            graph.track_commands.navigation.current.sub_index = CueIndex(
                [Cue(0, 1, "彼は映画を見る。")]
            )
            graph.analysis_commands.invalidate()
            settle()
            return {
                "during": during,
                "status": graph.analysis.status,
                "has_result": graph.analysis.result is not None,
                "cue_count": len(graph.analysis.result.cues) if graph.analysis.result else 0,
            }
        raise ValueError(f"unknown analysis lifecycle operation: {op!r}")
    finally:
        reader.close()
        gateway.close()


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
