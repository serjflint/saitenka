from __future__ import annotations

import os
import tempfile
import tomllib
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from saitenka.app import config_editor
from saitenka.app.command_defaults import resolve_mine_model
from saitenka.app.commands.attach import _build_attach_options
from saitenka.app.launch.run import (
    RunDepsRequest,
    RunFlags,
    _build_run_options,
    _run_effective_config,
)

BASE_FLAGS = RunFlags(
    mine_key="Ctrl+m",
    mine_all_key="Shift+m",
    translate_key="t",
    preview_key="p",
    tip_height=0.4,
    tip_scale=0.0,
    pause_on_tooltip=True,
    hover_switch_delay=0.15,
    no_audio_play=False,
    mine_preview=True,
    auto_translate=False,
    prefetch=True,
    layout_engine="default",
)
PROTOCOL = "saitenka.config/v0"
CAPABILITY = "config-v0"
ANSWER_KEYS = frozenset({"expect", "expect_contains"})
MODE_KEYS = {
    "run": (
        frozenset({"scenario_id", "mode", "config", "flags", "observe", "expect"}),
        False,
    ),
    "attach": (
        frozenset({"scenario_id", "mode", "config", "mine", "observe", "expect"}),
        False,
    ),
    "editor-catalog": (frozenset({"scenario_id", "mode", "fields", "expect"}), False),
    "editor-defaults": (frozenset({"scenario_id", "mode", "checks", "expect"}), False),
    "editor-coerce": (frozenset({"scenario_id", "mode", "checks", "expect"}), False),
    "mine-model": (frozenset({"scenario_id", "mode", "configs", "expect"}), False),
    "run-effective": (
        frozenset({"scenario_id", "mode", "scoped_config", "cli", "observe", "expect"}),
        False,
    ),
    "editor-session": (
        frozenset(
            {
                "scenario_id",
                "mode",
                "initial",
                "edits",
                "observe_comments",
                "expect",
            }
        ),
        False,
    ),
    "profile-identity": (frozenset({"scenario_id", "mode", "config", "expect"}), False),
    "profile-policy": (frozenset({"scenario_id", "mode", "cases", "expect"}), False),
    "config-policy": (frozenset({"scenario_id", "mode", "cases", "expect"}), False),
    "profile-switch": (frozenset({"scenario_id", "mode", "cases", "expect"}), False),
}


def scenario_request(scenario: dict) -> dict:
    mode = scenario.get("mode")
    spec = MODE_KEYS.get(mode)
    if spec is None or set(scenario) != spec[0]:
        raise ValueError(f"invalid config-v0 scenario keys for {mode!r}")
    payload = {key: value for key, value in scenario.items() if key not in ANSWER_KEYS}
    return {"type": "run", "protocol": PROTOCOL, **payload}


def validate_request(request: dict) -> None:
    if ANSWER_KEYS & request.keys():
        raise ValueError("config-v0 requests cannot contain answer keys")
    required = {"type", "protocol", "scenario_id", "mode"}
    if (
        not required <= request.keys()
        or request["type"] != "run"
        or request["protocol"] != PROTOCOL
    ):
        raise ValueError("invalid config-v0 request envelope")
    mode = request["mode"]
    spec = MODE_KEYS.get(mode)
    if spec is None:
        raise ValueError(f"unknown config-v0 mode: {mode!r}")
    allowed = (spec[0] - ANSWER_KEYS) | {"type", "protocol"}
    if spec[1]:
        allowed |= {"observe"}
    if set(request) != allowed:
        raise ValueError(f"invalid config-v0 request keys for {mode!r}")


def evaluate(scenario: dict) -> object:
    validate_request(scenario)
    if scenario["mode"] == "editor-session":
        return _evaluate_editor_session(scenario)
    if scenario["mode"].startswith("editor-"):
        return _evaluate_editor(scenario)
    if scenario["mode"] == "mine-model":
        return [resolve_mine_model(config) for config in scenario["configs"]]
    if scenario["mode"] == "run-effective":
        return _evaluate_run_config(scenario)
    if scenario["mode"] == "profile-identity":
        return _evaluate_profile_identity(scenario["config"])
    if scenario["mode"] == "profile-policy":
        return [_evaluate_profile_policy(case) for case in scenario["cases"]]
    if scenario["mode"] == "config-policy":
        return [_evaluate_config_policy(case) for case in scenario["cases"]]
    if scenario["mode"] == "profile-switch":
        return [_evaluate_profile_switch(case) for case in scenario["cases"]]
    if scenario["mode"] == "run":
        options = _build_run_options(
            scenario["config"], replace(BASE_FLAGS, **scenario.get("flags", {}))
        )
    elif scenario["mode"] == "attach":
        options = _build_attach_options(scenario["config"], mine=scenario.get("mine", {}))
    else:
        raise ValueError(f"unknown config adapter mode: {scenario['mode']!r}")
    paths = scenario["observe"]
    return {path: _read_path(options, path) for path in paths}


def _evaluate_run_config(scenario: dict) -> dict[str, object]:
    cli = scenario.get("cli", {})
    request = RunDepsRequest(
        mine=False,
        mine_deck="",
        mine_model="",
        mine_key="",
        mine_all_key="",
        mine_normalize_audio=False,
        mine_animated_screenshot=False,
        raw_mine={},
        known_cfg=None,
        known="",
        color=True,
        dict_titles=cli.get("dict_titles", []),
        freq_titles=cli.get("freq_titles", []),
        pitch_titles=cli.get("pitch_titles", []),
        scoped_cfg=scenario["scoped_config"],
    )
    effective = _run_effective_config(request)
    return {key: effective[key] for key in scenario["observe"]}


def _evaluate_profile_identity(config: dict) -> dict[str, object]:
    import saitenka.app.subtitle_providers as providers
    from saitenka.app.profiles import resolve_profile
    from saitenka.app.subtitle_providers import SubtitleProvider

    original = providers._REGISTRY
    providers._REGISTRY = {}
    try:
        for name, languages in (("jimaku", {"jp"}), ("tsukihime", set())):
            providers.register_provider(
                SubtitleProvider(
                    name=name,
                    languages=frozenset(languages),
                    candidates=lambda _video, _context: ([], []),
                    fetch_attempt=lambda _video, _context: lambda: (None, "stub"),
                )
            )
        profile = resolve_profile(config)
        enabled = providers.enabled_providers_for(
            profile.langs.main, (("jimaku", True), ("tsukihime", True))
        )
        return {
            "tokenizer": profile.tokenizer,
            "main_language": profile.langs.main,
            "second_language": profile.langs.second,
            "providers": list(enabled),
        }
    finally:
        providers._REGISTRY = original


def _profile_value(profile) -> dict[str, object]:
    return {
        "name": profile.name,
        "main_language": profile.langs.main,
        "second_language": profile.langs.second,
        "tokenizer": profile.tokenizer,
        "slang": profile.slang,
    }


def _evaluate_profile_policy(case: dict) -> object:
    from saitenka.app.launch.run import _resolvemine_target, defaultmine_target
    from saitenka.app.mining_config import mine_config_from
    from saitenka.app.profiles import (
        configured_profiles,
        primary_font_for,
        resolve_launch_identity,
        resolve_profile,
        scope_config,
        validate_language_code,
    )

    op = case["op"]
    try:
        if op == "resolve":
            return _profile_value(resolve_profile(case["config"], override=case.get("override")))
        if op == "scope":
            return scope_config(case["config"], override=case.get("override"))
        if op == "configured":
            return [_profile_value(profile) for profile in configured_profiles(case["config"])]
        if op == "validate-language":
            return validate_language_code(case["language"])
        if op == "launch":
            identity = resolve_launch_identity(
                case["config"], profile_override=case.get("override"), slang=case["slang"]
            )
            return {
                "profile": _profile_value(identity.profile),
                "language": identity.language,
                "slang": identity.slang,
                "config": identity.cfg,
            }
        if op == "primary-font":
            return primary_font_for(case["language"])
        if op == "mine-config":
            scoped = scope_config(case["config"], override=case.get("override"))
            mine = mine_config_from(scoped.get("mine", {}))
            return {"deck": mine.deck, "model": mine.model, "fields": mine.fields}
        if op == "mine-target":
            return list(defaultmine_target(case["mine"]))
        if op == "run-mine-target":
            identity = resolve_launch_identity(
                case["config"], profile_override=case.get("override"), slang=case["slang"]
            )
            return list(
                _resolvemine_target(identity.cfg, case.get("mine_deck"), case.get("mine_model"))
            )
    except ValueError as exc:
        return {"error": str(exc)}
    raise ValueError(f"unknown profile policy operation: {op!r}")


def _evaluate_config_policy(case: dict) -> object:
    from dataclasses import asdict

    from saitenka.app.config import (
        config_path,
        expand_paths,
        load_config,
        resolve_jlpt_underlines,
        resolve_resync_split_penalty,
        resolve_telemetry,
        subtitle_geometry_options,
        warn_retired,
    )

    op = case["op"]
    previous = {name: os.environ.get(name) for name in ("SAITENKA_CONFIG", "OTEL_SDK_DISABLED")}
    try:
        for name, value in case.get("env", {}).items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if op == "resync-split":
            return resolve_resync_split_penalty(case["config"])
        if op == "expand-paths":
            return expand_paths(case["paths"])
        if op == "telemetry":
            return asdict(resolve_telemetry(case["config"]))
        if op == "retired":
            return warn_retired(case["config"])
        if op == "geometry":
            return asdict(subtitle_geometry_options(case["config"]))
        if op == "jlpt":
            try:
                return resolve_jlpt_underlines(case["config"])
            except TypeError as exc:
                return {"error": str(exc)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if op == "load":
                path = root / "overlay.toml"
                if case.get("content") is not None:
                    path.write_text(case["content"], encoding="utf-8")
                return load_config(path)
            if op == "config-path":
                os.environ["SAITENKA_CONFIG"] = str(root / "env.toml")
                chosen = config_path(root / "explicit.toml" if case["explicit"] else None)
                return chosen.name
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    raise ValueError(f"unknown config policy operation: {op!r}")


def _evaluate_profile_switch(case: dict) -> object:
    from saitenka_tokenize import registry
    from saitenka_tokenize.languages import ReaderLanguages
    from saitenka_tokenize.registry import register_tokenizer

    from saitenka import fonts
    from saitenka.app.features.profiles.profile_controller import (
        ProfileAftermath,
        ProfileController,
        ProfileInvalidation,
        ProfileSubtitles,
        next_profile_index,
    )
    from saitenka.app.profiles import Profile

    class Tokenizer:
        name = "latin"

        def tokenize(self, *_args, **_kwargs):
            return []

    saved = dict(registry._FACTORIES)
    register_tokenizer("latin", Tokenizer)
    calls: list[list[object]] = []
    notices: list[list[str]] = []
    profiles = [
        Profile(
            item["name"],
            ReaderLanguages(item["main"], item.get("second", "en")),
            item.get("tokenizer", "latin"),
            item.get("slang"),
        )
        for item in case["profiles"]
    ]
    available = frozenset(case.get("available", []))
    controller = ProfileController(
        profiles[case.get("initial", 0)],
        None,
        ProfileInvalidation(
            lambda: calls.append(["invalidate-tokenizer"]),
            lambda: calls.append(["invalidate-dictionary"]),
            lambda: calls.append(["reset-warm"]),
        ),
        ProfileSubtitles(
            current_subtitle_slang=lambda: case.get("current_slang", "ja,jpn,jp"),
            current_second_slang=lambda: case.get("current_second", "en"),
            has_subtitle_track=lambda slang: slang in available,
            select_subtitle_track=lambda slang: calls.append(["select-primary", slang]),
            retokenize_current_cue=lambda: calls.append(["retokenize"]),
            select_subtitle_languages=lambda slang, second: (
                calls.append(["select-languages", slang, second]) or case.get("selection_changed")
            ),
            select_translation_track=lambda second: calls.append(["select-translation", second]),
            select_degraded_subtitle_languages=lambda slang, second: calls.append(
                ["select-degraded", slang, second]
            ),
        ),
        ProfileAftermath(
            warm_episode=lambda: calls.append(["warm"]),
            notify=lambda text, kind: notices.append([text, kind]),
        ),
    )
    controller.configure_cycle(profiles, base_slang=case.get("base_slang", "ja,jpn,jp"))
    try:
        if case.get("action") == "cycle":
            target = next_profile_index(len(profiles), controller.profile_index)
            status = "no-op" if target is None else controller.switch_to(target).status.value
        else:
            status = controller.switch_to(case["target"]).status.value
        return {
            "status": status,
            "profile": controller.profile.name,
            "main": controller.langs.main,
            "second": controller.langs.second,
            "tokenizer": controller.tokenizer.name,
            "index": controller.profile_index,
            "calls": calls,
            "notices": notices,
        }
    finally:
        registry._FACTORIES.clear()
        registry._FACTORIES.update(saved)
        fonts.set_primary_font(None)


def _evaluate_editor(scenario: dict):
    catalog = config_editor.catalog()
    by_label = {spec.label: spec for spec in catalog}
    by_path = {spec.toml_path: spec for spec in catalog}
    if scenario["mode"] == "editor-catalog":
        return {
            label: {
                "kind": by_label[label].kind,
                "choices": list(by_label[label].choices),
                "path": list(by_label[label].toml_path),
                "optional": by_label[label].optional,
                "help": bool(by_label[label].help),
                "default": by_label[label].default,
            }
            for label in scenario["fields"]
        }
    if scenario["mode"] == "editor-defaults":
        return [
            config_editor.current_default(
                by_path[tuple(check.get("path", [check["field"]]))], check["config"]
            )
            for check in scenario["checks"]
        ]
    if scenario["mode"] == "editor-coerce":
        results = []
        for check in scenario["checks"]:
            try:
                results.append(
                    {"value": config_editor.coerce(by_label[check["field"]], check["input"])}
                )
            except ValueError:
                results.append({"error": "ValueError"})
        return results
    raise ValueError(f"unknown editor mode: {scenario['mode']!r}")


def _evaluate_editor_session(scenario: dict) -> dict[str, object]:
    from saitenka.app import prompt

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "overlay.toml"
        if scenario["initial"] is not None:
            path.write_text(scenario["initial"], encoding="utf-8")
        by_label = {spec.label: spec for spec in config_editor.catalog()}
        selections: list[object] = []
        values: list[object] = []
        bools: dict[str, bool] = {}
        for edit in scenario["edits"]:
            selections.extend((edit["section"], edit["field"]))
            kind = by_label[edit["field"]].kind
            if kind == "select":
                selections.append(edit["value"])
            elif kind == "bool":
                bools[edit["field"]] = edit["value"]
            else:
                values.append(edit["value"])
        selects = iter([*selections, config_editor._DONE])
        texts = iter(values)
        original = prompt.select, prompt.text, prompt.confirm
        prompt.select = lambda *_args, **_kwargs: next(selects)
        prompt.text = lambda *_args, default, **_kwargs: (
            default if (value := next(texts)) is None else value
        )
        prompt.confirm = lambda message, **_kwargs: next(
            (value for label, value in bools.items() if message.startswith(label)), True
        )
        try:
            with redirect_stdout(StringIO()):
                exit_code = config_editor.run_editor(path)
        finally:
            prompt.select, prompt.text, prompt.confirm = original
        exists = path.exists()
        raw = path.read_text(encoding="utf-8") if exists else ""
        return {
            "exit_code": exit_code,
            "exists": exists,
            "config": tomllib.loads(raw) if raw else None,
            "comments": {comment: comment in raw for comment in scenario["observe_comments"]},
        }


def _read_path(value: object, path: str) -> object:
    for part in path.split("."):
        value = getattr(value, part)
    return value
