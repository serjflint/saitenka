"""Own the colored copy of the Japanese track: when to make one, and when to put it on screen."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import colored_subs, subtitle_modes
from saitenka.runtime import EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.annotation.annotation_controller import AnnotationInputs
    from saitenka.app.token_cache import TokenizedCue
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime import EffectFinished

log = logging.getLogger(__name__)

LANE = "colored-track"
_SUFFIX = " (colored)"


def configure_runtime_job(ipc: MpvIPC) -> JobSubmitter | None:
    return configure_lane(ipc, LANE, JobLanePolicy(capacity=2), colored_subs.generate)


@dataclass(frozen=True, slots=True)
class ColoredTrackPorts:
    """What the colored track reads and does, named rather than reached for.

    `query` asks mpv now; `value` is the observed property, cheap enough for every draw.
    """

    query: Callable[[str], object]
    value: Callable[[str], object]
    cue_text: Callable[[], str]
    annotation_inputs: Callable[[], AnnotationInputs]
    event_annotation: Callable[..., TokenizedCue]
    token_generation: Callable[[], int]
    dependency_generation: Callable[[], int]
    track_ports: Callable[[], subtitle_modes.TrackPorts]


@dataclass(frozen=True, slots=True)
class _Source:
    origin: str
    path: Path
    title: str
    lang: str


@dataclass(frozen=True, slots=True)
class _Copy:
    source: _Source
    key: tuple[str, str, int]
    path: Path


def _selected(loaded: list[dict]) -> dict | None:
    return min(
        (track for track in loaded if track.get("selected")),
        key=lambda track: track.get("main-selection", 0),
        default=None,
    )


class ColoredTrackController:
    """One writer for the copy's lifecycle: generate off-thread, swap in a cue gap, stand down.

    Generation follows the same two signals as the episode token warm — a new cue index and newly
    arrived dependencies — because those are exactly when the colors can change.
    """

    def __init__(
        self, ports: ColoredTrackPorts, *, enabled: bool, submit: JobSubmitter | None
    ) -> None:
        self._ports = ports
        self._enabled = enabled
        self._submit = submit
        self._sequence = 0
        self._pending: tuple[str, str, int] | None = None
        self._ready: _Copy | None = None
        self._live: _Copy | None = None
        self._failed: tuple[str, str, int] | None = None
        #: The live copy was made under dependencies that have since changed.
        self._stale = False
        self._stand_down = False
        self._deferred = False

    @property
    def enabled(self) -> bool:
        return self._enabled and self._submit is not None

    def dependencies_changed(self) -> None:
        """The live copy's colors are now stale: replace it, or stand it down if nothing can."""
        self._sequence += 1
        self._pending = None
        self._ready = None
        self._failed = None
        self._stale = self._live is not None
        self.request()

    def request(self) -> None:
        """Generate a copy for the selected Japanese track unless the current one is still right."""
        if self.enabled and not self._request() and self._stale:
            self._stand_down = True
            self.try_swap()

    def _request(self) -> bool:
        """Whether the selected track has, or will have, a current copy."""
        inputs = self._ports.annotation_inputs()
        if inputs.scorer is None or not inputs.dependencies_ready or not inputs.annotate:
            return False
        loaded = self._loaded()
        selected = _selected(loaded)
        source = None if selected is None else self._source(loaded, selected)
        if selected is None or source is None:
            return False
        key = (str(self._ports.query("path")), source.origin, self._ports.dependency_generation())
        live = self._live
        if live is not None and live.key == key and self._is_live(selected):
            return True
        if key == self._failed:
            return False
        if key == self._pending or (self._ready is not None and self._ready.key == key):
            return True
        refusal = colored_subs.refusing_options(
            {name: self._ports.query(f"options/{name}") for name in colored_subs.REFUSING_OPTIONS}
        )
        if refusal is not None:
            _record("refused", reason=refusal)
            return False
        self._start(source, key, inputs)
        return True

    def _start(self, source: _Source, key: tuple[str, str, int], inputs: AnnotationInputs) -> None:
        assert self._submit is not None
        self._sequence += 1
        identity = self._sequence
        self._pending = key
        self._ready = None
        generation = self._ports.token_generation()

        def annotate(text: str) -> TokenizedCue:
            # A newer request supersedes this one; stop rather than tokenize for a discarded copy.
            if identity != self._sequence:
                raise colored_subs.Superseded
            return self._ports.event_annotation(text, inputs, generation=generation)

        colors = colored_subs.event_colors(annotate, inputs.tokenizer.is_skippable)
        request = colored_subs.ColorRequest(
            source.path, source.origin, f"{key[0]}\0{key[1]}", colors
        )

        def finished(completion: EffectFinished) -> None:
            self._finished(identity, source, key, completion)

        if not self._submit(
            owner=Owner.SUBTITLE,
            identity=(LANE, identity),
            lane=LANE,
            request=request,
            on_finished=finished,
        ):
            self._pending = None

    def _finished(
        self,
        identity: int,
        source: _Source,
        key: tuple[str, str, int],
        completion: EffectFinished,
    ) -> None:
        if identity != self._sequence:
            return
        self._pending = None
        result = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
        if isinstance(result, colored_subs.ColorResult):
            _record(result.reason, colored=result.colored, verbatim=result.verbatim)
        else:
            _record("failed")
        if isinstance(result, colored_subs.ColorResult) and result.path is not None:
            self._ready = _Copy(source, key, result.path)
        else:
            # Not retried on every warm signal; a new index or new dependencies try again.
            self._failed = key
            self._stand_down = self._stale
        self.try_swap()

    def try_swap(self) -> None:
        """Put a ready copy on screen, or take a stale one down, in a gap between cues.

        Not while paused on a line: re-selecting the track retires the cue and with it the tooltip
        the user paused to read.
        """
        if self._ready is None and not self._stand_down:
            return
        if self._ports.cue_text().strip():
            if not self._deferred:
                self._deferred = True
                _record("waiting-for-gap")
            return
        self._deferred = False
        if self._ready is not None:
            self._swap(self._ready)
        else:
            self._retire_copy()

    def _swap(self, ready: _Copy) -> None:
        self._ready = None
        loaded = self._loaded()
        selected = _selected(loaded)
        current = None if selected is None else self._source(loaded, selected)
        if selected is None or current is None or current.origin != ready.source.origin:
            _record("superseded")
            return
        # Before the swap: it rebuilds the index, which asks for a copy again.
        self._live = ready
        if not self._is_live(selected):
            with otel_metrics.traced("colored_track_swap") as span:
                sid = subtitle_modes.select_colored_copy(
                    self._ports.track_ports(),
                    ready.path,
                    title=ready.source.title,
                    lang=ready.source.lang,
                    previous=selected.get("id") if colored_subs.is_copy(selected) else None,
                )
                span.set("sid", str(sid))
            now = _selected(self._loaded())
            if now is None or not self._is_live(now):
                self._live = None
                self._failed = ready.key
                _record("swap-failed")
                return
            log.info("colored track selected: sid=%s", sid)
        self._stale = self._stand_down = False
        colored_subs.evict_siblings(ready.path)

    def _retire_copy(self) -> None:
        self._stand_down = False
        loaded = self._loaded()
        selected = _selected(loaded)
        origin = None if selected is None else colored_subs.origin_track(loaded, selected)
        if selected is None or not colored_subs.is_copy(selected) or origin is None:
            _record("stand-down-impossible")
            return
        self._live = None
        self._stale = False
        subtitle_modes.stand_down_colored_copy(
            self._ports.track_ports(), selected["id"], origin["id"]
        )
        _record("stood-down")

    def retire_episode(self) -> None:
        self._sequence += 1
        self._failed = None
        self._stale = self._stand_down = self._deferred = False
        self._pending = None
        self._ready = None
        self._live = None

    def _loaded(self) -> list[dict]:
        tracks = self._ports.query("track-list")
        return [
            track
            for track in (tracks if isinstance(tracks, list) else ())
            if isinstance(track, dict) and track.get("type") == "sub"
        ]

    def _is_live(self, selected: dict) -> bool:
        live = self._live
        return live is not None and selected.get("external-filename") == str(live.path)

    def _source(self, loaded: list[dict], selected: dict) -> _Source | None:
        """The authored document behind the selected track, or `None` when it cannot be colored."""
        if colored_subs.is_copy(selected):
            origin = colored_subs.copy_origin(selected)
            path = selected.get("external-filename")
            described = colored_subs.origin_track(loaded, selected) or selected
            return (
                None
                if origin is None or not path
                else _describe(origin, Path(str(path)), described)
            )
        origin = colored_subs.origin_ref(selected)
        if origin is None or any(colored_subs.copy_origin(track) == origin for track in loaded):
            # The user chose the origin over its loaded copy; the copy stands down.
            return None
        path = self._authored_path(selected)
        return None if path is None else _describe(origin, path, selected)

    def _authored_path(self, track: dict) -> Path | None:
        from saitenka.app.embedded_subs import embedded_subs_cache_dir, embedded_subs_cache_key

        external = track.get("external-filename")
        if track.get("external"):
            path = Path(str(external)) if external else None
            return path if path is not None and path.suffix.casefold() == ".ass" else None
        media = self._ports.query("path")
        ff_index = track.get("ff-index")
        if str(track.get("codec") or "") not in {"ass", "ssa"} or not media or ff_index is None:
            return None
        path = embedded_subs_cache_dir() / embedded_subs_cache_key(
            str(media), int(ff_index), ".ass"
        )
        return path if path.exists() else None


def _describe(origin: str, path: Path, track: dict) -> _Source:
    title = str(track.get("title") or track.get("lang") or "subtitles").removesuffix(_SUFFIX)
    return _Source(origin, path, f"{title}{_SUFFIX}", str(track.get("lang") or "jpn"))


def _record(outcome: str, **attributes: object) -> None:
    with otel_metrics.traced("colored_track") as span:
        span.set("outcome", outcome)
        for name, value in attributes.items():
            if name == "verbatim":
                for reason, count in value:  # type: ignore[attr-defined]
                    span.set(f"verbatim_{reason}", count)
            else:
                span.set(name, value)
    log.info("colored track: %s %s", outcome, attributes or "")
