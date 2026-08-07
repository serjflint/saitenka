"""Subtitle resync via alass (preferred) or ffsubsync (fallback via uvx).

Flow:
  1. Check for a ``<out>.synced`` marker — if present the file was already synced, skip.
  2. Try ``alass``/``alass-cli`` (on PATH); if absent try ``uvx ffsubsync``.
  3. On success, write the marker so the next run skips the tool.
  4. On failure (non-zero exit, timeout, tool absent) raise the appropriate exception so the
     caller can toast + fall back gracefully.

The public entry point for the jimaku path is :func:`maybe_resync` — it swallows
:exc:`ResyncUnavailable` and :exc:`ResyncFailed` and returns the original path, matching the
"graceful fallback" requirement.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

# Text subtitle codecs we can extract as an alignment reference (image subs — pgs/dvdsub — can't align).
_TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


def _reference_extract_spec(codec: str) -> tuple[str, list[str]]:
    """(reference file suffix, ffmpeg ``-c:s`` args) for an embedded sub of *codec*. ASS/SSA are COPIED
    to a native ``.ass`` — alass parses ASS directly, whereas ffmpeg's srt CONVERSION injects
    ``<font>``/``<b>`` tags a strict SubRip parser rejects (live: ep02's embedded credits line
    ``<b>Edição</b>`` → ``alass-cli`` exit 1 → subs left raw → several seconds late). subrip is copied
    verbatim; anything else (mov_text/webvtt) converts to srt, which is clean for those codecs."""
    if codec in ("ass", "ssa"):
        return ".ass", ["-c:s", "copy"]
    if codec in ("subrip", "srt"):
        return ".srt", ["-c:s", "copy"]
    return ".srt", ["-c:s", "srt"]


# The reference language, in preference order: the ENGLISH dialogue track is what the overlay already
# shows as top-subs and is present in virtually every multi-sub release — a deterministic, meaningful
# choice, unlike "the fullest text track" which ties arbitrarily to whatever language ffprobe lists
# first (a real release tied English/Italian/Portuguese at 278 cues → we were aligning to Portuguese).
_REF_PREFER_LANGS = ("eng", "en")


def _cue_count(s: dict) -> int:
    try:
        return int(s.get("tags", {}).get("NUMBER_OF_FRAMES", 0))
    except (TypeError, ValueError):
        return 0


def _pick_reference_stream(text: list[dict]) -> dict:
    """Choose the embedded text track to align against: the fullest ENGLISH track (matching the
    top-subs), or — when the release carries no English — the fullest track overall (most cues ⇒ a
    dialogue track, not a signs/songs one). All candidate tracks are co-timed against this encode, so
    the choice is about determinism + consistency with what's displayed, not timing per se."""
    for want in _REF_PREFER_LANGS:
        matches = [
            s for s in text if str(s.get("tags", {}).get("language", "")).lower().startswith(want)
        ]
        if matches:
            return max(matches, key=_cue_count)
    return max(text, key=_cue_count)


def _embedded_sub_reference(video: Path, workdir: Path) -> Path | None:
    """Extract a co-timed EMBEDDED subtitle track from *video* to use as the alignment reference.

    A multi-sub release (e.g. Crunchyroll's en/pt/es/… tracks) carries subtitles authored against THIS
    exact encode, so their cue timing is ground truth. Aligning the fetched JP subs to one of them is
    sub-to-sub — deterministic, and free of the audio-VAD / framerate guesswork that mistimes a
    different-broadcast source (found live: an AT-X rip was 30s out; via the embedded track it lands
    dead-on). Prefers the ENGLISH track the overlay already shows as top-subs (see
    :func:`_pick_reference_stream`). The extracted file is named ``reference.<lang>.<ext>`` so the log +
    telemetry show which track drove the sync. Returns the path in *workdir*, or None when there are no
    embedded text subs / the tools are missing."""
    from overlay.mpvio.discover import find_tool

    ffprobe, ffmpeg = find_tool("ffprobe"), find_tool("ffmpeg")
    if not ffprobe or not ffmpeg:
        return None

    # Fully fail-soft: a missing tool, an odd container, or a probe hiccup just means "no reference" —
    # resync falls back to the video's audio. It must never raise into the resync path.
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "s", "-show_entries",
             "stream=index,codec_name:stream_tags=language,NUMBER_OF_FRAMES", "-of", "json", str(video)],
            capture_output=True, text=True, check=False, timeout=30,
        )  # fmt: skip
        if probe.returncode != 0:
            return None
        text = [
            s
            for s in json.loads(probe.stdout).get("streams", [])
            if s.get("codec_name") in _TEXT_SUB_CODECS
        ]
        if not text:
            return None
        chosen = _pick_reference_stream(text)
        lang = str(chosen.get("tags", {}).get("language", "") or "und").lower()
        suffix, codec_args = _reference_extract_spec(chosen.get("codec_name", ""))
        ref = workdir / f"reference.{lang}{suffix}"
        extracted = subprocess.run(
            [ffmpeg, "-y", "-i", str(video), "-map", f"0:{chosen['index']}",
             *codec_args, str(ref)],
            capture_output=True, check=False, timeout=120,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        log.debug("embedded-sub reference extraction failed", exc_info=True)
        return None
    return ref if extracted.returncode == 0 and ref.exists() else None


_CUE_START = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->")  # SRT: HH:MM:SS,mmm -->
_ASS_START = re.compile(
    r"(?m)^Dialogue:[^,]*,(\d+):(\d\d):(\d\d)\.(\d\d)"
)  # ASS Dialogue start, cc


def _cue_starts_ms(path: Path, k: int = 8) -> list[int]:
    """First up-to-*k* cue start times (ms) — a compact, TEXT-FREE timing fingerprint of a subtitle
    file. Integers only: safe to put in a shared trace (no copyrighted cue text) and enough to see the
    shape of an alignment — a no-op shows as src==out while the reference differs — and to replay the
    case as an offline regression from the vectors alone. Handles SRT and ASS (the native format an
    embedded reference is extracted in). Fail-soft: unreadable/cueless → []."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    starts: list[int] = []
    for h, m, s, ms in (map(int, g.groups()) for g in _CUE_START.finditer(text)):
        starts.append(((h * 60 + m) * 60 + s) * 1000 + ms)
        if len(starts) >= k:
            return starts
    if starts:
        return starts
    for h, m, s, cs in (map(int, g.groups()) for g in _ASS_START.finditer(text)):  # cc → ms (×10)
        starts.append(((h * 60 + m) * 60 + s) * 1000 + cs * 10)
        if len(starts) >= k:
            break
    return starts


def _first_cue_start_ms(path: Path) -> int | None:
    """Start time (ms) of a subtitle file's first cue, or None if it has none / can't be read — the
    anchor for the shift a resync applied. Fail-soft: this feeds telemetry only, never gates resync."""
    starts = _cue_starts_ms(path, 1)
    return starts[0] if starts else None


def _first_cue_shift_ms(src: Path, out: Path) -> int | None:
    """How far resync moved the first cue (out − src, ms). ~0 means the tool computed no offset — subs
    already aligned OR a silent VAD no-op — the difference between right and mistimed subs, so it's
    worth surfacing rather than hiding behind an indistinguishable 'synced' result."""
    a, b = _first_cue_start_ms(src), _first_cue_start_ms(out)
    return None if a is None or b is None else b - a


class ResyncUnavailable(RuntimeError):
    """Neither alass nor uvx is on PATH — resync cannot be performed."""


class ResyncFailed(RuntimeError):
    """The resync tool ran but returned a non-zero exit code or timed out."""


def _marker(out: Path) -> Path:
    """Path of the cache marker for ``out``."""
    return out.with_suffix(out.suffix + ".synced")


#: alass ships its binary as ``alass`` from some builds and ``alass-cli`` from others (Homebrew's
#: ``alass`` formula installs ``alass-cli``) — check both, else a working alass install silently loses
#: to ffsubsync, whose single-offset sync can't fix the per-scene drift alass split-aligns (found live).
_ALASS_BINS = ("alass", "alass-cli")


def _resync_command(
    reference: Path, src: Path, out: Path, *, split_penalty: float | None = None
) -> tuple[list[str], str]:
    """The aligner command + a label, preferring alass (split/framerate-aware, far better on anime)
    over ``uvx ffsubsync`` (single global offset). ``reference`` is what ``src`` is aligned TO — a
    video (audio sync) OR a subtitle file (sub-to-sub); both tools accept either as the first arg.
    ``split_penalty`` (when set) tunes alass's ``--split-penalty`` — lower splits more readily at an
    OP/scene boundary; ffsubsync has no equivalent, so it's ignored there.
    Raises :exc:`ResyncUnavailable` if neither exists."""
    from overlay.mpvio.discover import find_tool

    for name in _ALASS_BINS:
        alass = find_tool(name)  # find_tool augments PATH for a GUI-launched (minimal-PATH) process
        if alass:
            penalty = [] if split_penalty is None else ["--split-penalty", f"{split_penalty:g}"]
            return [alass, *penalty, str(reference), str(src), str(out)], name
    uvx = find_tool("uvx")
    if uvx:
        return [uvx, "ffsubsync", str(reference), "-i", str(src), "-o", str(out)], "uvx ffsubsync"
    raise ResyncUnavailable(
        "subtitle resync requires alass or ffsubsync; install alass (brew install alass)"
        " or ensure uvx is on PATH"
    )


def resync(
    video: Path,
    src: Path,
    out: Path,
    *,
    timeout: int | None = None,
    force: bool = False,
    details: dict | None = None,
    split_penalty: float | None = None,
) -> Path:
    """Synchronise ``src`` to ``video`` and write the result to ``out``.

    Uses ``alass``/``alass-cli`` if on PATH, else ``uvx ffsubsync``.  Writes a ``<out>.synced``
    marker on success so subsequent calls are no-ops; ``force`` re-runs even when the marker exists
    (the user's re-sync shortcut).

    Parameters
    ----------
    video:
        The reference video file (for audio-based sync).
    src:
        The subtitle file to resync.
    out:
        Destination path for the resynced subtitle.
    timeout:
        Maximum seconds to wait for the subprocess (``None`` resolves the ``resync_timeout``
        config value, default 300).

    Returns
    -------
    Path
        ``out`` (the resynced subtitle path).

    Raises
    ------
    ResyncUnavailable
        Neither ``alass`` nor ``uvx`` is found on PATH.
    ResyncFailed
        The tool exited with a non-zero code or timed out.
    """
    if timeout is None:
        from overlay.app.config import resolve_resync_timeout

        timeout = resolve_resync_timeout()
    if split_penalty is None:
        from overlay.app.config import resolve_resync_split_penalty

        split_penalty = resolve_resync_split_penalty()
    marker = _marker(out)
    if not force and marker.exists() and out.exists():
        log.debug("resync: cache hit for %s — skipping", out.name)
        return out

    workdir = out.parent / f".{out.stem}.refwork"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        # Prefer a co-timed embedded sub as the reference (sub-to-sub, exact-encode timing); the video's
        # audio is the fallback only when the file carries no embedded text track.
        ref = _embedded_sub_reference(video, workdir) or video
        cmd, tool = _resync_command(ref, src, out, split_penalty=split_penalty)
        if (
            details is not None
        ):  # captured before the workdir (holding the embedded ref) is cleaned up
            details["tool"] = tool
            details["reference"] = "audio" if ref is video else "embedded"
            details["reference_fmt"] = (
                "" if ref is video else ref.suffix
            )  # .ass vs .srt (the codec bug)
            # reference.<lang>.<ext> → the track's language (eng/por/…), so a report shows which we used
            details["reference_lang"] = (
                ref.suffixes[0].lstrip(".") if ref is not video and len(ref.suffixes) >= 2 else ""
            )
            details["split_penalty"] = split_penalty
            details["ref_cue_ms"] = _cue_starts_ms(ref)
        log.info(
            "resync: running %s on %s (ref=%s, split_penalty=%s)",
            tool, src.name, ref.name, split_penalty,
        )  # fmt: skip
        try:
            result = subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ResyncFailed(f"resync timed out after {timeout}s ({tool})") from exc
        if result.returncode != 0:
            # alass writes its parse errors to STDOUT, not stderr — capture both, else the failure is a
            # blank message (live: "resync failed (exit 1) via alass-cli:" hid an unparseable reference).
            detail = " ".join(
                s.decode(errors="replace").strip() for s in (result.stdout, result.stderr) if s
            ).strip()
            raise ResyncFailed(
                f"resync failed (exit {result.returncode}) via {tool}: {detail[:300]}"
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    marker.touch()
    log.debug("resync: wrote %s", out.name)
    return out


def _record_resync_details(span, details: dict, *, src: Path, out: Path | None) -> None:
    """Attach the aligner (tool + reference kind) and a text-free cue fingerprint of src/out/reference
    to the resync span, so a report can (a) show which tool/reference actually ran and (b) be replayed
    as an offline regression from integer cue vectors alone — no copyrighted cue text. All optional:
    an unavailable run (nothing selected) simply has no tool/reference in *details*."""
    if details.get("tool"):
        span.set("tool", details["tool"])
    if details.get("reference"):
        span.set("reference", details["reference"])
    if details.get("reference_fmt"):
        span.set("reference_fmt", details["reference_fmt"])
    if details.get("reference_lang"):
        span.set("reference_lang", details["reference_lang"])
    if details.get("split_penalty") is not None:
        span.set("split_penalty", details["split_penalty"])
    span.set("ref_cue_ms", details.get("ref_cue_ms", []))
    span.set("src_cue_ms", _cue_starts_ms(src))
    span.set("out_cue_ms", _cue_starts_ms(out) if out is not None else [])


def maybe_resync(
    video: Path,
    src: Path,
    *,
    enabled: bool = True,
    timeout: int | None = None,
    force: bool = False,
    trigger: str = "auto",
) -> Path:
    """Resync ``src`` to ``video`` if *enabled*, returning the synced path.

    Swallows :exc:`ResyncUnavailable` and :exc:`ResyncFailed` — in both cases the original
    ``src`` is returned so the caller can proceed with unsynced subtitles.  A warning is
    logged so the issue is visible in the rotating log without crashing the overlay.

    The output path is placed next to ``src`` with the stem extended by ``.synced``
    (e.g. ``ep01.srt`` → ``ep01.synced.srt``). ``force`` re-runs even past the ``.synced`` marker.
    ``trigger`` (``auto`` initial fetch / ``retry`` the manual re-sync key) tags the span so repeated
    presses are attributable in a report.
    """
    if not enabled:
        return src

    from overlay import otel_metrics

    out = src.with_name(src.stem + ".synced" + src.suffix)
    # A span + INFO log per resync so a report shows whether it ran, its outcome, the shift it applied,
    # and — via the cue fingerprints — enough to replay it offline. Without this a silent fallback-to-raw
    # (tool missing/failed) and a real-but-zero-offset sync are indistinguishable — the ep03 "synced"
    # cache was byte-identical to raw, mistimed subs.
    with otel_metrics.traced("subtitle.resync") as span:
        span.set("trigger", trigger)
        details: dict = {}
        try:
            result = resync(video, src, out, timeout=timeout, force=force, details=details)
        except ResyncUnavailable as exc:
            span.set("outcome", "unavailable")
            log.warning("subtitle resync unavailable — using original (install alass): %s", exc)
            return src
        except ResyncFailed as exc:
            span.set("outcome", "failed")
            span.set(
                "fail_reason", str(exc)[:200]
            )  # the aligner's own error (alass writes it to stdout)
            _record_resync_details(span, details, src=src, out=None)
            log.warning("subtitle resync failed — using original: %s", exc)
            return src
        shift_ms = _first_cue_shift_ms(src, result)
        span.set("outcome", "synced")
        span.set("shift_ms", shift_ms if shift_ms is not None else 0)
        _record_resync_details(span, details, src=src, out=result)
        log.info(
            "resync: %s → shift %+dms (0 = tool applied no offset)", result.name, shift_ms or 0
        )
        return result


def resync_current(video: Path, sub: Path, *, timeout: int | None = None) -> Path:
    """Re-align an EXISTING subtitle file to *video* — the user's re-sync shortcut. No provider fetch
    (you already have the subs; you only need them re-timed). Forces a fresh run past the ``.synced``
    marker. For OUR cache files, overwrites in place so the persisted cache also picks up the new
    timing; a user-supplied ``--sub-file`` is never clobbered — its ``.synced`` sibling is returned
    instead. Returns ``sub`` unchanged on failure."""
    synced = maybe_resync(video, sub, enabled=True, force=True, timeout=timeout, trigger="retry")
    if synced == sub or not synced.exists():
        return sub  # tool missing / failed → nothing re-timed
    from overlay.app.subtitle_cache import subs_cache_dir

    try:
        sub.resolve().relative_to(subs_cache_dir().resolve())
    except ValueError:
        return synced  # not ours (a user --sub-file) → play the sibling, don't overwrite their file
    shutil.copy2(str(synced), str(sub))  # our cache file → persist the aligned timing in place
    return sub


def _fmt_srt_ts(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(path: Path, cues) -> None:
    """Serialize ``SubCue``\\s to an SRT file (the format both alass and mpv read unconditionally)."""
    blocks = [
        f"{i}\n{_fmt_srt_ts(c.start)} --> {_fmt_srt_ts(c.end)}\n{c.text}\n"
        for i, c in enumerate(cues, 1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def _parse_cues(path: Path):
    from overlay.app.sub_index import parse_ass, parse_srt

    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_ass(text) if path.suffix.lower() == ".ass" else parse_srt(text)


def _persist_windowed(sub: Path, tmp: Path) -> Path:
    """Write the re-timed result to a stable ``<base>.win.srt`` sibling (idempotent across repeated
    presses — a second window on an already-windowed file reuses the same name, never ``.win.win``).
    Never clobbers the original cache/``--sub-file``, so a bad window is one keypress from undone."""
    stem = sub.stem.removesuffix(".win")
    dest = sub.with_name(f"{stem}.win.srt")
    shutil.copy2(str(tmp), str(dest))
    return dest


def _windowed_offset(
    video: Path,
    window: list,
    workdir: Path,
    span,
    *,
    timeout: int | None,
    split_penalty: float | None,
) -> float | None:
    """Extract the reference, align *window* to it, and return the MEDIAN offset the aligner applied
    (robust to a few unmatched SFX cues) — or None on any failure, tagging *span* with tool/reference +
    a ``fail_reason``. The whole windowed alignment lives here so :func:`resync_window` stays the thin
    parse→splice→persist orchestration."""
    import statistics

    ref = _embedded_sub_reference(video, workdir)
    if ref is None:  # windowed alignment needs a reference; audio VAD on a slice is unreliable
        span.set("fail_reason", "no embedded reference")
        return None
    win_srt, out_srt = workdir / "window.srt", workdir / "window.synced.srt"
    _write_srt(win_srt, window)
    try:
        cmd, tool = _resync_command(ref, win_srt, out_srt, split_penalty=split_penalty)
    except ResyncUnavailable as exc:
        span.set("fail_reason", str(exc))
        return None
    span.set("tool", tool)
    span.set("reference", "embedded")
    span.set("reference_lang", ref.suffixes[0].lstrip(".") if len(ref.suffixes) >= 2 else "")
    if split_penalty is not None:
        span.set("split_penalty", split_penalty)
    try:
        result = subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)
    except subprocess.TimeoutExpired:
        span.set("fail_reason", "timeout")
        return None
    if result.returncode != 0 or not out_srt.exists():
        detail = " ".join(
            s.decode(errors="replace").strip() for s in (result.stdout, result.stderr) if s
        )
        span.set("fail_reason", detail[:200])
        return None
    aligned = _parse_cues(out_srt)
    if len(aligned) != len(window):  # alass should preserve cue count; bail if it didn't
        span.set("fail_reason", "cue count changed")
        return None
    return statistics.median(a.start - w.start for a, w in zip(aligned, window, strict=True))


def resync_window(
    video: Path,
    sub: Path,
    *,
    start_s: float,
    lookback_s: float = 20.0,
    timeout: int | None = None,
    split_penalty: float | None = None,
) -> Path | None:
    """Re-time the CURRENT subtitle segment from ~``start_s`` onward against the embedded reference —
    for a source that DRIFTS across the episode (a fixed whole-file offset that's right after the OP is
    early before it; found live: NanakoRaws ep02 ran +4.7s pre-OP → +11s post-OP). Aligns only the
    window ``[start_s - lookback_s, end]`` and shifts those cues by the median offset the aligner found,
    leaving earlier cues untouched — press again at the next drift point. Returns the re-timed
    ``<base>.win.srt`` path; ``sub`` unchanged when the window is already aligned (no net shift); or None
    on a hard failure (no reference / too few cues / tool failure), so the caller can fall back to a
    whole-file re-sync."""
    from overlay import otel_metrics
    from overlay.app.sub_index import SubCue

    if timeout is None:
        from overlay.app.config import resolve_resync_timeout

        timeout = resolve_resync_timeout()
    if split_penalty is None:
        from overlay.app.config import resolve_resync_split_penalty

        split_penalty = resolve_resync_split_penalty()

    with otel_metrics.traced("subtitle.resync") as span:
        span.set("trigger", "window")
        span.set("window_start_s", round(start_s, 1))
        boundary = max(0.0, start_s - lookback_s)
        try:
            cues = _parse_cues(sub)
        except OSError:
            span.set("outcome", "failed")
            return None
        window = [c for c in cues if c.start >= boundary]
        span.set("window_cues", len(window))
        if len(window) < 2:  # too little to correlate a reliable offset
            span.set("outcome", "failed")
            span.set("fail_reason", "too few cues in window")
            return None

        workdir = sub.parent / f".{sub.stem}.winwork"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            delta = _windowed_offset(
                video, window, workdir, span, timeout=timeout, split_penalty=split_penalty
            )
            if delta is None:
                span.set("outcome", "failed")
                return None
            span.set("window_delta_ms", round(delta * 1000))
            if abs(delta) < 0.001:  # already aligned here → sub unchanged (distinct from failure)
                span.set("outcome", "synced")
                return sub
            new_cues = [
                SubCue(c.start + delta, c.end + delta, c.text) if c.start >= boundary else c
                for c in cues
            ]
            tmp = workdir / "result.srt"
            _write_srt(tmp, new_cues)
            out_path = _persist_windowed(sub, tmp)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        span.set("outcome", "synced")
        span.set("src_cue_ms", _cue_starts_ms(sub))
        span.set("out_cue_ms", _cue_starts_ms(out_path))
        log.info(
            "resync: windowed from %.1fs → shifted %d cue(s) by %+dms",
            start_s, len(window), round(delta * 1000),
        )  # fmt: skip
        return out_path
