from __future__ import annotations

from pathlib import Path
from typing import Any

PROTOCOL = "saitenka.subtitle/v0"
CAPABILITY = "subtitle-v0"
ANSWER_KEYS = frozenset({"expect"})
MODE_KEYS = {
    "render-inputs": frozenset({"scenario_id", "mode", "cases", "expect"}),
    "renderer-state": frozenset({"scenario_id", "mode", "cases", "expect"}),
    "selection-policy": frozenset({"scenario_id", "mode", "cases", "expect"}),
    "navigation-policy": frozenset({"scenario_id", "mode", "cases", "expect"}),
    "provider-policy": frozenset({"scenario_id", "mode", "cases", "expect"}),
    "artifact-policy": frozenset({"scenario_id", "mode", "cases", "expect"}),
}
BASELINE_SETTINGS = {
    "sub-ass-override": "no",
    "sub-ass-scale-with-window": False,
    "sub-scale": 1.0,
    "sub-pos": 100.0,
    "sub-use-margins": True,
    "sub-ass-force-margins": False,
    "sub-ass-video-aspect-override": None,
    "sub-ass-use-video-data": "all",
    "sub-ass-style-overrides": None,
    "sub-scale-with-window": True,
    "sub-scale-by-window": True,
    "blend-subtitles": False,
    "sub-filter-sdh": False,
    "video-crop": "",
    "video-rotate": 0,
    "sub-font-provider": "auto",
    "embeddedfonts": False,
    "sub-fonts-dir": None,
    "sub-shaper": "complex",
    "sub-ass-justify": False,
    "sub-line-spacing": 0.0,
    "sub-hinting": "none",
    "sub-scale-signs": False,
}


def scenario_request(scenario: dict) -> dict:
    mode = scenario.get("mode")
    allowed = MODE_KEYS.get(mode)
    if allowed is None or set(scenario) != allowed:
        raise ValueError(f"invalid subtitle-v0 scenario for {mode!r}")
    return {
        "type": "run",
        "protocol": PROTOCOL,
        **{key: value for key, value in scenario.items() if key not in ANSWER_KEYS},
    }


def validate_request(request: dict) -> None:
    if ANSWER_KEYS & request.keys():
        raise ValueError("subtitle-v0 requests cannot contain answer keys")
    mode = request.get("mode")
    allowed = MODE_KEYS.get(mode)
    expected = ((allowed or frozenset()) - ANSWER_KEYS) | {"type", "protocol"}
    if (
        allowed is None
        or set(request) != expected
        or request.get("type") != "run"
        or request.get("protocol") != PROTOCOL
    ):
        raise ValueError("invalid subtitle-v0 request envelope")


def _decode(value: object) -> object:
    if value == "$nan":
        return float("nan")
    if value == "$inf":
        return float("inf")
    return value


def _normalize(value: object) -> Any:
    if isinstance(value, tuple | list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    return value


def _render_inputs(case: dict) -> dict:
    from saitenka.app.native_subtitles import render_inputs_of

    osd = {"w": 1920, "h": 1080, "mt": 0, "mb": 0, "ml": 0, "mr": 0, "par": 1.0}
    video = {"w": 1920, "h": 1080, "par": 1.0}
    osd.update({key: _decode(value) for key, value in case.get("osd", {}).items()})
    video.update({key: _decode(value) for key, value in case.get("video", {}).items()})
    settings = dict(BASELINE_SETTINGS)
    settings.update({key: _decode(value) for key, value in case.get("settings", {}).items()})
    try:
        result = render_inputs_of(
            osd,
            video,
            settings,
            frame_size=tuple(case.get("frame_size", [1920, 1080])),
            authored=case.get("authored", True),
        )
    except ValueError as exc:
        return {"error": str(exc).partition("=")[0]}
    observed = {key: _normalize(getattr(result, key)) for key in case["observe"]}
    if "font_option" in case:
        name = case["font_option"]
        observed["font_option_accepted"] = any(key == name for key, _value in result.font_options)
    return observed


def _renderer_state(case: dict) -> dict:
    from saitenka.app.native_subtitles import _scaled_renderer_state

    rendered = _render_inputs(case | {"observe": []})
    if "error" in rendered:
        return rendered
    from saitenka.app.native_subtitles import render_inputs_of

    settings = dict(BASELINE_SETTINGS)
    settings.update(case.get("settings", {}))
    inputs = render_inputs_of(
        {"w": 1920, "h": 1080, "mt": 0, "mb": 0, "ml": 0, "mr": 0, "par": 1.0},
        {"w": 1920, "h": 1080, "par": 1.0},
        settings,
        frame_size=(1920, 1080),
        authored=True,
    )
    state = _scaled_renderer_state(inputs.scale)
    return {
        "font_scale": state.font_scale,
        "line_position": state.line_position,
        "line_spacing": state.line_spacing,
        "hinting": state.hinting,
        "selective_font_scale": state.selective_font_scale,
    }


def evaluate(request: dict) -> object:
    validate_request(request)
    if request["mode"] == "selection-policy":
        return [_selection_policy(case) for case in request["cases"]]
    if request["mode"] == "navigation-policy":
        return [_navigation_policy(case) for case in request["cases"]]
    if request["mode"] == "provider-policy":
        return [_provider_policy(case) for case in request["cases"]]
    if request["mode"] == "artifact-policy":
        return [_artifact_policy(case) for case in request["cases"]]
    evaluator = _render_inputs if request["mode"] == "render-inputs" else _renderer_state
    return [evaluator(case) for case in request["cases"]]


def _selection_policy(case: dict) -> object:
    from saitenka.app.subtitle_selection import (
        SubtitleTracks,
        discover,
        fetch_action,
        initial,
        language_name,
        matches_target_language,
        primary_role,
        selects_background_japanese,
        toggle_target,
    )

    op = case["op"]
    if op == "discover":
        tracks = discover(case["tracks"], case["primary"])
        return {"jp_sid": tracks.jp_sid, "en_sid": tracks.en_sid}
    if op == "initial":
        startup = initial(case["tracks"], case["primary"], case["secondary"])
        return {
            "tracks": {"jp_sid": startup.tracks.jp_sid, "en_sid": startup.tracks.en_sid},
            "active": startup.active,
        }
    if op == "toggle":
        decision = toggle_target(
            SubtitleTracks(case["jp_sid"], case["en_sid"]),
            active_sid=case["active_sid"],
            language=case["language"],
        )
        return {"target": decision.target, "sid": decision.sid, "available": decision.available}
    if op == "role":
        return primary_role(
            case["sid"],
            SubtitleTracks(case["jp_sid"], case["en_sid"]),
            track_lang=case["track_lang"],
            sample=case["sample"],
            primary_slang=case.get("primary", "ja,jpn,jp"),
            second_slang=case.get("secondary", "en"),
        )
    if op == "language-name":
        return language_name(case["language"])
    if op == "fetch":
        return fetch_action(
            path_available=case["path_available"],
            force_select=case["force_select"],
            replace=case["replace"],
            language=case["language"],
        ).value
    if op == "background-select":
        return selects_background_japanese(
            **{key: value for key, value in case.items() if key not in {"case_id", "op"}}
        )
    if op == "matches-language":
        return matches_target_language(case["tag"], case["target"])
    raise ValueError(f"unknown selection policy operation: {op!r}")


def _navigation_policy(case: dict) -> object:
    from saitenka_subtitles import Cue, CueIndex

    from saitenka.app.subnav_policy import cue_is_on_screen, filters_can_drop_a_cue, resolve_target

    op = case["op"]
    if op == "on-screen":
        cue = Cue(**case["cue"])
        return cue_is_on_screen(
            cue,
            text=case["text"],
            sub_start=case["sub_start"],
            time_pos=case["time_pos"],
        )
    if op == "resolve":
        index = CueIndex([Cue(**cue) for cue in case["cues"]])
        chosen = resolve_target(
            index,
            delta=case["delta"],
            text=case["text"],
            sub_start=case["sub_start"],
            time_pos=case["time_pos"],
            nav_idx=case["nav_idx"],
        )
        if chosen is None:
            return None
        return {"start": chosen.cue.start, "text": chosen.cue.text}
    if op == "filters-can-drop":
        return filters_can_drop_a_cue(case["settings"])
    raise ValueError(f"unknown navigation policy operation: {op!r}")


def _provider_policy(case: dict) -> object:
    import saitenka.app.subtitle_providers as providers
    from saitenka.app.subtitle_providers import ProviderContext, SubtitleProvider

    op = case["op"]
    if op == "fetch-first":
        calls: list[str] = []

        def attempt(item: dict):
            def run():
                calls.append(item["name"])
                path = Path(item["path"]) if item["path"] is not None else None
                return path, item["status"]

            return item["name"], run

        path, status = providers.fetch_first(tuple(attempt(item) for item in case["attempts"]))
        return {"calls": calls, "path": str(path) if path is not None else None, "status": status}
    if op == "context-default":
        return ProviderContext().language

    original = providers._REGISTRY
    providers._REGISTRY = {}
    try:
        for item in case.get("providers", []):
            provider = SubtitleProvider(
                name=item["name"],
                languages=frozenset(item["languages"]),
                candidates=lambda _video, _context: ([], []),
                fetch_attempt=lambda _video, _context: lambda: (None, "stub"),
            )
            providers.register_provider(provider)
        if op == "providers-for-language":
            candidates = case.get("candidates")
            return list(
                providers.providers_for_language(
                    case["language"],
                    candidates=tuple(candidates) if candidates is not None else None,
                )
            )
        if op == "enabled-providers":
            return list(
                providers.enabled_providers_for(
                    case["language"], tuple((name, enabled) for name, enabled in case["flags"])
                )
            )
        if op == "get-provider":
            return providers.get_provider(case["name"]) is not None
        if op == "duplicate-register":
            duplicate = case["duplicate"]
            try:
                providers.register_provider(
                    SubtitleProvider(
                        name=duplicate["name"],
                        languages=frozenset(duplicate["languages"]),
                        candidates=lambda _video, _context: ([], []),
                        fetch_attempt=lambda _video, _context: lambda: (None, "stub"),
                    )
                )
            except ValueError:
                return "ValueError"
            return "accepted"
        if op == "launch-builtins":
            from saitenka.app.launch import run

            names = run._enabled_provider_names(
                "episode.mkv",
                jimaku=case["jimaku"],
                jimaku_cfg={},
                tsukihime_cfg={"enabled": case["tsukihime"]},
                language=case["language"],
            )
            return {
                "names": list(names),
                "tsukihime_registered": providers.get_provider("tsukihime") is not None,
            }
        raise ValueError(f"unknown provider policy operation: {op!r}")
    finally:
        providers._REGISTRY = original


def _artifact_policy(case: dict) -> object:
    from saitenka.app.subtitle_artifact import (
        ArtifactUnavailable,
        EmbeddedArtifact,
        ExternalArtifact,
        extract_spec,
        resolve,
    )

    if case["op"] == "extract":
        suffix, args = extract_spec(case["codec"])
        return {"suffix": suffix, "args": list(args)}
    artifact = resolve(case["track"], media_path=case["media_path"])
    if isinstance(artifact, ExternalArtifact):
        return {"kind": "external", "path": artifact.path}
    if isinstance(artifact, EmbeddedArtifact):
        return {
            "kind": "embedded",
            "media_path": artifact.media_path,
            "stream_index": artifact.ff_index,
            "codec": artifact.codec,
        }
    if isinstance(artifact, ArtifactUnavailable):
        return {"unavailable": artifact.value}
    raise TypeError(f"unknown artifact result: {artifact!r}")
