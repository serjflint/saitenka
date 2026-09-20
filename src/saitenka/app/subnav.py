"""Indexed subtitle navigation: present and seek to one selected destination."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import subnav_policy, subnav_settle
from saitenka.app.sub_index import load_index
from saitenka.app.subtitle_geometry_diagnostics import cue_digest

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka_subtitles import Cue

    from saitenka.app.features.subtitle.navigation_state import NavigationState
    from saitenka.app.native_subtitles import NativeSubtitleGeometry

    PropertyGet = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class NavPorts:
    """What navigating cues needs from the session.

    `episode` is the container itself, not five callables over it: every nav field these functions
    read and write is one of its own, and reaching them through the host's flat names is what hid
    that they are one lifetime. Writing `episode.nav_idx` also puts the write where the reset is,
    so a re-slot cannot leave a stale hint behind.
    """

    episode: NavigationState
    geometry: NativeSubtitleGeometry | None
    get: PropertyGet
    cue_text: Callable[[], str]
    cue_retired: Callable[[], bool]
    draw_cue: Callable[..., None]
    replace_source: Callable[..., None]
    invalidate: Callable[[], None]
    open_settle: Callable[[], None]
    retire_settle: Callable[[], None]
    warm_tokens: Callable[[], None]
    index_changed: Callable[[], None]
    geometry_hint: Callable[[Cue | None], None]


def load_sub_index(ports: NavPorts, path) -> None:
    """Parse the external subtitle file at ``path`` into a cue index so Alt+←/→/↓ can render the
    target line instantly. Fail-soft: an unreadable/empty/unsupported file RETAINS the prior cues
    (a transient track-switch/resolve failure must not blank a good index) — navigation still falls
    back to a plain mpv sub-seek when there was never an index."""
    idx = load_index(path)
    if idx is None:
        return
    ports.replace_source(path, reason="subtitle-index")
    ports.episode.sub_index = idx
    if ports.geometry is not None:
        ports.geometry.set_source(Path(path), live=True)

    ports.invalidate()
    # Reinstall the cue that is still on screen under the new source. `replace_source`
    # retired its identity, and nothing re-derives one: cue arrival is event-driven, so mpv sends
    # nothing until its *next* sub-text change and the overlay stays blank until then. Same
    # reconcile `start_observing` runs after seeding, for the same reason.
    reconcile_sub_text(ports, ports.cue_text())
    ports.index_changed()
    ports.warm_tokens()  # warm the whole episode's cues into the token cache (bg, best-effort)


def _get_float(get: Callable[[str], object], prop: str) -> float | None:
    """Read one mpv property as a float, or None if it is absent or not numeric.

    Takes the getter rather than the host *or* the IPC: taking `ipc` would only trade this row of
    `reader-parameter` debt for a row of `direct-mpv-read`, since `SessionController._get` is a bare
    `get_property`. A callable leaves the caller owning how the read happens.
    """
    v = get(prop)  # a direct get_property is fine: nav keys are rare, not per-tick
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]  # TypeError is the point of the except below
    except (TypeError, ValueError):
        return None


def sub_nav(ports: NavPorts, delta: int) -> float | None:
    """Present an indexed cue and return its absolute playback destination.

    A missing destination delegates to mpv without publishing a provisional cue.
    """
    episode = ports.episode
    idx = episode.sub_index
    if idx is None:
        return None
    delay = subnav_policy.indexed_delay(ports.get)
    if delay is None:
        return None
    # The index is the subtitle FILE's cues; mpv's filters run between the file and the screen. With
    # one active, stepping by index can land on a cue mpv drops — the instant render shows a line
    # that never appears and the seek settles somewhere else. mpv's own `sub-seek` cannot make that
    # mistake, so the instant half is given up rather than aimed at silence.
    if subnav_policy.filters_can_drop_a_cue(
        {name: ports.get(f"options/{name}") for name in subnav_policy.FILTER_OPTIONS}
    ):
        return None
    with otel_metrics.instrumented(otel_metrics.sub_seek_duration_ms, "sub_seek") as span:
        target = subnav_policy.resolve_target(
            idx,
            delta=delta,
            text=ports.cue_text(),
            sub_start=subnav_policy.subtitle_time(_get_float(ports.get, "sub-start"), delay),
            time_pos=subnav_policy.subtitle_time(_get_float(ports.get, "time-pos"), delay),
            nav_idx=episode.nav_idx,
        )
        if target is None:
            return None  # no index match / out of range → let mpv's sub-seek handle it
        destination = subnav_policy.seek_position(target.cue, delay)
        if destination is None:
            return None
        span.set("seek_position", destination)
        span.set("delta", delta)
        span.set("target_index", target.index)
        span.set("target_start", target.cue.start)
        # A step measured inside an overlap and one measured from a lone cue land the user in
        # different places, and only the trace can tell them apart afterwards.
        span.set("overlapping", target.overlapping)
        # The digest of what gets DRAWN, so this span joins the draw it causes. Digesting the
        # stepped-to event instead gave an overlap two identities and split one wait across them.
        span.set("cue", cue_digest(target.drawn_text))
        # Earlier seeks can still report any cue superseded in this navigation burst.
        episode.nav_prev_text = ports.cue_text()
        previous = episode.nav_superseded_texts if episode.sub_settle.open else frozenset()
        episode.nav_superseded_texts = previous | {episode.nav_prev_text}
        ports.geometry_hint(target.cue)
        try:
            ports.draw_cue(
                target.drawn_text,
                provisional_navigation=True,
            )  # instant overlay render (also resets nav_idx)
        finally:
            ports.geometry_hint(None)
        episode.nav_idx = target.index
        # Guard the reconcile: mpv's sub-text briefly reads empty (or the pre-nav cue) mid-seek;
        # ignoring that avoids reverting the render before it settles.
        ports.open_settle()
    return destination


def reconcile_sub_text(ports: NavPorts, text: str, *, confirmed: bool = True) -> None:
    """Adopt observations, absorbing superseded navigation cues until the seek settles."""
    # Empty is a stable retired state: the first transition already cleared every interaction
    # surface, so reinstalling the same empty observation would only repeat teardown every poll.
    episode, drawn = ports.episode, ports.cue_text()
    if text == drawn and (not ports.cue_retired() or not text.strip()):
        if confirmed:
            ports.retire_settle()
        return
    identity_reinstall = text == drawn and ports.cue_retired()
    window = episode.sub_settle
    if subnav_settle.swallows(
        window,
        text=text,
        nav_prev_text=episode.nav_prev_text,
        superseded_texts=episode.nav_superseded_texts,
        identity_reinstall=identity_reinstall,
    ):
        return
    # Only spans an actual cue change (guarded above), not every poll tick — sibling to sub_nav's
    # "sub_seek" span, but for changes mpv itself drove (native sub-seek key bound in the lua
    # script, or a normal cue advance during playback) rather than our own instant-nav.
    # set_subtitle's "cue_redraw" span nests inside, so this span's duration is the best proxy this
    # process has for "mpv-observed sub-text change → overlay drawn" — it can't see when the seek
    # command itself was issued (that's mpv-internal / lua-side).
    with otel_metrics.instrumented(
        otel_metrics.sub_text_reconcile_duration_ms, "sub_text_reconcile", cue=cue_digest(text)
    ):
        nav_idx = episode.nav_idx
        if identity_reinstall and window.open and episode.nav_provisional_cue_counted:
            ports.draw_cue(text, revise_session_cue=True)
        else:
            ports.draw_cue(text)
        episode.nav_provisional_cue_counted = False
        if identity_reinstall:
            episode.nav_idx = nav_idx
    if confirmed:
        ports.retire_settle()
