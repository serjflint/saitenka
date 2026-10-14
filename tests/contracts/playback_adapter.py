from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from saitenka.runtime.playback import (
    AuthoredCueStale,
    ConnectionChanged,
    CueIdentityRetired,
    CueObservationChanged,
    GeometryInputChanged,
    MediaPathChanged,
    PauseChanged,
    PlaybackProjection,
    PlaybackState,
    PointerMoved,
    RenderSpaceChanged,
    SecondaryTextChanged,
    SourceChanged,
    SubtitleSelectionChanged,
    SubtitleTimingChanged,
    SubtitleTracksChanged,
)

PROTOCOL = "saitenka.playback/v0"
CAPABILITY = "playback-v0"


def _identity(state: PlaybackState) -> dict[str, object]:
    return _observed_identity(state.identity())


def _observed_identity(identity) -> dict[str, object]:
    return {
        "source": identity.source.value,
        "track": identity.track.value,
        "role": identity.role,
        "text": identity.text,
        "start": identity.start,
        "end": identity.end,
        "cue": identity.cue.value,
    }


def _event(delta: object) -> dict[str, object]:
    if isinstance(delta, CueIdentityRetired):
        return {"kind": "cue-retired", "reason": delta.reason.value}
    if isinstance(delta, CueObservationChanged):
        return {"kind": "cue-observed", "identity": _observed_identity(delta.cue)}
    if isinstance(delta, SubtitleSelectionChanged):
        return {
            "kind": "subtitle-selection",
            "track": delta.track.value,
            "sid": delta.sid,
            "role": delta.role,
        }
    if isinstance(delta, AuthoredCueStale):
        return {"kind": "authored-cue-stale"}
    if isinstance(delta, SourceChanged):
        return {"kind": "source-changed", "source": delta.source.value}
    if isinstance(delta, ConnectionChanged):
        return {"kind": "connection-changed", "epoch": delta.epoch, "ready": delta.ready}
    if isinstance(delta, RenderSpaceChanged):
        return {
            "kind": "render-space-changed",
            "revision": delta.render_space.value,
            "property": delta.property_name,
        }
    if isinstance(delta, GeometryInputChanged):
        return {"kind": "geometry-input-changed", "property": delta.property_name}
    if isinstance(delta, SubtitleTimingChanged):
        return {"kind": "subtitle-timing-changed", "delay": delta.delay}
    if isinstance(delta, PointerMoved):
        return {"kind": "pointer-moved", "position": delta.position}
    if isinstance(delta, PauseChanged):
        return {"kind": "pause-changed", "paused": delta.paused}
    if isinstance(delta, SecondaryTextChanged):
        return {"kind": "secondary-text-changed", "value": delta.value}
    if isinstance(delta, MediaPathChanged):
        return {"kind": "media-path-changed", "path": delta.path}
    if isinstance(delta, SubtitleTracksChanged):
        return {"kind": "subtitle-tracks-changed"}
    raise TypeError(f"unsupported playback delta: {type(delta).__name__}")


def _apply(projection: PlaybackProjection, state: PlaybackState, action: dict):
    operation = action["operation"]
    if operation == "observe":
        return projection.observe(
            state,
            action["name"],
            action.get("value"),
            connection_epoch=action.get("connection_epoch"),
        )
    if operation == "cue-replaced":
        return SimpleNamespace(state=projection.cue_replaced(state, action["text"]), deltas=())
    if operation == "source-replaced":
        return projection.source_replaced(state, action.get("path"))
    if operation == "role-changed":
        return projection.role_changed(state, action["role"])
    if operation == "connection-changed":
        return projection.connection_changed(state, epoch=action["epoch"], ready=action["ready"])
    raise ValueError(f"unsupported playback-v0 operation: {operation}")


def evaluate(scenario: dict) -> dict[str, object]:
    projection = PlaybackProjection()
    state = PlaybackState()
    for name, value in scenario.get("given", {}).get("seed", {}).items():
        state = projection.seed(state, name, value)
    installed = scenario.get("given", {}).get("installed")
    if installed is not None:
        state = projection.install(state, start=installed.get("start"), end=installed.get("end"))
    for action in scenario.get("given", {}).get("prepare", []):
        state = _apply(projection, state, action).state

    events: list[dict[str, object]] = []
    for action in scenario["when"]:
        projected = _apply(projection, state, action)
        state = projected.state
        events.extend(_event(delta) for delta in projected.deltas)

    available = {
        "identity": _identity(state),
        "installed": state.cue.installed is not None,
        "authored_stale": state.cue.authored_stale,
        "connection": {"epoch": state.connection.epoch, "ready": state.connection.ready},
        "render_space": state.render_space.render_space.value,
        "delay": state.timing.delay,
    }
    return {"events": events, "state": {key: available[key] for key in scenario["observe"]}}


def main() -> int:
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("type") == "hello":
            if request.get("protocol") != PROTOCOL or request.get("requires") != [CAPABILITY]:
                return 2
            response = {"type": "hello", "protocol": PROTOCOL, "capabilities": [CAPABILITY]}
        elif request.get("type") == "run" and request.get("protocol") == PROTOCOL:
            response = {
                "type": "result",
                "protocol": PROTOCOL,
                "scenario_id": request["scenario_id"],
                "status": "ok",
                "observations": evaluate(request),
            }
        else:
            return 2
        sys.stdout.write(json.dumps(response, separators=(",", ":"), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
